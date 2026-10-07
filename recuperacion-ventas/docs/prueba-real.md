# Prueba real con tu teléfono (desde tu Mac, sin VPS)

Objetivo: comprobar contra Meta, Anthropic y Google de verdad todo lo que hasta ahora solo se probó simulado. Si
todos los pasos pasan, se fusiona el PR.

Se corre desde tu Mac con un túnel gratuito, así que no pagas servidor todavía. Dura dos sesiones cortas: una de
noche (para el mensaje fuera de horario) y otra al día siguiente entre 10:00 y 18:00.

## Qué necesitas

- Los pasos 1 a 6 de [arranque-cuentas.md](arranque-cuentas.md). Las 9 plantillas deben estar **Activas**.
- Dos teléfonos dados de alta como destinatarios del número de prueba: el tuyo (hace de cliente) y otro que hace de
  dueño o recepción. **No pueden ser el mismo**: el sistema ignora los mensajes que vienen del dueño o del equipo.
- Python 3.11 o más reciente. La Mac trae uno más viejo. Instálalo con `brew install python@3.12`, o con el
  instalador de python.org.
- **ngrok** con cuenta gratuita.

### Por qué ngrok y no cloudflared

El túnel rápido de cloudflared no pide cuenta, pero cambia de dirección cada vez que lo reinicias, y habría que
volver a configurar Meta en cada sesión. La cuenta gratuita de ngrok incluye un dominio fijo: configuras Meta una
sola vez aunque la prueba dure dos días.

1. Crea la cuenta en <https://ngrok.com> e instálalo: `brew install ngrok`.
2. Conecta tu cuenta (el comando exacto con tu token aparece en el panel de ngrok):
   `ngrok config add-authtoken <tu-token>`.
3. En el panel: **Domains**. Copia tu dominio gratuito, algo como `nombre-al-azar.ngrok-free.app`.

## 1. Preparar la carpeta de la prueba (una vez)

```bash
cd ~/ponytail/recuperacion-ventas          # donde clonaste el repo, en la rama del PR
mkdir -p ~/rv-prueba
cp ejemplo/cliente.json ~/rv-prueba/
```

Edita `~/rv-prueba/cliente.json`:

| Campo | Valor |
|---|---|
| `modo_prueba` | `false` |
| `url_publica` | `https://<tu-dominio>.ngrok-free.app` (sin `/` al final) |
| `whatsapp.phone_number_id` y `whatsapp.waba_id` | los del paso 1 de la guía de cuentas |
| `dueno.telefono` | el **segundo** teléfono (recibe los avisos) |
| `equipo` | `[]`, o el segundo teléfono otra vez |
| `agenda` | `{"proveedor": "google", "calendar_id": "<ID del calendario de prueba>"}` |
| `ia.tope_mensual_usd` | `5` |
| `horario` | déjalo así; el mensaje de noche debe salir fuera de este horario (después de las 19:00 entre semana) |

Crea `~/rv-prueba/secretos.env` con las 5 líneas del final de la guía de cuentas y pon ahí `google-sa.json`.

Crea tu usuario de la bandeja (pide una contraseña de 10 caracteres o más):

```bash
python3 -m rv --cliente ~/rv-prueba usuario oliver
```

## 2. Cada sesión: tres ventanas de Terminal

```bash
# Terminal 1: el sistema
python3 -m rv --cliente ~/rv-prueba serve

# Terminal 2: el túnel
ngrok http --url=<tu-dominio>.ngrok-free.app 8080

# Terminal 3: la prueba guiada
python3 -m rv --cliente ~/rv-prueba prueba-real --tel <tu número a 10 dígitos>
```

## 3. Conectar el webhook en Meta (una vez, con las terminales 1 y 2 corriendo)

En developers.facebook.com → tu app → **WhatsApp → Configuración**, sección **Webhook**:

1. **URL de devolución de llamada:** `https://<tu-dominio>.ngrok-free.app/webhook`.
2. **Token de verificación:** el valor de `WA_VERIFY_TOKEN` de tu `secretos.env`.
3. **Verificar y guardar.** Si falla, revisa que el token sea idéntico y que la terminal 1 esté corriendo.
4. En la lista de campos del webhook, **Suscribir** el campo `messages`.

El primer paso de la prueba (`conexion`) hace lo demás: suscribe la app a tu WABA y pone el override del número
apuntando a la misma URL, con tu confirmación.

## 4. Los pasos

`prueba-real` corre los pasos en orden y se salta los que ya pasaron. Cada paso:

1. Te dice qué hacer.
2. Espera hasta 10 minutos a que el sistema reaccione, revisando la base de datos.
3. Te pregunta si en tu teléfono o en la bandeja viste lo esperado, y te deja una nota.

Para repetir un solo paso: `prueba-real --tel <tu número> <paso>`.

| # | Paso | Cuándo | Qué haces | Qué debe pasar |
|---|---|---|---|---|
| 0 | `conexion` | Noche | Nada: lo hace el comando | Chequeos OK, override configurado y respuesta real de Meta guardada |
| 1 | `noche` | Después del horario | Preguntas un precio. No agendes. | La IA responde en segundos; queda "fuera de horario" y se activa el seguimiento |
| 2 | `seguimiento` | Día siguiente, 10:00 a 18:00 | Nada: el comando simula que pasaron 2 días | Te llega la plantilla `seguimiento_1` |
| 3 | `cita` | Igual | "quiero agendar una cita de limpieza" y eliges un número | Confirmación en WhatsApp y evento en Google Calendar |
| 4 | `baja` | Igual | Escribes BAJA | Confirmación de baja |
| 5 | `urgencia` | Igual | "tengo una urgencia, mucho dolor" | Mensaje del 911 y aviso al segundo teléfono |
| 6 | `handoff` | Igual | En la bandeja: Tomar, responder y Devolver al bot. Luego escribes "quiero hablar con una persona" | Te llega la respuesta de la bandeja y al segundo teléfono el aviso |
| 7 | `asistencia` | Igual | Bandeja → Citas → **Asistió** | Cita marcada |
| 8 | `venta` | Igual | En la misma fila, registras una venta (por ejemplo 800) | Venta con origen **Seguimiento** |
| 9 | `reporte` | Igual | Nada | Reporte del mes con la venta recuperada y la garantía |

La bandeja se abre en `https://<tu-dominio>.ngrok-free.app/bandeja`. La primera vez, ngrok muestra un aviso: pulsa
**Visit Site**. Ábrela siempre por esa dirección y no por `127.0.0.1`: el sistema rechaza formularios que vienen de
otra dirección.

Notas:

- **Asistencia:** si tu cita quedó para otro día, el comando la mueve a hace una hora solo en la base, para poder
  marcar Asistió. En Google Calendar sigue en su fecha original.
- **Avisos de noche:** un handoff fuera de horario avisa al equipo cuando el negocio abre, en el siguiente `tick`. En
  la Mac no hay timer. Si quieres verlo, corre `python3 -m rv --cliente ~/rv-prueba tick` en la mañana.
- **Después de la baja,** tu número queda dado de baja en esa carpeta y ya no recibe seguimientos ni reseñas. Para
  repetir la prueba completa, usa una carpeta nueva.

## 5. Qué me mandas al terminar

1. `~/rv-prueba/prueba-real-resultados.md`: la lista de lo que pasó y lo que falló, con tus notas.
2. `~/rv-prueba/meta-webhook-configuration.json`: la respuesta real de Meta sobre el override. El paso `conexion`
   dice en qué campo apareció tu URL. La documentación de Meta dice `webhook_configuration.phone_number`, y
   `verificar --remoto` ya lee ese campo; este archivo lo confirma con datos reales.

Si todo pasó, se fusiona el PR. El `Caddyfile` se valida después, en el VPS (paso 7 de la guía de cuentas).

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| Meta no verifica el webhook | `WA_VERIFY_TOKEN` distinto, o la terminal 1 o la 2 no están corriendo |
| En la terminal 1 aparece `401` en cada mensaje | `WA_APP_SECRET` equivocado: los mensajes llegan pero se rechazan por firma |
| No te llega nada y el detalle dice error `131030` | Tu teléfono no está en la lista de destinatarios del número de prueba |
| Error de plantilla (`132001`) | La plantilla no está Activa, o su nombre o idioma no es exactamente el de `plantillas.md` |
| La cita no aparece en Google | El calendario no está compartido con el correo de la cuenta de servicio, con permiso "Hacer cambios en eventos" |
| La IA no contesta y todo pasa a humano | Clave de Anthropic inválida o sin saldo: `verificar --remoto` lo dice |
| Tus mensajes no hacen nada | Tu número está como `dueno` o en `equipo`: el sistema los ignora |
