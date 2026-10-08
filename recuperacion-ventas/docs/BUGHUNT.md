# Ciclo autónomo de caza de errores

Estado: rondas = 2 · rondas seguidas sin hallazgos = 0 · se detiene con 2 limpias seguidas o al llegar a 8.

Orden de áreas: 1 motor/guardrails IA · 2 agenda/Google · 3 tick/ventanas · 4 ventas/atribución/reporte ·
5 bandeja/seguridad · 6 CSV/teléfonos · 7 migraciones/respaldo/caídas · 8 kit (auditoria, demo, prueba) ·
después: simulaciones adversariales de punta a punta.

Pruebas de cada hallazgo: `tests/test_bughunt.py`.

## Ronda 1 · motor y guardrails de la IA
| # | Hallazgo (reproducido) | Corrección | Prueba |
|---|---|---|---|
| 1 | "quiero darme de baja", "ya no me manden mensajes", "no me envíen más promociones" no registraban la baja (solo palabras exactas): se seguiría mandando marketing a quien lo pidió. | Lista `palabras.baja_frases` (frases inequívocas, coincidencia dentro del mensaje) además de las palabras exactas. | `test_frases_naturales_de_baja_registran_opt_out` |
| 2 | Una foto con texto se guardaba como `[image]`: el texto del cliente se perdía. | `wa.no_texto` conserva el texto de foto/documento, el nombre de archivo y los datos de ubicación. | `test_foto_conserva_su_texto_y_el_id_del_archivo` |
| 3 | El equipo no podía ver fotos ni escuchar notas de voz (con la API de Meta no aparecen en la app de WhatsApp). | Esquema v3 (`mensaje.media_id`, `media_mime`); la bandeja muestra reproductor/imagen y `/bandeja/media/<id>` descarga de Meta bajo sesión; tipos no seguros (SVG, HTML) se fuerzan como descarga. | `test_nota_de_voz_se_puede_escuchar_en_la_bandeja`, `test_media_requiere_sesion_y_svg_se_descarga_no_se_ejecuta` |
| 4 | Una reacción 👍 se trataba como "mensaje que no es texto": pasaba a humano y mandaba avisos al equipo. | Las reacciones se guardan y no se contestan. | `test_reaccion_no_pasa_a_humano_ni_avisa` |

Observación sin cambio: las palabras de urgencia son por frase exacta ("sangran" no coincide con "sangra"); se ajustan por giro en `palabras.urgencia_medica` del config y la IA real pasa a humano lo médico de todos modos.

## Ronda 2 · agenda y Google Calendar
| # | Hallazgo (reproducido) | Corrección | Prueba |
|---|---|---|---|
| 1 | Respuestas naturales a la propuesta de horarios no agendaban: "la opción 2", "opción 2 por favor", "la 2 porfa", "el segundo", "a las 4" (5 de 6 casos reales terminaban en humano; con la IA real, en una nueva propuesta). | `agenda.elegir_opcion`: número suelto en mensaje corto, ordinales, "la última", hora del horario ofrecido ("a las 4", "9:30"); nunca elige si hay negación ("no", "ninguno", "otro") o ambigüedad ("el 2 o el 3"). | `test_respuestas_naturales_eligen_el_horario`, `test_la_opcion_2_agenda` |
| 2 | Confirmar una cancelación con "sí, cancélala por favor" o "si porfavor" no cancelaba (solo frases exactas) y volvía a preguntar. | `agenda.es_si`: empieza con sí/claro/ok/confirmo… y no trae dudas ("sí, pero mejor cámbiala" no cancela). | `test_confirmaciones_de_cancelacion` |

Revisado sin hallazgos: cálculo de horarios con margen y anticipación, resta de intervalos de Google al reprogramar, candado de reserva, cancelación con falla de Google.
