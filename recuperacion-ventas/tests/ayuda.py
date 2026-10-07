"""Utilidades de prueba: cliente temporal, reloj fijo y mensajes simulados."""
import contextlib
import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rv import base, motor, wa, web
from rv.__main__ import payload_falso

EJEMPLO = Path(__file__).resolve().parent.parent / "ejemplo" / "cliente.json"
MTY = dt.timezone(dt.timedelta(hours=-6))  # Monterrey no tiene horario de verano desde 2022


def local(*a):
    """Datetime en hora de Monterrey → UTC."""
    return dt.datetime(*a, tzinfo=MTY).astimezone(base.UTC)


class Caso(unittest.TestCase):
    """Cada prueba corre en una carpeta de cliente nueva con reloj fijo (martes 6 oct 2026, 10:00)."""
    AHORA = local(2026, 10, 6, 10, 0)
    CONFIG = {}

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        datos = json.loads(EJEMPLO.read_text(encoding="utf-8"))
        datos.update(self.CONFIG)
        (self.dir / "cliente.json").write_text(json.dumps(datos), encoding="utf-8")
        self.t = self.AHORA
        p = mock.patch.object(base, "ahora", lambda: self.t)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict("os.environ", {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for k in ("ANTHROPIC_API_KEY", "WA_APP_SECRET", "WA_TOKEN", "WA_VERIFY_TOKEN"):
            import os
            os.environ.pop(k, None)
        self.cfg = base.cargar_config(self.dir)
        self.con = base.abrir_db(self.cfg)
        self.addCleanup(self.con.close)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def escribir(self, texto, de="+528100000001", nombre="Cliente", tipo="text"):
        """Simula un mensaje entrante completo (firma → cola → motor). Devuelve los mensajes salientes nuevos."""
        ultimo = self.con.execute("SELECT COALESCE(MAX(id),0) FROM mensaje").fetchone()[0]
        cuerpo = json.dumps(payload_falso(de, texto, nombre, tipo)).encode()
        codigo, _ = web.recibir_webhook(self.con, self.cfg, cuerpo, wa.firmar(wa.app_secret(self.cfg), cuerpo))
        self.assertEqual(codigo, 200)
        motor.procesar_pendientes(self.con, self.cfg)
        return self.con.execute("SELECT * FROM mensaje WHERE id>? AND direccion='out' ORDER BY id", (ultimo,)).fetchall()

    def contacto(self, tel="+528100000001"):
        return self.con.execute("SELECT * FROM contacto WHERE telefono=?", (tel,)).fetchone()


@contextlib.contextmanager
def ia_falsa(salidas):
    """Sustituye la llamada HTTP a Claude por respuestas fijas (una por llamada)."""
    salidas = list(salidas)
    with mock.patch("rv.ia._llamar_claude", side_effect=lambda cfg, msgs: salidas.pop(0)) as m:
        yield m


def respuesta_claude(accion="responder", texto="", intencion="otro", servicio_id="", motivo="",
                     stop="tool_use", uso=None):
    return {"model": "claude-haiku-4-5", "stop_reason": stop,
            "usage": uso or {"input_tokens": 1000, "output_tokens": 100},
            "content": [{"type": "tool_use", "name": "responder", "id": "t1", "input": {
                "accion": accion, "texto": texto, "motivo": motivo, "intencion": intencion,
                "servicio_id": servicio_id}}]}
