"""Kit de venta y prueba real: auditoría de fugas, demo-ventas y las revisiones de prueba-real."""
import datetime as dt
import shutil
import tempfile
import unittest
from pathlib import Path

from ayuda import Caso, local
from rv import auditoria, base, demo, motor, prueba, ventas

RAIZ = Path(__file__).resolve().parent.parent
NICHO = auditoria.cargar_nicho(RAIZ / "nichos" / "clinica-estetica.json")
CABECERA = ",".join(auditoria.COLUMNAS)


class Auditoria(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def csv(self, *filas):
        (self.dir / "negocios.csv").write_text(CABECERA + "\n" + "\n".join(filas) + "\n", encoding="utf-8")

    def test_puntaje_sigue_la_tabla_del_plan(self):
        self.csv("Perfecto,81,2026-10-13 11:00,2026-10-13 11:04,2026-10-17 20:00,2026-10-17 20:30,si,si,si,,101,si,si,si,,,",
                 "Lento,82,2026-10-13 11:00,2026-10-13 11:59,2026-10-17 20:00,2026-10-18 10:00,si,no,no,si,150,no,si,no,,,",
                 "Mudo,83,2026-10-13 11:00,,2026-10-17 20:00,,no,no,no,no,29,no,no,no,,,")
        (perfecto, lento, mudo), errores = auditoria.leer(self.dir / "negocios.csv")
        self.assertEqual(errores, [])
        self.assertEqual(auditoria.total(auditoria.puntaje(perfecto)), 100)
        p = auditoria.puntaje(lento)
        self.assertEqual({k: v[0] for k, v in p.items()},
                         {"habil": 15, "no_habil": 5, "calidad": 10, "seguimiento": 20, "resenas": 5, "contacto": 5})
        self.assertEqual(auditoria.total(auditoria.puntaje(mudo)), 0)

    def test_sin_prueba_fuera_de_horario_se_calcula_sobre_85(self):
        self.csv("Solo hábil,81,2026-10-13 11:00,2026-10-13 11:01,,,si,si,si,,101,si,si,si,,,")
        (n,), _ = auditoria.leer(self.dir / "negocios.csv")
        p = auditoria.puntaje(n)
        self.assertNotIn("no_habil", p)
        self.assertEqual(auditoria.total(p), 100)

    def test_filas_invalidas_se_reportan_por_linea(self):
        self.csv("Al revés,81,2026-10-13 11:00,2026-10-13 10:00,,,si,si,no,no,1,no,no,no,,,",
                 "Sí raro,82,2026-10-13 11:00,,,,quizas,si,no,no,1,no,no,no,,,")
        negocios, errores = auditoria.leer(self.dir / "negocios.csv")
        self.assertEqual(negocios, [])
        self.assertEqual([x[0] for x in errores], [2, 3])

    def test_reportes_anonimos_con_promedio_mejor_y_venta_en_riesgo(self):
        self.csv("Clínica Bella,81,2026-10-13 11:00,2026-10-13 11:03,,,si,si,si,no,150,si,si,si,,,",
                 "Estética Luz,82,2026-10-13 11:05,2026-10-13 15:00,,,si,no,no,no,40,no,si,no,60,5000,")
        resultados, errores = auditoria.generar(self.dir, NICHO, fecha="13/10/2026")
        self.assertEqual((len(resultados), errores), (2, []))
        luz = (self.dir / "reportes" / "estetica-luz.html").read_text(encoding="utf-8")
        self.assertNotIn("Bella", luz)
        self.assertIn("Estética Luz obtuvo 29/100", luz)
        self.assertIn("El promedio del grupo (2 clínicas estéticas de Monterrey) es 64 y el mejor obtuvo 100", luz)
        self.assertIn("1 de cada 5", luz)
        self.assertIn("$60,000 MXN al mes en ventas en riesgo", luz)
        self.assertIn("respondió en 3 h 55 min", luz)
        bella = (self.dir / "reportes" / "clinica-bella.html").read_text(encoding="utf-8")
        self.assertIn("Pendiente: con tus consultas", bella)
        self.assertIn("Clínica Bella", (self.dir / "resumen.html").read_text(encoding="utf-8"))


class DemoVentas(unittest.TestCase):
    def test_demo_recupera_dos_ventas_y_restaura_el_reloj(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        reloj = base.ahora
        texto = demo.correr(NICHO, d, pausa=0)
        self.assertIs(base.ahora, reloj)
        self.assertIn("**$11,000.00 MXN** en 2 ventas", texto)
        self.assertIn("| Fuera de horario | 1 | $4,500.00 MXN |", texto)
        self.assertIn("| Seguimiento | 1 | $6,500.00 MXN |", texto)
        self.assertTrue((d / "envios-prueba.log").exists())   # modo prueba: nada salió a Meta

    def test_primer_lunes_no_cruza_de_mes(self):
        for dia in range(1, 31):
            lunes = demo.primer_lunes(dt.date(2026, 11, dia))
            self.assertEqual((lunes.month, lunes.weekday()), (11, 0))
            self.assertLessEqual(lunes.day, 7)


class PruebaReal(Caso):
    """Las revisiones de cada paso, con el recorrido completo simulado (el comando real usa Meta)."""
    TEL = "+528100000001"
    AHORA = local(2026, 10, 5, 23, 0)   # lunes de noche

    def paso(self, clave):
        return prueba.correr_paso(self.con, self.cfg, self.TEL, clave, esperar_s=0, entrada=lambda _: "s")

    def test_recorrido_completo(self):
        self.escribir("Hola, ¿cuánto cuesta la limpieza?")
        self.assertEqual(self.paso("noche"), "pasó")

        self.t = local(2026, 10, 6, 10, 0)
        self.assertEqual(self.paso("seguimiento"), "pasó")

        self.escribir("Quiero agendar una cita de limpieza")
        self.escribir("1")
        self.assertEqual(self.paso("cita"), "pasó")

        self.escribir("BAJA")
        self.assertEqual(self.paso("baja"), "pasó")

        self.t += dt.timedelta(minutes=5)
        self.escribir("tengo una urgencia, mucho dolor")
        self.assertEqual(self.paso("urgencia"), "pasó")

        self.t += dt.timedelta(minutes=5)
        c = self.contacto()
        motor.responder(self.con, self.cfg, c, "Hola, soy Ana", autor="humano:ana")
        self.con.execute("UPDATE contacto SET estado='bot' WHERE id=?", (c["id"],))
        self.escribir("quiero hablar con una persona")
        self.assertEqual(self.paso("handoff"), "pasó")

        self.t += dt.timedelta(minutes=5)
        self.con.execute("UPDATE cita SET inicio=?", (base.iso(self.t + dt.timedelta(days=3)),))   # cita de otro día
        self.assertIsNone(prueba.p_asistencia(self.con, self.cfg, self.TEL))
        cita = self.con.execute("SELECT * FROM cita").fetchone()
        self.assertLess(base.de_iso(cita["inicio"]), self.t)   # se movió a hace una hora
        self.con.execute("UPDATE cita SET estado='asistio', asistio_en=? WHERE id=?", (base.iso(self.t), cita["id"]))
        self.assertTrue(prueba.r_asistencia(self.con, self.cfg, self.TEL, base.iso(self.t))[0])

        ventas.registrar_venta(self.con, self.cfg, c["id"], 800, self.t.astimezone(self.cfg.tz).date(), "humano:ana",
                               cita["id"])
        ok, detalle = prueba.r_venta(self.con, self.cfg, self.TEL, base.iso(self.t))
        self.assertTrue(ok, detalle)
        self.assertIn("Seguimiento", detalle)
        self.assertEqual(self.paso("reporte"), "pasó")
        resultados = (self.dir / "prueba-real-resultados.md").read_text(encoding="utf-8")
        self.assertEqual(resultados.count("| pasó |"), 7)

    def test_mensaje_en_horario_no_pasa_como_noche(self):
        self.t = local(2026, 10, 6, 10, 0)
        self.escribir("Hola, ¿cuánto cuesta la limpieza?")
        ok, detalle = prueba.r_noche(self.con, self.cfg, self.TEL, base.iso(self.t))
        self.assertFalse(ok)
        self.assertIn("horario abierto", detalle)

    def test_telefono_del_equipo_no_puede_ser_el_de_prueba(self):
        with self.assertRaises(SystemExit):
            prueba.correr(self.con, self.cfg, "+528122222222", "noche", entrada=lambda _: "s")

    def test_ruta_del_override_en_la_respuesta_de_meta(self):
        conf = {"webhook_configuration": {"phone_number": "https://x/webhook", "application": "https://a"}, "id": "1"}
        self.assertIn(("webhook_configuration.phone_number", "https://x/webhook"), prueba._rutas(conf))


if __name__ == "__main__":
    unittest.main()
