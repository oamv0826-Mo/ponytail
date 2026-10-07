# Decisiones tomadas sin consulta

Una línea por decisión, con su porqué. Las decisiones aprobadas en la conversación están en DISEÑO.md.

## Generales
- El sistema vive en `recuperacion-ventas/` dentro del repo, con sus propios `docs/`: no mezcla con el plugin ponytail ni con sus pruebas de Node.
- Python solo con stdlib (≥ 3.11; probado en 3.13): cero dependencias que instalar o actualizar en N instancias.
- La API de Claude se llama por HTTP con `urllib` y no con el SDK oficial: el SDK sería la única dependencia del proyecto.
- El esquema SQL vive solo en `docs/esquema.sql`, en secciones `-- version: N` que el código aplica en orden: una sola fuente de verdad para documentación y migraciones.
- Horas en la base en UTC ISO 8601 y conversión a `America/Monterrey` solo para mostrar y decidir horarios: comparaciones de texto correctas y sin ambigüedad en cambios de horario.
- Se respalda la base automáticamente antes de aplicar una migración: una migración fallida no pierde datos.

## Mensajería
- Teléfonos en E.164; `521` + 10 dígitos (móvil México, formato antiguo) se normaliza a `+52` + 10: un mismo cliente no queda duplicado.
- Para responder se usa el `wa_id` exacto que mandó Meta: es la dirección que Meta garantiza válida.
- Palabras de baja: coincide el mensaje completo normalizado (sin acentos, mayúsculas ni puntuación), no una subcadena: "ya no me duele" no debe dar de baja a nadie.
- Urgencia y handoff: coincidencia por palabra/frase dentro del mensaje normalizado: es mejor pasar de más a humano que de menos.
- Un contacto con opt-out que vuelve a escribir sí recibe respuesta: el opt-out bloquea todo envío proactivo (seguimiento, reactivación, reseñas, recordatorios), no la atención que el cliente mismo pide.
- Mensajes del equipo/dueño al número del negocio se descartan sin guardarse: el pedido fue excluirlos del bot, la IA y los ciclos.
- Mensajes que no son texto (audio, imagen, ubicación…) pasan a humano: la IA solo lee texto.
- Avisos al equipo por plantilla de WhatsApp (`aviso_equipo`), no por correo: el equipo vive en WhatsApp.
- Fuera de horario el aviso al equipo se pospone a la apertura, salvo urgencia médica (inmediato): no despertar al equipo por consultas normales.
- Escalamiento: una vez por mensaje sin responder, contando 15 min solo de horario abierto: evita spam y no escala de madrugada.
- Días cerrados = fechas de `dias_cerrados` + días de la semana sin horario: así un negocio cerrado en sábado no envía proactivos en sábado.
- Si el envío a Meta falla, el mensaje queda con estado `error` visible en la bandeja y no se reintenta solo: un reintento ciego puede duplicar mensajes.
- Envíos proactivos: se envía y luego se marca; ventana de duplicado de milisegundos si el proceso cae justo entre ambos (`ponytail:` en el código).
- Fuera de la ventana de 24 h la bandeja solo ofrece la plantilla `retomar_contacto`: regla de Meta aplicada en la interfaz.
- Estados de entrega (`sent/delivered/read/failed`) se guardan en el mensaje: un `failed` (p. ej. 131049) se ve en la bandeja.
- Una entrada de la cola que falla al procesarse se marca con su error y no se reintenta en bucle; `verificar` cuenta las fallidas.

## IA
- Modelo default `claude-haiku-4-5`; opción `claude-sonnet-5-5` con `effort: low` (Haiku 4.5 no acepta `effort`): costo y latencia de chat.
- `tool_choice: auto` + instrucción + validación propia del JSON, sin `strict`: Sonnet 5.5 rechaza el tool_choice forzado y así funciona igual en ambos modelos; cualquier salida inválida → handoff.
- Sin parámetro `fallbacks` de la API: si el modelo se niega (`refusal`) la conversación pasa a humano, que es lo correcto en atención al cliente.
- Prompt de sistema con `cache_control`: el bloque del negocio es estable y se reutiliza entre mensajes (si es menor al mínimo cacheable simplemente no se cachea).
- Precios por modelo en código (Haiku 4.5: 1/5 USD por MTok; Sonnet 5.5: 2/10) con override en config: el costo mensual se calcula sin consultar a nadie.
- Los datos del momento (fecha, abierto/cerrado, citas) van en un bloque aparte marcado "datos del sistema" dentro del turno del cliente: Haiku 4.5 no acepta mensajes de sistema a mitad de conversación.
- Post-validación: montos con `$`, "pesos" o "MXN" y porcentajes deben existir en el config; dosis (mg, ml, tabletas…) siempre bloquean.
- IA simulada por reglas en modo prueba sin API key: la demo completa no requiere ninguna cuenta.
- Timeout de 20 s a la API de Claude: más que eso, el cliente ya está esperando de más; pasa a humano.

## Bandeja
- Rate limit de login en memoria (5 fallos → 15 min, por usuario y por IP): se reinicia si se reinicia el proceso; aceptable para este volumen (`ponytail:`).
- IP del cliente desde `X-Forwarded-For` solo si la conexión viene de 127.0.0.1 (Caddy): evita falsificar la IP.
- Token de sesión guardado como SHA-256: una copia filtrada de la base no da sesiones válidas.
- Refresco de la bandeja con polling de 10 s a un fragmento HTML, no WebSockets: stdlib y suficiente.

## Agregadas en la Etapa 1
- El aviso de "tope de IA alcanzado" va solo al dueño, una vez al mes; cada conversación que pasa a humano se avisa al equipo como cualquier handoff: evita que el dueño reciba el mismo hecho dos veces.
- Urgencia médica en una conversación que ya está con humano: se envía el mensaje de emergencia y se avisa al equipo de inmediato (no cambia el estado).
- La bandeja tiene también "Pasar a humano" (manual) además de Tomar/Devolver: el equipo puede silenciar al bot en cualquier conversación.
- Items del webhook con un `phone_number_id` distinto al del config se ignoran y se registran: un override mal configurado no mezcla clientes.
- Si `WA_APP_SECRET` o `WA_VERIFY_TOKEN` faltan fuera de modo prueba, el webhook rechaza todo (401/403): nunca se aceptan mensajes sin firma.
- Modo prueba usa secretos fijos de prueba (`secreto-de-prueba`, `verificar-prueba`) para que `simular` firme igual que Meta.
- `simular` se niega a correr con `modo_prueba=false` salvo que apunte con `--url` a un servidor: no hay forma de mandar mensajes reales por accidente desde el simulador.
- Las cabeceras de seguridad (CSP, X-Frame-Options DENY, no-store, nosniff) van en todas las respuestas: la bandeja muestra datos personales.
- El nombre de perfil de WhatsApp (lo controla el cliente) se guarda sin caracteres de control y máximo 60, y va a la IA entre comillas JSON marcado como dato del cliente: evita inyección de instrucciones por el nombre (hallazgo de la revisión de seguridad).

## Agregadas en la Etapa 2
- JWT RS256 de la cuenta de servicio firmado en Python puro (DER + `pow`): verificado byte por byte contra `openssl` con claves PKCS#8 y PKCS#1; evita dependencia (`ponytail:` en el código sobre tiempo no constante).
- Token de Google en memoria y reutilizado hasta 1 min antes de expirar: una firma por hora, no por petición.
- El cliente elige con "1/2/3" (también "la 2", "opción 3"); la propuesta vence a las 24 h y otro texto pasa a la IA normal.
- Horarios propuestos: pasos de 30 min desde el inicio de cada rango, el primero libre y los siguientes separados ≥ 3 h: opciones distintas en vez de 10:00/10:30/11:00.
- "Cambio simple" = exactamente una cita futura y faltan **más** de 2 h; en cualquier otro caso (cero o varias citas, o muy cerca) pasa a humano.
- Cancelar requiere "SÍ" explícito; "no" deja la cita: la IA puede malinterpretar "cancelar".
- Reprogramar: se crea la nueva y solo después se cancela la anterior: nunca queda el cliente sin cita por una falla a mitad.
- Al reprogramar no se ofrece el mismo horario que ya tiene.
- Candado de proceso alrededor de "consultar disponibilidad + crear": el bot y la bandeja viven en el mismo proceso `serve`; dos clientes no pueden tomar el mismo horario.
- Cualquier error de Google (red, permisos, freeBusy con errores) pasa la conversación a humano con motivo `agenda_error`.
- Agendar desde la bandeja permite cualquier hora futura (el equipo puede hacer excepciones), pero nunca encima de otra cita; avisa al cliente con texto libre o con la plantilla `cita_confirmada` si la ventana está cerrada.
- Cancelar desde la vista Citas no avisa al cliente: lo hace el equipo, que ya está hablando con él.
- Citas canceladas directamente en Google Calendar no se sincronizan de regreso: el equipo debe cancelar desde la bandeja (queda en el runbook). Sincronizar sería un webhook de Google más; no se pidió.

## Agregadas en la Etapa 3
- Recordatorio de 24 h fuera de ventana: se adelanta al último momento permitido menos 30 min de holgura (p. ej. sábado 19:30 para una cita del lunes 9:30): el tick de 5 min siempre lo alcanza.
- Recordatorio de 2 h: solo si ese momento cae en ventana; si no, se marca `omitido` (el de 24 h ya cubrió).
- Una cita creada después del momento de un recordatorio no recibe ese recordatorio: el cliente acaba de recibir la confirmación.
- Seguimiento: nunca dos mensajes el mismo día aunque el sistema haya estado caído; los pasos vencidos se mandan uno por día.
- Seguimiento: un día que cae en domingo/día cerrado se manda en la siguiente ventana permitida (no se salta el paso).
- Reseñas: si "Asistió" se marcó hace más de 7 días, ya no se pide (`omitida`): una solicitud tardía se siente fuera de lugar.
- Reactivación: hasta 10 envíos por tick (reparte el lote del día y no inunda la bandeja de respuestas al mismo tiempo); prioridad a quien vino más recientemente (más probable que regrese).
- Reactivación: excluye a quien escribió en los últimos 30 días o tiene cita futura.
- Calidad del número: se consulta una vez al día; sin dato (`UNKNOWN`) o `YELLOW` se usa el lote base; `RED` pausa la reactivación y avisa al dueño (cada día que siga en rojo). En modo prueba se simula con la clave `calidad_simulada` de la tabla `estado`.
- El consentimiento solo viene del CSV de importación; reimportar con "no" lo revoca y con vacío no lo cambia.
- Palabras de baja por defecto incluyen "detener promociones"/"stop promotions" (texto del botón de baja de Meta en plantillas de marketing).
- Cada paso del tick corre aislado: la falla de uno se registra y los demás siguen; `tick` sale con código 1 si alguno falló (systemd lo marca).

## Agregadas en la Etapa 4
- La atribución se calcula y se guarda al registrar la venta (no al hacer el reporte): el origen no cambia si después llegan más mensajes, y el dueño puede ver por qué se asignó.
- "Antes de la venta" = hasta el fin del día de la venta en hora de Monterrey: la venta se registra por fecha, no por hora.
- El evento `fuera_horario` lleva la hora real del mensaje de Meta, no la de procesamiento: un reintento tardío no corre la ventana de 60 días.
- Montos en centavos enteros; se aceptan "$1,500.50", "1500.5" y "800"; nunca cero ni negativo; fechas futuras se rechazan.
- Venta duplicada = mismo contacto, fecha y monto (restricción UNIQUE): evita doble conteo al reimportar el mismo CSV.
- Importar ventas solo cruza con contactos existentes; teléfonos desconocidos se reportan por línea, no se crean contactos nuevos.
- Consulta = mensaje entrante sin otro entrante del mismo contacto en las 24 h previas; tiempo de respuesta = hasta el primer mensaje saliente (bot o humano).
- Reseñas en el reporte: solicitudes enviadas (dato del sistema) y, opcional, `--resenas-google N` como dato manual: el sistema no lee el perfil de Google.
- El reporte se guarda en `reportes/reporte-AAAA-MM.md` de la carpeta del cliente y se imprime; por defecto es el mes anterior.
- La garantía se evalúa por fecha de venta dentro de los 60 días naturales desde `fecha_inicio` (día 1 incluido); antes de que termine el periodo se muestra "EN CURSO".
- El anexo de contrato incluye exclusiones (no registrar ventas, suspensión por causa del negocio, no entregar la base con consentimiento): son condiciones contractuales, el sistema no las evalúa.
- Página estática con precios visibles (los mismos que da el bot), horario completo y enlace a Google Maps por dirección; sin JS ni recursos externos.

## Agregadas en la Etapa 5
- El callback a nivel app de Meta apunta a la instancia del primer cliente (Meta exige que pase la verificación `hub.challenge`; un 200 fijo de Caddy no la pasa). Mensajes de números sin override llegan ahí, se descartan por `phone_number_id` y quedan en el log.
- `configurar-webhook` hace el `POST /{phone_number_id}` con `webhook_configuration.override_callback_uri` y el mismo `WA_VERIFY_TOKEN` del cliente; se corre a mano en la instalación (cambia la configuración en Meta).
- `verificar --remoto` busca la URL esperada en toda la respuesta de `?fields=webhook_configuration` en vez de leer un campo fijo: la documentación de Meta no fue accesible desde este entorno para confirmar el nombre exacto del campo (`ponytail:` en el código).
- `verificar --remoto` solo hace lecturas que no cuestan ni envían nada: datos del número, plantillas, Models API de Anthropic (valida clave y modelo) y freeBusy de Google.
- `verificar` marca ERROR si no hay usuarios de bandeja, si nadie recibe avisos, o (en producción) si el último tick tiene más de 15 min.
- El tick guarda `ultimo_tick` en la tabla `estado` en cada corrida: así `verificar` detecta un timer detenido.
- Una migración que falla hace rollback completo y no cambia `user_version`; además ya existe el respaldo previo.
- systemd con plantillas de instancia (`rv@<id>`), endurecimiento (`ProtectSystem=strict`, solo escribe en la carpeta del cliente) y timers con `Persistent=true` (si el servidor estuvo apagado, el tick corre al arrancar).
- Respaldo: local 30 días + `rclone copy` a un remoto externo + `rclone delete --min-age 30d`; el destino va en `/etc/rv/respaldo.env`, fuera del repo.
- Un solo dominio con una ruta por cliente (`/c/<id>/` para el sistema y `/p/<id>/` para la página estática servida por Caddy).

## Correcciones de la revisión de código final (cada una con su prueba en tests/test_revision.py)
- `Referrer-Policy: same-origin` (antes `no-referrer`): con `no-referrer` los navegadores mandan `Origin: null` en los POST y la revisión anti-CSRF rechazaba hasta el login.
- Un horario propuesto que ya pasó o ya no cumple la anticipación mínima no se agenda aunque la propuesta siga vigente: se responde "ya no está disponible" y se proponen nuevos.
- Reprogramar con Google: se resta el intervalo de la cita propia a lo ocupado en Google (que recorta y une bloques), en vez de compararlo exacto.
- Reprogramar: si falla borrar el evento anterior en Google, la cita anterior se cancela en la base, el cliente recibe su confirmación y el equipo recibe aviso para borrar el evento a mano; si la cita anterior ya se marcó Asistió/No asistió, no se toca.
- Red de seguridad en el motor: cualquier excepción al atender un mensaje (no solo errores de la IA) pasa la conversación a humano (`error_interno`) y queda registrada en la cola.
- Recuperación tras caída (esquema v2, columna `entrada.terminado`): una entrada reclamada y sin terminar por más de 2 min se vuelve a procesar; si el mensaje ya estaba guardado y nadie le respondió, se atiende; si ya se respondió, no se duplica.
- Envíos del tick: solo se marcan como hechos si Meta los aceptó; si fallan se reintentan en los siguientes ticks y tras 3 fallas en 24 h se marcan `error` (los reportes solo cuentan envíos reales).
- Respaldo con archivo temporal único y copia en flujo (sin cargar la base en memoria); el nombre lleva el PID para que dos respaldos del mismo segundo no se pisen.
- `precio_mxn` numérico y `duracion_min` entero son obligatorios al cargar el config: un error de captura no rompe la IA en producción.
- Texto del escalamiento sin minutos fijos (el umbral es configurable).
- La revisión de opt-out vive en un solo lugar (`base.dio_baja`).
