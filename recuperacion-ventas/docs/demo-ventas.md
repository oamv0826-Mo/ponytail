# Demo de ventas: guion de 10 minutos

Para la reunión de 20 minutos con un dueño, después de la auditoría. La demo corre en tu laptop, sin internet y
sin ninguna cuenta. Todo es simulado y no sale ningún mensaje.

## Antes de la reunión (5 minutos)

1. Elige el archivo del giro en `nichos/`. Si no existe, copia `nichos/_plantilla.json` como
   `nichos/<giro>.json` y llénalo con servicios y precios reales o creíbles del nicho.
2. Ensaya una vez sin pausas: `python3 -m rv demo-ventas --nicho nichos/<giro>.json --rapido`
3. Ten a la mano el reporte de auditoría de ese negocio (impreso o en PDF).
4. Letra grande en la Terminal (⌘ +) y la pantalla en modo "No molestar".

## Comando de la reunión

```bash
python3 -m rv demo-ventas --nicho nichos/clinica-estetica.json
```

Tarda unos 3 minutos y se detiene sola entre escenas para que puedas hablar. Al final imprime cómo abrir la bandeja
con un usuario de demo.

## Guion

### 0:00 a 1:30: abrir con su auditoría

- "En la auditoría su negocio obtuvo X de 100. El promedio del grupo fue Y."
- Lee el hallazgo más fuerte, con la hora: "Le escribimos un martes a las 11:05 y la respuesta llegó casi 4 horas
  después."
- "Le voy a enseñar en 3 minutos cómo se ve esto resuelto, con sus mismos servicios."

### 1:30 a 5:00: la demo corre

Habla sobre lo que va apareciendo, una idea por escena:

1. **Mensaje de noche (11:05 p. m.).** "Esta clienta escribió con el negocio cerrado. Tuvo precio en segundos y salió
   con cita. Hoy ese mensaje lo verían mañana, cuando ya le contestó otra clínica."
2. **Pide precio y ya no contesta.** "Aquí se pierde la mayoría: preguntan, comparan y desaparecen."
3. **Seguimiento del día 2.** "Dos días después el sistema le vuelve a escribir, con un mensaje aprobado por
   WhatsApp, y él agenda. Se manda solo, de 9 a 8 y nunca en domingo."
4. **Pasa a una persona.** "La IA no contesta lo delicado: quejas, dudas médicas, urgencias. Avisa a su equipo por
   WhatsApp y la conversación sigue en la bandeja." Si preguntan por salud: "Nunca da indicaciones médicas; ante una
   urgencia manda el 911 y avisa a la doctora."
5. **Asistieron y se registra la venta.** "Su recepción marca 'Asistió' y anota cuánto pagó. Así sabemos qué venta
   vino del sistema y cuál no."
6. **Reporte con la garantía.** "Cada mes recibe 5 números. Solo cuentan como recuperadas tres cosas: lo que entró
   fuera de horario, lo que vino del seguimiento y lo que vino de la reactivación. Si en 60 días no recuperamos al
   menos lo que paga de mensualidad, el siguiente mes no se cobra."

### 5:00 a 6:30: la bandeja (opcional)

Si hay interés en cómo trabajaría la recepción, corre el comando que imprimió la demo (`serve`) y abre
`http://127.0.0.1:8080/bandeja` con el usuario `demo`. Enseña la conversación de Sofía, el botón **Tomar** y la
pestaña **Citas**.

### 6:30 a 8:30: lo que cuesta y lo que gana

- Su número de ventas en riesgo, de la auditoría: "Con sus 60 consultas y su ticket de $5,000, son hasta $60,000 al
  mes en riesgo."
- El paquete que le conviene y el precio de fundador, si aplica.
- "No le pido que cambie su forma de trabajar: su equipo contesta desde la bandeja en lugar de la app."

### 8:30 a 10:00: preguntas y siguiente paso

Preguntas que suelen salir:

- **"¿Y si la IA dice algo mal?"** Solo usa los datos que usted aprueba: servicios, precios, horario y preguntas
  frecuentes. Si un precio no está en su lista, el mensaje no sale y pasa a una persona.
- **"¿Me pueden bloquear el WhatsApp?"** Usamos la API oficial de Meta, solo escribimos a quien dio consentimiento y
  en tandas pequeñas. Quien responde BAJA no vuelve a recibir mensajes.
- **"¿Qué pasa con mi número actual?"** Se registra en la plataforma oficial de WhatsApp. Su equipo deja de contestar
  desde la app normal y usa la bandeja.

Cierre: "Si le hace sentido, el siguiente paso es una reunión de 30 minutos con la propuesta y la fecha de arranque."

## Crear un nicho nuevo

1. `cp nichos/_plantilla.json nichos/<giro>.json` y llénalo.
2. En `demo`, pon los `id` de dos servicios: uno para la escena de noche y otro para la de seguimiento.
3. En `palabras_urgencia`, las frases que deben pasar a una persona de inmediato: en minúsculas y sin acentos.
4. En `auditoria`, el mensaje de prueba que mandarás a los 20 negocios y la fracción de consultas que se pierden
   (0.2 = 1 de cada 5).
5. Ensaya con `--rapido`. Si en alguna escena la IA simulada no reconoce el servicio, usa un nombre de servicio con
   palabras más distintivas.
