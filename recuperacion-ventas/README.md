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

## Pruebas

```bash
cd recuperacion-ventas && python3 -m unittest discover -s tests -v
```
