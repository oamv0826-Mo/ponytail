# Arranque de cuentas, paso a paso

Guía para crear las cuentas que el sistema necesita, en el orden en que conviene hacerlas. No hace falta saber
programar: solo copiar datos de una página a dos archivos de la carpeta del cliente.

- **`cliente.json`**: datos del negocio y de las cuentas que no son secretos.
- **`secretos.env`**: contraseñas y claves, una por línea (`NOMBRE=valor`). Nunca se sube a GitHub ni se comparte
  por WhatsApp o correo.

Las pantallas de Meta, Anthropic y Google cambian seguido de nombre; si un botón no aparece igual, busca el más
parecido. Los tiempos son aproximados.

## Resumen

| # | Cuenta | Costo | Tiempo | Dato que obtienes → dónde va |
|---|---|---|---|---|
| 1 | App de Meta con número de prueba | Gratis | 30 min | `phone_number_id`, `waba_id` → `cliente.json` |
| 2 | Las 9 plantillas en Meta | Gratis | 1 h para someterlas; la aprobación tarda de minutos a días | Nada; solo deben quedar "Activas" |
| 3 | Token permanente de Meta y secreto de la app | Gratis | 20 min | `WA_TOKEN`, `WA_APP_SECRET`, `WA_VERIFY_TOKEN` → `secretos.env` |
| 4 | Clave de Anthropic con límite de gasto | 5 USD de saldo prepagado (mínimo) | 15 min | `ANTHROPIC_API_KEY` → `secretos.env` |
| 5 | Cuenta de servicio de Google | Gratis | 20 min | archivo JSON → `GOOGLE_SA_FILE` en `secretos.env` |
| 6 | Calendario de prueba | Gratis | 10 min | `agenda.calendar_id` → `cliente.json` |
| 7 | VPS y dominio | 6 a 12 USD al mes, más el dominio | 1 h | **Solo cuando tengas el primer cliente** |
| 8 | Respaldo externo con rclone | Gratis hasta 10 GB | 20 min | **Solo cuando tengas el primer cliente** |
| 9 | Correo para avisos y reporte (opcional) | Gratis con Gmail; Microsoft 365 ya pagado por el negocio | 15 a 30 min | bloque `email` → `cliente.json`; `SMTP_CLAVE` o `MS_*` → `secretos.env` |
| 10 | Calendario de Microsoft 365 (en vez de Google, si el negocio usa Outlook) | Lo que el negocio ya paga | 20 min | `agenda` → `cliente.json`; `MS_*` → `secretos.env` |

Con los pasos 1 a 6 ya puedes hacer la prueba real desde tu Mac ([prueba-real.md](prueba-real.md)).

---

## 1. App de Meta con el número de prueba gratuito

**Costo:** gratis. El número de prueba no pide método de pago. **Tiempo:** 30 minutos.

1. Entra a <https://developers.facebook.com> con tu Facebook personal y pulsa **Comenzar** para registrarte como
   desarrollador (te pide confirmar tu teléfono).
2. **Mis apps → Crear app**. Elige el caso de uso **Conectar con clientes por WhatsApp** y el tipo **Negocio**.
   Nombre: por ejemplo "Agorá".
3. Te pide un **portafolio comercial** (antes "Business Manager"). Crea uno a tu nombre o al de tu negocio. Es la
   cuenta donde vivirán después los números de tus clientes.
4. En el menú de la app: **WhatsApp → Configuración de la API**. Meta ya creó dos cosas:
   - Un **número de prueba** gratuito.
   - Una **cuenta de WhatsApp Business** de prueba (WABA).
5. En esa misma pantalla, en **Para**, agrega los teléfonos que van a recibir mensajes en la prueba. Cada uno se
   confirma con un código que llega por WhatsApp. El número de prueba solo puede escribir a **5 números**; agrega:
   - Tu teléfono (el que hará de cliente).
   - Un segundo teléfono que hará de dueño o recepción del negocio (recibe los avisos del sistema). Puede ser el
     de alguien de confianza.
6. Copia dos datos que aparecen ahí mismo:

| En Meta se llama | Va en `cliente.json` |
|---|---|
| Identificador del número de teléfono (Phone number ID) | `"whatsapp": {"phone_number_id": "..."}` |
| Identificador de la cuenta de WhatsApp Business (WhatsApp Business Account ID) | `"whatsapp": {"waba_id": "..."}` |

## 2. Someter las 9 plantillas (hazlo en cuanto tengas la app)

**Costo:** gratis. **Tiempo:** alrededor de 1 hora para capturarlas. Meta las revisa en minutos u horas, a veces
en días, por eso va antes que todo lo demás.

Fuera de las 24 horas después del último mensaje del cliente, WhatsApp solo deja enviar plantillas aprobadas. El
sistema usa 9 y las busca por su nombre exacto.

1. En la app: **WhatsApp → Configuración de la API → Administrar plantillas de mensajes** (abre el WhatsApp
   Manager). Revisa que arriba esté seleccionada la cuenta de prueba del paso 1.
2. **Crear plantilla**, una por una, con los datos de [plantillas.md](plantillas.md):
   - **Categoría:** la que dice la tabla (Utilidad o Marketing).
   - **Nombre:** exactamente el de la tabla, en minúsculas y con guion bajo (por ejemplo `seguimiento_1`).
   - **Idioma:** Español (MEX).
   - **Cuerpo:** copia el texto tal cual, con las variables `{{1}}`, `{{2}}`…
   - **Ejemplos de variables:** Meta pide un ejemplo por variable. Usa los de la tabla de abajo.
3. **Enviar**. El estado pasa de "En revisión" a **Activa**. Si alguna sale "Rechazada", lee el motivo, ajusta el
   texto sin cambiar el nombre ni el número de variables, y vuelve a enviarla.

| Plantilla | Ejemplos para las variables |
|---|---|
| `aviso_equipo` | Clínica Aura · Mariana (+5281…) · pidió hablar con una persona · https://rv.ejemplo.mx/bandeja/c/12 |
| `retomar_contacto` | Mariana · Clínica Aura |
| `cita_confirmada` | Mariana · Limpieza facial · Clínica Aura · martes 14 de octubre a las 10:00 |
| `recordatorio_cita` | Mariana · Limpieza facial · martes 14 de octubre a las 10:00 · Av. Ejemplo 123 |
| `seguimiento_1` | Mariana · Limpieza facial |
| `seguimiento_2` | Mariana · Clínica Aura · Limpieza facial |
| `seguimiento_3` | Mariana · Limpieza facial |
| `reactivacion` | Mariana · Clínica Aura |
| `resena` | Mariana · Clínica Aura · https://g.page/r/ejemplo/review |

**Importante:** las plantillas pertenecen a una cuenta de WhatsApp Business. Estas sirven para la prueba. Cuando el
número del primer cliente quede en otra cuenta, hay que volver a someterlas ahí, con los mismos nombres y textos.
Las plantillas no mencionan a ningún negocio fijo (el nombre va en una variable), así que se reutilizan tal cual.

`python3 -m rv --cliente <carpeta> verificar --remoto` revisa cuáles ya están activas.

## 3. Token permanente de Meta y secreto de la app

**Costo:** gratis. **Tiempo:** 20 minutos.

El token que aparece en **Configuración de la API** vence en horas: no sirve para el sistema. Se usa uno de un
**usuario del sistema**, que no vence.

1. Entra a <https://business.facebook.com> → **Configuración** (engrane) → **Usuarios → Usuarios del sistema →
   Agregar**. Nombre "rv", rol **Administrador**.
2. Con el usuario seleccionado: **Asignar activos**.
   - **Apps:** tu app, con control total.
   - **Cuentas de WhatsApp:** la cuenta de prueba, con control total.
3. **Generar token**. Elige tu app, vencimiento **Nunca**, y marca los permisos `whatsapp_business_messaging` y
   `whatsapp_business_management`. Copia el token en ese momento: Meta no lo vuelve a mostrar.
4. Secreto de la app: en developers.facebook.com, tu app → **Configuración de la app → Básica → Clave secreta de la
   app → Mostrar**.
5. Token de verificación del webhook: lo inventas tú. Genera uno al azar con este comando en la Terminal de tu Mac:
   `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`

| Dato | Va en `secretos.env` |
|---|---|
| Token del usuario del sistema | `WA_TOKEN=...` |
| Clave secreta de la app | `WA_APP_SECRET=...` |
| Token de verificación que generaste | `WA_VERIFY_TOKEN=...` |

El webhook (la dirección a la que Meta manda los mensajes) se configura en la prueba real, porque depende del túnel.

## 4. Clave de Anthropic con límite de gasto

**Costo:** saldo prepagado, mínimo 5 USD. Una prueba completa gasta centavos con el modelo `claude-haiku-4-5`.
**Tiempo:** 15 minutos.

1. Crea tu cuenta en <https://console.anthropic.com> (te puede llevar a platform.claude.com; es lo mismo).
2. **Billing (Facturación):** agrega tu tarjeta y compra 5 USD de créditos. Deja **apagada** la recarga automática.
3. **Limits (Límites):** pon un límite de gasto mensual bajo, por ejemplo 10 USD. Si se alcanza, la API deja de
   responder y el sistema pasa las conversaciones a humano: nunca cobra de más.
4. **API Keys → Create Key**. Nombre "rv-prueba". Cópiala en ese momento: no se vuelve a mostrar.

| Dato | Va en |
|---|---|
| La clave (empieza con `sk-ant-`) | `secretos.env` → `ANTHROPIC_API_KEY=...` |
| Tope del propio sistema (segunda protección) | `cliente.json` → `"ia": {"tope_mensual_usd": 10}` |

## 5. Cuenta de servicio de Google

**Costo:** gratis; la API de Calendar no cobra y no hace falta activar facturación. **Tiempo:** 20 minutos.

Es un "usuario robot" que el sistema usa para leer y crear citas en el calendario. Usa tu Gmail personal: algunas
cuentas de empresa (Google Workspace) bloquean la creación de claves.

1. Entra a <https://console.cloud.google.com> → arriba, selector de proyecto → **Proyecto nuevo**. Nombre "rv".
2. **APIs y servicios → Biblioteca** → busca **Google Calendar API** → **Habilitar**.
3. **IAM y administración → Cuentas de servicio → Crear cuenta de servicio**. Nombre "rv-agenda". No necesita roles:
   pulsa **Listo**.
4. Abre la cuenta creada → **Claves → Agregar clave → Crear clave nueva → JSON**. Se descarga un archivo.
5. Guarda ese archivo en la carpeta del cliente como `google-sa.json`. Es una contraseña: no lo compartas ni lo
   subas a GitHub.
6. Anota el correo de la cuenta de servicio (termina en `iam.gserviceaccount.com`); lo usas en el paso 6.

| Dato | Va en `secretos.env` |
|---|---|
| Ruta completa del archivo, por ejemplo `/Users/oliver/rv-prueba/google-sa.json` | `GOOGLE_SA_FILE=...` |

## 6. Calendario de prueba

**Costo:** gratis. **Tiempo:** 10 minutos.

1. En <https://calendar.google.com>, a la izquierda: **Otros calendarios → + → Crear calendario**. Nombre
   "Citas prueba RV". Zona horaria: Ciudad de México / Monterrey.
2. En la configuración de ese calendario: **Compartir con personas específicas → Agregar personas** → el correo de
   la cuenta de servicio del paso 5, con permiso **Hacer cambios en eventos**.
3. En la misma página, sección **Integrar el calendario**: copia el **ID del calendario** (termina en
   `@group.calendar.google.com`).

| Dato | Va en `cliente.json` |
|---|---|
| ID del calendario | `"agenda": {"proveedor": "google", "calendar_id": "..."}` |

Con un cliente real se repite igual, pero el calendario lo crea el negocio en su propia cuenta y lo comparte con el
mismo correo de la cuenta de servicio.

---

## 7. VPS y dominio (solo cuando tengas el primer cliente)

**Costo:** de 6 a 12 USD al mes por el servidor (por ejemplo Hetzner CX22 o DigitalOcean de 6 USD), más un
dominio, de 200 a 400 MXN al año. **Tiempo:** 1 hora la primera vez.

Contrátalo con el primer anticipo, no antes. Un solo servidor atiende a varios clientes.

- Ubuntu LTS, con acceso por llave SSH.
- Un subdominio, por ejemplo `rv.tudominio.mx`, apuntando a la IP del servidor.
- La instalación técnica (Caddy, systemd, carpetas por cliente) está en
  [checklist-instalacion.md](checklist-instalacion.md) y [runbook.md](runbook.md). Ahí se valida el `Caddyfile`
  con `caddy validate --config /etc/caddy/Caddyfile`.

| Dato | Va en `cliente.json` |
|---|---|
| Dirección pública del cliente | `"url_publica": "https://rv.tudominio.mx/c/<id-del-cliente>"` |

## 8. Respaldo externo con rclone (solo cuando tengas el primer cliente)

**Costo:** gratis hasta 10 GB con Backblaze B2 o Cloudflare R2, de sobra para años de respaldos. **Tiempo:** 20 minutos.

1. Crea la cuenta y un "bucket" privado llamado, por ejemplo, `rv-respaldos`.
2. Crea una clave de aplicación con acceso solo a ese bucket.
3. En el servidor: `rclone config` → nuevo remoto llamado `respaldo`, con esa clave.
4. El destino va en `/etc/rv/respaldo.env` (ver [runbook.md](runbook.md)), fuera de la carpeta del cliente.

---

## 9. Correo para avisos y reporte (opcional)

**Costo:** gratis con una cuenta de Gmail; con Microsoft 365, lo que el negocio ya paga. **Tiempo:** 15 min (Gmail) o
30 min (Microsoft 365, necesita a quien administra la cuenta del negocio).

Los mismos avisos que llegan por WhatsApp al equipo (cliente pide humano, urgencia, escalamiento, tope de IA) llegan
también por correo, y `reporte AAAA-MM --enviar` manda el reporte del mes.

**Opción A: Gmail, Zoho o el correo del hosting (SMTP).**
1. Gmail: activa la verificación en dos pasos en <https://myaccount.google.com/security> y luego crea una
   **contraseña de aplicación** en <https://myaccount.google.com/apppasswords> (16 letras). La contraseña normal no
   funciona. En cuentas de Google Workspace, el administrador puede tenerlas desactivadas.
2. Zoho o hosting (cPanel): usa el servidor SMTP que te da el proveedor y la contraseña del buzón.

```json
"email": {"proveedor": "smtp", "remitente": "agora.clinica@gmail.com", "avisos_a": ["dueno@clinica.mx"],
          "smtp": {"host": "smtp.gmail.com", "puerto": 587, "seguridad": "starttls"}}
```

| Dato | Va en `secretos.env` |
|---|---|
| La contraseña de aplicación (sin espacios) | `SMTP_CLAVE=...` |
| Solo si el usuario no es el mismo que `remitente` | `SMTP_USUARIO=...` |

**Outlook.com y Hotmail personales no sirven:** Microsoft ya no acepta contraseña (ni contraseña de aplicación) por
SMTP en cuentas personales. Usa Gmail o el correo de un dominio propio.

**Opción B: Microsoft 365 del negocio (Microsoft Graph).** La hace quien administra la cuenta de Microsoft del negocio.
1. Entra a <https://entra.microsoft.com> → **Aplicaciones → Registros de aplicaciones → Nuevo registro**. Nombre:
   "Agorá". Tipo de cuenta: solo este directorio. Sin URI de redirección.
2. Anota el **Id. de aplicación (cliente)** y el **Id. de directorio (inquilino)**.
3. **Permisos de API → Agregar → Microsoft Graph → Permisos de aplicación → `Mail.Send`**. Luego **Conceder
   consentimiento de administrador**. Sin el consentimiento, el token sale bien pero el envío falla.
4. **Certificados y secretos → Nuevo secreto de cliente** (vence: máximo 24 meses; anota la fecha). Copia el
   **Valor** (no el Id.); solo se ve una vez.
5. Recomendado: `Mail.Send` de aplicación permite enviar como cualquier buzón del negocio. Pide a quien administra
   Exchange que limite la app al buzón de Agorá (control de acceso basado en roles para aplicaciones de Exchange Online).

```json
"email": {"proveedor": "microsoft", "remitente": "agora@clinica.mx", "avisos_a": ["dueno@clinica.mx"]}
```

| Dato | Va en `secretos.env` |
|---|---|
| Id. de directorio (inquilino) | `MS_TENANT_ID=...` |
| Id. de aplicación (cliente) | `MS_CLIENT_ID=...` |
| Valor del secreto | `MS_CLIENT_SECRET=...` |

**Correo como canal de clientes (opcional).** Agorá también puede leer el buzón del `remitente` y contestar a los
clientes que escriben por correo, igual que por WhatsApp (mismas reglas, handoff y bajas; responde en el mismo hilo).
Usa un buzón **exclusivo para clientes** (por ejemplo `citas@clinica.mx` o `agora.clinica@gmail.com`), nunca el
correo personal del dueño: todo lo no leído de ese buzón se trata como consulta. Agorá ignora respuestas automáticas,
boletines y remitentes "no-reply", y lee cada 5 minutos (con el tick).

- Gmail/Zoho/hosting: misma contraseña de aplicación; agrega el servidor IMAP (Gmail: `imap.gmail.com`, puerto 993;
  si Gmail lo pide, activa IMAP en Configuración → Reenvío y correo POP/IMAP).
- Microsoft 365: agrega a la app los permisos de aplicación **`Mail.ReadWrite`** (leer y marcar como leído) además de
  `Mail.Send`, y vuelve a conceder el consentimiento.

```json
"email": {..., "entrada": {"activa": true, "imap": {"host": "imap.gmail.com", "puerto": 993}}}
```

Comprueba con `python3 -m rv --cliente <carpeta> verificar --remoto`: inicia sesión en SMTP (y abre el buzón IMAP en
solo lectura) sin mandar nada, o revisa que el token de Microsoft traiga `Mail.Send` (y `Mail.ReadWrite` si lees el
buzón).

---

## 10. Calendario de Microsoft 365 (en vez de Google)

**Costo:** lo que el negocio ya paga por Microsoft 365. **Tiempo:** 20 min. Lo hace quien administra la cuenta.

1. Crea (o reutiliza) un buzón para las citas, por ejemplo `citas@clinica.mx`; puede ser un buzón compartido o de
   sala, sin licencia.
2. En la app "Agorá" del paso 9 (o créala igual: Entra → Registros de aplicaciones → Nuevo registro), agrega
   **Microsoft Graph → Permisos de aplicación → `Calendars.ReadWrite`** y **concede el consentimiento de
   administrador**. Los secretos `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET` son los mismos del paso 9.
3. Igual que con el correo: limita la app a ese buzón con el control de acceso para aplicaciones de Exchange Online;
   sin eso, `Calendars.ReadWrite` alcanza todos los calendarios del negocio.

```json
"agenda": {"proveedor": "microsoft", "calendar_id": "citas@clinica.mx"}
```

`verificar --remoto` revisa que el token traiga `Calendars.ReadWrite` y lee la disponibilidad de mañana. Las citas
aparecen en el Outlook de quien tenga acceso a ese buzón.

---

## Cómo queda `secretos.env` al terminar los pasos 1 a 6

```
WA_TOKEN=EAAG...
WA_APP_SECRET=1a2b3c...
WA_VERIFY_TOKEN=lo-que-generaste
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_SA_FILE=/Users/oliver/rv-prueba/google-sa.json
```
