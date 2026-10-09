# Agorá

Sistema de recuperación de ventas.

WhatsApp (y correo) con IA, citas en Google Calendar o Microsoft 365, seguimiento, reactivación, reseñas, ventas
registradas solas desde Stripe/Mercado Pago y reporte mensual para negocios de servicio.
Python 3.11+ sin dependencias. Resumen para retomar el proyecto: [docs/HANDOFF.md](docs/HANDOFF.md) · Diseño: [docs/DISEÑO.md](docs/DISEÑO.md) · Decisiones: [docs/DECISIONES.md](docs/DECISIONES.md).

## Demo sin cuentas (modo prueba)

```bash
cd recuperacion-ventas
mkdir -p /tmp/demo && cp ejemplo/cliente.json /tmp/demo/

# conversación por consola (ningún mensaje sale a Meta; todo queda en /tmp/demo/envios-prueba.log)
python3 -m rv --cliente /tmp/demo simular --interactivo

# historia completa de una clienta (noche → seguimiento → cita → recordatorio → humano → venta → reseña → reporte)
python3 ejemplo/historia.py

# bandeja web
python3 -m rv --cliente /tmp/demo usuario ana          # pide contraseña (mín. 10)
python3 -m rv --cliente /tmp/demo serve                # http://127.0.0.1:8080/bandeja
python3 -m rv --cliente /tmp/demo simular --url http://127.0.0.1:8080 --texto "quiero hablar con una persona"
```

Abre la bandeja exactamente en la `url_publica` del config (`http://127.0.0.1:8080`): los POST de otro
origen se rechazan.

## Comandos

`python3 -m rv --cliente <carpeta> <comando>`

| Comando | Para qué |
|---|---|
| `serve` | webhook de Meta + bandeja web (servicio `rv@<id>`): conversaciones, citas, fichas de clientes (`/bandeja/clientes`) y cotizaciones |
| `tick` | envíos programados (timer cada 5 min) |
| `simular` | mensaje falso de WhatsApp, o de correo con `--de cliente@correo.mx --asunto ...` (solo modo prueba) |
| `usuario <nombre>` | crea o cambia la contraseña de la bandeja |
| `importar-clientes clientes.csv` | base para reactivación (`nombre,telefono,ultima_visita,consentimiento`) |
| `importar-ventas ventas.csv` | ventas (`telefono,fecha,monto`) |
| `reporte [AAAA-MM] [--resenas-google N] [--enviar]` | reporte mensual con garantía; `--enviar` lo manda por correo |
| `cotejo [AAAA-MM]` | cotización → cita → venta: lo que no cuadra en el mes (también en `/bandeja/cotejo`) |
| `pagina [--salida archivo]` | página estática con botón a WhatsApp |
| `verificar [--remoto]` | chequeos de instalación y salud |
| `respaldo` | copia comprimida de la base |
| `configurar-webhook` | dirige el webhook del número a este cliente (instalación) |
| `prueba-real --tel <tu número> [paso]` | prueba guiada con tu teléfono contra Meta, Anthropic y Google ([docs/prueba-real.md](docs/prueba-real.md)) |

Sin `--cliente` (herramientas de venta):

| Comando | Para qué |
|---|---|
| `auditoria --nicho nichos/<giro>.json <carpeta>` | la primera vez crea `negocios.csv`; después genera un reporte HTML por negocio y `resumen.html` |
| `demo-ventas --nicho nichos/<giro>.json [--rapido]` | demo de ~3 minutos para dueños, sin cuentas ([docs/demo-ventas.md](docs/demo-ventas.md)) |

## Kit de venta

- **Nichos:** `nichos/<giro>.json` describe un giro (servicios, precios, horario, preguntas frecuentes, palabras de
  urgencia, mensaje de auditoría). `clinica-estetica.json` es el primero; `_plantilla.json` se copia para otro giro.
- **Auditoría de fugas:** una fila por negocio en `negocios.csv` (se abre en Excel o Numbers). Fechas como
  `2026-10-13 11:00`; respuesta vacía = no respondió; envío vacío = esa prueba no se hizo (el puntaje se calcula sobre
  lo probado); sí/no en `dio_precio`, `ofrecio_agendar`, `seguimiento_2d`, `seguimiento_5d`, `contesta_resenas`,
  `boton_whatsapp`, `horario_visible`. `consultas_mes` y `ticket_promedio` los da el dueño: con ellos el reporte
  estima la venta en riesgo. Cada reporte muestra solo el promedio y el mejor del grupo, sin nombres.
- **Cuentas:** [docs/arranque-cuentas.md](docs/arranque-cuentas.md), en orden, con costo y tiempo de cada una.

Operación: [docs/runbook.md](docs/runbook.md) · Cuentas: [docs/arranque-cuentas.md](docs/arranque-cuentas.md) · Instalación: [docs/checklist-instalacion.md](docs/checklist-instalacion.md) ·
Plantillas para Meta: [docs/plantillas.md](docs/plantillas.md) · Anexo de contrato: [docs/anexo-contrato.md](docs/anexo-contrato.md).

## Pruebas

```bash
cd recuperacion-ventas && python3 -m unittest discover -s tests -v
```
