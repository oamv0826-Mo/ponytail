# Checklist de instalación (3 semanas por cliente)

Marca cada casilla. Lo que dice **[dueño]** lo aprueba o lo entrega el dueño del negocio.

## Semana 0: auditoría (antes de vender)
- [ ] Escribir al WhatsApp del negocio en horario y fuera de horario; anotar cuánto tarda en contestar (capturas).
- [ ] Pedir precio y no volver a escribir: ¿alguien da seguimiento? (capturas).
- [ ] Comparar con 2 competidores del mismo giro.
- [ ] Registrar todo en `negocios.csv` y generar el reporte de una página: `python3 -m rv auditoria --nicho nichos/<giro>.json <carpeta>`
      (calcula el puntaje de 100 y la venta en riesgo con los datos del dueño). Presentar con capturas.
- [ ] Demo de 3 minutos en la reunión: `python3 -m rv demo-ventas --nicho nichos/<giro>.json` (guion en `docs/demo-ventas.md`).

## Semana 1: cuentas y datos
Paso a paso de cada cuenta, con costo y tiempo: [arranque-cuentas.md](arranque-cuentas.md).

- [ ] **[dueño]** Firma del contrato y del anexo (`docs/anexo-contrato.md`), con fecha de inicio y mensualidad.
- [ ] **[dueño]** Número para WhatsApp Business Platform (Cloud API). Si el número ya usa la app de WhatsApp Business, decidir: número nuevo o coexistencia.
- [ ] Alta del número en tu app de Meta (Embedded Signup o WhatsApp Manager); anotar `phone_number_id` y `waba_id`.
- [ ] Método de pago en la WABA (los mensajes de plantilla tienen costo en Meta).
- [ ] Someter las 9 plantillas de `docs/plantillas.md` (idioma `es_MX`); se aprueban en horas o días.
- [ ] **[dueño]** Servicios con precio y duración, horario, días cerrados, dirección, preguntas frecuentes → `cliente.json`.
- [ ] **[dueño]** Aprueba cada respuesta del FAQ y los textos de `mensajes` (revisión en persona).
- [ ] **[dueño]** Teléfonos del dueño y del equipo (`dueno`, `equipo[]`): reciben avisos y nunca reciben campañas.
- [ ] **[dueño]** Link de reseña de Google (Perfil de Empresa → "Pedir reseñas") → `resenas.link`.
- [ ] Perfil de Google: horario, fotos, teléfono/WhatsApp y enlace a la página (`pagina`). Es manual, no lo hace el sistema.
- [ ] **[dueño]** Calendario: crear un calendario de Google para las citas y compartirlo con el correo de la cuenta de servicio con permiso "Hacer cambios en eventos" → `agenda.calendar_id`.
- [ ] **[dueño]** Base de clientes antiguos en CSV (`nombre,telefono,ultima_visita,consentimiento`), solo marcando "sí" a quien aceptó recibir mensajes.

## Semana 2: instalación técnica
- [ ] Carpeta `/srv/rv/clientes/<id>/` con `cliente.json` (puerto libre, `url_publica` = `https://rv.tudominio.mx/c/<id>`, `modo_prueba: true`).
- [ ] `secretos.env` (permisos 600, dueño `rv`): `WA_TOKEN` (token de sistema de larga duración), `WA_APP_SECRET`, `WA_VERIFY_TOKEN` (aleatorio, p. ej. `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`), `ANTHROPIC_API_KEY`, `GOOGLE_SA_FILE`.
- [ ] Ruta del cliente en el `Caddyfile` y `systemctl reload caddy`.
- [ ] `python3 -m rv --cliente /srv/rv/clientes/<id> usuario <nombre>` para cada persona del equipo.
- [ ] `systemctl enable --now rv@<id> rv-tick@<id>.timer rv-respaldo@<id>.timer`.
- [ ] Demo en modo prueba con el dueño: `simular --interactivo` (precio, FAQ, cita, cancelar, urgencia, "hablar con una persona") y la bandeja.
- [ ] `importar-clientes clientes.csv` y revisar el resumen (rechazados por línea).
- [ ] `pagina` y probar el botón de WhatsApp en un celular.

## Semana 2–3: salida a producción
- [ ] Plantillas aprobadas: `verificar --remoto` sin ERROR en plantillas.
- [ ] `modo_prueba: false` y `systemctl restart rv@<id>`.
- [ ] `configurar-webhook` (override del número → `url_publica/webhook`); `verificar --remoto` debe marcar OK el override.
- [ ] Prueba real desde tu celular: precio, cita (aparece en Google Calendar), urgencia, handoff (llega el aviso al equipo).
- [ ] Primera campaña de reactivación (sale sola en la ventana 9:00–20:00, 50 por día).
- [ ] Capacitación de 30 min al equipo: bandeja, Tomar/Responder/Devolver, Citas → Asistió → Registrar venta.

## Mensual
- [ ] Revisar motivos de handoff del reporte y mejorar FAQ/mensajes.
- [ ] `reporte AAAA-MM --resenas-google N` y reunión con el dueño (5 números + garantía en el segundo mes).
- [ ] Prueba de restauración de un respaldo (ver runbook).
