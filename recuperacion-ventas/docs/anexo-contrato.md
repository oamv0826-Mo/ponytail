# Anexo: medición de ventas recuperadas y garantía

> Borrador listo para anexar al contrato de servicio. Recomendación: que lo revise un abogado antes de
> firmarlo. Los términos entre corchetes se llenan por cliente.

**Partes:** [NOMBRE DEL PRESTADOR] ("el Prestador") y [NOMBRE DEL NEGOCIO] ("el Negocio").
**Fecha de inicio del servicio:** [AAAA-MM-DD] ("Fecha de Inicio").
**Mensualidad:** $[MONTO] MXN más IVA ("la Mensualidad").

## 1. Qué es una venta

1.1. Una venta es un pago recibido por el Negocio de un cliente, por un monto en pesos mexicanos, en una
fecha determinada.

1.2. Las ventas se registran en el sistema de una de dos formas: (a) el personal del Negocio la marca en la
bandeja del sistema, con su monto, o (b) el Negocio entrega un archivo con teléfono del cliente, fecha y monto.

1.3. Solo cuentan las ventas registradas. Si el Negocio no registra una venta, el sistema no puede medirla.
El Negocio se compromete a registrar sus ventas al menos una vez por semana.

1.4. Una misma venta no se cuenta dos veces: el sistema descarta el registro repetido de un mismo cliente,
fecha y monto.

## 2. Cuándo una venta cuenta como "recuperada"

Al registrarse, el sistema asigna a cada venta un **origen**, revisando las reglas en este orden y usando
la primera que se cumpla. "Antes de la venta" incluye todo el día de la venta, en hora de Monterrey.

| Orden | Origen | Se cumple cuando… | ¿Es recuperada? |
|---|---|---|---|
| 1 | Reactivación | El cliente recibió un mensaje de la campaña de reactivación en los 60 días previos a la venta, **y** respondió por WhatsApp después de recibirlo y antes de la venta. | Sí |
| 2 | Seguimiento | El cliente recibió un mensaje de seguimiento (día 2, 5 o 10 después de pedir precio o información) en los 60 días previos a la venta, **y** después de recibirlo respondió por WhatsApp o agendó una cita, antes de la venta. | Sí |
| 3 | Fuera de horario | La primera vez que el cliente escribió al Negocio por el sistema fue con el Negocio cerrado según su horario registrado, y la venta ocurrió dentro de los 60 días siguientes. | Sí |
| 4 | Respuesta rápida | Cualquier otra venta de un cliente que llegó por el sistema. | No (se informa, no cuenta) |
| 5 | Sin atribución | El resto (por ejemplo, un cliente antiguo que compró sin haber respondido a ninguna campaña). | No |

**Ventas recuperadas** = suma de los montos con origen Reactivación, Seguimiento o Fuera de horario.

## 3. Garantía

3.1. **Periodo de garantía:** los 60 días naturales que empiezan en la Fecha de Inicio (la Fecha de Inicio
cuenta como el día 1).

3.2. Al terminar el periodo, si la suma de las ventas recuperadas cuyas fechas caen dentro del periodo es
**menor** que una Mensualidad, el Prestador no cobrará la Mensualidad del mes siguiente.

3.3. Si la suma es igual o mayor que una Mensualidad, la garantía se considera cumplida.

3.4. La garantía aplica una sola vez por contrato y no es acumulable ni canjeable por dinero.

3.5. La garantía no aplica si, durante el periodo: (a) el Negocio no registró sus ventas conforme al punto
1.3; (b) el Negocio desactivó el sistema, cambió el número de WhatsApp sin aviso, o Meta suspendió el número
por causas atribuibles al Negocio; o (c) el Negocio no entregó la base de clientes antiguos con
consentimiento para la campaña de reactivación dentro de los primeros 14 días.

## 4. Reporte

4.1. Cada mes el Prestador entrega un reporte con: consultas recibidas, tiempo de respuesta, citas
agendadas, ventas recuperadas (con su origen) y reseñas solicitadas. El reporte del segundo mes incluye el
resultado de la garantía.

4.2. El Negocio puede pedir el detalle de cualquier venta y la regla con la que se le asignó su origen.
Si el Negocio demuestra que un registro tiene un error de captura (monto, fecha o cliente), se corrige y se
vuelve a calcular.

## 5. Privacidad

5.1. El sistema guarda nombre, teléfono, mensajes, citas y ventas de los clientes del Negocio únicamente
para prestar este servicio. El Negocio es el responsable de esos datos ante sus clientes; el Prestador
actúa como encargado y no los usa para nada más ni los comparte con terceros, salvo con los proveedores
técnicos necesarios (Meta/WhatsApp, Anthropic para la IA, Google Calendar y el servidor).

5.2. Los mensajes de marketing (seguimiento, reactivación, reseñas) solo se envían a quien no ha pedido la
baja; la campaña de reactivación solo a quien el Negocio indicó que dio su consentimiento. Cualquier cliente
puede darse de baja respondiendo "BAJA".
