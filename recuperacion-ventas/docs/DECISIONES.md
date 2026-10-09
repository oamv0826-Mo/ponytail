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
- Para responder se usa el teléfono normalizado (`52` + 10), no el `wa_id`: en México Meta manda `521` + 10, pero la lista de destinatarios del número de prueba y la marcación actual son `52` + 10, y responder al `521` falla con 131030. Los contactos importados ya se escribían así.
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
- (Encontrado al probar la bandeja en Chromium real) "Asistió"/"No asistió" solo se pueden marcar el día de la cita o después, y la fecha de la venta desde una cita nunca es futura: antes se podía marcar asistencia de una cita futura y la venta se rechazaba.

## Agregadas con el kit de venta y la prueba real
- `verificar --remoto` lee el campo documentado por Meta, `webhook_configuration.phone_number`, en vez de buscar la URL en toda la respuesta (la documentación ya fue accesible). `prueba-real` guarda la respuesta real en `meta-webhook-configuration.json` para confirmarlo.
- Túnel para la prueba real: ngrok con su dominio gratuito fijo, no el túnel rápido de cloudflared: la prueba dura dos sesiones (noche y día siguiente) y con una URL fija Meta se configura una sola vez.
- `prueba-real` no espera días reales: el paso `seguimiento` pone `seg_inicio` 2 días atrás y corre el seguimiento; el paso `asistencia` mueve la cita a hace una hora (solo en la base) si no es de hoy. El mensaje de noche sí debe mandarse de noche, porque la hora la pone Meta.
- `prueba-real` revisa la base automáticamente y además pregunta qué se vio en el teléfono: un paso pasa solo si ambas cosas salen bien. Resultados en `prueba-real.json` y `prueba-real-resultados.md`.
- Antes del override, `prueba-real` suscribe la app a la WABA (`POST /{waba_id}/subscribed_apps`): Meta lo exige para el override. Ambas llamadas piden confirmación.
- Un archivo por giro en `nichos/` sirve a la demo y a la auditoría (el mensaje de prueba y la pérdida estimada viven en su sección `auditoria`).
- Auditoría: una prueba sin hora de envío no se hizo y no cuenta; el puntaje se escala a 100 sobre lo probado (el plan prueba fuera de horario solo a 10 de los 20).
- Auditoría, casos que la tabla del plan no define: más de 100 reseñas sin contestarlas = 5 puntos (nivel 30-100); facilidad de contacto con solo botón o solo horario = 5; respuesta fuera de horario después de 1 hora, a cualquier hora = 5 ("al día siguiente").
- Venta en riesgo = consultas al mes × pérdida estimada (0.2 por defecto, "1 de cada 5" del plan) × ticket promedio, marcada como estimación. Sin datos del dueño, el reporte dice que está pendiente.
- Reporte de auditoría en HTML de una página carta, sin recursos externos; se imprime a PDF desde el navegador. `resumen.html` lleva todos los nombres y es solo para uso interno.
- `demo-ventas` usa siempre la IA simulada (quita `ANTHROPIC_API_KEY` mientras corre) y un reloj simulado que empieza el primer lunes del mes actual, para que todo caiga en el mismo mes del reporte. Siempre empieza de cero: rechaza una carpeta que ya tenga una demo.

## Encontrado en la simulación de punta a punta
- Seguimiento: "responde" se interpreta como responder a un mensaje de seguimiento ya enviado. Antes, cualquier mensaje después de pedir precio (p. ej. "ok gracias, lo pienso") cancelaba la secuencia antes del primer envío, justo en el caso central del documento ("piden precio, dicen 'lo pienso' y desaparecen"). Ahora esos mensajes solo recorren el conteo de días al último mensaje.
- La IA simulada (solo modo prueba) responde con cortesía a "gracias / lo pienso" y pasa a humano las preguntas de salud en vez de pasar a humano, como lo haría la IA real; así la demo refleja el flujo real.

## Ciclo de caza de errores (detalle en docs/BUGHUNT.md)
- Baja por frase: además de las palabras exactas, frases inequívocas dentro del mensaje ("darme de baja", "no me manden", "no me envíen"…) registran el opt-out. Un falso positivo solo apaga los envíos proactivos; el cliente sigue recibiendo respuestas si escribe.
- Reacciones (👍) no se contestan ni pasan a humano.
- Archivos del cliente: se guarda el id de Meta (esquema v3) y la bandeja los pide a Meta en el momento (la URL de descarga dura minutos; Meta los conserva ~30 días). Máximo 25 MB. Solo se muestran en línea imagen/audio/video/PDF comunes; lo demás se descarga.
## Encontrado en la revisión de errores del núcleo (2026-10-08)
- Envíos a `52` + 10 en vez del `wa_id` con `521` (ver Mensajería). Prueba: `MovilMexicoSinUno`.
- El servidor rechaza con 413 un `Content-Length` negativo: antes `read(-1)` leía sin límite hasta que el cliente cerraba la conexión. Prueba: `CuerpoConLargoNegativo`.
- Elección de horario flexible pero conservadora: un solo número (opción u hora ofrecida) u ordinal en un mensaje de hasta 8 palabras; con negación o dos números no se elige y el mensaje pasa a la IA.
- Confirmar cancelación: primera palabra afirmativa y sin "pero/mejor/cambia/no" después; ante la duda no se cancela.
- Reacciones: no cuentan como consulta, ni como mensaje pendiente para escalar. Sí abren la ventana de 24 h y sí cuentan como respuesta a un seguimiento (son una señal de interés).
- Escalamiento: 15 min de horario abierto desde lo último entre el mensaje del cliente y el paso a humano.
- Venta duplicada (esquema v4): con cita, misma cita y mismo monto; sin cita, mismo contacto, fecha y monto. Una cita puede tener varias ventas de montos distintos (servicio + producto).
- "Citas agendadas" del reporte = citas creadas en el mes que no quedaron canceladas; las canceladas (incluidas las reprogramadas) se reportan aparte.
- Login: bloqueo de 15 min tras 5 fallos por usuario y tras 20 por IP (antes 5 y 5): un error de una persona no deja fuera a toda la oficina.
- CSV: UTF-8 o Windows-1252; separador coma, punto y coma o tab (se elige el más frecuente en el encabezado); alias de columnas comunes; fechas AAAA-MM-DD o DD/MM/AAAA (nunca MM/DD: en México el día va primero).
- Teléfonos: 044/045 + 10 dígitos y 01 + 10 dígitos (marcación antigua) se convierten a +52 + 10; un número nacional con 0 inicial se rechaza.
- Migraciones bajo candado de archivo (`fcntl`, Linux) para que dos procesos que arrancan juntos no apliquen la misma migración.
- Venta duplicada con cita = misma cita, fecha y monto (antes sin fecha): los pagos a plazos iguales en días distintos son ventas distintas. Se ajustó la sección v4 del esquema, que aún no estaba en ninguna instalación real, y la v5 corrige las bases de prueba que ya la tenían.
- La auditoría lee `negocios.csv` con el mismo lector que las importaciones (Excel en Windows, punto y coma) y acepta fechas con segundos; si no encuentra la columna `negocio` lo dice en vez de devolver una lista vacía.
- Nombre del producto: Agorá (elegido por el dueño). Solo cambia el nombre visible (README, diseño, ayuda del comando, nombre sugerido de la app de Meta); la carpeta `recuperacion-ventas/`, el paquete `rv` y las unidades `rv@` se quedan para no romper rutas de instalación ni comandos documentados. Las páginas que ve el cliente final siguen mostrando el nombre de cada negocio.
- Pasada de simplificación (sin cambiar comportamiento): la bandeja llama directo a agenda y ventas en vez de registros de "extras" por etapa; una sola `motor.citas_futuras`, `cfg.nombre_servicio`, `base.ventana_abierta`, `verificar.url_webhook` y `ventas.guardar_reporte` en lugar de copias; el reporte cuenta eventos con un solo GROUP BY.
- Esquema v6: cuatro índices (cita por contacto, mensaje por contacto en orden de id, ia_uso por fecha, evento por tipo y fecha). Medido con 3k contactos y 100k mensajes: la reactivación pasa de 290 ms a 2 ms por tick y la lista de la bandeja de 46 ms a 4 ms cada 10 s.
- Importar clientes o ventas corre en una sola transacción: un fsync en vez de uno por fila (3,000 filas en un disco de VPS: de 6 a 30 s según el disco, a menos de 1 s) y, si algo falla, no queda a medias; reimportar es idempotente.
- La bandeja solo reemplaza la conversación o la lista cuando el servidor devuelve algo distinto (compara con la última respuesta, no con el DOM, que el navegador reescribe), y las fotos cargan con loading=lazy. Las fotos siguen con Cache-Control no-store a propósito: la bandeja se abre en computadoras compartidas.
- Fase 1 (auditoría de integraciones): las llamadas a Meta y Google ya coincidían con la documentación vigente; `whatsapp.graph_version` se queda en v23.0 (vigente hasta ~2027) y se cambia por cliente sin tocar código.
- `verificar --remoto` revisa el token con `debug_token`: ERROR si vence en menos de 14 días o le faltan permisos de WhatsApp, porque Meta no avisa cuando un token se invalida y el primer síntoma sería un envío fallido.
- `verificar --remoto` revisa `/{waba_id}/subscribed_apps`: sin la app suscrita a la WABA el override del número no recibe nada y el error no se ve en ningún otro lado.
- La escritura en Google Calendar no se prueba desde `verificar` (crearía eventos en el calendario real del negocio); la cubre `prueba-real`.
- Correo saliente con dos caminos: SMTP (stdlib) para Gmail/Zoho/hosting y Microsoft Graph para Microsoft 365, porque Exchange Online apaga el SMTP con contraseña a fines de 2026 y Graph con permiso de aplicación no depende de una contraseña de usuario.
- Outlook.com/Hotmail personales no se soportan: Microsoft ya no acepta contraseña por SMTP y su OAuth delegado exige que una persona inicie sesión y renovar tokens; se recomienda Gmail o un dominio propio.
- El aviso por correo usa el mismo texto que la plantilla `aviso_equipo` (una sola fuente) y va a `email.avisos_a`, aparte de los teléfonos del equipo.
- El reporte mensual se manda por correo solo con `reporte --enviar`: el operador lo revisa antes de mandarlo, como ya decía el checklist mensual.
- `verificar --remoto` prueba el correo sin mandar nada: inicia sesión SMTP o lee el claim `roles` del token de Microsoft, porque un token sin consentimiento de administrador sale bien y el envío falla después.
- Calendario de Microsoft 365 por Graph con permiso de aplicación (`Calendars.ReadWrite`) sobre un buzón de citas, igual que la cuenta de servicio de Google: nadie tiene que iniciar sesión ni renovar tokens.
- Con Microsoft, un evento "tentativo" bloquea el horario (solo `free` y `workingElsewhere` lo dejan libre): ofrecer un horario dudoso cuesta más que no ofrecerlo.
- `ocupado_google` pasa a `ocupado_externo` y despacha por proveedor; la lógica de horarios libres no cambia.
- Correo como canal: el contacto de correo usa su dirección como `telefono` (columna única y NOT NULL ya existente) y `email`; así bajas, bandeja, cola y reportes funcionan sin reconstruir la tabla. Costo: WhatsApp y correo de la misma persona son dos contactos.
- El buzón se lee en el tick (cada 5 min) y el correo se marca leído solo después de entrar a la cola: perder un correo es peor que leerlo dos veces (la cola deduplica por Message-ID).
- Se ignoran respuestas automáticas, listas, no-reply, el propio buzón y el equipo: un bot de correo que contesta autorespuestas entra en bucle.
- De los envíos proactivos solo el recordatorio de cita sale por correo: es transaccional y reduce inasistencias; seguimiento, reactivación y reseñas requieren plantillas aprobadas y consentimiento y se quedan en WhatsApp.
- Se pide un buzón exclusivo para clientes: todo lo no leído se trata como consulta.
- Correo entrante: el bot solo contesta si el remitente pasa DMARC según el encabezado de NUESTRO servidor (el primero, y por IMAP con su id); si no, pasa a humano sin respuesta. Evita que un "From" falsificado cancele citas ajenas o use al bot para mandar correo a terceros. Costo: dominios sin DMARC los atiende una persona.
- Las respuestas por correo van siempre a la dirección guardada; con Microsoft se usa sendMail y no /reply porque /reply obedece el Reply-To, que lo escribe el remitente.
- HTML de correo a texto con `html.parser` y tope de 100 KB en vez de una expresión regular (la revisión de seguridad mostró que la regex podía tardar muchísimo con HTML malicioso); un correo ilegible se marca leído y no bloquea la cola.
- Pagos de Stripe y Mercado Pago entran por la misma cola que WhatsApp: el webhook solo verifica la firma y encola (responde rápido; el proveedor reintenta si no hay 200) y el trabajador registra la venta con la atribución existente.
- Stripe: solo `checkout.session.completed` (links de pago y Checkout traen el teléfono del cliente si se pide); escuchar también `charge.succeeded` duplicaría el cobro.
- Un pago sin cliente reconocible no se pierde ni se adivina: queda en `pagos-sin-contacto.csv`, que `importar-ventas` acepta tal cual tras corregir el teléfono.
- Solo se registran pagos en MXN y aprobados; los reembolsos no se restan solos (no hay borrado de ventas y la garantía se revisa con el dueño cada mes).
- La tolerancia de 5 min de la firma de Stripe usa el reloj del sistema (`base.ahora`), el mismo que el resto.
- Clip queda por CSV: no hay webhooks de pagos documentados.
- DMARC se lee como RFC 8601 (id exacto del servidor, sin comentarios, `dmarc=pass` con `header.from` igual al dominio del remitente) y no buscando el texto "dmarc=pass": la revisión de seguridad mostró que un comentario con datos del remitente (p. ej. `dmarc=pass@evil.mx`) lo burlaba.
- `pagos-sin-contacto.csv` se escribe con `csv.writer` y sin saltos de línea ni fórmulas: el nombre lo escribe el pagador y una fila inyectada se volvería una venta falsa al importarla.
- Mercado Pago: se exige `x-request-id` y se deduplica por él (va dentro de la firma), para que un aviso capturado no se pueda repetir.
- Reseñas de Google: se quedan manuales (`--resenas-google`). La API de Business Profile exige una aprobación de Google que nadie tiene y OAuth del dueño; construir el cliente sin poder probarlo sería adivinar. La alternativa por Places API cuesta dinero por consulta y requiere cuenta de facturación: queda para que el dueño decida.
- DMARC: un encabezado con comillas o barras invertidas no se interpreta (no verificado), y se exige exactamente un resultado `dmarc` con propiedades bien formadas y un solo `header.from`: cualquier ambigüedad cae del lado seguro (lo atiende una persona).
- Fichas: módulo `rv/clientes.py` con dos puntos de entrada (`get`/`post`) que `web.py` llama después del login y del chequeo de Origin; así la seguridad de la bandeja no se duplica.
- Un cliente dado de alta a mano queda con `origen='importado'` (el CHECK de la tabla no admite otro valor y, sobre todo, no llegó escribiendo: no debe contar como respuesta rápida en la garantía).
- El teléfono es la llave de la ficha y no se edita desde ella; un número equivocado se corrige dando de alta el correcto (no hay borrado de datos).
- Notas del equipo solo se agregan, nunca se editan: es el historial de lo que se sabía de un cliente y cuándo.
- Cotizaciones: la vigencia cuenta desde el primer envío (no desde que se creó el borrador) y vence al terminar el último día; reenviar no la extiende.
- La IA puede mencionar los montos de las cotizaciones abiertas de ESE cliente (total, precio y subtotal de cada línea) y ningún otro monto fuera del config; aceptar o cambiar una cotización siempre lo confirma una persona (handoff `cotizacion:<folio>`): un "sí" ambiguo no debe comprometer al negocio.
- Si hay dos o más cotizaciones abiertas y la IA no dice cuál, no se adivina: el handoff queda como cualquier otro de la IA.
- Envío de cotización fuera de la ventana: primero correo (si la ficha lo tiene), luego la plantilla `cotizacion` (con folio, total y vigencia, sin líneas: Meta no admite listas variables); se respetan bajas y horario de envío.
- PDF: página HTML para imprimir desde el navegador (sin biblioteca de PDF).
- Los formularios de la bandeja conservan los campos vacíos (`keep_blank_values`): sin eso, las filas de una cotización se desalineaban.
- `Config` copia los valores por omisión: antes, modificar `cfg["email"]["smtp"]` en una instancia alteraba los valores por omisión de todo el proceso (encontrado por las pruebas de cotizaciones).
- La venta se liga a una cotización dentro de `registrar_venta`, el único punto por el que entran todas las ventas (a mano, CSV, pagos en línea): una sola regla, sin copias por canal.
- Ligado automático solo con una cotización aceptada con saldo y un monto que no lo rebasa; cualquier otro caso queda sin ligar y aparece en el cotejo (mejor revisar a mano que repartir mal un pago).
- "Monto distinto al total" se reporta como "ventas ligadas que no cubren el total": con pagos parciales, una venta sola casi nunca iguala el total; lo que importa es si la suma lo cubre.
- Una cita cuenta para la cotización si está ligada o si se creó después de aceptarla (agendar desde la ficha no obliga a ligarla); "asistió sin venta" acepta cualquier venta del cliente desde ese día, porque los pagos en línea no traen la cita.
- El cotejo por omisión es del mes en curso (sirve para revisar durante el mes); el reporte mensual sigue siendo del mes anterior.
