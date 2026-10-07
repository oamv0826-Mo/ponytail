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
