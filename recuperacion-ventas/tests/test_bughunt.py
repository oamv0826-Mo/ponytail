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


class Ronda3Tick(Caso):
    def avisos_escalamiento(self):
        return self.con.execute("SELECT COUNT(*) FROM mensaje WHERE plantilla='aviso_equipo' "
                                "AND texto LIKE '%sigue sin respuesta%'").fetchone()[0]

    def test_reaccion_a_la_respuesta_del_equipo_no_escala(self):
        from ayuda import local
        from rv import tick
        self.escribir("quiero hablar con una persona", de="+528100000299")
        self.t = local(2026, 10, 6, 10, 5)
        motor.responder(self.con, self.cfg, self.contacto("+528100000299"), "Hola, soy Ana", autor="humano:ana")
        self.t = local(2026, 10, 6, 10, 6)
        motor.procesar_item(self.con, self.cfg, mensaje_media(self.t, "reaction", {"message_id": "x", "emoji": "👍"}, "wamid.R"))
        self.t = local(2026, 10, 6, 10, 40)
        tick.escalamientos(self.con, self.cfg, self.t)
        self.assertEqual(self.avisos_escalamiento(), 0)

    def test_pasar_a_humano_a_mano_cuenta_desde_ese_momento(self):
        from ayuda import local
        from rv import tick
        self.t = local(2026, 10, 5, 12, 0)
        self.escribir("¿cuánto cuesta la limpieza?")
        self.t = local(2026, 10, 6, 11, 0)
        web.accion_conversacion(self.con, self.cfg, self.contacto(), "ana", "pasar", {})
        self.t = local(2026, 10, 6, 11, 5)
        tick.escalamientos(self.con, self.cfg, self.t)
        self.assertEqual(self.avisos_escalamiento(), 0)
        self.t = local(2026, 10, 6, 11, 15)
        tick.escalamientos(self.con, self.cfg, self.t)
        self.assertEqual(self.avisos_escalamiento(), 2)   # 15 min después sigue sin respuesta: dueño + equipo

    def test_reaccion_no_cuenta_como_consulta_en_el_reporte(self):
        from ayuda import local
        from rv import ventas
        self.escribir("hola", de="+528100000299")
        self.t = local(2026, 10, 9, 10, 0)
        motor.procesar_item(self.con, self.cfg, mensaje_media(self.t, "reaction", {"message_id": "x", "emoji": "❤️"}, "wamid.R2"))
        self.assertEqual(ventas.datos_reporte(self.con, self.cfg, "2026-10")["consultas"], 1)


class Ronda4Ventas(Caso):
    def test_dos_citas_distintas_mismo_dia_y_monto_cuentan_las_dos(self):
        from ayuda import local
        from rv import agenda, ventas
        self.escribir("hola")
        c = self.contacto()
        citas = [agenda.reservar(self.con, self.cfg, c, "limpieza", local(2026, 10, 6, h, 0), creado_por="humano:ana")
                 for h in (12, 13)]
        self.t = local(2026, 10, 6, 14, 0)
        r = [ventas.registrar_venta(self.con, self.cfg, c["id"], "800", "2026-10-06", "x", cid)[1] for cid in citas]
        self.assertEqual(r, [None, None])
        self.assertIn("misma cita", ventas.registrar_venta(self.con, self.cfg, c["id"], "800", "2026-10-06", "x", citas[0])[1])
        self.assertIsNone(ventas.registrar_venta(self.con, self.cfg, c["id"], "500", "2026-10-06", "x")[1])
        self.assertIn("mismo contacto", ventas.registrar_venta(self.con, self.cfg, c["id"], "500", "2026-10-06", "x")[1])

    def test_reprogramar_no_cuenta_dos_citas(self):
        from rv import ventas
        self.escribir("quiero agendar una limpieza")
        self.escribir("3")
        self.escribir("necesito cambiar mi cita")
        self.escribir("2")
        d = ventas.datos_reporte(self.con, self.cfg, "2026-10")
        self.assertEqual((d["citas_agendadas"], d["citas_canceladas"]), (1, 1))

    def test_mes_invalido_da_mensaje_claro(self):
        from rv import ventas
        with self.assertRaisesRegex(ValueError, "usa AAAA-MM"):
            ventas.reporte(self.con, self.cfg, "2026-13")

    def test_migracion_v4_conserva_ventas(self):
        import sqlite3
        ruta = self.dir / "vieja.db"
        con = base.conectar(ruta)
        with mock.patch.object(base, "secciones_esquema", return_value=[x for x in base.secciones_esquema() if x[0] <= 3]):
            base.migrar(con)
        con.execute("INSERT INTO contacto (telefono, creado) VALUES ('+528100000001', 'x')")
        con.execute("INSERT INTO venta (contacto_id, monto_centavos, fecha, origen, registrado_por, creado) "
                    "VALUES (1, 80000, '2026-10-01', 'seguimiento', 'x', 'x')")
        base.migrar(con, ruta)
        self.assertEqual(con.execute("SELECT monto_centavos, origen FROM venta").fetchone()[:], (80000, "seguimiento"))
        self.assertGreaterEqual(con.execute("PRAGMA user_version").fetchone()[0], 4)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO venta (contacto_id, monto_centavos, fecha, origen, registrado_por, creado) "
                        "VALUES (1, 80000, '2026-10-01', 'x', 'x', 'x')")
        con.close()


class Ronda5Bandeja(Caso):
    def setUp(self):
        super().setUp()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.puerto = s.getsockname()[1]
        self.cfg["url_publica"] = f"http://127.0.0.1:{self.puerto}"
        srv = web.crear_servidor(self.cfg, puerto=self.puerto)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

    def pedir(self, metodo, ruta, cuerpo=None, cabeceras=None):
        h = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        h.request(metodo, ruta, body=cuerpo, headers=cabeceras or {})
        r = h.getresponse()
        r.read()
        return r.status

    def test_texto_no_ascii_en_verificacion_y_firma_no_tumba_la_conexion(self):
        from rv import wa
        self.assertEqual(self.pedir("GET", "/webhook?hub.mode=subscribe&hub.verify_token=%C3%B1&hub.challenge=1"), 403)
        self.assertFalse(wa.firma_valida(self.cfg, b"{}", "sha256=\xe9"))
        self.assertEqual(self.pedir("POST", "/webhook", b"{}", {"X-Hub-Signature-256": "sha256=\xe9"}), 401)

    def test_fallos_de_una_persona_no_bloquean_a_la_oficina(self):
        from urllib.parse import urlencode
        web.crear_usuario(self.con, "ana", "clave-segura-123")
        web.crear_usuario(self.con, "luis", "clave-segura-456")
        cab = {"Origin": self.cfg["url_publica"], "Content-Type": "application/x-www-form-urlencoded"}
        login = lambda u, c: self.pedir("POST", "/bandeja/login", urlencode({"usuario": u, "clave": c}), cab)  # noqa: E731
        for _ in range(5):
            self.assertEqual(login("ana", "mal"), 401)
        self.assertEqual(login("ana", "clave-segura-123"), 429)     # ana sí queda bloqueada
        self.assertEqual(login("luis", "clave-segura-456"), 303)    # luis, misma IP, entra
        for i in range(20):
            login(f"x{i}", "mal")                                   # muchos usuarios distintos desde la misma IP
        self.assertEqual(login("luis", "clave-segura-456"), 429)    # la IP sí se bloquea a los 20
