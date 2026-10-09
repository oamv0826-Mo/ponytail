"""Integraciones externas (correo, Microsoft 365, pagos): siempre con respuestas falsas, nunca contra servicios reales."""
import base64
import json
import os
from unittest import mock

from ayuda import Caso
from rv import base, correo, motor, ms, verificar
from rv.__main__ import main


def jwt_falso(roles):
    carga = base64.urlsafe_b64encode(json.dumps({"roles": roles}).encode()).rstrip(b"=").decode()
    return f"x.{carga}.y"


class ConCorreo(Caso):
    CONFIG = {"email": {"proveedor": "smtp", "remitente": "agora@clinica.mx", "avisos_a": ["dueno@clinica.mx"],
                        "smtp": {"host": "smtp.gmail.com", "puerto": 587, "seguridad": "starttls"}}}

    def setUp(self):
        super().setUp()
        ms._token.update(valor=None, expira=0)

    def log(self):
        p = self.dir / "envios-prueba.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""


class CorreoSaliente(ConCorreo):
    def test_aviso_al_equipo_tambien_llega_por_correo(self):
        cid = self.con.execute("INSERT INTO contacto (telefono, wa_id, nombre, origen, creado) VALUES "
                               "('+528100000001', '528100000001', 'Ana', 'entrante', ?)", (base.iso(self.t),)).lastrowid
        motor.handoff(self.con, self.cfg, cid, "pidio_humano:asesor")
        lineas = [x for x in self.log().splitlines() if "\tcorreo:" in x]
        self.assertEqual(len(lineas), 1)
        self.assertIn("correo:dueno@clinica.mx", lineas[0])
        self.assertIn("Ana (+528100000001)", lineas[0])   # mismo texto que la plantilla aviso_equipo

    def test_sin_correo_configurado_no_se_intenta(self):
        self.cfg["email"]["proveedor"] = ""
        self.assertEqual(correo.enviar(self.cfg, ["x@y.mx"], "a", "b"), (None, "correo no configurado"))

    def test_smtp_cifra_antes_de_la_clave_y_no_lanza_si_falla(self):
        self.cfg["modo_prueba"] = False
        os.environ["SMTP_CLAVE"] = "clave-app"
        with mock.patch("smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = smtp.return_value   # como smtplib: with devuelve la conexión
            mid, error = correo.enviar(self.cfg, ["dueno@clinica.mx"], "Asunto", "Hola")
        s = smtp.return_value
        self.assertIsNone(error)
        self.assertEqual([c[0] for c in s.method_calls[:2]], ["starttls", "login"])
        s.login.assert_called_once_with("agora@clinica.mx", "clave-app")
        enviado = s.send_message.call_args.args[0]
        self.assertEqual((enviado["To"], enviado["Subject"], enviado["Message-ID"]), ("dueno@clinica.mx", "Asunto", mid))
        with mock.patch("smtplib.SMTP", side_effect=OSError("sin red")):
            self.assertEqual(correo.enviar(self.cfg, ["dueno@clinica.mx"], "A", "B"), (None, "sin red"))

    def test_cabeceras_con_salto_de_linea_no_rompen_ni_inyectan(self):
        mid, error = correo.enviar(self.cfg, ["dueno@clinica.mx"], "Aviso\r\nBcc: x@y.mx", "texto")
        self.assertIsNone(error)
        self.assertIn("\tAviso Bcc: x@y.mx | texto", self.log())
        self.assertEqual(correo.enviar(self.cfg, ["a@b.mx\r\nBcc: x@y.mx"], "A", "B")[0], None)   # error, sin excepción

    def test_microsoft_365_por_graph(self):
        self.cfg["modo_prueba"] = False
        self.cfg["email"]["proveedor"] = "microsoft"
        with mock.patch.object(ms, "graph", return_value={}) as g:
            _, error = correo.enviar(self.cfg, ["a@b.mx", "c@d.mx"], "Reporte", "texto")
        self.assertIsNone(error)
        metodo, ruta, cuerpo = g.call_args.args
        self.assertEqual((metodo, ruta), ("POST", "users/agora@clinica.mx/sendMail"))
        self.assertEqual(cuerpo["message"]["toRecipients"][1], {"emailAddress": {"address": "c@d.mx"}})
        self.assertEqual(cuerpo["message"]["body"], {"contentType": "Text", "content": "texto\n"})

    def test_token_de_microsoft_y_permisos(self):
        os.environ.update(MS_TENANT_ID="t", MS_CLIENT_ID="c", MS_CLIENT_SECRET="s")
        with mock.patch.object(ms, "_pedir", return_value={"access_token": jwt_falso(["Mail.Send"]), "expires_in": 3599}) as p:
            self.assertEqual(ms.permisos(), {"Mail.Send"})
            ms.permisos()   # segundo uso: del caché
        self.assertEqual(p.call_count, 1)
        url, datos = p.call_args.args[1], p.call_args.args[2]
        self.assertEqual(url, "https://login.microsoftonline.com/t/oauth2/v2.0/token")
        self.assertEqual((datos["grant_type"], datos["scope"]), ("client_credentials", "https://graph.microsoft.com/.default"))

    def test_verificar_avisa_si_falta_mail_send(self):
        self.cfg["email"]["proveedor"] = "microsoft"
        with mock.patch.object(ms, "permisos", return_value={"Calendars.ReadWrite"}):
            with self.assertRaisesRegex(RuntimeError, "Mail.Send"):
                verificar.probar_correo(self.cfg)

    def test_reporte_se_manda_por_correo(self):
        main(["--cliente", str(self.dir), "reporte", "2026-09", "--enviar"])
        self.assertIn("correo:dueno@clinica.mx\tsistema\tReporte de 2026-09", self.log())

    def test_config_de_correo_invalida(self):
        for malo in ({"proveedor": "yahoo"}, {"proveedor": "smtp", "remitente": "sin-arroba"},
                     {"proveedor": "smtp", "smtp": {"host": "", "puerto": 587, "seguridad": "starttls"}}):
            self.cfg["email"] = base._fusionar(self.CONFIG["email"], malo) | {"avisos_a": []}
            with self.assertRaises(ValueError):
                self.cfg.validar()


class CalendarioMicrosoft(Caso):
    CONFIG = {"agenda": {"proveedor": "microsoft", "calendar_id": "citas@clinica.mx"}}

    def test_disponibilidad_eventos_y_errores(self):
        from rv import agenda
        t0 = self.t
        respuesta = {"value": [{"scheduleId": "citas@clinica.mx", "scheduleItems": [
            {"status": "busy", "start": {"dateTime": "2026-10-06T18:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-10-06T19:00:00.0000000", "timeZone": "UTC"}},
            {"status": "tentative", "start": {"dateTime": "2026-10-06T20:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-10-06T20:30:00.0000000", "timeZone": "UTC"}},
            {"status": "free", "start": {"dateTime": "2026-10-06T21:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-10-06T22:00:00.0000000", "timeZone": "UTC"}}]}]}
        with mock.patch.object(ms, "graph", return_value=respuesta) as g:
            ocupado = agenda.ocupado_externo(self.cfg, t0, t0 + agenda.dt.timedelta(days=1))
        self.assertEqual([(a.hour, b.hour, b.minute) for a, b in ocupado], [(18, 19, 0), (20, 20, 30)])
        metodo, ruta, cuerpo = g.call_args.args
        self.assertEqual((metodo, ruta), ("POST", "users/citas@clinica.mx/calendar/getSchedule"))
        self.assertEqual(cuerpo["startTime"], {"dateTime": "2026-10-06T16:00:00", "timeZone": "UTC"})
        with mock.patch.object(ms, "graph", return_value={"value": [{"error": {"message": "no existe"}}]}), \
                self.assertRaises(agenda.AgendaError):
            agenda.ocupado_externo(self.cfg, t0, t0 + agenda.dt.timedelta(hours=1))
        with mock.patch.object(ms, "graph", side_effect=ms.MSError("Microsoft 403")), self.assertRaises(agenda.AgendaError):
            agenda.ocupado_externo(self.cfg, t0, t0 + agenda.dt.timedelta(hours=1))   # la agenda lo convierte en handoff
        with mock.patch.object(ms, "graph", return_value={"id": "AAMk="}) as g:
            self.assertEqual(agenda.crear_evento(self.cfg, t0, t0 + agenda.dt.timedelta(hours=1), "Limpieza", "d"), "AAMk=")
            agenda.borrar_evento(self.cfg, "AAMk=")
        crear, borrar = g.call_args_list
        self.assertEqual(crear.args[1], "users/citas@clinica.mx/calendar/events")
        self.assertEqual(crear.args[2]["end"], {"dateTime": "2026-10-06T17:00:00", "timeZone": "UTC"})
        self.assertEqual(borrar.args[:2], ("DELETE", "users/citas@clinica.mx/events/AAMk%3D"))

    def test_verificar_y_config(self):
        self.cfg["modo_prueba"] = False
        with mock.patch.object(ms, "permisos", return_value={"Mail.Send"}), \
                mock.patch("rv.wa.graph_get", return_value={}), \
                mock.patch.object(verificar, "_anthropic_modelo", return_value={"id": "x"}):
            errores = [m for n, m in verificar.remotos(self.con, self.cfg) if n == "ERROR"]
        self.assertTrue(any("Calendars.ReadWrite" in m for m in errores))
        self.cfg["agenda"]["calendar_id"] = "AAMkAGI2"
        with self.assertRaisesRegex(ValueError, "correo del buzón"):
            self.cfg.validar()


CORREO_CLIENTE = (b"Authentication-Results: mx.google.com; dkim=pass header.i=@gmail.com; spf=pass; dmarc=pass "
                  b"(p=NONE) header.from=gmail.com\r\nFrom: Ana Ruiz <ana@gmail.com>\r\nTo: citas@clinica.mx\r\nSubject: Precio\r\n"
                  b"Message-ID: <abc123@mail.gmail.com>\r\nDate: Tue, 06 Oct 2026 09:58:00 -0600\r\n"
                  b"MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=XX\r\n\r\n"
                  b"--XX\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
                  b"Hola, \xc2\xbfcu\xc3\xa1nto cuesta la limpieza dental?\r\n\r\n"
                  b"El lun, 5 oct 2026 a las 10:00, Cl\xc3\xadnica <citas@clinica.mx> escribi\xc3\xb3:\r\n> Hola Ana\r\n"
                  b"--XX\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Hola</p>\r\n--XX--\r\n")


class CanalCorreo(ConCorreo):
    CONFIG = {"email": ConCorreo.CONFIG["email"] | {"remitente": "citas@clinica.mx",
                                                     "entrada": {"activa": True, "imap": {"host": "imap.gmail.com"}}}}

    def imap_falso(self, correos):
        imap = mock.MagicMock()
        uids = [str(i + 1).encode() for i in range(len(correos))]

        def uid(cmd, *a):
            if cmd == "SEARCH":
                return "OK", [b" ".join(uids)]
            if cmd == "FETCH":
                return "OK", [(a[0] + b" (BODY[] {9}", correos[int(a[0]) - 1]), b")"]
            return "OK", [b""]
        imap.uid.side_effect = uid
        return imap

    def leer(self, correos):
        imap = self.imap_falso(correos)
        orden = []
        with mock.patch("imaplib.IMAP4_SSL", return_value=imap):
            n = correo.leer_entrada(self.cfg, lambda item: orden.append(("guardar", motor.encolar_correo(self.con, item))))
        motor.procesar_pendientes(self.con, self.cfg)
        return n, imap, orden

    def test_correo_sin_dmarc_no_lo_contesta_el_bot(self):
        sin = CORREO_CLIENTE.split(b"\r\n", 1)[1].replace(b"Subject: Precio", b"Subject: cancelar mi cita")
        falso = b"Authentication-Results: mx.google.com; dmarc=pass\r\n"
        casos = (sin,                                                                    # sin encabezado
                 b"Authentication-Results: mx.google.com; dmarc=fail\r\n" + falso + sin,  # el falso va abajo
                 falso.replace(b"mx.google.com", b"otro.mx") + sin)                       # no es de nuestro servidor
        for valor in ("mx.google.com; spf=pass (google.com: domain of dmarc=pass@evil.mx) smtp.mailfrom=evil.mx",
                      "mx.google.com.evil.mx; dmarc=pass header.from=gmail.com",
                      "mx.google.com; dmarc=pass header.from=evil.mx",
                      "mx.google.com; dmarc=fail header.from=gmail.com; x=y dmarc=pass header.from=gmail.com",
                      "mx.google.com; spf=pass (a) b) dmarc=pass header.from=gmail.com",
                      'mx.google.com; spf=pass smtp.mailfrom="x;dmarc=pass header.from=gmail.com"@evil.mx',
                      "mx.google.com; spf=pass (a\\) dmarc=pass header.from=gmail.com",
                      "mx.google.com; dmarc=pass header.from=gmail.com; dmarc=fail header.from=gmail.com",
                      "mx.google.com; dmarc=pass header.from=gmail.com header.from=evil.mx"):
            self.assertFalse(correo.verificado(valor, "gmail.com", "mx.google.com"), valor)
        self.assertTrue(correo.verificado("mx.google.com; dmarc=pass (p=NONE) header.from=Gmail.com", "gmail.com",
                                          "mx.google.com"))
        for crudo in casos:
            self.assertFalse(correo.de_mime(self.cfg, crudo)["verificado"])
        self.leer([sin])
        c = self.con.execute("SELECT * FROM contacto WHERE email='ana@gmail.com'").fetchone()
        self.assertEqual((c["estado"], c["handoff_motivo"]), ("humano", "correo_sin_verificar"))
        self.assertNotIn("correo:ana@gmail.com", self.log())   # nada al remitente (podría ser un tercero)
        self.assertIn("correo sin verificar", self.log())      # el equipo sí se entera

    def test_un_correo_ilegible_no_bloquea_a_los_demas(self):
        with mock.patch.object(correo, "de_mime", side_effect=[LookupError("charset raro"),
                                                               correo.de_mime(self.cfg, CORREO_CLIENTE)]):
            n, imap, _ = self.leer([b"basura", CORREO_CLIENTE])
        self.assertEqual(n, 2)
        self.assertEqual([c.args[0] for c in imap.uid.call_args_list].count("STORE"), 2)
        self.assertIn("correo:ana@gmail.com", self.log())

    def test_texto_util_quita_html_cita_y_firma(self):
        self.assertEqual(correo.texto_util("Hola\n\nOn Mon, Ana wrote:\n> viejo"), "Hola")
        self.assertEqual(correo.texto_util("<p>Hola &amp; gracias</p><style>p{}</style>", es_html=True), "Hola & gracias")
        self.assertEqual(correo.texto_util("<script>" * 50000 + "hola", es_html=True), "")   # sin cuelgue: parser, no regex
        self.assertEqual(correo.texto_util("Quiero cita\n-- \nAna Ruiz\nGerente"), "Quiero cita")

    def test_no_contesta_robots_listas_ni_al_propio_buzon(self):
        normal = {"From": "x"}
        self.assertIsNone(correo.ignorar(self.cfg, "ana@gmail.com", normal))
        for de, cab in (("no-reply@banco.mx", normal), ("ana@gmail.com", {"Auto-Submitted": "auto-replied"}),
                        ("promo@tienda.mx", {"List-Unsubscribe": "<mailto:x>"}), ("citas@clinica.mx", normal),
                        ("dueno@clinica.mx", normal), ("ana@gmail.com", {"Precedence": "bulk"})):
            self.assertIsNotNone(correo.ignorar(self.cfg, de, cab), de)

    def test_correo_por_imap_se_contesta_en_el_mismo_hilo(self):
        n, imap, orden = self.leer([CORREO_CLIENTE])
        self.assertEqual(n, 1)
        cmds = [c.args[0] for c in imap.uid.call_args_list]
        self.assertEqual(cmds, ["SEARCH", "FETCH", "STORE"])   # leído solo después de guardarlo en la cola
        self.assertIn("BODY.PEEK[]", imap.uid.call_args_list[1].args[2])
        c = self.con.execute("SELECT * FROM contacto WHERE email='ana@gmail.com'").fetchone()
        self.assertEqual((c["telefono"], c["nombre"]), ("ana@gmail.com", "Ana Ruiz"))
        entrante = self.con.execute("SELECT texto FROM mensaje WHERE direccion='in'").fetchone()["texto"]
        self.assertEqual(entrante, "Hola, ¿cuánto cuesta la limpieza dental?")   # sin el historial citado
        salida = [x for x in self.log().splitlines() if "correo:ana@gmail.com" in x]
        self.assertEqual(len(salida), 1)
        self.assertIn("\tRe: Precio | ", salida[0])
        respuesta = self.con.execute("SELECT * FROM mensaje WHERE direccion='out' AND contacto_id=?", (c["id"],)).fetchone()
        self.assertEqual(respuesta["estado"], "prueba")

    def test_el_mismo_correo_dos_veces_se_contesta_una(self):
        self.leer([CORREO_CLIENTE])
        self.leer([CORREO_CLIENTE])
        self.assertEqual(len([x for x in self.log().splitlines() if "correo:ana@gmail.com" in x]), 1)

    def test_respuesta_automatica_no_crea_contacto_ni_se_contesta(self):
        auto = CORREO_CLIENTE.replace(b"Subject: Precio", b"Auto-Submitted: auto-replied\r\nSubject: Fuera de oficina")
        self.leer([auto])
        self.assertIsNone(self.con.execute("SELECT 1 FROM contacto").fetchone())
        self.assertNotIn("correo:ana@gmail.com", self.log())

    def test_microsoft_365_lee_marca_leido_y_contesta_con_reply(self):
        self.cfg["email"]["proveedor"] = "microsoft"
        g = {"id": "AAMk1", "internetMessageId": "<m1@outlook.com>", "subject": "Cita", "receivedDateTime":
             "2026-10-06T15:58:00Z", "from": {"emailAddress": {"name": "Luis", "address": "Luis@Outlook.com"}},
             "body": {"contentType": "text", "content": "Quiero hablar con una persona"}, "internetMessageHeaders": [
                 {"name": "Authentication-Results", "value": "spf=pass (sender IP is 1.2.3.4) smtp.mailfrom=outlook.com; "
                  "dkim=pass header.d=outlook.com;dmarc=pass action=none header.from=outlook.com;compauth=pass reason=100"},
                 {"name": "Reply-To", "value": "atacante@x.mx"}]}
        with mock.patch.object(ms, "graph", return_value={"value": [g]}) as graph:
            correo.leer_entrada(self.cfg, lambda item: motor.encolar_correo(self.con, item))
        get, patch = graph.call_args_list
        self.assertIn("isRead%20eq%20false", get.args[1])
        self.assertEqual(get.kwargs["cabeceras"], {"Prefer": 'outlook.body-content-type="text"'})
        self.assertEqual(patch.args, ("PATCH", "users/citas@clinica.mx/messages/AAMk1", {"isRead": True}))
        self.cfg["modo_prueba"] = False
        with mock.patch.object(ms, "graph", return_value={}) as graph, mock.patch("rv.wa._graph", return_value={
                "messages": [{"id": "wamid.x"}]}):
            motor.procesar_pendientes(self.con, self.cfg)
        c = self.con.execute("SELECT * FROM contacto WHERE email='luis@outlook.com'").fetchone()
        self.assertEqual(c["estado"], "humano")
        envio = [x for x in graph.call_args_list if x.args[1].endswith("/sendMail")][0]   # nunca /reply: usa el Reply-To
        self.assertEqual(envio.args[2]["message"]["toRecipients"], [{"emailAddress": {"address": "luis@outlook.com"}}])
        self.assertEqual(envio.args[2]["message"]["subject"], "Re: Cita")

    def test_recordatorio_si_seguimiento_no(self):
        from rv import tick
        self.leer([CORREO_CLIENTE])
        c = self.con.execute("SELECT * FROM contacto WHERE email='ana@gmail.com'").fetchone()
        self.assertFalse(tick.puede_proactivo(self.con, self.cfg, c))
        self.assertTrue(tick.puede_proactivo(self.con, self.cfg, c, por_correo=True))
        inicio = self.t + agenda_dt().timedelta(days=2)
        self.con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, estado, creado, creado_por) VALUES "
                         "(?,?,?,?,?,?,?)", (c["id"], "limpieza", base.iso(inicio), base.iso(inicio + agenda_dt().timedelta(hours=1)),
                                             "agendada", base.iso(self.t), "bot"))
        self.t = inicio - agenda_dt().timedelta(hours=23)
        self.assertEqual(tick.recordatorios(self.con, self.cfg, self.t), 1)
        self.assertIn("\tRe: Precio | ", self.log().splitlines()[-1])   # por correo, en su hilo
        self.assertEqual(self.con.execute("SELECT rec24 FROM cita").fetchone()[0], base.iso(self.t))
        self.con.execute("UPDATE contacto SET seg_activo=1, seg_inicio=? WHERE id=?",
                         (base.iso(self.t - agenda_dt().timedelta(days=3)), c["id"]))
        tick.seguimiento(self.con, self.cfg, self.t)
        self.assertEqual(self.con.execute("SELECT seg_activo FROM contacto WHERE id=?", (c["id"],)).fetchone()[0], 0)

    def test_bandeja_responde_por_correo(self):
        from rv import web
        self.leer([CORREO_CLIENTE])
        self.con.execute("UPDATE contacto SET ultimo_entrante='2026-09-01T10:00:00Z'")   # por correo no hay ventana de 24 h
        c = self.con.execute("SELECT * FROM contacto WHERE email='ana@gmail.com'").fetchone()
        self.assertIsNone(web.accion_conversacion(self.con, self.cfg, c, "ana", "responder", {"texto": ["Te esperamos"]}))
        self.assertIn("Re: Precio | Te esperamos", self.log())
        self.assertIn("por correo, en el mismo hilo", web.html_conversacion(self.con, self.cfg, c, "ana"))


def agenda_dt():
    import datetime
    return datetime


class Pagos(Caso):
    CONFIG = {"pagos": {"stripe": {"activo": True}, "mercadopago": {"activo": True}}}

    def setUp(self):
        super().setUp()
        os.environ.update(STRIPE_WEBHOOK_SECRET="whsec_prueba", MP_WEBHOOK_SECRET="mp_secreto", MP_ACCESS_TOKEN="APP_USR-x")
        self.cid = self.con.execute("INSERT INTO contacto (telefono, wa_id, nombre, creado) VALUES "
                                    "('+528100000001', '528100000001', 'Ana', ?)", (base.iso(self.t),)).lastrowid

    def stripe(self, telefono="+52 81 0000 0001", monto=125050, estado="paid", moneda="mxn", tipo="checkout.session.completed"):
        from rv import pagos
        evento = {"id": f"evt_{monto}{telefono[-2:]}", "type": tipo, "data": {"object": {
            "id": "cs_1", "payment_status": estado, "amount_total": monto, "currency": moneda,
            "created": int(self.t.timestamp()), "customer_details": {"phone": telefono, "email": "ana@gmail.com", "name": "Ana"}}}}
        cuerpo = json.dumps(evento).encode()
        t = str(int(self.t.timestamp()))
        firma = f"t={t},v1={pagos._hmac('whsec_prueba', t.encode() + b'.' + cuerpo)}"
        return cuerpo, firma

    def test_firma_de_stripe(self):
        from rv import pagos
        cuerpo, firma = self.stripe()
        ahora = self.t.timestamp()
        self.assertTrue(pagos.firma_stripe_valida(cuerpo, firma, "whsec_prueba", ahora))
        self.assertTrue(pagos.firma_stripe_valida(cuerpo, firma.replace(",v1=", ",v1=viejo,v1="), "whsec_prueba", ahora))
        self.assertFalse(pagos.firma_stripe_valida(cuerpo + b" ", firma, "whsec_prueba", ahora))
        self.assertFalse(pagos.firma_stripe_valida(cuerpo, firma, "otro", ahora))
        self.assertFalse(pagos.firma_stripe_valida(cuerpo, firma, "whsec_prueba", ahora + 301))   # reenvío viejo
        self.assertFalse(pagos.firma_stripe_valida(cuerpo, "t=abc,v1=x", "whsec_prueba", ahora))
        self.assertFalse(pagos.firma_stripe_valida(cuerpo, firma, "", ahora))

    def test_firma_de_mercado_pago(self):
        from rv import pagos
        plantilla = b"id:abc123;request-id:req-1;ts:1742505638683;"
        firma = f"ts=1742505638683,v1={pagos._hmac('mp_secreto', plantilla)}"
        self.assertTrue(pagos.firma_mercadopago_valida(firma, "req-1", "ABC123", "mp_secreto"))   # id en minúsculas
        self.assertFalse(pagos.firma_mercadopago_valida(firma, "req-2", "ABC123", "mp_secreto"))
        self.assertFalse(pagos.firma_mercadopago_valida(firma, "req-1", "ABC123", ""))
        sin_request = f"ts=1,v1={pagos._hmac('mp_secreto', b'id:9;ts:1;')}"
        self.assertTrue(pagos.firma_mercadopago_valida(sin_request, None, "9", "mp_secreto"))   # se omite lo que falta

    def venta(self):
        return self.con.execute("SELECT * FROM venta").fetchall()

    def test_stripe_por_http_registra_la_venta(self):
        import http.client
        import socket
        import threading
        from rv import web
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            puerto = s.getsockname()[1]
        srv = web.crear_servidor(self.cfg, puerto=puerto)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        cuerpo, firma = self.stripe()
        for f, esperado in ((firma.replace("v1=", "v1=0"), 401), (firma, 200), (firma, 200)):   # inválida, válida, repetida
            h = http.client.HTTPConnection("127.0.0.1", puerto, timeout=5)
            h.request("POST", "/pagos/stripe", body=cuerpo, headers={"Stripe-Signature": f, "Content-Type": "application/json"})
            self.assertEqual(h.getresponse().status, esperado)
        motor.procesar_pendientes(self.con, self.cfg)
        [v] = self.venta()
        self.assertEqual((v["contacto_id"], v["monto_centavos"], v["registrado_por"]), (self.cid, 125050, "pago:stripe:cs_1"))

    def test_stripe_sin_pagar_otra_moneda_u_otro_evento_no_cuentan(self):
        from rv import pagos
        for kw in ({"estado": "unpaid"}, {"moneda": "usd"}):
            cuerpo, firma = self.stripe(**kw)
            self.assertEqual(pagos.recibir(self.con, self.cfg, "stripe", cuerpo, {"Stripe-Signature": firma}, {})[0], 200)
        cuerpo, firma = self.stripe(tipo="charge.refunded")
        self.assertEqual(pagos.recibir(self.con, self.cfg, "stripe", cuerpo, {"Stripe-Signature": firma}, {}),
                         (200, "evento ignorado"))
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual(self.venta(), [])
        self.cfg["pagos"]["stripe"]["activo"] = False
        self.assertEqual(pagos.recibir(self.con, self.cfg, "stripe", cuerpo, {"Stripe-Signature": firma}, {})[0], 404)

    def test_oxxo_se_registra_cuando_stripe_avisa_que_ya_se_pago(self):
        # OXXO y transferencias: checkout.session.completed llega sin pagar y el pago llega después en
        # checkout.session.async_payment_succeeded (docs.stripe.com/checkout/fulfillment)
        from rv import pagos
        for tipo, estado in (("checkout.session.completed", "unpaid"), ("checkout.session.async_payment_succeeded", "paid")):
            evento = json.loads(self.stripe(estado=estado, tipo=tipo)[0])
            evento["id"] = f"evt_{tipo}"
            cuerpo = json.dumps(evento).encode()
            t = str(int(self.t.timestamp()))
            firma = f"t={t},v1={pagos._hmac('whsec_prueba', t.encode() + b'.' + cuerpo)}"
            self.assertEqual(pagos.recibir(self.con, self.cfg, "stripe", cuerpo, {"Stripe-Signature": firma}, {}), (200, "ok"))
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual([v["monto_centavos"] for v in self.venta()], [125050])

    def test_pago_de_quien_no_es_cliente_queda_en_csv_para_importar(self):
        from rv import pagos
        cuerpo, firma = self.stripe(telefono="+52 55 9999 0000")
        evento = json.loads(cuerpo)
        evento["data"]["object"]["customer_details"]["name"] = "Ana\n+528100000001,2026-10-06,99999\n=HYPERLINK(1)"
        cuerpo = json.dumps(evento).encode()
        t = str(int(self.t.timestamp()))
        firma = f"t={t},v1={pagos._hmac('whsec_prueba', t.encode() + b'.' + cuerpo)}"
        pagos.recibir(self.con, self.cfg, "stripe", cuerpo, {"Stripe-Signature": firma}, {})
        motor.procesar_pendientes(self.con, self.cfg)
        self.assertEqual(self.venta(), [])
        lineas = (self.dir / "pagos-sin-contacto.csv").read_text().splitlines()
        self.assertEqual(len(lineas), 2)   # el nombre no mete filas
        self.assertEqual(lineas[0], "telefono,fecha,monto,email,nombre,proveedor,pago")
        self.assertEqual(lineas[1].split(",")[:3], ["+525599990000", "2026-10-06", "1250.50"])
        from rv import ventas
        r = ventas.importar_ventas(self.con, self.cfg, self.dir / "pagos-sin-contacto.csv")
        self.assertEqual(r["registradas"], 0)   # el teléfono no es cliente: nada se registra hasta corregirlo
        self.assertEqual(pagos._celda("=HYPERLINK(1)"), "'=HYPERLINK(1)")

    def test_mercado_pago_consulta_el_pago_y_busca_por_correo(self):
        from rv import pagos
        self.con.execute("UPDATE contacto SET email='ana@gmail.com' WHERE id=?", (self.cid,))
        firma = f"ts=1,v1={pagos._hmac('mp_secreto', b'id:777;request-id:r1;ts:1;')}"
        cab = {"x-signature": firma, "x-request-id": "r1"}
        self.assertEqual(pagos.recibir(self.con, self.cfg, "mercadopago", b"{}", cab, {"data.id": ["777"], "type": ["payment"]}),
                         (200, "ok"))
        pago = {"id": 777, "status": "approved", "transaction_amount": 980.0, "currency_id": "MXN",
                "date_approved": "2026-10-06T09:30:00.000-06:00", "payer": {"email": "Ana@Gmail.com", "phone": {}}}
        with mock.patch.object(pagos, "pago_mercadopago", return_value=pago) as consulta:
            motor.procesar_pendientes(self.con, self.cfg)
        consulta.assert_called_once_with("777")
        [v] = self.venta()
        self.assertEqual((v["contacto_id"], v["monto_centavos"], v["fecha"]), (self.cid, 98000, "2026-10-06"))
        self.assertEqual(pagos.recibir(self.con, self.cfg, "mercadopago", b"{}", cab | {"x-request-id": "otro"},
                                       {"data.id": ["777"], "type": ["payment"]})[0], 401)
        antes = self.con.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]
        pagos.recibir(self.con, self.cfg, "mercadopago", b"{}", cab, {"data.id": ["777"], "type": ["payment"]})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], antes)   # repetido: no se encola
        sin_id = {"x-signature": f"ts=1,v1={pagos._hmac('mp_secreto', b'id:777;ts:1;')}"}
        self.assertEqual(pagos.recibir(self.con, self.cfg, "mercadopago", b"{}", sin_id,
                                       {"data.id": ["777"], "type": ["payment"]})[0], 400)
