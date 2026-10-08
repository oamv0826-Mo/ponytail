# Ciclo autónomo de caza de errores

Estado: rondas = 6 · rondas seguidas sin hallazgos = 0 · se detiene con 2 limpias seguidas o al llegar a 8.

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

## Ronda 3 · tick y ventanas de envío
| # | Hallazgo (reproducido) | Corrección | Prueba |
|---|---|---|---|
| 1 | Si el cliente reaccionaba 👍 a la respuesta del equipo, el escalamiento lo tomaba como mensaje sin responder y avisaba a todos "sigue sin respuesta". | Las reacciones no cuentan como mensaje pendiente (`base.NO_REACCION`). | `test_reaccion_a_la_respuesta_del_equipo_no_escala` |
| 2 | "Pasar a humano" a mano en una conversación de ayer disparaba el aviso de escalamiento en el siguiente tick (contaba desde el mensaje viejo). | El escalamiento cuenta desde lo último entre: mensaje del cliente, apertura y paso a humano. | `test_pasar_a_humano_a_mano_cuenta_desde_ese_momento` |
| 3 | Una reacción días después contaba como "consulta recibida" en el reporte (inflaba el número 1 y el de sin respuesta). | Las reacciones se excluyen del cálculo de consultas. | `test_reaccion_no_cuenta_como_consulta_en_el_reporte` |

Revisado sin hallazgos: recordatorios 24 h/2 h y su adelanto, reseñas cada 90 días, seguimiento sin dos envíos el mismo día, lotes de reactivación y calidad del número, reintentos ante fallas de Meta.

## Ronda 4 · ventas, atribución y reporte
| # | Hallazgo (reproducido) | Corrección | Prueba |
|---|---|---|---|
| 1 | Dos citas distintas del mismo contacto, el mismo día y con el mismo monto (p. ej. dos hijos): la segunda venta se rechazaba como duplicada y no contaba para la garantía. | Esquema v4: duplicado = misma cita y monto (doble clic) o, sin cita, mismo contacto/fecha/monto (CSV reimportado). La migración reconstruye la tabla y conserva las ventas. | `test_dos_citas_distintas_mismo_dia_y_monto_cuentan_las_dos`, `test_migracion_v4_conserva_ventas` |
| 2 | Una cita reprogramada contaba dos veces en "citas agendadas" (la nueva y la anterior, cancelada). | Solo cuentan las citas que siguen en pie; las canceladas se muestran aparte. | `test_reprogramar_no_cuenta_dos_citas` |
| 3 | `reporte 2026-13` terminaba con un traceback. | Mensaje claro y código de salida 1. | `test_mes_invalido_da_mensaje_claro` |

Revisado sin hallazgos: reglas de atribución contra el anexo (ventanas de 60 días, orden de reglas, fin de día local), validación de montos, garantía y su periodo, página estática.

## Ronda 5 · bandeja web y seguridad
| # | Hallazgo (reproducido) | Corrección | Prueba |
|---|---|---|---|
| 1 | Un `hub.verify_token` o una firma `X-Hub-Signature-256` con caracteres no ASCII hacía fallar `hmac.compare_digest` (TypeError): el servidor cortaba la conexión con traceback en vez de responder 403/401. | Comparación en bytes. | `test_texto_no_ascii_en_verificacion_y_firma_no_tumba_la_conexion` |
| 2 | Cinco fallos de una persona bloqueaban el login de toda la oficina 15 min (mismo límite por IP que por usuario; en una oficina todos salen por la misma IP). | 5 fallos por usuario (lo pedido) y 20 por IP. | `test_fallos_de_una_persona_no_bloquean_a_la_oficina` |

Revisado sin hallazgos: escape de todo dato del cliente en bandeja/citas/ventas, anti-CSRF por Origin, cookies, expiración de sesión, cabeceras de seguridad, descarga de archivos (ronda 1), límites de tamaño de cuerpo.

## Ronda 6 · importaciones CSV y teléfonos
| # | Hallazgo (reproducido) | Corrección | Prueba |
|---|---|---|---|
| 1 | Un CSV guardado desde Excel en Windows (Windows-1252, "María Peña") hacía tronar toda la importación con UnicodeDecodeError. | `ventas.leer_csv`: UTF-8 y, si no, Windows-1252. | `test_csv_de_excel_en_windows_latin1` |
| 2 | CSV con punto y coma: todas las filas rechazadas. | Separador detectado en el encabezado (coma, punto y coma o tab). | `test_punto_y_coma_encabezado_celular_y_fecha_mexicana` |
| 3 | Encabezados comunes ("Celular", "WhatsApp", "Tel", "Importe", "Última visita") no se reconocían: todas las filas rechazadas. | Alias de columnas. | (misma prueba) y `test_ventas_csv_con_fecha_mexicana_y_columna_importe` |
| 4 | Fechas DD/MM/AAAA (como las guarda Excel en México) rechazaban la fila completa y el contacto se perdía. | Se aceptan AAAA-MM-DD y DD/MM/AAAA (clientes y ventas). | (mismas pruebas) |
| 5 | Teléfonos con prefijos antiguos 044/045/01 o de 10 dígitos con 0 inicial se aceptaban como números inválidos (+044…, +01…, +5208…). | 044/045 + 10 y 01 + 10 se convierten a +52 + 10; cualquier número que quede con 0 inicial se rechaza. | `test_prefijos_antiguos_de_mexico_y_numeros_con_cero` |

Prueba existente actualizada: `test_e4.Atribucion.test_importar_ventas` usaba "06/10/2026" como fecha inválida; ahora es válida a propósito y se usa "2026/13/45".
