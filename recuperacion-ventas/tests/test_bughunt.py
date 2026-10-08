"""Pruebas de los hallazgos del ciclo autónomo de caza de errores (ver docs/BUGHUNT.md)."""
import http.client
import socket
import threading
from unittest import mock

from ayuda import Caso
from rv import base, motor, web


def mensaje_media(t, tipo, datos, msg_id):
    return {"tipo": "mensaje", "pnid": "", "nombre": "Luis",
            "msg": {"from": "528100000299", "id": msg_id, "timestamp": str(int(t.timestamp())), "type": tipo, tipo: datos}}


class Ronda1Motor(Caso):
    def test_frases_naturales_de_baja_registran_opt_out(self):
        for i, t in enumerate(["quiero darme de baja", "Ya no me manden mensajes por favor", "No me envíen más promociones"]):
            tel = f"+52810000020{i}"
            [m] = self.escribir(t, de=tel)
            self.assertEqual(m["texto"], self.cfg.msg["baja"])
            self.assertIsNotNone(self.con.execute("SELECT 1 FROM optout WHERE telefono=?", (tel,)).fetchone())
        self.escribir("ya no me duele, gracias", de="+528100000210")
        self.assertIsNone(self.con.execute("SELECT 1 FROM optout WHERE telefono='+528100000210'").fetchone())

    def test_foto_conserva_su_texto_y_el_id_del_archivo(self):
        motor.procesar_item(self.con, self.cfg, mensaje_media(self.t, "image", {
            "id": "MEDIA123", "mime_type": "image/jpeg", "caption": "¿Cuánto cuesta arreglar esto?"}, "wamid.IMG"))
        fila = self.con.execute("SELECT texto, media_id, media_mime FROM mensaje WHERE wa_id='wamid.IMG'").fetchone()
        self.assertEqual(tuple(fila), ("[foto] ¿Cuánto cuesta arreglar esto?", "MEDIA123", "image/jpeg"))

    def test_reaccion_no_pasa_a_humano_ni_avisa(self):
        self.escribir("hola", de="+528100000299")
        motor.procesar_item(self.con, self.cfg, mensaje_media(self.t, "reaction", {"message_id": "x", "emoji": "👍"}, "wamid.R"))
        self.assertEqual(self.contacto("+528100000299")["estado"], "bot")
        self.assertEqual(self.con.execute("SELECT texto FROM mensaje WHERE wa_id='wamid.R'").fetchone()[0], "[reacción 👍]")
        self.assertIsNone(self.con.execute("SELECT 1 FROM mensaje WHERE plantilla='aviso_equipo'").fetchone())

    def test_nota_de_voz_se_puede_escuchar_en_la_bandeja(self):
        motor.procesar_item(self.con, self.cfg, mensaje_media(self.t, "audio", {"id": "AUD1", "mime_type": "audio/ogg; codecs=opus"},
                                                              "wamid.A"))
        cid = self.contacto("+528100000299")["id"]
        self.assertIn("<audio controls", web.html_mensajes(self.con, self.cfg, cid))


class Ronda1Media(Caso):
    def setUp(self):
        super().setUp()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.puerto = s.getsockname()[1]
        self.cfg["url_publica"] = f"http://127.0.0.1:{self.puerto}"
        self.cfg["modo_prueba"] = False
        srv = web.crear_servidor(self.cfg, puerto=self.puerto)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        web.crear_usuario(self.con, "ana", "clave-segura-123")
        motor.procesar_item(self.con, self.cfg, mensaje_media(self.t, "document", {"id": "DOC1", "mime_type": "image/svg+xml"},
                                                              "wamid.D"))
        self.mid = self.con.execute("SELECT id FROM mensaje WHERE wa_id='wamid.D'").fetchone()[0]

    def pedir(self, ruta, cookie=None):
        h = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        h.request("GET", ruta, headers={"Cookie": cookie} if cookie else {})
        r = h.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_media_requiere_sesion_y_svg_se_descarga_no_se_ejecuta(self):
        self.assertEqual(self.pedir(f"/bandeja/media/{self.mid}")[0], 303)
        token = web.abrir_sesion(self.con, "ana")
        with mock.patch("rv.wa.descargar_media", return_value=(b"<svg onload=alert(1)>", "image/svg+xml")):
            st, cab, cuerpo = self.pedir(f"/bandeja/media/{self.mid}", cookie=f"rv_sesion={token}")
        self.assertEqual((st, cab["Content-Type"], cab["Content-Disposition"]), (200, "application/octet-stream", "attachment"))
        self.assertEqual(cuerpo, b"<svg onload=alert(1)>")


class Ronda2Agenda(Caso):
    def test_respuestas_naturales_eligen_el_horario(self):
        from ayuda import local
        from rv import agenda
        slots = [local(2026, 10, 6, 12, 0), local(2026, 10, 6, 16, 0), local(2026, 10, 7, 9, 30)]
        casos = {"2": 1, "la opcion 2": 1, "opcion 2 por favor": 1, "la 2 porfa": 1, "el segundo": 1, "la ultima": 2,
                 "a las 4": 1, "la de las 12": 0, "9 30": 2, "las 9": 2,
                 "no": None, "ninguno me queda": None, "otro horario": None, "a las 5": None, "la 7": None,
                 "el 2 o el 3": None, "": None}
        for texto, esperado in casos.items():
            self.assertEqual(agenda.elegir_opcion(self.cfg, texto, slots), esperado, texto)

    def test_la_opcion_2_agenda(self):
        self.escribir("quiero agendar una limpieza")
        [m] = self.escribir("La opción 2 🙏")
        self.assertIn("quedó para el martes 6 de octubre a las 16:00", m["texto"])

    def test_confirmaciones_de_cancelacion(self):
        from rv import agenda
        for si in ("si", "sí, cancélala por favor", "si porfavor", "claro", "ok gracias", "de acuerdo"):
            self.assertTrue(agenda.es_si(base.normalizar_texto(si)), si)
        for no in ("sí, pero mejor cámbiala", "si no hay de otra", "no", "mmm", ""):
            self.assertFalse(agenda.es_si(base.normalizar_texto(no)), no)
