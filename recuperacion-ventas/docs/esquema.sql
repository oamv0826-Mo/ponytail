-- Esquema de datos de una instancia (un negocio). Fuente única: rv/base.py aplica cada sección
-- "-- version: N" cuya N sea mayor que PRAGMA user_version, en orden, dentro de una transacción.
-- Nunca editar una sección ya publicada: agregar una nueva al final.
-- Fechas/horas: texto ISO 8601 UTC 'AAAA-MM-DDTHH:MM:SSZ'. Fechas de venta: 'AAAA-MM-DD' local.

-- version: 1
CREATE TABLE contacto (
  id INTEGER PRIMARY KEY,
  telefono TEXT NOT NULL UNIQUE,              -- E.164, ej. +528112345678
  wa_id TEXT,                                 -- como lo manda Meta; se usa para responder
  nombre TEXT NOT NULL DEFAULT '',
  origen TEXT NOT NULL DEFAULT 'entrante' CHECK (origen IN ('entrante', 'importado')),
  consentimiento INTEGER NOT NULL DEFAULT 0,  -- 1 = aceptó mensajes de marketing (reactivación)
  ultima_visita TEXT,
  creado TEXT NOT NULL,
  primer_entrante TEXT,
  ultimo_entrante TEXT,                       -- abre la ventana de 24 h
  estado TEXT NOT NULL DEFAULT 'bot' CHECK (estado IN ('bot', 'humano')),
  asignado_a TEXT,
  handoff_desde TEXT,
  handoff_motivo TEXT,
  aviso_pendiente INTEGER NOT NULL DEFAULT 0, -- handoff fuera de horario: avisar al abrir
  escalado TEXT,                              -- último escalamiento (para no repetir)
  propuesta TEXT,                             -- JSON: horarios ofrecidos o cancelación por confirmar
  seg_activo INTEGER NOT NULL DEFAULT 0,      -- seguimiento 2/5/10
  seg_inicio TEXT,
  seg_paso INTEGER NOT NULL DEFAULT 0,        -- envíos ya hechos (0..3)
  seg_servicio TEXT,
  reactivacion_enviada TEXT
);

CREATE TABLE mensaje (
  id INTEGER PRIMARY KEY,
  contacto_id INTEGER REFERENCES contacto(id), -- NULL para avisos al equipo
  telefono TEXT NOT NULL,
  direccion TEXT NOT NULL CHECK (direccion IN ('in', 'out')),
  tipo TEXT NOT NULL CHECK (tipo IN ('texto', 'plantilla', 'otro')),
  texto TEXT NOT NULL DEFAULT '',
  plantilla TEXT,
  autor TEXT NOT NULL,                         -- cliente | bot | sistema | humano:<usuario>
  wa_id TEXT UNIQUE,                           -- id de mensaje de Meta
  estado TEXT NOT NULL DEFAULT 'recibido',     -- recibido | pendiente | prueba | enviado | sent | delivered | read | failed | error
  error TEXT,
  creado TEXT NOT NULL
);
CREATE INDEX mensaje_contacto ON mensaje(contacto_id, creado);

CREATE TABLE entrada (                         -- cola persistente del webhook
  id INTEGER PRIMARY KEY,
  clave TEXT NOT NULL UNIQUE,                  -- m:<message_id> | s:<id>:<status>  (dedupe)
  payload TEXT NOT NULL,
  recibido TEXT NOT NULL,
  procesado TEXT,
  error TEXT
);
CREATE INDEX entrada_pendiente ON entrada(procesado, id);

CREATE TABLE optout (
  telefono TEXT PRIMARY KEY,
  creado TEXT NOT NULL,
  origen TEXT NOT NULL
);

CREATE TABLE cita (
  id INTEGER PRIMARY KEY,
  contacto_id INTEGER NOT NULL REFERENCES contacto(id),
  servicio_id TEXT NOT NULL,
  inicio TEXT NOT NULL,
  fin TEXT NOT NULL,
  evento_id TEXT,                              -- id del evento en Google Calendar
  estado TEXT NOT NULL DEFAULT 'agendada' CHECK (estado IN ('agendada', 'cancelada', 'asistio', 'no_asistio')),
  creado TEXT NOT NULL,
  creado_por TEXT NOT NULL,                    -- bot | humano:<usuario>
  rec24 TEXT,
  rec2 TEXT,
  asistio_en TEXT,
  resena_enviada TEXT
);
CREATE INDEX cita_inicio ON cita(estado, inicio);

CREATE TABLE venta (
  id INTEGER PRIMARY KEY,
  contacto_id INTEGER NOT NULL REFERENCES contacto(id),
  cita_id INTEGER REFERENCES cita(id),
  monto_centavos INTEGER NOT NULL CHECK (monto_centavos > 0),
  fecha TEXT NOT NULL,
  origen TEXT NOT NULL,                        -- reactivacion | seguimiento | fuera_horario | respuesta_rapida | sin_atribucion
  registrado_por TEXT NOT NULL,
  creado TEXT NOT NULL,
  UNIQUE (contacto_id, fecha, monto_centavos)
);

CREATE TABLE evento (                          -- bitácora para atribución y reporte
  id INTEGER PRIMARY KEY,
  contacto_id INTEGER REFERENCES contacto(id),
  tipo TEXT NOT NULL,                          -- fuera_horario | handoff | seguimiento | reactivacion | resena | cita | baja | intencion
  creado TEXT NOT NULL,
  detalle TEXT NOT NULL DEFAULT ''
);
CREATE INDEX evento_contacto ON evento(contacto_id, tipo, creado);

CREATE TABLE ia_uso (
  id INTEGER PRIMARY KEY,
  creado TEXT NOT NULL,
  modelo TEXT NOT NULL,
  tokens_entrada INTEGER NOT NULL,
  tokens_salida INTEGER NOT NULL,
  tokens_cache_escritura INTEGER NOT NULL DEFAULT 0,
  tokens_cache_lectura INTEGER NOT NULL DEFAULT 0,
  costo_micro_usd INTEGER NOT NULL
);

CREATE TABLE usuario (
  nombre TEXT PRIMARY KEY,
  hash TEXT NOT NULL,
  sal TEXT NOT NULL,
  creado TEXT NOT NULL
);

CREATE TABLE sesion (
  token_sha256 TEXT PRIMARY KEY,
  usuario TEXT NOT NULL REFERENCES usuario(nombre),
  expira TEXT NOT NULL
);

CREATE TABLE estado (                          -- pares clave/valor de la instancia
  clave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);

-- version: 2
-- Recuperación tras caída: una entrada reclamada (procesado) sin terminar se vuelve a procesar.
ALTER TABLE entrada ADD COLUMN terminado TEXT;
UPDATE entrada SET terminado = procesado WHERE procesado IS NOT NULL;
CREATE INDEX entrada_sin_terminar ON entrada(terminado, id);

-- version: 3
-- Archivos del cliente (foto, nota de voz, documento): id de Meta para descargarlos desde la bandeja.
ALTER TABLE mensaje ADD COLUMN media_id TEXT;
ALTER TABLE mensaje ADD COLUMN media_mime TEXT;

-- version: 4
-- Ventas: dos citas distintas pueden tener ventas del mismo monto el mismo día. Duplicado = misma cita, fecha y monto
-- (doble clic) o, sin cita, mismo contacto, fecha y monto (reimportar el mismo CSV).
-- (Corregida en la ronda 7 antes de llegar a producción: sin la fecha, una base con pagos a plazos fallaba aquí.)
CREATE TABLE venta_v4 (
  id INTEGER PRIMARY KEY,
  contacto_id INTEGER NOT NULL REFERENCES contacto(id),
  cita_id INTEGER REFERENCES cita(id),
  monto_centavos INTEGER NOT NULL CHECK (monto_centavos > 0),
  fecha TEXT NOT NULL,
  origen TEXT NOT NULL,
  registrado_por TEXT NOT NULL,
  creado TEXT NOT NULL
);
INSERT INTO venta_v4 SELECT id, contacto_id, cita_id, monto_centavos, fecha, origen, registrado_por, creado FROM venta;
DROP TABLE venta;
ALTER TABLE venta_v4 RENAME TO venta;
CREATE UNIQUE INDEX venta_sin_cita ON venta(contacto_id, fecha, monto_centavos) WHERE cita_id IS NULL;
CREATE UNIQUE INDEX venta_por_cita ON venta(cita_id, fecha, monto_centavos) WHERE cita_id IS NOT NULL;

-- version: 5
-- Pagos a plazos: la misma cita puede tener pagos iguales en días distintos (bases que ya aplicaron la v4 original).
DROP INDEX IF EXISTS venta_por_cita;
CREATE UNIQUE INDEX venta_por_cita ON venta(cita_id, fecha, monto_centavos) WHERE cita_id IS NOT NULL;

-- version: 6
-- Índices para consultas de cada tick, de la bandeja y de cada mensaje (medido con 3k contactos y 100k mensajes):
-- "¿tiene cita futura?" por contacto (reactivación: 290 ms → 2 ms), último mensaje por contacto en orden de id
-- (lista de la bandeja cada 10 s: 46 ms → 4 ms), gasto de IA del mes y conteo de eventos por tipo.
CREATE INDEX cita_contacto ON cita(contacto_id, estado, inicio);
CREATE INDEX mensaje_contacto_id ON mensaje(contacto_id);
CREATE INDEX ia_uso_creado ON ia_uso(creado);
CREATE INDEX evento_tipo ON evento(tipo, creado);

-- version: 7
-- Correo como canal: un cliente que escribe por correo es un contacto cuyo "telefono" es su dirección (única, igual
-- que un teléfono) y email = la misma dirección. email_hilo: JSON del último correo recibido para responder en el
-- mismo hilo ({"id": Message-ID, "asunto": ..., "graph_id": id de Microsoft Graph o null}).
ALTER TABLE contacto ADD COLUMN email TEXT;
ALTER TABLE contacto ADD COLUMN email_hilo TEXT;
CREATE UNIQUE INDEX contacto_email ON contacto(email) WHERE email IS NOT NULL;

-- version: 8
-- Fichas de clientes: datos que el equipo captura a mano y notas (historial: solo se agregan, nunca se editan).
ALTER TABLE contacto ADD COLUMN fecha_nacimiento TEXT;   -- AAAA-MM-DD
ALTER TABLE contacto ADD COLUMN como_nos_conocio TEXT;
CREATE TABLE nota_cliente (
  id INTEGER PRIMARY KEY,
  contacto_id INTEGER NOT NULL REFERENCES contacto(id),
  texto TEXT NOT NULL,
  autor TEXT NOT NULL,
  creado TEXT NOT NULL
);
CREATE INDEX nota_cliente_contacto ON nota_cliente(contacto_id, id);

-- version: 9
-- Cotizaciones: folio consecutivo del negocio (C-0001), líneas con precio en centavos, MXN.
CREATE TABLE cotizacion (
  id INTEGER PRIMARY KEY,
  folio TEXT UNIQUE,                           -- 'C-' || id en 4 dígitos; se asigna al crearla
  contacto_id INTEGER NOT NULL REFERENCES contacto(id),
  estado TEXT NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'enviada', 'aceptada', 'rechazada', 'vencida')),
  vigencia_dias INTEGER NOT NULL,
  total_centavos INTEGER NOT NULL DEFAULT 0,
  creado_por TEXT NOT NULL,
  creado TEXT NOT NULL,
  enviada_en TEXT,                             -- la primera vez que se envió; la vigencia cuenta desde aquí
  respondida_en TEXT,                          -- cuando se marcó aceptada o rechazada
  cita_id INTEGER REFERENCES cita(id)
);
CREATE INDEX cotizacion_contacto ON cotizacion(contacto_id, estado);
CREATE TABLE cotizacion_linea (
  id INTEGER PRIMARY KEY,
  cotizacion_id INTEGER NOT NULL REFERENCES cotizacion(id),
  servicio_id TEXT,                            -- NULL = línea libre
  descripcion TEXT NOT NULL,
  cantidad INTEGER NOT NULL CHECK (cantidad > 0),
  precio_centavos INTEGER NOT NULL CHECK (precio_centavos > 0)
);
CREATE INDEX cotizacion_linea_cot ON cotizacion_linea(cotizacion_id);

-- version: 10
-- Saldo: una venta puede quedar ligada a la cotización aceptada que paga (solo si no hay duda de cuál es).
ALTER TABLE venta ADD COLUMN cotizacion_id INTEGER REFERENCES cotizacion(id);
CREATE INDEX venta_cotizacion ON venta(cotizacion_id);
