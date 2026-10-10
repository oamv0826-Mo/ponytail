"""Etapa 4: registro e importación de ventas, atribución, reporte con garantía y página estática."""
import datetime as dt
import html.parser
import unittest

from ayuda import Caso, local
from rv import base, tick, ventas


class Atribucion(Caso):
    def venta(self, tel, fecha, monto="1000"):
        vid, error = ventas.registrar_venta(self.con, self.cfg, self.contacto(tel)["id"], monto, fecha, "prueba")
        self.assertIsNone(error)
        return self.con.execute("SELECT origen FROM venta WHERE id=?", (vid,)).fetchone()[0]

    def importar(self, filas):
        ruta = self.dir / "c.csv"
        ruta.write_text("nombre,telefono,ultima_visita,consentimiento\n" + "\n".join(filas), encoding="utf-8")
        ventas.importar_clientes(self.con, self.cfg, ruta)

    def test_reactivacion_requiere_respuesta_y_60_dias(self):
        self.importar(["Ana,8112340001,,si", "Beto,8112340002,,si", "Caro,8112340003,,si"])
        self.t = local(2026, 10, 6, 10, 0)
        tick.reactivacion(self.con, self.cfg, self.t)
        self.t = local(2026, 10, 7, 11, 0)
        self.escribir("¡hola! sí quiero volver", de="+528112340001")
        self.escribir("hola", de="+528112340003")
        self.t = local(2026, 10, 20, 12, 0)
        self.assertEqual(self.venta("+528112340001", "2026-10-20"), "reactivacion")
        self.assertEqual(self.venta("+528112340002", "2026-10-20"), "sin_atribucion")   # no respondió
        self.t = local(2026, 12, 20, 12, 0)
        self.assertEqual(self.venta("+528112340003", "2026-12-20"), "sin_atribucion")   # > 60 días del envío

    def test_seguimiento_con_respuesta_o_cita(self):
        self.escribir("¿cuánto cuesta la limpieza?")
        self.t = local(2026, 10, 8, 10, 0)
        tick.seguimiento(self.con, self.cfg, self.t)
        self.t = local(2026, 10, 8, 12, 0)
        self.escribir("ok, quiero agendar una limpieza")
        self.t = local(2026, 10, 15, 12, 0)
        self.assertEqual(self.venta("+528100000001", "2026-10-15"), "seguimiento")

    def test_seguimiento_sin_respuesta_no_cuenta(self):
        self.escribir("¿cuánto cuesta la limpieza?")
        self.t = local(2026, 10, 8, 10, 0)
        tick.seguimiento(self.con, self.cfg, self.t)
        self.assertEqual(self.venta("+528100000001", "2026-10-08"), "respuesta_rapida")

    def test_fuera_de_horario_y_respuesta_rapida(self):
        self.t = local(2026, 10, 6, 22, 0)
        self.escribir("hola", de="+528100000002")
        self.t = local(2026, 10, 7, 10, 0)
        self.escribir("hola", de="+528100000003")
        self.assertEqual(self.venta("+528100000002", "2026-10-07"), "fuera_horario")
        self.assertEqual(self.venta("+528100000003", "2026-10-07"), "respuesta_rapida")

    def test_validaciones(self):
        self.escribir("hola")
        cid = self.contacto()["id"]
        r = lambda m, f: ventas.registrar_venta(self.con, self.cfg, cid, m, f, "x")[1]  # noqa: E731
        self.assertIn("monto inválido", r("abc", "2026-10-06"))
        self.assertIn("mayor a 0", r("0", "2026-10-06"))
        self.assertIn("futura", r("100", "2026-10-07"))
        self.assertIsNone(r("$1,500.50", "2026-10-06"))
        self.assertIn("ya estaba registrada", r("1500.50", "2026-10-06"))
        self.assertEqual(self.con.execute("SELECT monto_centavos FROM venta").fetchone()[0], 150050)

    def test_importar_ventas(self):
        self.escribir("hola")
        ruta = self.dir / "v.csv"
        ruta.write_text("telefono,fecha,monto\n8100000001,2026-10-06,800\n8100000001,2026-10-06,800\n"
                        "8199999999,2026-10-06,500\n8100000001,2026/13/45,300\n", encoding="utf-8")
        r = ventas.importar_ventas(self.con, self.cfg, ruta)
        self.assertEqual((r["registradas"], r["duplicadas"]), (1, 1))
        self.assertEqual([x[0] for x in r["rechazadas"]], [4, 5])


class Reporte(Caso):
    def test_cinco_numeros_y_garantia(self):
        self.t = local(2026, 10, 6, 22, 0)
        self.escribir("hola", de="+528100000002")                    # consulta fuera de horario
        self.t = local(2026, 10, 7, 10, 0)
        self.escribir("quiero agendar una limpieza")                  # consulta 2
        self.escribir("1")
        self.con.execute("UPDATE cita SET estado='asistio', asistio_en=?", (base.iso(self.t),))
        self.t = local(2026, 10, 7, 13, 0)
        tick.resenas(self.con, self.cfg, self.t)
        ventas.registrar_venta(self.con, self.cfg, self.contacto("+528100000002")["id"], "4000", "2026-10-07", "x")
        ventas.registrar_venta(self.con, self.cfg, self.contacto()["id"], "800", "2026-10-07", "x")
        texto = ventas.reporte(self.con, self.cfg, "2026-10", resenas_google=3)
        self.assertIn("| 1. Consultas recibidas | **2** (1 fuera de horario) |", texto)
        self.assertIn("| 3. Citas agendadas | **1**", texto)
        self.assertIn("| 4. Ventas recuperadas | **$4,000.00 MXN** en 1 venta |", texto)
        self.assertIn("1 solicitud enviada; reseñas nuevas en Google: 3", texto)
        self.assertIn("Respuesta rápida (no cuenta) | 1 | $800.00 MXN", texto)
        self.assertIn("EN CURSO: $4,000.00 MXN de $6,000.00 MXN; el periodo termina el 2026-11-29", texto)

    def test_garantia_cumple_y_no_cumple(self):
        self.t = local(2026, 10, 6, 22, 0)
        self.escribir("hola")
        self.t = local(2026, 10, 7, 12, 0)
        self.assertIsNone(ventas.registrar_venta(self.con, self.cfg, self.contacto()["id"], "5999.99", "2026-10-07", "x")[1])
        self.t = local(2026, 11, 30, 10, 0)                           # día 61: ya terminó el periodo
        self.assertTrue(ventas.garantia(self.con, self.cfg)["estado"].startswith("NO CUMPLE"))
        self.t = local(2026, 11, 29, 10, 0)
        ventas.registrar_venta(self.con, self.cfg, self.contacto()["id"], "0.01", "2026-11-29", "x")  # último día
        self.t = local(2026, 11, 30, 10, 0)
        self.assertTrue(ventas.garantia(self.con, self.cfg)["estado"].startswith("CUMPLE"))
        ventas.registrar_venta(self.con, self.cfg, self.contacto()["id"], "100", "2026-11-30", "x")   # fuera del periodo
        self.assertEqual(ventas.garantia(self.con, self.cfg)["recuperado"], 600000)

    def test_tiempo_de_respuesta(self):
        self.escribir("hola")
        self.con.execute("UPDATE mensaje SET creado=? WHERE direccion='out'", (base.iso(self.t + dt.timedelta(minutes=8)),))
        d = ventas.datos_reporte(self.con, self.cfg, "2026-10")
        self.assertEqual((d["mediana_s"], d["pct_5min"]), (480, 0))


class Pagina(Caso):
    def test_html_valido_con_enlace_a_whatsapp_y_escapado(self):
        self.cfg["nombre"] = "Clínica <Sonrisa> & Co"
        h = ventas.pagina(self.cfg)
        self.assertIn("https://wa.me/528110000000?text=Hola%2C%20quiero%20informaci%C3%B3n", h)
        self.assertIn("Clínica &lt;Sonrisa&gt; &amp; Co", h)
        self.assertIn("Domingo: cerrado", h)

        class P(html.parser.HTMLParser):
            abiertos = []

            def handle_starttag(self, tag, attrs):
                if tag not in ("meta", "br", "!doctype"):
                    self.abiertos.append(tag)

            def handle_endtag(self, tag):
                self.abiertos.pop() if self.abiertos and self.abiertos[-1] == tag else self.abiertos.append("!" + tag)

        p = P()
        p.feed(h)
        self.assertEqual(p.abiertos, [])   # etiquetas balanceadas


class BandejaVentas(Caso):
    def test_registrar_venta_desde_cita(self):
        self.escribir("quiero agendar una limpieza")
        self.escribir("1")
        cita = self.con.execute("SELECT * FROM cita").fetchone()
        self.con.execute("UPDATE cita SET estado='asistio' WHERE id=?", (cita["id"],))
        fila = self.con.execute("SELECT * FROM cita").fetchone()
        self.assertIn("Registrar", ventas.html_venta_en_cita(self.con, self.cfg, fila))
        c = self.contacto()
        self.assertIsNone(ventas.accion_venta(self.con, self.cfg, c, "ana",
                                              {"cita": [str(cita["id"])], "monto": ["800"], "fecha": ["2026-10-06"]}))
        self.assertEqual(ventas.html_venta_en_cita(self.con, self.cfg, fila), "")
        self.assertEqual(self.con.execute("SELECT cita_id, registrado_por FROM venta").fetchone()[:],
                         (cita["id"], "humano:ana"))
        otro = self.con.execute("INSERT INTO contacto (telefono, creado) VALUES ('+528100000099', 'x')").lastrowid
        self.assertEqual(ventas.accion_venta(self.con, self.cfg, {"id": otro}, "ana",
                                             {"cita": [str(cita["id"])], "monto": ["1"], "fecha": ["2026-10-06"]}),
                         "La cita no es de este contacto.")


if __name__ == "__main__":
    unittest.main()
