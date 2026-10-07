# Runbook de operación

Todas las órdenes: `cd /srv/rv/app/recuperacion-ventas && python3 -m rv --cliente /srv/rv/clientes/<id> <comando>`
(abreviado aquí como `rv <comando>`). Corre como el usuario `rv` (`sudo -u rv ...`).

## Salud diaria
- `rv verificar` (local) y `rv verificar --remoto` (Meta, Anthropic, Google; solo lectura). Código de salida 1 = hay ERROR.
- `journalctl -u rv@<id> -n 100` (servidor) y `journalctl -u rv-tick@<id> -n 50` (envíos).
- `systemctl list-timers 'rv-*'`: el tick debe correr cada 5 min y el respaldo cada noche.

## Primer servidor (una vez)
1. Ubuntu LTS; `apt install python3 tzdata caddy rclone git`.
2. `adduser --system --group --home /home/rv rv`; `mkdir -p /srv/rv/clientes /etc/rv`; `chown -R rv:rv /srv/rv`.
3. `git clone <repo> /srv/rv/app` (como `rv`).
4. `cp /srv/rv/app/recuperacion-ventas/deploy/*.service /srv/rv/app/recuperacion-ventas/deploy/*.timer /etc/systemd/system/ && systemctl daemon-reload`.
5. `cp deploy/Caddyfile /etc/caddy/Caddyfile` (ajustar dominio y clientes) y `systemctl reload caddy`.
6. `sudo -u rv rclone config` (remoto externo, p. ej. Backblaze B2 o S3) y `/etc/rv/respaldo.env` con `RV_RCLONE_DESTINO=remoto:bucket/rv`.
7. Meta: en tu app, callback de webhook = `https://rv.tudominio.mx/c/<primer-cliente>/webhook` con el `WA_VERIFY_TOKEN` de ese cliente, campo `messages` suscrito. Cada número nuevo se dirige a su cliente con `rv configurar-webhook`.

## Cliente nuevo
Seguir `docs/checklist-instalacion.md`.

## Actualizar el código (todas las instancias)
```bash
sudo -u rv git -C /srv/rv/app pull
cd /srv/rv/app/recuperacion-ventas && sudo -u rv python3 -m unittest discover -s tests
systemctl restart 'rv@*'          # las migraciones de base corren al arrancar, con respaldo previo automático
for c in /srv/rv/clientes/*/; do sudo -u rv python3 -m rv --cliente "$c" verificar; done
```

## Respaldo y restauración
- Automático cada noche: `respaldos/datos-AAAAMMDD-HHMMSS.db.gz` local (30 días) y copia en `RV_RCLONE_DESTINO/<id>` (30 días).
- Manual: `rv respaldo`.
- Restaurar:
  ```bash
  systemctl stop rv@<id> rv-tick@<id>.timer
  cd /srv/rv/clientes/<id>
  mv datos.db datos.db.danado; rm -f datos.db-wal datos.db-shm
  gunzip -c respaldos/datos-AAAAMMDD-HHMMSS.db.gz > datos.db   # o: rclone copy remoto:... .
  chown rv:rv datos.db
  systemctl start rv@<id> rv-tick@<id>.timer
  rv verificar
  ```
- Mensajes que llegaron entre el respaldo y la falla: Meta reintenta hasta 7 días los que no recibieron 200; los que sí se recibieron después del respaldo se pierden de la base (no del WhatsApp del cliente).
- Prueba mensual: restaurar el último respaldo en una carpeta temporal y correr `rv --cliente <tmp> verificar`.

## Problemas comunes
| Síntoma | Revisión |
|---|---|
| No llegan mensajes | `rv verificar --remoto` (override del número); `journalctl -u caddy`; el log de `rv@` muestra `POST /webhook ... 401` si `WA_APP_SECRET` no corresponde a la app. |
| "item de otro número ignorado" en el log del primer cliente | Un número sin override: `rv configurar-webhook` en el cliente de ese número. |
| Envíos con estado `failed` 131049 | Meta limitó mensajes de marketing a ese usuario; no reintentar. |
| Envíos `error` con "Graph 401" | Token de Meta vencido o revocado: renovar `WA_TOKEN` y `systemctl restart rv@<id>`. |
| Todas las conversaciones pasan a humano con "la IA no pudo responder" | Clave de Anthropic, red, o modelo inválido: `rv verificar --remoto`. |
| "se alcanzó el tope mensual de IA" | Subir `ia.tope_mensual_usd` en `cliente.json` (acordarlo con el dueño) y reiniciar `rv@<id>`. |
| La IA bloquea respuestas con montos | El monto no está en `servicios`/`faq`: agregarlo al config si es real. |
| Cita cancelada en Google y el cliente recibió recordatorio | Las cancelaciones deben hacerse en la bandeja (Citas → Cancelar); el sistema no lee cancelaciones hechas directo en Google. |
| Calidad del número en rojo (aviso al dueño) | La reactivación se pausa sola. Revisar plantillas/frecuencia; esperar a que vuelva a verde. |
| Bandeja dice "origen no permitido" | Abrir la bandeja exactamente en `url_publica` (mismo esquema, dominio y puerto). |
