# Sistema de Recuperación de Ventas

WhatsApp con IA, citas, seguimiento, reactivación, reseñas y reporte mensual para negocios de servicio.
Python 3.11+ sin dependencias. Diseño: [docs/DISEÑO.md](docs/DISEÑO.md) · Decisiones: [docs/DECISIONES.md](docs/DECISIONES.md).

## Demo sin cuentas (modo prueba)

```bash
cd recuperacion-ventas
mkdir -p /tmp/demo && cp ejemplo/cliente.json /tmp/demo/

# conversación por consola (ningún mensaje sale a Meta; todo queda en /tmp/demo/envios-prueba.log)
python3 -m rv --cliente /tmp/demo simular --interactivo

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
| `serve` | webhook de Meta + bandeja web (servicio `rv@<id>`) |
| `tick` | envíos programados (timer cada 5 min) |
| `simular` | mensaje falso de WhatsApp (solo modo prueba) |
| `usuario <nombre>` | crea o cambia la contraseña de la bandeja |
| `importar-clientes clientes.csv` | base para reactivación (`nombre,telefono,ultima_visita,consentimiento`) |
| `importar-ventas ventas.csv` | ventas (`telefono,fecha,monto`) |
| `reporte [AAAA-MM] [--resenas-google N]` | reporte mensual con garantía |
| `pagina [--salida archivo]` | página estática con botón a WhatsApp |
| `verificar [--remoto]` | chequeos de instalación y salud |
| `respaldo` | copia comprimida de la base |
| `configurar-webhook` | dirige el webhook del número a este cliente (instalación) |

Operación: [docs/runbook.md](docs/runbook.md) · Instalación: [docs/checklist-instalacion.md](docs/checklist-instalacion.md) ·
Plantillas para Meta: [docs/plantillas.md](docs/plantillas.md) · Anexo de contrato: [docs/anexo-contrato.md](docs/anexo-contrato.md).

## Pruebas

```bash
cd recuperacion-ventas && python3 -m unittest discover -s tests -v
```
