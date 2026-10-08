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
