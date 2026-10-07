"""Etapa 1: webhook, cola, IA con guardrails, handoff y bandeja con login."""
import datetime as dt
import http.client
import json
import os
import socket
import threading
import unittest
from unittest import mock

from ayuda import Caso, ia_falsa, local, respuesta_claude
from rv import base, ia, motor, wa, web
from rv.__main__ import payload_falso


class Telefonos(unittest.TestCase):
    def test_normalizacion(self):
        n = base.normalizar_tel
        self.assertEqual(n("81 1234 5678"), "+528112345678")
        self.assertEqual(n("5218112345678"), "+528112345678")   # formato antiguo de móvil
        self.assertEqual(n("+52 (81) 1234-5678"), "+528112345678")
        self.assertEqual(n("12105551234"), "+12105551234")
        self.assertIsNone(n("123"))
        self.assertIsNone(n("52123"))


class Horarios(Caso):
    def test_abierto_y_proxima_apertura(self):
        cfg = self.cfg
        self.assertTrue(base.abierto(cfg, local(2026, 10, 6, 10, 0)))
        self.assertFalse(base.abierto(cfg, local(2026, 10, 6, 15, 0)))           # comida
        self.assertEqual(base.proxima_apertura(cfg, local(2026, 10, 6, 15, 0)), local(2026, 10, 6, 16, 0))
        # sábado 15:00 → domingo cerrado → lunes 9:00
        self.assertEqual(base.proxima_apertura(cfg, local(2026, 10, 10, 15, 0)), local(2026, 10, 12, 9, 0))
        # domingo 15 nov → lunes 16 nov es día cerrado → martes 17 nov
        self.assertEqual(base.proxima_apertura(cfg, local(2026, 11, 15, 12, 0)), local(2026, 11, 17, 9, 0))
        self.assertEqual(base.apertura_humana(cfg, local(2026, 10, 10, 15, 0)), "el lunes 12 de octubre a las 9:00")
        self.assertEqual(base.apertura_humana(cfg, local(2026, 10, 6, 7, 0)), "hoy a las 9:00")

    def test_ventana_de_envio(self):
        v = lambda *a: base.en_ventana_envio(self.cfg, local(*a))  # noqa: E731
        self.assertFalse(v(2026, 10, 6, 8, 59))
        self.assertTrue(v(2026, 10, 6, 9, 0))
        self.assertTrue(v(2026, 10, 6, 19, 59))
        self.assertFalse(v(2026, 10, 6, 20, 0))
        self.assertFalse(v(2026, 10, 11, 12, 0))   # domingo
        self.assertFalse(v(2026, 11, 16, 12, 0))   # día cerrado del config
        self.assertTrue(v(2026, 10, 10, 18, 0))    # sábado: el negocio cierra a las 14 pero la ventana es 9-20


class Webhook(Caso):
    def test_firma_invalida_se_rechaza(self):
        cuerpo = json.dumps(payload_falso("+528100000001", "hola")).encode()
        self.assertEqual(web.recibir_webhook(self.con, self.cfg, cuerpo, "sha256=00")[0], 401)
        self.assertEqual(web.recibir_webhook(self.con, self.cfg, cuerpo, None)[0], 401)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], 0)

    def test_reintento_de_meta_se_deduplica(self):
        cuerpo = json.dumps(payload_falso("+528100000001", "hola", msg_id="wamid.X1")).encode()
        firma = wa.firmar(wa.app_secret(self.cfg), cuerpo)
        self.assertEqual(web.recibir_webhook(self.con, self.cfg, cuerpo, firma), (200, 1))
        self.assertEqual(web.recibir_webhook(self.con, self.cfg, cuerpo, firma), (200, 0))
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM mensaje WHERE direccion='out'").fetchone()[0], 1)

    def test_estado_de_entrega_no_retrocede(self):
        [m] = self.escribir("hola")
        for st in ("read", "delivered"):
            motor.actualizar_estado(self.con, {"id": m["wa_id"], "status": st})
        self.assertEqual(self.con.execute("SELECT estado FROM mensaje WHERE id=?", (m["id"],)).fetchone()[0], "read")
        motor.actualizar_estado(self.con, {"id": m["wa_id"], "status": "failed", "errors": [{"code": 131049, "title": "x"}]})
        fila = self.con.execute("SELECT estado, error FROM mensaje WHERE id=?", (m["id"],)).fetchone()
        self.assertEqual((fila[0], fila[1]), ("failed", "131049 x"))

    def test_numero_de_otro_cliente_se_ignora(self):
        self.cfg["whatsapp"]["phone_number_id"] = "111"
        motor.procesar_item(self.con, self.cfg, {"tipo": "mensaje", "pnid": "222", "msg": {}})
        self.assertIsNone(self.contacto())

    def test_entrada_rota_no_detiene_la_cola(self):
        self.con.execute("INSERT INTO entrada (clave, payload, recibido) VALUES ('m:roto', '{\"tipo\":\"mensaje\"}', 'x')")
        self.escribir("hola")
        fila = self.con.execute("SELECT procesado, error FROM entrada WHERE clave='m:roto'").fetchone()
        self.assertIsNotNone(fila["procesado"])
        self.assertIn("KeyError", fila["error"])
        self.assertIsNotNone(self.contacto())


class Flujo(Caso):
    def test_equipo_y_dueno_excluidos(self):
        self.assertEqual(self.escribir("hola", de="+528122222222"), [])
        self.assertEqual(self.escribir("hola", de="5218111111111"), [])  # dueño en formato antiguo
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM contacto").fetchone()[0], 0)

    def test_baja_solo_con_mensaje_completo(self):
        self.escribir("Ya no me duele, gracias")
        self.assertIsNone(self.con.execute("SELECT 1 FROM optout").fetchone())
        [m] = self.escribir("¡BAJA!")
        self.assertEqual(m["texto"], self.cfg.msg["baja"])
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM optout WHERE telefono='+528100000001'").fetchone())

    def test_urgencia_medica_de_noche(self):
        self.t = local(2026, 10, 6, 23, 30)
        salida = self.escribir("tengo mucho dolor y sangrado")
        self.assertEqual(salida[0]["texto"], self.cfg.msg["emergencia"])
        self.assertIn("el miércoles 7 de octubre a las 9:00", salida[1]["texto"])
        avisos = [m for m in salida if m["plantilla"] == "aviso_equipo"]
        self.assertEqual(len(avisos), 2)          # dueño + equipo, de inmediato aunque esté cerrado
        self.assertEqual(self.contacto()["estado"], "humano")

    def test_handoff_fuera_de_horario_avisa_a_la_apertura(self):
        self.t = local(2026, 10, 10, 15, 0)   # sábado después del cierre
        salida = self.escribir("quiero hablar con una persona")
        self.assertEqual([m["plantilla"] for m in salida], [None])
        self.assertIn("el lunes 12 de octubre a las 9:00", salida[0]["texto"])
        c = self.contacto()
        self.assertEqual((c["estado"], c["aviso_pendiente"]), ("humano", 1))

    def test_en_humano_el_bot_calla(self):
        self.escribir("quiero hablar con una persona")
        self.assertEqual(self.escribir("¿cuánto cuesta la limpieza?"), [])

    def test_no_texto_pasa_a_humano(self):
        salida = self.escribir(None, tipo="audio")
        self.assertEqual(salida[0]["texto"], self.cfg.msg["no_texto"])
        self.assertEqual(self.contacto()["handoff_motivo"], "no_texto")

    def test_primer_contacto_fuera_de_horario_queda_registrado(self):
        self.t = local(2026, 10, 6, 22, 0)
        self.escribir("hola")
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM evento WHERE tipo='fuera_horario'").fetchone())


class IA(Caso):
    def setUp(self):
        super().setUp()
        os.environ["ANTHROPIC_API_KEY"] = "clave-falsa"   # fuerza el camino real (HTTP sustituido)

    def test_respuesta_normal_registra_costo_e_inicia_seguimiento(self):
        with ia_falsa([respuesta_claude(texto="La limpieza cuesta $800 MXN.", intencion="precio", servicio_id="limpieza")]):
            [m] = self.escribir("¿cuánto cuesta la limpieza?")
        self.assertEqual(m["texto"], "La limpieza cuesta $800 MXN.")
        c = self.contacto()
        self.assertEqual((c["seg_activo"], c["seg_servicio"]), (1, "limpieza"))
        uso = self.con.execute("SELECT * FROM ia_uso").fetchone()
        self.assertEqual(uso["costo_micro_usd"], 1000 * 1 + 100 * 5)

    def test_monto_inventado_se_bloquea(self):
        with ia_falsa([respuesta_claude(texto="Te la dejo en $650 MXN.", intencion="precio")]):
            salida = self.escribir("¿me haces descuento?")
        self.assertNotIn("650", salida[0]["texto"])
        self.assertEqual(self.contacto()["handoff_motivo"], "monto_no_config:650")

    def test_dosis_se_bloquea(self):
        with ia_falsa([respuesta_claude(texto="Toma ibuprofeno de 400 mg cada 8 horas.")]):
            self.escribir("¿qué tomo para el dolor de muela?")
        self.assertEqual(self.contacto()["handoff_motivo"], "posible_dosis")

    def test_refusal_sin_herramienta_y_error_pasan_a_humano(self):
        sin_tool = {"stop_reason": "end_turn", "usage": {}, "content": [{"type": "text", "text": "hola"}]}
        casos = [respuesta_claude(stop="refusal"), sin_tool, respuesta_claude(accion="volar")]
        for i, r in enumerate(casos):
            with ia_falsa([r]):
                self.escribir("hola", de=f"+52810000010{i}")
            self.assertEqual(self.contacto(f"+52810000010{i}")["handoff_motivo"], "ia_error")
        with mock.patch("rv.ia._llamar_claude", side_effect=ia.IAError("timeout")):
            self.escribir("hola", de="+528100000199")
        self.assertEqual(self.contacto("+528100000199")["handoff_motivo"], "ia_error")

    def test_tope_mensual_pasa_todo_a_humano_y_avisa_una_vez(self):
        self.con.execute("INSERT INTO ia_uso (creado, modelo, tokens_entrada, tokens_salida, costo_micro_usd) "
                         "VALUES (?, 'x', 0, 0, ?)", (base.iso(self.t), 30_000_000))
        with ia_falsa([]) as llamada:
            s1 = self.escribir("hola", de="+528100000201")
            s2 = self.escribir("hola", de="+528100000202")
        llamada.assert_not_called()
        sistema = lambda s: [m["telefono"] for m in s if "| el sistema |" in m["texto"]]  # noqa: E731
        self.assertEqual(sistema(s1), ["+528111111111"])   # aviso del tope: solo al dueño, una vez al mes
        self.assertEqual(sistema(s2), [])
        self.assertEqual(self.contacto("+528100000202")["handoff_motivo"], "tope_ia")

    def test_contexto_empieza_en_cliente_y_trae_datos_del_sistema(self):
        with ia_falsa([respuesta_claude(texto="¡Hola!"), respuesta_claude(texto="Claro.")]) as llamada:
            self.escribir("hola")
            self.escribir("gracias")
        mensajes = llamada.call_args_list[1].args[1]
        self.assertEqual([m["role"] for m in mensajes], ["user", "assistant", "user"])
        self.assertIn("Datos del sistema", mensajes[-1]["content"][0]["text"])
        self.assertEqual(mensajes[-1]["content"][1]["text"], "gracias")

    def test_prompt_solo_con_datos_del_config(self):
        p = ia.prompt_sistema(self.cfg)
        self.assertIn("$3,500 MXN", p)
        self.assertIn("estacionamiento gratuito", p)
        self.assertIn("Nunca des diagnósticos", p)


class Bandeja(Caso):
    def setUp(self):
        super().setUp()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            puerto = s.getsockname()[1]
        self.cfg["url_publica"] = f"http://127.0.0.1:{puerto}"
        self.srv = web.crear_servidor(self.cfg, puerto=puerto)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        web.crear_usuario(self.con, "ana", "clave-segura-123")

    def pedir(self, metodo, ruta, form=None, cookie=None, origen=True):
        h = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        cab = {"Content-Type": "application/x-www-form-urlencoded"}
        if origen:
            cab["Origin"] = f"http://127.0.0.1:{self.srv.server_address[1]}"
        if cookie:
            cab["Cookie"] = cookie
        from urllib.parse import urlencode
        h.request(metodo, ruta, body=urlencode(form or {}) if metodo == "POST" else None, headers=cab)
        r = h.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode()

    def entrar(self):
        st, cab, _ = self.pedir("POST", "/bandeja/login", {"usuario": "ana", "clave": "clave-segura-123"})
        self.assertEqual(st, 303)
        self.assertIn("HttpOnly", cab["Set-Cookie"])
        self.assertIn("SameSite=Strict", cab["Set-Cookie"])
        return cab["Set-Cookie"].split(";")[0]

    def test_verificacion_de_webhook(self):
        st, _, cuerpo = self.pedir("GET", "/webhook?hub.mode=subscribe&hub.verify_token=verificar-prueba&hub.challenge=123")
        self.assertEqual((st, cuerpo), (200, "123"))
        self.assertEqual(self.pedir("GET", "/webhook?hub.mode=subscribe&hub.verify_token=mal&hub.challenge=1")[0], 403)

    def test_sin_sesion_redirige_al_login(self):
        st, cab, _ = self.pedir("GET", "/bandeja")
        self.assertEqual((st, cab["Location"]), (303, "/bandeja/login"))

    def test_bloqueo_tras_5_fallos(self):
        for _ in range(5):
            self.assertEqual(self.pedir("POST", "/bandeja/login", {"usuario": "ana", "clave": "mala"})[0], 401)
        self.assertEqual(self.pedir("POST", "/bandeja/login", {"usuario": "ana", "clave": "clave-segura-123"})[0], 429)

    def test_sesion_expira_a_las_12_horas(self):
        cookie = self.entrar()
        self.assertEqual(self.pedir("GET", "/bandeja", cookie=cookie)[0], 200)
        self.t = self.t + dt.timedelta(hours=12, minutes=1)
        self.assertEqual(self.pedir("GET", "/bandeja", cookie=cookie)[0], 303)

    def test_post_sin_origen_se_rechaza(self):
        cookie = self.entrar()
        self.assertEqual(self.pedir("POST", "/bandeja/c/1/tomar", cookie=cookie, origen=False)[0], 403)

    def test_tomar_responder_y_devolver(self):
        self.escribir("quiero hablar con una persona")
        cid = self.contacto()["id"]
        cookie = self.entrar()
        st, _, cuerpo = self.pedir("GET", "/bandeja", cookie=cookie)
        self.assertEqual(st, 200)
        self.assertIn("Cliente", cuerpo)
        self.pedir("POST", f"/bandeja/c/{cid}/tomar", cookie=cookie)
        self.assertEqual(self.contacto()["asignado_a"], "ana")
        st, cab, _ = self.pedir("POST", f"/bandeja/c/{cid}/responder", {"texto": "Hola, soy Ana <b>"}, cookie=cookie)
        self.assertEqual(st, 303)
        m = self.con.execute("SELECT * FROM mensaje WHERE autor='humano:ana'").fetchone()
        self.assertEqual(m["texto"], "Hola, soy Ana <b>")
        _, _, pag = self.pedir("GET", f"/bandeja/c/{cid}", cookie=cookie)
        self.assertIn("Hola, soy Ana &lt;b&gt;", pag)   # escapado
        self.pedir("POST", f"/bandeja/c/{cid}/devolver", cookie=cookie)
        self.assertEqual(self.contacto()["estado"], "bot")

    def test_ventana_cerrada_solo_permite_plantilla(self):
        self.escribir("quiero hablar con una persona")
        cid = self.contacto()["id"]
        self.t = self.t.replace(day=8)   # 2 días después, miércoles 10:00
        cookie = self.entrar()
        _, cab, _ = self.pedir("POST", f"/bandeja/c/{cid}/responder", {"texto": "hola"}, cookie=cookie)
        self.assertIn("error=", cab["Location"])
        self.pedir("POST", f"/bandeja/c/{cid}/retomar", cookie=cookie)
        m = self.con.execute("SELECT * FROM mensaje WHERE autor='humano:ana'").fetchone()
        self.assertEqual(m["plantilla"], "retomar_contacto")


class Limitador(unittest.TestCase):
    def test_bloqueo_expira(self):
        reloj = [0.0]
        lim = web.Limitador(reloj=lambda: reloj[0])
        for _ in range(5):
            lim.fallo("u:x")
        self.assertTrue(lim.bloqueado("u:x"))
        reloj[0] = 901
        self.assertFalse(lim.bloqueado("u:x"))
        lim.fallo("u:x")
        self.assertFalse(lim.bloqueado("u:x"))   # el conteo empezó de nuevo


if __name__ == "__main__":
    unittest.main()
