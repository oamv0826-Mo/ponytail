"""Una prueba por cada hallazgo de la revisión de código de la rama (para que no regresen)."""
import datetime as dt
import http.client
import json
import os
import socket
import threading
import unittest
from unittest import mock

from ayuda import Caso, local
from rv import agenda, base, motor, tick, wa, web
from rv.__main__ import payload_falso


class RefererPolicy(Caso):
    def test_no_usa_no_referrer_porque_rompe_el_origin_de_los_post(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            puerto = s.getsockname()[1]
        self.cfg["url_publica"] = f"http://127.0.0.1:{puerto}"
        srv = web.crear_servidor(self.cfg, puerto=puerto)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        h = http.client.HTTPConnection("127.0.0.1", puerto, timeout=5)
        h.request("GET", "/bandeja/login")
        r = h.getresponse()
        r.read()
        self.assertEqual(r.getheader("Referrer-Policy"), "same-origin")


class HorarioVencido(Caso):
    def test_opcion_que_ya_paso_no_se_agenda(self):
        self.escribir("quiero agendar una limpieza")       # 10:00 → 1) hoy 12:00
        self.t = local(2026, 10, 6, 15, 0)                # el cliente contesta a las 15:00 (propuesta vigente)
        salida = self.escribir("1")
        self.assertEqual(salida[0]["texto"], self.cfg.msg["horario_ocupado"])
        self.assertIn("tengo estos horarios", salida[1]["texto"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM cita").fetchone()[0], 0)


class ReprogramarConGoogle(Caso):
    CONFIG = {"agenda": {"proveedor": "google", "calendar_id": "cal"}}

    def cita_vieja(self, estado="agendada"):
        self.escribir("hola")
        c = self.contacto()
        return c, self.con.execute(
            "INSERT INTO cita (contacto_id, servicio_id, inicio, fin, evento_id, estado, creado, creado_por) "
            "VALUES (?, 'limpieza', ?, ?, 'ev-viejo', ?, 'x', 'bot')",
            (c["id"], base.iso(local(2026, 10, 7, 10, 0)), base.iso(local(2026, 10, 7, 10, 45)), estado)).lastrowid

    def test_bloque_recortado_o_unido_por_google_no_bloquea_su_propio_horario(self):
        c, vieja = self.cita_vieja()
        unido = [(local(2026, 10, 7, 9, 0), local(2026, 10, 7, 10, 45))]   # otro evento 9-10 unido al propio
        with mock.patch.object(agenda, "ocupado_google", return_value=unido), \
                mock.patch.object(agenda, "crear_evento", return_value="ev-nuevo"), \
                mock.patch.object(agenda, "borrar_evento"):
            nueva = agenda.reservar(self.con, self.cfg, c, "limpieza", local(2026, 10, 7, 10, 30), reprograma=vieja)
        self.assertIsNotNone(nueva)
        self.assertEqual(self.con.execute("SELECT estado FROM cita WHERE id=?", (vieja,)).fetchone()[0], "cancelada")

    def test_si_falla_borrar_el_evento_viejo_no_queda_doble_cita_y_se_avisa(self):
        c, vieja = self.cita_vieja()
        with mock.patch.object(agenda, "ocupado_google", return_value=[]), \
                mock.patch.object(agenda, "crear_evento", return_value="ev-nuevo"), \
                mock.patch.object(agenda, "borrar_evento", side_effect=agenda.AgendaError("Google 500")):
            nueva = agenda.reservar(self.con, self.cfg, c, "limpieza", local(2026, 10, 8, 10, 0), reprograma=vieja)
        estados = dict(self.con.execute("SELECT id, estado FROM cita").fetchall())
        self.assertEqual((estados[vieja], estados[nueva]), ("cancelada", "agendada"))
        avisos = self.con.execute("SELECT texto FROM mensaje WHERE plantilla='aviso_equipo'").fetchall()
        self.assertTrue(avisos and all("bórralo a mano" in a[0] for a in avisos))

    def test_no_pisa_una_cita_que_ya_se_marco_asistio(self):
        c, vieja = self.cita_vieja(estado="asistio")
        with mock.patch.object(agenda, "ocupado_google", return_value=[]), \
                mock.patch.object(agenda, "crear_evento", return_value="ev-nuevo"), \
                mock.patch.object(agenda, "borrar_evento") as borrar:
            agenda.reservar(self.con, self.cfg, c, "limpieza", local(2026, 10, 8, 10, 0), reprograma=vieja)
        borrar.assert_not_called()
        self.assertEqual(self.con.execute("SELECT estado FROM cita WHERE id=?", (vieja,)).fetchone()[0], "asistio")


class RedDeSeguridad(Caso):
    def test_excepcion_cualquiera_de_la_ia_pasa_a_humano(self):
        os.environ["ANTHROPIC_API_KEY"] = "x"
        with mock.patch("rv.ia._llamar_claude", side_effect=KeyError("precio_mxn")):
            salida = self.escribir("hola")
        self.assertEqual(self.contacto()["handoff_motivo"], "ia_error")
        self.assertIn("te atiende una persona", salida[0]["texto"])

    def test_excepcion_en_el_motor_pasa_a_humano_y_queda_registrada(self):
        with mock.patch.object(motor, "ejecutar", side_effect=RuntimeError("bug")):
            salida = self.escribir("hola")
        self.assertEqual(self.contacto()["handoff_motivo"], "error_interno")
        self.assertIn("te atiende una persona", salida[0]["texto"])
        self.assertIn("RuntimeError", self.con.execute("SELECT error FROM entrada").fetchone()[0])

    def test_config_exige_precio_numerico(self):
        datos = json.loads((self.dir / "cliente.json").read_text())
        del datos["servicios"][0]["precio_mxn"]
        (self.dir / "cliente.json").write_text(json.dumps(datos))
        with self.assertRaises(ValueError):
            base.cargar_config(self.dir)


class RecuperacionTrasCaida(Caso):
    def entrada_a_medias(self, msg_id, hace_min):
        payload = payload_falso("+528100000001", "hola", "Cliente", msg_id=msg_id)
        [(clave, item)] = wa.separar(payload)
        self.con.execute("INSERT INTO entrada (clave, payload, recibido, procesado) VALUES (?,?,?,?)",
                         (clave, json.dumps(item), base.iso(self.t),
                          base.iso(self.t - dt.timedelta(minutes=hace_min))))
        return item

    def salientes(self):
        return self.con.execute("SELECT COUNT(*) FROM mensaje WHERE direccion='out'").fetchone()[0]

    def test_entrada_reclamada_sin_terminar_se_retoma(self):
        self.entrada_a_medias("wamid.A", hace_min=5)
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual(self.salientes(), 1)
        self.assertIsNotNone(self.con.execute("SELECT terminado FROM entrada").fetchone()[0])

    def test_mensaje_guardado_sin_respuesta_se_responde_una_vez(self):
        item = self.entrada_a_medias("wamid.B", hace_min=5)
        motor.guardar_entrante(self.con, self.cfg, item)          # la caída fue justo después de guardarlo
        motor.procesar_pendientes(self.con, self.cfg)
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual(self.salientes(), 1)

    def test_mensaje_ya_respondido_no_se_responde_de_nuevo(self):
        item = self.entrada_a_medias("wamid.C", hace_min=5)
        motor.procesar_mensaje(self.con, self.cfg, item)          # se respondió y luego cayó antes de marcar
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual(self.salientes(), 1)

    def test_reclamo_reciente_no_se_toca(self):
        self.entrada_a_medias("wamid.D", hace_min=1)              # otro proceso la está atendiendo
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual(self.salientes(), 0)


class TickConFallas(Caso):
    def test_recordatorio_fallido_se_reintenta_y_luego_se_rinde(self):
        self.escribir("hola")
        c = self.contacto()
        self.con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, creado, creado_por) VALUES "
                         "(?, 'limpieza', ?, ?, ?, 'bot')", (c["id"], base.iso(local(2026, 10, 8, 12, 0)),
                                                             base.iso(local(2026, 10, 8, 12, 45)), base.iso(self.t)))
        self.cfg["modo_prueba"] = False
        with mock.patch("rv.wa._graph", side_effect=RuntimeError("Graph 500")):
            for minuto in (0, 5):
                self.t = local(2026, 10, 7, 12, minuto)
                tick.recordatorios(self.con, self.cfg, self.t)
                self.assertIsNone(self.con.execute("SELECT rec24 FROM cita").fetchone()[0])
            self.t = local(2026, 10, 7, 12, 10)
            tick.recordatorios(self.con, self.cfg, self.t)
        self.assertEqual(self.con.execute("SELECT rec24 FROM cita").fetchone()[0], "error")

    def test_reactivacion_fallida_no_cuenta_como_enviada(self):
        self.con.execute("INSERT INTO contacto (telefono, wa_id, nombre, origen, consentimiento, creado) "
                         "VALUES ('+528112340001', '528112340001', 'Ana', 'importado', 1, 'x')")
        self.cfg["modo_prueba"] = False
        with mock.patch("rv.wa._graph", side_effect=RuntimeError("Graph 500")), \
                mock.patch("rv.wa.graph_get", return_value={"quality_rating": "GREEN"}):
            tick.reactivacion(self.con, self.cfg, self.t)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM evento WHERE tipo='reactivacion'").fetchone()[0], 0)
        self.assertIsNone(self.contacto("+528112340001")["reactivacion_enviada"])


class AsistenciaFutura(Caso):
    def test_no_se_marca_asistencia_antes_del_dia_de_la_cita(self):
        self.escribir("quiero agendar una limpieza")
        self.escribir("3")                                         # mañana 9:00
        cita = self.con.execute("SELECT * FROM cita").fetchone()
        self.assertNotIn(">Asistió</button>", agenda.pagina_citas(self.con, self.cfg, "ana", {}))
        destino = agenda.marcar_cita(self.con, self.cfg, "ana", {"cita": [str(cita["id"])], "estado": ["asistio"]})
        self.assertIn("error=", destino)
        self.assertEqual(self.con.execute("SELECT estado FROM cita").fetchone()[0], "agendada")
        self.t = local(2026, 10, 7, 9, 50)                         # el día de la cita
        self.assertEqual(agenda.marcar_cita(self.con, self.cfg, "ana", {"cita": [str(cita["id"])], "estado": ["asistio"]}),
                         "/bandeja/citas")


class Varios(Caso):
    def test_texto_de_escalamiento_no_fija_minutos(self):
        self.assertNotRegex(motor.motivo_legible("escalamiento"), r"\d")

    def test_respaldos_seguidos_no_chocan_ni_dejan_temporales(self):
        a = base.respaldar(self.con, self.dir / "r")
        b = base.respaldar(self.con, self.dir / "r")
        self.assertTrue(a.exists() and b.exists())
        self.assertEqual(list((self.dir / "r").glob("tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
