"""Etapa 2: agenda, propuestas, reservas sin doble cita, cancelar/reprogramar, Google Calendar (simulado)."""
import base64
import datetime as dt
import hashlib
import json
import os
import random
import unittest
from unittest import mock

from ayuda import Caso, local
from rv import agenda, base


# ---------- clave RSA de prueba generada en Python puro (sin openssl) ----------

def _primo(bits, rnd):
    while True:
        n = rnd.getrandbits(bits) | (1 << bits - 1) | 1
        if all(pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)):
            return n


def _der_entero(x):
    b = x.to_bytes((x.bit_length() + 8) // 8 or 1, "big")
    return _der(0x02, b)


def _der(tag, contenido):
    n = len(contenido)
    largo = bytes([n]) if n < 128 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag]) + largo + contenido


def clave_de_prueba(semilla=7):
    rnd = random.Random(semilla)
    e = 65537
    while True:
        p, q = _primo(512, rnd), _primo(512, rnd)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    n, d = p * q, pow(e, -1, phi)
    pkcs1 = _der(0x30, b"".join(_der_entero(x) for x in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
    algoritmo = _der(0x30, bytes.fromhex("06092a864886f70d010101") + b"\x05\x00")
    pkcs8 = _der(0x30, _der_entero(0) + algoritmo + _der(0x04, pkcs1))
    pem = "-----BEGIN PRIVATE KEY-----\n" + base64.encodebytes(pkcs8).decode() + "-----END PRIVATE KEY-----\n"
    return pem, n, e


class RSA(unittest.TestCase):
    def test_firma_rs256_verifica_con_la_clave_publica(self):
        pem, n, e = clave_de_prueba()
        msg = b"cabecera.datos"
        firma = agenda.firmar_rs256(pem, msg)
        em = pow(int.from_bytes(firma, "big"), e, n).to_bytes(len(firma), "big")
        self.assertTrue(em.startswith(b"\x00\x01\xff"))
        self.assertTrue(em.endswith(agenda._DIGEST_INFO_SHA256 + hashlib.sha256(msg).digest()))

    def test_jwt_de_cuenta_de_servicio(self):
        pem, n, e = clave_de_prueba()
        sa = {"client_email": "rv@proyecto.iam.gserviceaccount.com", "private_key": pem,
              "token_uri": "https://oauth2.googleapis.com/token"}
        cab, datos, firma = agenda.jwt_cuenta_servicio(sa, 1_800_000_000).split(".")
        relleno = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
        claims = json.loads(relleno(datos))
        self.assertEqual(json.loads(relleno(cab)), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual((claims["iss"], claims["exp"] - claims["iat"]), (sa["client_email"], 3600))
        self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/calendar")
        em = pow(int.from_bytes(relleno(firma), "big"), e, n)
        self.assertEqual(em.to_bytes(128, "big")[-32:], hashlib.sha256(f"{cab}.{datos}".encode()).digest())


class Horarios(Caso):
    # martes 6 oct 2026, 10:00. Horario: 9-14 y 16-19; anticipación 2 h; paso 30 min; margen 10 min.

    def test_propone_tres_separados_respetando_anticipacion_y_comida(self):
        slots = agenda.horarios_libres(self.con, self.cfg, "limpieza")
        self.assertEqual(slots, [local(2026, 10, 6, 12, 0), local(2026, 10, 6, 16, 0), local(2026, 10, 7, 9, 0)])

    def test_respeta_cita_existente_y_margen(self):
        self.escribir("hola")
        c = self.contacto()
        self.con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, creado, creado_por) VALUES "
                         "(?, 'limpieza', ?, ?, 'x', 'bot')", (c["id"], base.iso(local(2026, 10, 6, 12, 0)),
                                                             base.iso(local(2026, 10, 6, 12, 45))))
        # 12:45 + 10 min de margen → 13:00 es el primero; 13:00+45 = 13:45 cabe antes de las 14:00
        self.assertEqual(agenda.horarios_libres(self.con, self.cfg, "limpieza")[0], local(2026, 10, 6, 13, 0))

    def test_ocupado_en_google_se_respeta(self):
        busy = [(local(2026, 10, 6, 11, 0), local(2026, 10, 6, 14, 0))]
        with mock.patch.object(agenda, "ocupado_google", return_value=busy):
            self.assertEqual(agenda.horarios_libres(self.con, self.cfg, "limpieza")[0], local(2026, 10, 6, 16, 0))

    def test_sabado_tarde_salta_domingo(self):
        self.t = local(2026, 10, 10, 13, 0)
        self.assertEqual(agenda.horarios_libres(self.con, self.cfg, "blanqueamiento")[0], local(2026, 10, 12, 9, 0))


class Flujo(Caso):
    def test_agendar_elegir_y_confirmar(self):
        [m] = self.escribir("quiero agendar una limpieza")
        self.assertIn("1) martes 6 de octubre a las 12:00", m["texto"])
        [m] = self.escribir("la 2")
        self.assertIn("quedó para el martes 6 de octubre a las 16:00", m["texto"])
        cita = self.con.execute("SELECT * FROM cita").fetchone()
        self.assertEqual((cita["inicio"], cita["estado"]), (base.iso(local(2026, 10, 6, 16, 0)), "agendada"))
        self.assertIsNone(self.contacto()["propuesta"])

    def test_no_hay_doble_reserva(self):
        self.escribir("quiero agendar una limpieza", de="+528100000001")
        self.escribir("quiero agendar una limpieza", de="+528100000002")
        self.escribir("1", de="+528100000001")
        salida = self.escribir("1", de="+528100000002")
        self.assertEqual(salida[0]["texto"], self.cfg.msg["horario_ocupado"])
        self.assertIn("Para Limpieza dental tengo estos horarios", salida[1]["texto"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM cita").fetchone()[0], 1)

    def test_revisa_google_justo_antes_de_crear(self):
        self.escribir("quiero agendar una limpieza")
        ocupado = [(local(2026, 10, 6, 12, 0), local(2026, 10, 6, 13, 0))]   # alguien lo tomó en Google
        with mock.patch.object(agenda, "ocupado_google", return_value=ocupado):
            salida = self.escribir("1")
        self.assertEqual(salida[0]["texto"], self.cfg.msg["horario_ocupado"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM cita").fetchone()[0], 0)

    def test_cancelar_pide_confirmacion(self):
        self.escribir("quiero agendar una limpieza")
        self.escribir("3")                                     # mañana 9:00 (más de 2 h: cambio simple)
        [m] = self.escribir("quiero cancelar mi cita")
        self.assertIn("Responde SÍ", m["texto"])
        [m] = self.escribir("no")
        self.assertEqual(self.con.execute("SELECT estado FROM cita").fetchone()[0], "agendada")
        self.escribir("quiero cancelar mi cita")
        [m] = self.escribir("Sí")
        self.assertIn("quedó cancelada", m["texto"])
        self.assertEqual(self.con.execute("SELECT estado FROM cita").fetchone()[0], "cancelada")

    def test_reprogramar_crea_la_nueva_y_cancela_la_anterior(self):
        self.escribir("quiero agendar una limpieza")
        self.escribir("3")                                     # mañana 9:00
        [m] = self.escribir("necesito cambiar mi cita")
        self.assertNotIn("miércoles 7 de octubre a las 9:00", m["texto"])   # no ofrece el mismo horario
        self.escribir("1")
        estados = self.con.execute("SELECT estado, inicio FROM cita ORDER BY id").fetchall()
        self.assertEqual([x[0] for x in estados], ["cancelada", "agendada"])

    def test_cambio_complejo_pasa_a_humano(self):
        self.escribir("quiero agendar una limpieza")
        self.escribir("1")                                     # hoy 12:00
        self.t = local(2026, 10, 6, 10, 30)                    # faltan 1.5 h (no es más de 2 h)
        self.escribir("quiero cancelar mi cita")
        self.assertEqual(self.contacto()["handoff_motivo"], "cambio_cita_complejo")

    def test_sin_cita_cancelar_pasa_a_humano(self):
        self.escribir("quiero cancelar mi cita")
        self.assertEqual(self.contacto()["handoff_motivo"], "cambio_cita_complejo")

    def test_propuesta_vencida_ya_no_se_usa(self):
        self.escribir("quiero agendar una limpieza")
        self.t += dt.timedelta(hours=25)
        self.escribir("1")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM cita").fetchone()[0], 0)
        self.assertIsNone(self.contacto()["propuesta"])

    def test_sin_horarios_pasa_a_humano(self):
        self.cfg["agenda"]["dias_adelante"] = 0
        salida = self.escribir("quiero agendar una limpieza")
        self.assertEqual(salida[0]["texto"], self.cfg.msg["sin_horarios"])
        self.assertEqual(self.contacto()["handoff_motivo"], "sin_horarios")

    def test_agendar_detiene_seguimiento(self):
        self.escribir("¿cuánto cuesta la limpieza?")
        self.assertEqual(self.contacto()["seg_activo"], 1)
        self.escribir("quiero agendar una limpieza")
        self.escribir("1")
        self.assertEqual(self.contacto()["seg_activo"], 0)


class Google(Caso):
    CONFIG = {"agenda": {"proveedor": "google", "calendar_id": "negocio@group.calendar.google.com"}}

    def test_crea_y_borra_evento_en_el_calendario(self):
        llamadas = []

        def gcal(metodo, ruta, cuerpo=None):
            llamadas.append((metodo, ruta, cuerpo))
            if ruta == "freeBusy":
                return {"calendars": {"negocio@group.calendar.google.com": {"busy": [
                    {"start": "2026-10-06T18:00:00Z", "end": "2026-10-06T20:00:00Z"}]}}}   # 12:00-14:00 local
            return {"id": "ev123"} if metodo == "POST" else {}

        with mock.patch.object(agenda, "_gcal", side_effect=gcal):
            [m] = self.escribir("quiero agendar una limpieza")
            self.assertIn("1) martes 6 de octubre a las 16:00", m["texto"])
            self.escribir("1")
            self.escribir("quiero cancelar mi cita")
            self.escribir("sí")
        metodos = [(x[0], x[1].split("/")[0]) for x in llamadas]
        self.assertIn(("POST", "calendars"), metodos)
        self.assertIn(("DELETE", "calendars"), metodos)
        evento = next(x for x in llamadas if x[0] == "POST" and x[1].startswith("calendars"))
        self.assertIn("negocio%40group.calendar.google.com", evento[1])
        self.assertEqual(evento[2]["start"], {"dateTime": base.iso(local(2026, 10, 6, 16, 0))})
        self.assertEqual(self.con.execute("SELECT evento_id FROM cita").fetchone()[0], "ev123")

    def test_error_de_google_pasa_a_humano(self):
        with mock.patch.object(agenda, "_gcal", side_effect=agenda.AgendaError("Google 403")):
            self.escribir("quiero agendar una limpieza")
        self.assertEqual(self.contacto()["handoff_motivo"], "agenda_error")

    def test_token_usa_jwt_y_se_reutiliza(self):
        pem, _, _ = clave_de_prueba()
        sa = self.dir / "sa.json"
        sa.write_text(json.dumps({"client_email": "x@y", "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"}))
        os.environ["GOOGLE_SA_FILE"] = str(sa)
        agenda._token.update(valor=None, expira=0)
        with mock.patch.object(agenda, "_http", return_value={"access_token": "tok", "expires_in": 3600}) as http:
            self.assertEqual(agenda._token_google(), "tok")
            self.assertEqual(agenda._token_google(), "tok")
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.args[2]["grant_type"], "urn:ietf:params:oauth:grant-type:jwt-bearer")
        agenda._token.update(valor=None, expira=0)


class BandejaCitas(Caso):
    def test_agendar_desde_bandeja_y_marcar_asistio(self):
        self.escribir("quiero hablar con una persona")
        c = self.contacto()
        err = agenda.accion_agendar(self.con, self.cfg, c, "ana",
                                    {"servicio": ["valoracion"], "fecha": ["2026-10-07"], "hora": ["17:00"]})
        self.assertIsNone(err)
        cita = self.con.execute("SELECT * FROM cita").fetchone()
        self.assertEqual((cita["inicio"], cita["creado_por"]), (base.iso(local(2026, 10, 7, 17, 0)), "humano:ana"))
        ultimo = self.con.execute("SELECT texto FROM mensaje ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertIn("miércoles 7 de octubre a las 17:00", ultimo)
        # mismo horario otra vez → ocupado
        self.assertEqual(agenda.accion_agendar(self.con, self.cfg, c, "ana", {"servicio": ["valoracion"],
                         "fecha": ["2026-10-07"], "hora": ["17:00"]}), "Ese horario está ocupado.")
        pagina = agenda.pagina_citas(self.con, self.cfg, "ana", {})
        self.assertIn("Cliente", pagina)
        self.assertIn(">Asistió</button>", pagina)
        self.assertEqual(agenda.marcar_cita(self.con, self.cfg, "ana", {"cita": [str(cita["id"])], "estado": ["asistio"]}),
                         "/bandeja/citas")
        fila = self.con.execute("SELECT estado, asistio_en FROM cita").fetchone()
        self.assertEqual((fila[0], fila[1]), ("asistio", base.iso(self.t)))

    def test_agendar_con_ventana_cerrada_usa_plantilla(self):
        self.escribir("quiero hablar con una persona")
        self.t = local(2026, 10, 8, 10, 0)
        agenda.accion_agendar(self.con, self.cfg, self.contacto(), "ana",
                              {"servicio": ["valoracion"], "fecha": ["2026-10-09"], "hora": ["10:00"]})
        m = self.con.execute("SELECT plantilla FROM mensaje ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(m[0], "cita_confirmada")


if __name__ == "__main__":
    unittest.main()
