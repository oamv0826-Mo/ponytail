# Sistema de Recuperación de Ventas: diseño

Fuente: "La idea: Sistema de Recuperación de Ventas" (Oliver Méndez, 5 oct 2026) y las decisiones
aprobadas en la conversación de diseño. Las decisiones tomadas sin consulta están en
[DECISIONES.md](DECISIONES.md), una por línea con su porqué.

## 1. Qué hace

Un servicio por negocio de servicios (clínica, dentista, inmobiliaria…) que evita que se pierdan ventas
en tres momentos: nadie contesta a tiempo, nadie da seguimiento a quien pidió precio, y nadie vuelve a
buscar a clientes antiguos. Las siete funciones del documento:

| # | Función | Dónde vive |
|---|---|---|
| 1 | Responder al instante por WhatsApp con datos reales y pasar a humano | `rv/motor.py`, `rv/ia.py`, `rv/web.py` (bandeja) |
| 2 | Agendar citas solo (confirmación y recordatorios) | `rv/agenda.py`, `rv/tick.py` |
| 3 | Registrar a cada prospecto (CRM sencillo) | SQLite `contacto` + bandeja |
| 4 | Seguimiento a quien pidió precio y no compró (días 2, 5, 10) | `rv/tick.py` |
| 5 | Reactivar clientes antiguos | `rv/ventas.py` (import), `rv/tick.py` (envío) |
| 6 | Pedir reseña en Google después del servicio | `rv/tick.py` |
| 7 | Presencia básica: página que lleva a WhatsApp | `rv/ventas.py` (`pagina`); perfil de Google = checklist manual |

Promesa medible: reporte mensual con 5 números (consultas, tiempo de respuesta, citas, ventas
recuperadas, reseñas) y la verificación de la garantía de 60 días.

## 2. Arquitectura

- Python 3.11+ solo con biblioteca estándar (sin dependencias). `http.server`, `sqlite3`, `urllib`,
  `zoneinfo`, `hmac`, `hashlib`.
- **Una instancia por negocio**: una carpeta de cliente con `cliente.json` (configuración),
  `secretos.env` (credenciales) y `datos.db` (SQLite en modo WAL). El código es uno solo, compartido.
- Procesos por cliente:
  - `serve`: servidor HTTP (webhook de Meta + bandeja web) con un hilo trabajador que procesa la cola.
  - `tick`: corre cada 5 min (timer de systemd): seguimiento, recordatorios, reseñas, reactivación,
    avisos y escalamientos.
- Comandos: `python3 -m rv --cliente DIR <comando>`:
  `serve`, `tick`, `simular`, `usuario`, `importar-clientes`, `importar-ventas`, `reporte`, `pagina`,
  `respaldo`, `verificar`.

```
Meta (WhatsApp Cloud API) ──override por número──▶ Caddy (HTTPS) ──/c/<cliente>/*──▶ rv serve (127.0.0.1:puerto)
                                                                                    │  webhook ─▶ tabla entrada (cola)
                                                                                    │  hilo trabajador ─▶ motor ─▶ IA / agenda / handoff
                                                                                    │  bandeja web (equipo)
systemd timer (5 min) ──▶ rv tick ──▶ envíos proactivos (plantillas)
```

### 2.1 Webhook

- Override de callback **por número** (`override_callback_uri`): Meta entrega cada número directo a la
  ruta de su cliente. Sin router propio. El callback de la app (obligatorio en Meta y verificado con
  `hub.challenge`) apunta a la instancia del primer cliente: si algún número quedara sin override, sus
  mensajes llegan ahí, se descartan por `phone_number_id` distinto y quedan en el log (señal de que falta
  el override). `verificar --remoto` revisa el override de cada número.
- `GET /webhook`: verificación `hub.challenge` con `WA_VERIFY_TOKEN`.
- `POST /webhook`:
  1. lee el cuerpo crudo;
  2. verifica `X-Hub-Signature-256` (HMAC-SHA256 con `WA_APP_SECRET`, `hmac.compare_digest`); si falla → 401;
  3. separa mensajes y estados; cada uno se inserta en `entrada` con `INSERT OR IGNORE` sobre una clave
     única (`m:<message_id>` o `s:<id>:<status>`) → deduplicación de reintentos de Meta;
  4. responde 200 de inmediato y despierta al hilo trabajador.
- Un solo hilo trabajador por instancia procesa `entrada` en orden: sin carreras sobre la misma
  conversación ni contención de escritura en SQLite. La cola es persistente: tras una caída se reanuda.
- `ThreadingHTTPServer` escuchando solo en `127.0.0.1`; HTTPS lo pone Caddy.

### 2.2 Procesamiento de un mensaje entrante (motor)

Orden exacto (el primer paso que aplica termina el procesamiento):

1. Si el teléfono es del equipo o del dueño → se ignora (ni bot, ni IA, ni ciclos).
2. Se guarda el mensaje y se actualiza el contacto (`ultimo_entrante`, primer contacto, evento
   `fuera_horario` si llegó con el negocio cerrado). Si había seguimiento activo, se detiene (respondió).
3. Mensaje = palabra de baja (mensaje completo normalizado) → opt-out, confirmación, fin.
4. Palabra de **urgencia médica** → mensaje fijo de emergencia (911 / urgencias) a cualquier hora +
   handoff urgente (aviso inmediato al equipo, aunque sea de noche).
5. Conversación en estado `humano` → el bot no responde (la bandeja lo muestra; el tick escala).
6. Palabra de handoff ("hablar con una persona", "queja"…) o mensaje que no es texto → handoff.
7. Respuesta a una propuesta pendiente (1/2/3 para horario, "sí" para cancelar) → agenda.
8. Tope mensual de IA alcanzado → handoff (y aviso único al dueño en el mes).
9. IA → acción `responder` | `proponer_cita` | `cancelar_cita` | `reprogramar_cita` | `humano`.
   Error o timeout de IA → mensaje fijo "en un momento te atiende una persona" + handoff.

### 2.3 Handoff y bandeja

- Estado por conversación (contacto): `bot` → `humano` → `bot`. En `humano` el bot no envía nada.
- Mensaje al cliente: si el negocio está abierto, "en unos minutos te atiende una persona"; si está
  cerrado, la **próxima apertura real** según horario y días cerrados ("el lunes 13 de octubre a las 9:00").
- Aviso al equipo (plantilla `aviso_equipo` a `equipo[]` y dueño): inmediato en horario o si es
  urgente; fuera de horario queda pendiente y el tick lo manda a la apertura.
- Escalamiento (tick): conversación en `humano` con mensaje del cliente sin respuesta > 15 min de
  horario abierto → nuevo aviso al equipo y al dueño (una vez por mensaje sin responder).
- No hay regreso automático al bot.
- Bandeja web `/bandeja` (HTML servido por `serve`, ~20 líneas de JS para refrescar):
  lista (pendientes de humano / mías / todas), conversación con **Tomar**, **Responder**,
  **Devolver al bot**, **Registrar venta**, **Agendar**, y vista de **Citas** (Asistió / No asistió / Venta).
  Con la ventana de 24 h cerrada solo se ofrece la plantilla `retomar_contacto`.
- Seguridad: usuario por persona (`usuario` crea/cambia contraseña, `hashlib.scrypt`), cookie de sesión
  `HttpOnly; SameSite=Strict; Secure` (si la URL es https), token guardado como SHA-256, sesión de 12 h,
  revisión de `Origin` en todo POST (anti-CSRF), todo texto escapado con `html.escape`.
  Rate limit: 5 intentos fallidos por usuario o por IP → bloqueo de 15 min.

### 2.4 IA y guardrails

- API de Claude por HTTP (`urllib`), `anthropic-version: 2023-06-01`. Modelo configurable:
  `claude-haiku-4-5` (default) o `claude-sonnet-5-5` (con `effort: low`).
- Prompt de sistema construido **solo** con datos del config (servicios, precios, horario, dirección,
  FAQ aprobadas). Contexto: últimos 20 mensajes. Los datos del momento (fecha, abierto/cerrado, citas
  del cliente) van en un bloque marcado como "datos del sistema", separado del texto del cliente.
- Salida estructurada vía la herramienta `responder` (`tool_choice: auto` + instrucción; el código
  valida el JSON; sin herramienta o JSON inválido → handoff).
- Antes de la IA: palabras de urgencia, handoff y baja (deterministas, sin tokens).
- Después de la IA: si el texto trae un monto `$`/pesos/MXN o un porcentaje que no existe en el config,
  o una dosis (mg, ml, tabletas…), se bloquea y se hace handoff.
- Reglas del prompt: nunca diagnóstico, indicación médica, dosis, resultados prometidos ni consejo
  legal/financiero; no inventar precios ni promociones; si no sabe → `humano`.
- `stop_reason: refusal` → handoff.
- Costo: se registran tokens (entrada, salida, caché) y costo en micro-USD por llamada (`ia_uso`).
  Al llegar a `ia.tope_mensual_usd`, todo pasa a handoff y se avisa al dueño una vez en el mes.
- Modo prueba sin `ANTHROPIC_API_KEY`: IA simulada por reglas (precio/cita/cancelar/FAQ) para demos.

### 2.5 Agenda (Google Calendar)

- Cuenta de servicio con un calendario compartido por negocio (el negocio comparte su calendario con el
  correo de la cuenta de servicio, permiso "hacer cambios en eventos").
- Token OAuth con JWT RS256 firmado en Python puro (sin dependencias).
- Horarios, duración por servicio y margen entre citas salen del config. Ventana: desde
  `anticipacion_min_horas` hasta `dias_adelante`.
- Ocupado = `freeBusy` de Google ∪ citas propias en SQLite, ampliado por el margen.
- Propone hasta 3 horarios (el primero libre y los siguientes separados ≥ 3 h) y el cliente responde 1/2/3.
- Justo antes de crear el evento se vuelve a consultar disponibilidad (bajo un candado de proceso):
  si se ocupó, se proponen nuevos horarios.
- Cancelar / reprogramar "simple" = el contacto tiene exactamente una cita futura y faltan más de
  `cambio_min_horas` (2 h). Cancelar pide confirmación ("SÍ"); reprogramar propone horarios y, al
  elegir, crea la nueva y cancela la anterior. Cualquier otro caso → handoff.
- `agenda.proveedor = "local"`: sin Google (solo citas en SQLite). Para demo y pruebas.

### 2.6 Envíos proactivos (tick)

Todos usan plantillas aprobadas por Meta ([plantillas.md](plantillas.md)) y respetan:
**ventana de envío** 9:00–20:00 hora de Monterrey, nunca domingo ni día cerrado del config;
**opt-out** global; exclusión de equipo/dueño.

| Ciclo | Regla |
|---|---|
| Seguimiento | Solo a quien pidió precio/info (intención detectada por la IA) y no agendó. Días 2, 5 y 10 desde su último mensaje. Se detiene si responde a un mensaje de seguimiento, agenda, entra a handoff o pide baja; si sigue escribiendo antes del primero ("ok, lo pienso") no se detiene, solo se recorre el conteo. Una nueva consulta de precio/info reinicia el ciclo. |
| Recordatorios | 24 h y 2 h antes de la cita (utilidad). Si el momento cae fuera de ventana: el de 24 h se adelanta al último momento permitido anterior; el de 2 h se omite. No se manda si la cita se creó dentro de ese plazo. |
| Reseñas | Una sola solicitud 2 h después de marcar "Asistió", con el link de Google del config. Máximo una por contacto cada 90 días. |
| Reactivación | Contactos importados con consentimiento, sin opt-out, sin cita futura y sin conversación en los últimos 30 días. Lote diario: 50; sube a 100 tras 7 días seguidos con calidad del número `GREEN`; con `RED` se pausa y se avisa al dueño. |
| Avisos | Handoffs fuera de horario se avisan a la apertura; escalamiento a 15 min. |

### 2.7 Ventas, atribución, reporte y garantía

- Registro de venta: en la bandeja (desde una cita o conversación, monto obligatorio) o
  `importar-ventas ventas.csv` (`telefono,fecha,monto`), cruce por teléfono E.164.
- Atribución al registrar (primera que aplique; ver texto contractual en
  [anexo-contrato.md](anexo-contrato.md)):
  1. `reactivacion`: recibió reactivación ≤ 60 días antes de la venta y respondió después del envío.
  2. `seguimiento`: recibió un seguimiento ≤ 60 días antes y después respondió o agendó.
  3. `fuera_horario`: su primera consulta llegó con el negocio cerrado y la venta es ≤ 60 días después.
  4. `respuesta_rapida`: otra venta de un prospecto que entró por el sistema (se reporta, no cuenta).
  5. `sin_atribucion`: el resto.
  Recuperadas = 1 + 2 + 3.
- Reporte `reporte AAAA-MM` (Markdown): consultas recibidas, tiempo de respuesta (mediana y % < 5 min),
  citas (agendadas, asistidas), ventas recuperadas (por origen, $), reseñas solicitadas; además
  handoffs por motivo, citas sin cerrar, costo de IA y estado de la garantía.
- Garantía: suma de ventas recuperadas entre `fecha_inicio` y `fecha_inicio + 60 días` ≥
  `mensualidad_mxn` → CUMPLE; si no → "el siguiente mes no se cobra".

### 2.8 Teléfonos

Se guardan en E.164 (`+52` + 10 dígitos para México). `521XXXXXXXXXX` (formato antiguo de móvil) se
normaliza a `+52XXXXXXXXXX`. Para responder se usa el `wa_id` tal como llegó de Meta.

## 3. Datos

Esquema completo y migraciones en [esquema.sql](esquema.sql) (secciones `-- version: N`, aplicadas al
arrancar según `PRAGMA user_version`, con respaldo automático antes de migrar).

## 4. Configuración del cliente

`cliente.json` (ver `ejemplo/cliente.json`). Campos principales: `nombre`, `modo_prueba`,
`url_publica`, `puerto`, `fecha_inicio`, `mensualidad_mxn`, `telefono_negocio`, `whatsapp`
(`phone_number_id`, `waba_id`, `graph_version`), `dueno`, `equipo[]`, `direccion`, `horario`
(`lun`…`dom` → lista de `["HH:MM","HH:MM"]`), `dias_cerrados`, `servicios[]` (`id`, `nombre`,
`precio_mxn`, `duracion_min`, `descripcion`), `faq[]`, `palabras` (`urgencia_medica`, `handoff`, `baja`),
`ia`, `agenda`, `envios`, `seguimiento_dias`, `reactivacion`, `resenas`.
Secretos en `secretos.env`: `WA_TOKEN`, `WA_APP_SECRET`, `WA_VERIFY_TOKEN`, `ANTHROPIC_API_KEY`,
`GOOGLE_SA_FILE`.

**Modo prueba** (`"modo_prueba": true`): ningún mensaje sale a Meta; cada envío se guarda en la base
con estado `prueba` y en `envios-prueba.log`. `simular` inyecta mensajes falsos de WhatsApp firmados
igual que Meta. Sin `ANTHROPIC_API_KEY` usa la IA simulada; con `agenda.proveedor = "local"` no usa Google.

## 5. Despliegue

- VPS pequeño (Ubuntu LTS) con Caddy; código en `/srv/rv/app`, clientes en `/srv/rv/clientes/<id>/`.
- systemd: `rv@<id>.service` (serve), `rv-tick@<id>.timer` (5 min), `rv-respaldo@<id>.timer` (diario).
- Actualizar: `git pull` + `systemctl restart 'rv@*'` (migraciones al arrancar).
- Respaldo: `respaldo` (API de backup de SQLite + gzip) → `rclone` a almacenamiento externo, 30 días.
- `verificar` (`--remoto` para Meta/Anthropic/Google, solo lectura): config, secretos, base, usuarios,
  último tick, plantillas aprobadas, override del webhook, clave de IA, acceso al calendario.
- Guías: [runbook.md](runbook.md) (operación) y [checklist-instalacion.md](checklist-instalacion.md).
- Límite conocido: un VPS es punto único de falla; Meta reintenta hasta 7 días. ~10 instancias.

## 6. Etapas

| Etapa | Contenido |
|---|---|
| E1 | webhook, cola, IA con guardrails, handoff, bandeja con login, modo prueba, `simular` |
| E2 | agenda y Google Calendar |
| E3 | tick: seguimiento, recordatorios, reseñas, reactivación; opt-out |
| E4 | ventas (registro e importación), reporte con garantía, página estática |
| E5 | despliegue (systemd, Caddy, rclone), `verificar`, runbook, checklist de instalación |

Pruebas: `cd recuperacion-ventas && python3 -m unittest discover -s tests -v`.
