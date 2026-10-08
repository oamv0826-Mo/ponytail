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
