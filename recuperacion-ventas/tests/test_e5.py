"""Etapa 5: verificar, respaldo/restauración, migraciones con respaldo previo, configuración del webhook y despliegue."""
import gzip
import os
import re
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from ayuda import Caso
from rv import base, verificar, web
from rv.__main__ import main

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def niveles(resultados):
    return {msg.split(":")[0]: nivel for nivel, msg in resultados}


class VerificarLocal(Caso):
    def test_modo_prueba_sin_usuarios_es_error_y_secretos_son_aviso(self):
        r = verificar.locales(self.con, self.cfg)
        self.assertIn(("ERROR", "usuarios de la bandeja: 0"), r)
        self.assertTrue(any(n == "AVISO" and m.startswith("faltan secretos") for n, m in r))
        web.crear_usuario(self.con, "ana", "clave-segura-123")
        self.assertFalse(any(n == "ERROR" for n, _ in verificar.locales(self.con, self.cfg)))

    def test_produccion_exige_secretos_https_ids_y_tick_reciente(self):
        self.cfg["modo_prueba"] = False
        web.crear_usuario(self.con, "ana", "clave-segura-123")
        errores = [m for n, m in verificar.locales(self.con, self.cfg) if n == "ERROR"]
        self.assertTrue(any(m.startswith("faltan secretos") for m in errores))
        self.assertTrue(any(m.startswith("url_publica") for m in errores))
        self.assertTrue(any(m.startswith("whatsapp.phone_number_id") for m in errores))
        self.assertIn("último tick: nunca", errores)
        base.set_estado(self.con, "ultimo_tick", base.iso(self.t))
        self.assertNotIn("último tick: nunca", [m for n, m in verificar.locales(self.con, self.cfg) if n == "ERROR"])

    def test_modelo_sin_precio_es_error(self):
        self.cfg["ia"]["modelo"] = "modelo-desconocido"
        self.assertTrue(any(n == "ERROR" and "sin precio" in m for n, m in verificar.locales(self.con, self.cfg)))


class VerificarRemoto(Caso):
    def setUp(self):
        super().setUp()
        self.cfg["modo_prueba"] = False
        self.cfg["url_publica"] = "https://rv.ejemplo.mx/c/demo"
        self.cfg["whatsapp"].update(phone_number_id="111", waba_id="222")

    def graph(self, override_ok=True, falta=None):
        def get(cfg, ruta):
            if ruta.startswith("111?fields=webhook_configuration"):
                url = "https://rv.ejemplo.mx/c/demo/webhook" if override_ok else "https://otra"
                return {"webhook_configuration": {"phone_number": url, "application": "https://app"}, "id": "111"}
            if ruta.startswith("111?"):
                return {"display_phone_number": "+52 81 1000 0000", "verified_name": "Demo", "quality_rating": "GREEN"}
            if ruta.startswith("222/message_templates"):
                return {"data": [{"name": n, "language": "es_MX", "status": "APPROVED"}
                                 for n in verificar.PLANTILLAS if n != falta]}
            raise AssertionError(ruta)
        return get

    def test_todo_bien(self):
        with mock.patch("rv.wa.graph_get", side_effect=self.graph()), \
                mock.patch.object(verificar, "_anthropic_modelo", return_value={"id": "claude-haiku-4-5"}):
            r = verificar.remotos(self.con, self.cfg)
        self.assertEqual([m for n, m in r if n != "OK"], [])

    def test_override_y_plantilla_faltantes(self):
        with mock.patch("rv.wa.graph_get", side_effect=self.graph(override_ok=False, falta="resena")), \
                mock.patch.object(verificar, "_anthropic_modelo", return_value={"id": "x"}):
            errores = [m for n, m in verificar.remotos(self.con, self.cfg) if n == "ERROR"]
        self.assertTrue(any("NO configurado" in m for m in errores))
        self.assertIn("plantilla resena (es_MX): no existe", errores)

    def test_configurar_override_manda_url_y_token(self):
        os.environ["WA_VERIFY_TOKEN"] = "tok"
        with mock.patch("rv.wa._graph", return_value={"success": True}) as g:
            verificar.configurar_override(self.cfg)
        self.assertEqual(g.call_args.args[1:], ("POST", "111", {"webhook_configuration": {
            "override_callback_uri": "https://rv.ejemplo.mx/c/demo/webhook", "verify_token": "tok"}}))

    def test_modo_prueba_no_toca_meta(self):
        self.cfg["modo_prueba"] = True
        with mock.patch("rv.wa.graph_get") as g:
            self.assertEqual(verificar.remotos(self.con, self.cfg)[0][0], "AVISO")
        g.assert_not_called()
        with self.assertRaises(RuntimeError):
            verificar.configurar_override(self.cfg)


class Respaldo(Caso):
    def test_respaldo_se_restaura_con_los_datos(self):
        self.escribir("hola")
        ruta = base.respaldar(self.con, self.dir / "respaldos")
        restaurado = self.dir / "restaurado.db"
        restaurado.write_bytes(gzip.decompress(ruta.read_bytes()))
        con = sqlite3.connect(restaurado)
        self.assertEqual(con.execute("SELECT telefono FROM contacto").fetchone()[0], "+528100000001")
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()

    def test_comando_respaldo_borra_copias_viejas(self):
        destino = self.dir / "respaldos"
        destino.mkdir()
        viejo = destino / "datos-20200101-000000.db.gz"
        viejo.write_bytes(b"x")
        os.utime(viejo, (0, 0))
        main(["--cliente", str(self.dir), "respaldo"])
        self.assertFalse(viejo.exists())
        self.assertEqual(len(list(destino.glob("datos-*.db.gz"))), 1)

    def test_migracion_respalda_antes(self):
        nuevas = base.secciones_esquema() + [(99, "CREATE TABLE prueba_migracion (x INTEGER);")]
        with mock.patch.object(base, "secciones_esquema", return_value=nuevas):
            base.migrar(self.con, self.dir / "datos.db")
        self.assertEqual(self.con.execute("PRAGMA user_version").fetchone()[0], 99)
        self.assertEqual(len(list((self.dir / "respaldos").glob("datos-*.db.gz"))), 1)

    def test_migracion_fallida_no_cambia_version(self):
        ultima = base.secciones_esquema()[-1][0]
        rotas = base.secciones_esquema() + [(ultima + 1, "CREATE TABLE ok_a (x); ESTO NO ES SQL;")]
        with mock.patch.object(base, "secciones_esquema", return_value=rotas), self.assertRaises(sqlite3.Error):
            base.migrar(self.con, self.dir / "datos.db")
        self.assertFalse(self.con.in_transaction)
        self.assertEqual(self.con.execute("PRAGMA user_version").fetchone()[0], ultima)
        self.assertIsNone(self.con.execute("SELECT 1 FROM sqlite_master WHERE name='ok_a'").fetchone())


class Despliegue(unittest.TestCase):
    def test_unidades_apuntan_al_comando_correcto(self):
        for nombre, comando in (("rv@.service", "serve"), ("rv-tick@.service", "tick"), ("rv-respaldo@.service", "respaldo")):
            texto = (DEPLOY / nombre).read_text()
            self.assertIn(f"-m rv --cliente /srv/rv/clientes/%i {comando}", texto)
            self.assertIn("WorkingDirectory=/srv/rv/app/recuperacion-ventas", texto)
            self.assertIn("ReadWritePaths=/srv/rv/clientes/%i", texto)
        self.assertIn("OnCalendar=*:0/5", (DEPLOY / "rv-tick@.timer").read_text())

    def test_caddy_enruta_por_cliente(self):
        texto = (DEPLOY / "Caddyfile").read_text()
        self.assertTrue(re.search(r"handle_path /c/[a-z-]+/\* \{\s*reverse_proxy 127\.0\.0\.1:\d+", texto))


if __name__ == "__main__":
    unittest.main()
