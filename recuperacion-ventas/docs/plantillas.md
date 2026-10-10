# Plantillas de WhatsApp para someter a Meta

Idioma: `es_MX`. Los nombres deben quedar **exactamente** así en el WhatsApp Manager; el código los usa
por nombre. Reglas de Meta respetadas: ninguna plantilla empieza ni termina con una variable, no hay
variables seguidas, y las de marketing incluyen cómo darse de baja.

Fuera de la ventana de 24 h (después del último mensaje del cliente) **solo** se pueden enviar estas
plantillas. Meta puede reclasificar la categoría al revisarlas; si cambia una de utilidad a marketing,
no hay que tocar código.

| Nombre | Categoría sugerida | Variables | Cuándo se usa |
|---|---|---|---|
| `aviso_equipo` | Utilidad | {{1}} negocio, {{2}} contacto, {{3}} motivo, {{4}} link a la bandeja | Handoff, escalamiento, tope de IA, calidad del número (al equipo/dueño) |
| `retomar_contacto` | Utilidad | {{1}} nombre, {{2}} negocio | Bandeja, con la ventana de 24 h cerrada |
| `cita_confirmada` | Utilidad | {{1}} nombre, {{2}} servicio, {{3}} negocio, {{4}} fecha y hora | Cita agendada desde la bandeja con la ventana cerrada |
| `recordatorio_cita` | Utilidad | {{1}} nombre, {{2}} servicio, {{3}} fecha y hora, {{4}} dirección | 24 h y 2 h antes de la cita |
| `seguimiento_1` | Marketing | {{1}} nombre, {{2}} servicio de interés | Día 2 |
| `seguimiento_2` | Marketing | {{1}} nombre, {{2}} negocio, {{3}} servicio de interés | Día 5 |
| `seguimiento_3` | Marketing | {{1}} nombre, {{2}} servicio de interés | Día 10 |
| `reactivacion` | Marketing | {{1}} nombre, {{2}} negocio | Campaña a clientes antiguos con consentimiento |
| `resena` | Marketing | {{1}} nombre, {{2}} negocio, {{3}} link de reseña de Google | 2 h después de "Asistió" |
| `cotizacion` | Utilidad | {{1}} nombre, {{2}} negocio, {{3}} folio, {{4}} total, {{5}} vigencia | Cotización enviada desde la bandeja con la ventana cerrada y sin correo |

Meta no acepta variables vacías: si el contacto no tiene nombre (raro: WhatsApp manda el nombre de
perfil y el CSV de importación trae nombre), el código envía `cliente` como respaldo.

## Textos

### aviso_equipo (Utilidad)
```
Aviso del sistema de {{1}}: la conversación con {{2}} necesita atención. Motivo: {{3}}. Ábrela en la bandeja: {{4}} . Gracias.
```

### retomar_contacto (Utilidad)
```
Hola {{1}}, te escribe el equipo de {{2}} para dar seguimiento a tu consulta. Responde a este mensaje para continuar la conversación.
```

### cita_confirmada (Utilidad)
```
Hola {{1}}, tu cita de {{2}} en {{3}} quedó confirmada para el {{4}}. Si necesitas cambiarla, responde a este mensaje.
```

### recordatorio_cita (Utilidad)
```
Hola {{1}}, te recordamos tu cita de {{2}} el {{3}} en {{4}}. Si necesitas cambiarla, responde a este mensaje.
```

### seguimiento_1 (Marketing)
```
Hola {{1}}, ¿pudiste revisar la información sobre {{2}}? Si quieres, te ayudo a agendar tu cita; solo responde a este mensaje. Si no deseas recibir más mensajes, responde BAJA.
```

### seguimiento_2 (Marketing)
```
Hola {{1}}, en {{2}} todavía tenemos horarios disponibles para {{3}}. ¿Te apartamos uno? Responde a este mensaje. Si no deseas recibir más mensajes, responde BAJA.
```

### seguimiento_3 (Marketing)
```
Hola {{1}}, este es nuestro último mensaje sobre {{2}}. Si más adelante quieres agendar, escríbenos aquí y con gusto te atendemos. Si no deseas recibir más mensajes, responde BAJA.
```

### reactivacion (Marketing)
```
Hola {{1}}, en {{2}} queremos saber cómo estás. Ya es buen momento para tu siguiente visita. ¿Te ayudamos a agendar? Responde a este mensaje. Si no deseas recibir más mensajes, responde BAJA.
```

### resena (Marketing)
```
Gracias por visitarnos, {{1}}. Tu opinión ayuda a más personas a conocer {{2}}. ¿Nos dejas una reseña en Google? {{3}} Si no deseas recibir más mensajes, responde BAJA.
```

## Mensajes libres (no se someten a Meta)

Se envían dentro de la ventana de 24 h, porque el cliente acaba de escribir. Viven en `rv/base.py`
(`MENSAJES`) y cada cliente puede sobrescribirlos en `cliente.json` → `mensajes`.

| Clave | Texto por defecto |
|---|---|
| `emergencia` | Si es una emergencia médica, llama al 911 o acude de inmediato a urgencias del hospital más cercano. |
| `handoff_abierto` | Gracias por escribir. En unos minutos te atiende una persona del equipo. |
| `handoff_cerrado` | Gracias por escribir. Te contacta una persona del equipo {apertura}. ({apertura} = próxima apertura real, p. ej. "el lunes 13 de octubre a las 9:00") |
| `no_texto` | Por ahora solo puedo leer mensajes de texto. (seguido del mensaje de handoff) |
| `baja` | Listo, ya no te enviaremos mensajes. Si nos escribes, con gusto te atendemos. |
| `propuesta` | Para {servicio} tengo estos horarios:\n{opciones}\nResponde con el número que prefieras. |
| `sin_horarios` | Por ahora no tengo horarios disponibles en línea. (seguido del mensaje de handoff) |
| `horario_ocupado` | Ese horario ya no está disponible. |
| `cita_confirmada` | Listo, tu cita de {servicio} quedó para el {fecha}. Te enviaremos un recordatorio. |
| `confirmar_cancelacion` | ¿Confirmas que cancelamos tu cita de {servicio} del {fecha}? Responde SÍ para cancelar. |
| `cita_cancelada` | Tu cita del {fecha} quedó cancelada. Si quieres otro horario, dime y te propongo opciones. |

### cotizacion (Utilidad)
```
Hola {{1}}, te compartimos de {{2}} la cotización {{3}} por un total de {{4}}, vigente hasta el {{5}}. Responde a este mensaje para recibir el detalle o agendar tu cita.
```
