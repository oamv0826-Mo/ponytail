# Agorá: hand off

Estado al 9 de octubre de 2026. Este documento resume todo lo construido en la conversación de desarrollo: qué hace el
sistema, dónde está cada cosa, cómo correrlo, qué se decidió y qué falta. El detalle vive en los documentos que se
enlazan; aquí está el mapa.

## 1. Qué es

**Agorá** es un sistema de recuperación de ventas para negocios de servicio en México (clínicas, estéticas,
consultorios). Contesta por WhatsApp y por correo con IA (Claude) dentro de reglas estrictas, agenda citas en Google
Calendar o Microsoft 365, da seguimiento a quien no agendó, reactiva clientes antiguos, pide reseñas, registra ventas
(a mano, CSV o pagos en línea), lleva fichas de clientes con cotizaciones y saldo, y entrega un reporte mensual con la
garantía del contrato. Solo biblioteca estándar de Python 3.11+, una instancia (proceso + SQLite) por negocio.

**Estado:** completo y probado sin cuentas (195 pruebas, modo prueba, demos). **No probado todavía contra servicios
reales** (Meta, Anthropic, Google, Microsoft, Stripe, Mercado Pago): eso es la "prueba real" pendiente.

## 2. Dónde está

| Qué | Dónde |
|---|---|
| Repositorio | `oamv0826-Mo/ponytail` (GitHub) |
| Rama | `claude/festive-ride-3kldx0` |
| Pull request | [oamv0826-Mo/ponytail#1](https://github.com/oamv0826-Mo/ponytail/pull/1) (abierto; se fusiona después de la prueba real) |
| Código | `recuperacion-ventas/` (el resto del repo es el plugin "ponytail", no se toca) |
| Reglas para agentes | `AGENTS.md` en la raíz del repo (desarrollador senior "flojo": reutilizar, el cambio más chico, una prueba por lógica) |

## 3. Cómo correrlo

```bash
cd recuperacion-ventas
python3 -m unittest discover -s tests                 # 195 pruebas, sin cuentas ni red
mkdir -p /tmp/demo && cp ejemplo/cliente.json /tmp/demo/
python3 -m rv --cliente /tmp/demo usuario ana         # contraseña de la bandeja
python3 -m rv --cliente /tmp/demo serve               # http://127.0.0.1:8080/bandeja
python3 -m rv --cliente /tmp/demo simular --interactivo          # escribir como cliente (WhatsApp)
python3 -m rv --cliente /tmp/demo simular --de ana@gmail.com --asunto Precio --texto "¿Precio?"   # correo
python3 ejemplo/historia.py                           # historia completa de una clienta
python3 -m rv demo-ventas --nicho nichos/clinica-estetica.json --rapido   # demo de venta de 3 min
```

`modo_prueba: true` en `cliente.json`: nada sale a Meta ni a correo; todo queda en `envios-prueba.log`. Sin
`ANTHROPIC_API_KEY` se usa una IA simulada por palabras clave (no entiende contexto; la real sí).

## 4. Mapa del código (`rv/`)

| Módulo | Qué hace |
|---|---|
| `base.py` | Config (con valores por omisión copiados), horarios y ventana de envío, teléfonos E.164, texto, SQLite, migraciones con candado, respaldo |
| `wa.py` | WhatsApp Cloud API: firma del webhook, lectura de mensajes y archivos, envío de texto y plantillas |
| `motor.py` | Cola persistente y pipeline de cada mensaje: bajas, urgencia médica, handoff, IA, respuesta por el canal del contacto, correo entrante |
| `ia.py` | Claude por HTTP con herramienta, guardrails (montos solo del config o de las cotizaciones del cliente, sin dosis), tope de costo, IA simulada |
| `agenda.py` | Disponibilidad y citas: Google (JWT de cuenta de servicio en Python puro) o Microsoft 365; proponer, reservar, cancelar, reprogramar |
| `tick.py` | Cada 5 min: correo entrante, avisos, escalamientos, recordatorios, reseñas, seguimiento 2/5/10, reactivación, vencimiento de cotizaciones |
| `ventas.py` | Importación CSV, registro de ventas con atribución, reporte mensual con garantía, página estática |
| `clientes.py` | Fichas de clientes, cotizaciones, saldo y cotejo (cotización → cita → venta) |
| `correo.py` | Correo: avisos y reporte (SMTP o Microsoft Graph); canal de clientes (IMAP o Graph) con verificación DMARC |
| `ms.py` | Token de Microsoft Graph (credenciales de aplicación) |
| `pagos.py` | Webhooks de Stripe y Mercado Pago con firma → ventas registradas solas |
| `web.py` | Servidor HTTP: webhook de Meta, pagos y la bandeja web (login, sesiones, anti-CSRF por Origin) |
| `verificar.py` | Chequeos locales y remotos (solo lectura) de cada integración |
| `auditoria.py`, `demo.py`, `prueba.py` | Kit de venta: auditoría de negocios, demo para dueños, prueba guiada con cuentas reales |
| `__main__.py` | Comandos de la línea de comandos |

**Comandos:** `serve`, `simular`, `tick`, `importar-clientes`, `importar-ventas`, `reporte [--enviar]`, `cotejo`,
`pagina`, `verificar [--remoto]`, `respaldo`, `configurar-webhook`, `usuario`, `auditoria`, `demo-ventas`,
`prueba-real`.

**Bandeja web:** `/bandeja` (conversaciones), `/bandeja/citas`, `/bandeja/clientes` (fichas), `/bandeja/cotizaciones/<id>`
(e `/imprimir`), `/bandeja/cotejo`.

**Esquema** (`docs/esquema.sql`, versiones 1–10; nunca se edita una publicada): v1 base · v2–v5 cola, archivos y ventas
· v6 índices · v7 correo como canal · v8 fichas · v9 cotizaciones · v10 venta ligada a cotización.

## 5. Documentos (`recuperacion-ventas/docs/`)

| Documento | Para qué |
|---|---|
| [DISEÑO.md](DISEÑO.md) | Diseño completo; §7 integraciones (con fuentes), §8 fichas, cotizaciones y cotejo |
| [DECISIONES.md](DECISIONES.md) | Cada decisión tomada sin consulta, una línea con el porqué (185) |
| [esquema.sql](esquema.sql) | Fuente única de la base de datos |
| [plantillas.md](plantillas.md) | Las 10 plantillas de WhatsApp para someter a Meta |
| [anexo-contrato.md](anexo-contrato.md) | Reglas de atribución y garantía para anexar al contrato |
| [arranque-cuentas.md](arranque-cuentas.md) | Paso a paso de cada cuenta (Meta, Anthropic, Google, correo, Microsoft 365, pagos, reseñas), con costo y tiempo |
| [prueba-real.md](prueba-real.md) | Prueba guiada con tu teléfono y cuentas reales |
| [checklist-instalacion.md](checklist-instalacion.md) | Instalación de un cliente en 3 semanas y tareas mensuales |
| [runbook.md](runbook.md) | Operación: servicios, respaldos, actualizaciones, fallas |
| [demo-ventas.md](demo-ventas.md) | Guion de la demo de 10 minutos para dueños |
| [BUGHUNT.md](BUGHUNT.md) | Las 8 rondas de caza de errores (24 errores corregidos) |

## 6. Avances, en orden

1. **Núcleo (etapas E1–E5).** Webhook con firma y cola, IA con guardrails, handoff y bandeja con login; agenda con
   Google Calendar; tick (seguimiento, recordatorios, reseñas, reactivación) y bajas; ventas, atribución, reporte con
   garantía y página; despliegue (systemd, Caddy, rclone), `verificar`, runbook y checklist.
2. **Revisión de código y simulaciones de punta a punta** (correcciones de reprogramación, recuperación tras caída,
   textos de plantillas, asistencia solo el día de la cita).
3. **Kit de venta y prueba real** (otra sesión, fusionado): guía de cuentas, `prueba-real`, `auditoria`, `demo-ventas`.
4. **Caza de errores autónoma, 8 rondas:** 24 errores, cada uno con prueba (motor, agenda, tick, ventas, bandeja, CSV y
   teléfonos, migraciones, kit).
5. **Nombre:** Agorá.
6. **Simplificación y eficiencia:** menos código con el mismo comportamiento; índices medidos (reactivación 290 → 2 ms,
   bandeja 46 → 4 ms); importaciones en una transacción.
7. **Integraciones (6 fases):** auditoría de WhatsApp y Google contra la documentación; correo saliente; calendario de
   Microsoft 365; correo como canal de clientes; pagos de Stripe y Mercado Pago; reseñas de Google (se quedan
   manuales). La revisión automática de seguridad encontró problemas en correo y pagos; todos corregidos con prueba.
8. **Fichas, cotizaciones y cotejo (3 fases):** fichas con notas y citas; cotizaciones con envío, aceptación,
   impresión y vencimiento; saldo y cotejo del mes. Se corrigió que la config compartía sus valores por omisión entre
   instancias y que la bandeja descartaba campos vacíos.
9. **Simulación con la ficha:** flujo completo de una clienta hasta saldo $0; corrigió la fecha de la cotización impresa.

## 7. Decisiones que más importan (todas en DECISIONES.md)

- La IA solo usa datos del config; nunca da indicaciones médicas; si no sabe, pasa a una persona. Los montos que
  puede decir son los del config y los de las cotizaciones abiertas de ese mismo cliente.
- Nunca confirma sola: aceptar una cotización, cancelar con duda o un correo sin DMARC → lo atiende una persona.
- Proactivos solo de 9:00 a 20:00 (Monterrey), nunca domingo ni días cerrados; bajas globales respetadas siempre.
- Atribución y garantía según `anexo-contrato.md`; un cliente dado de alta a mano no cuenta como "respuesta rápida".
- Una venta se liga a una cotización solo si no hay duda (una sola aceptada con saldo); lo demás aparece en el cotejo.
- Microsoft 365 por Graph con permisos de aplicación; Outlook.com/Hotmail personales no se soportan como remitente.

## 8. Pendiente (lo que necesita al dueño o una decisión)

1. **Prueba real** con cuentas (lista exacta en `arranque-cuentas.md`): Meta (app, número, **10 plantillas aprobadas**,
   token permanente), Anthropic (clave con saldo), Google (cuenta de servicio y calendario) o Microsoft 365, correo
   (Gmail con contraseña de aplicación o Microsoft 365), Stripe/Mercado Pago, ngrok. Después, fusionar el PR.
2. **Someter a Meta** la plantilla nueva `cotizacion` (sin ella, una cotización solo sale con la ventana abierta o por
   correo).
3. **Revisión legal** del anexo del contrato.
4. **Reseñas de Google:** pedir acceso a la API de Business Profile (~14 días) o decidir si se paga Places API.
5. **Límites conocidos:** Clip solo por CSV; reembolsos se corrigen a mano; WhatsApp y correo de la misma persona son
   dos contactos; un VPS es punto único de falla.
6. **Red de desarrollo:** la sesión de desarrollo no podía abrir los sitios de documentación oficial (Meta, Microsoft,
   Stripe, Mercado Pago); se verificó por búsqueda web. Para leerlos directo, permitir esos dominios en el entorno.

## 9. Reglas para quien siga

- Leer `AGENTS.md`, `docs/DISEÑO.md` y `docs/DECISIONES.md` antes de cambiar algo.
- Solo biblioteca estándar; nada de dependencias sin preguntar. Nunca editar una sección publicada del esquema.
- Todo lo nuevo respeta `modo_prueba` y tiene una prueba que falla si la lógica se rompe; correr la suite completa y
  revisar el código de salida real antes de cada commit.
- Preguntar antes de: dependencias, cuentas, costos, mensajes reales, borrar datos, o cambiar atribución/garantía.
- Textos para usuarios, documentos y decisiones en español de México.
