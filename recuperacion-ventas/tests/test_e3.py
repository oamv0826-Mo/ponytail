"""Etapa 3: tick (seguimiento, recordatorios, reseñas, reactivación, avisos) y opt-out."""
import datetime as dt
import unittest

from ayuda import Caso, local
from rv import base, tick, ventas


class Base3(Caso):
    def enviados(self, plantilla=None):
        q = "SELECT * FROM mensaje WHERE direccion='out' AND plantilla IS NOT NULL"
        filas = self.con.execute(q + (" AND plantilla=?" if plantilla else "") + " ORDER BY id",
                                 (plantilla,) if plantilla else ()).fetchall()
        return filas

    def tick_en(self, *fecha):
        self.t = local(*fecha)
        return tick.correr(self.con, self.cfg)

    def cita(self, inicio, creado, contacto="+528100000001", estado="agendada", servicio="limpieza"):
        if not self.contacto(contacto):
            self.escribir("hola", de=contacto)
        cid = self.contacto(contacto)["id"]
        return self.con.execute(
            "INSERT INTO cita (contacto_id, servicio_id, inicio, fin, estado, creado, creado_por) VALUES (?,?,?,?,?,?,'bot')",
            (cid, servicio, base.iso(inicio), base.iso(inicio + dt.timedelta(minutes=45)), estado,
             base.iso(creado))).lastrowid


class Seguimiento(Base3):
    def test_dias_2_5_10_en_ventana_y_luego_termina(self):
        self.escribir("¿cuánto cuesta la limpieza?")            # martes 6 oct 10:00
        self.tick_en(2026, 10, 8, 9, 59)                         # día 2 a las 9:59: aún no
        self.assertEqual(self.enviados(), [])
        self.tick_en(2026, 10, 8, 10, 0)
        self.assertEqual([m["plantilla"] for m in self.enviados()], ["seguimiento_1"])
        self.assertIn("Limpieza dental", self.enviados()[0]["texto"])
        self.tick_en(2026, 10, 11, 10, 0)                         # día 5 es domingo: no se envía
        self.assertEqual(len(self.enviados()), 1)
        self.tick_en(2026, 10, 12, 9, 0)                          # lunes a primera hora
        self.tick_en(2026, 10, 16, 10, 0)                         # día 10
        self.assertEqual([m["plantilla"] for m in self.enviados()], ["seguimiento_1", "seguimiento_2", "seguimiento_3"])
        self.assertEqual(self.contacto()["seg_activo"], 0)
        self.tick_en(2026, 10, 30, 10, 0)
        self.assertEqual(len(self.enviados()), 3)

    def test_lo_pienso_no_lo_detiene_y_cuenta_desde_el_ultimo_mensaje(self):
        self.escribir("¿cuánto cuesta la limpieza?")              # martes 10:00
        self.t = local(2026, 10, 7, 10, 0)
        self.escribir("ok gracias, lo pienso")                   # miércoles: sigue activo, cuenta desde aquí
        self.assertEqual(self.contacto()["seg_activo"], 1)
        self.tick_en(2026, 10, 8, 10, 0)                         # 2 días desde el martes: todavía no
        self.assertEqual(self.enviados("seguimiento_1"), [])
        self.tick_en(2026, 10, 9, 10, 0)                         # 2 días desde el miércoles
        self.assertEqual(len(self.enviados("seguimiento_1")), 1)

    def test_responder_a_un_seguimiento_lo_detiene(self):
        self.escribir("¿cuánto cuesta la limpieza?")
        self.tick_en(2026, 10, 8, 10, 0)
        self.t = local(2026, 10, 8, 11, 0)
        self.escribir("ahorita no puedo, gracias")
        self.assertEqual(self.contacto()["seg_activo"], 0)
        self.tick_en(2026, 10, 11, 10, 0)
        self.tick_en(2026, 10, 12, 10, 0)
        self.assertEqual([m["plantilla"] for m in self.enviados()], ["seguimiento_1"])

    def test_se_detiene_si_agenda_handoff_o_baja(self):
        casos = {"+528100000012": "quiero hablar con una persona", "+528100000013": "baja"}
        for tel, respuesta in casos.items():
            self.escribir("¿cuánto cuesta la limpieza?", de=tel)
            self.escribir(respuesta, de=tel)
        self.escribir("¿cuánto cuesta la limpieza?", de="+528100000014")
        self.escribir("quiero agendar una limpieza", de="+528100000014")
        self.escribir("3", de="+528100000014")
        self.tick_en(2026, 10, 8, 12, 0)
        self.assertEqual(self.enviados("seguimiento_1"), [])

    def test_nueva_consulta_reinicia_el_ciclo(self):
        self.escribir("¿cuánto cuesta la limpieza?")
        self.tick_en(2026, 10, 8, 10, 0)
        self.escribir("¿y el blanqueamiento cuánto cuesta?")      # responde → se detiene y reinicia
        self.assertEqual((self.contacto()["seg_paso"], self.contacto()["seg_servicio"]), (0, "blanqueamiento"))

    def test_nunca_dos_el_mismo_dia_tras_una_caida(self):
        self.escribir("¿cuánto cuesta la limpieza?")
        self.tick_en(2026, 10, 20, 10, 0)                         # sistema caído 2 semanas
        self.tick_en(2026, 10, 20, 10, 5)
        self.assertEqual(len(self.enviados()), 1)


class Recordatorios(Base3):
    def test_24h_y_2h(self):
        self.cita(local(2026, 10, 8, 12, 0), creado=self.t)       # jueves 12:00, creada el martes
        self.tick_en(2026, 10, 7, 11, 55)
        self.assertEqual(self.enviados(), [])
        self.tick_en(2026, 10, 7, 12, 0)
        self.assertEqual(len(self.enviados("recordatorio_cita")), 1)
        self.assertIn("jueves 8 de octubre a las 12:00", self.enviados()[0]["texto"])
        self.assertIn("Monterrey, N.L. Si necesitas", self.enviados()[0]["texto"])   # sin doble punto
        self.tick_en(2026, 10, 8, 10, 0)
        self.assertEqual(len(self.enviados("recordatorio_cita")), 2)
        self.tick_en(2026, 10, 8, 10, 5)
        self.assertEqual(len(self.enviados("recordatorio_cita")), 2)

    def test_24h_fuera_de_ventana_se_adelanta_y_2h_temprano_se_omite(self):
        self.cita(local(2026, 10, 12, 9, 30), creado=self.t)      # lunes 9:30 → 24 h antes es domingo
        self.tick_en(2026, 10, 10, 19, 25)
        self.assertEqual(self.enviados(), [])
        self.tick_en(2026, 10, 10, 19, 30)                        # sábado 19:30: último momento permitido
        self.assertEqual(len(self.enviados("recordatorio_cita")), 1)
        self.tick_en(2026, 10, 12, 9, 0)                          # 2 h antes = 7:30: fuera de ventana
        self.assertEqual(len(self.enviados("recordatorio_cita")), 1)
        self.assertEqual(self.con.execute("SELECT rec2 FROM cita").fetchone()[0], "omitido")

    def test_cita_creada_dentro_del_plazo_no_recibe_recordatorio(self):
        self.cita(local(2026, 10, 6, 13, 0), creado=self.t)       # en 3 h: ni 24 h ni... 2 h sí aplica
        self.tick_en(2026, 10, 6, 11, 0)
        self.assertEqual(len(self.enviados("recordatorio_cita")), 1)
        self.assertEqual(self.con.execute("SELECT rec24 FROM cita").fetchone()[0], "omitido")

    def test_opt_out_no_recibe_recordatorio(self):
        self.cita(local(2026, 10, 8, 12, 0), creado=self.t)
        self.escribir("stop")
        self.tick_en(2026, 10, 7, 12, 0)
        self.tick_en(2026, 10, 8, 10, 0)
        self.assertEqual(self.enviados("recordatorio_cita"), [])


class Resenas(Base3):
    def test_una_solicitud_2h_despues_de_asistio_y_maximo_cada_90_dias(self):
        c1 = self.cita(local(2026, 10, 6, 10, 0), creado=self.t)
        self.con.execute("UPDATE cita SET estado='asistio', asistio_en=? WHERE id=?", (base.iso(local(2026, 10, 6, 11, 0)), c1))
        self.tick_en(2026, 10, 6, 12, 55)
        self.assertEqual(self.enviados("resena"), [])
        self.tick_en(2026, 10, 6, 13, 0)
        [m] = self.enviados("resena")
        self.assertIn("https://g.page/r/EJEMPLO/review", m["texto"])
        c2 = self.cita(local(2026, 11, 3, 10, 0), creado=self.t)   # vuelve en noviembre (< 90 días)
        self.con.execute("UPDATE cita SET estado='asistio', asistio_en=? WHERE id=?", (base.iso(local(2026, 11, 3, 11, 0)), c2))
        self.tick_en(2026, 11, 3, 14, 0)
        self.assertEqual(len(self.enviados("resena")), 1)
        self.assertEqual(self.con.execute("SELECT resena_enviada FROM cita WHERE id=?", (c2,)).fetchone()[0], "omitida")

    def test_asistio_de_noche_espera_a_la_ventana(self):
        c1 = self.cita(local(2026, 10, 6, 18, 0), creado=self.t)
        self.con.execute("UPDATE cita SET estado='asistio', asistio_en=? WHERE id=?", (base.iso(local(2026, 10, 6, 19, 0)), c1))
        self.tick_en(2026, 10, 6, 21, 0)
        self.assertEqual(self.enviados("resena"), [])
        self.tick_en(2026, 10, 7, 9, 0)
        self.assertEqual(len(self.enviados("resena")), 1)


class Reactivacion(Base3):
    def importar(self, filas):
        ruta = self.dir / "clientes.csv"
        ruta.write_text("nombre,telefono,ultima_visita,consentimiento\n" + "\n".join(filas), encoding="utf-8")
        return ventas.importar_clientes(self.con, self.cfg, ruta)

    def test_importacion_normaliza_deduplica_y_valida(self):
        r = self.importar(["Ana,81 1234 0001,2026-01-01,sí", "Ana dup,5218112340001,,sí", "Equipo,8122222222,,sí",
                           "Malo,123,,sí", "Beto,8112340002,ayer,sí", "Caro,8112340003,,quizá", "Dani,8112340004,,"])
        self.assertEqual((r["nuevos"], r["duplicados"], r["equipo"]), (2, 1, 1))
        self.assertEqual([x[0] for x in r["rechazados"]], [5, 6, 7])
        self.assertEqual(self.contacto("+528112340001")["consentimiento"], 1)
        self.assertEqual(self.contacto("+528112340004")["consentimiento"], 0)
        r = self.importar(["Ana,8112340001,2026-05-01,no"])          # reimportar revoca y actualiza visita
        self.assertEqual(r["actualizados"], 1)
        c = self.contacto("+528112340001")
        self.assertEqual((c["consentimiento"], c["ultima_visita"]), (0, "2026-05-01"))

    def test_solo_con_consentimiento_sin_baja_y_en_lotes(self):
        self.cfg["reactivacion"]["lote_base"] = 15
        self.importar([f"C{i},81123{i:05d},2026-01-01,si" for i in range(30)] + ["Sin,8199999999,,no"])
        self.con.execute("INSERT INTO optout (telefono, creado, origen) VALUES ('+528112300000', 'x', 'x')")
        self.tick_en(2026, 10, 6, 10, 0)
        self.assertEqual(len(self.enviados("reactivacion")), 10)     # máximo 10 por tick
        self.tick_en(2026, 10, 6, 10, 5)
        self.tick_en(2026, 10, 6, 10, 10)
        self.assertEqual(len(self.enviados("reactivacion")), 15)     # lote diario
        tels = {m["telefono"] for m in self.enviados("reactivacion")}
        self.assertNotIn("+528112300000", tels)
        self.assertNotIn("+528199999999", tels)
        self.tick_en(2026, 10, 11, 10, 0)                              # domingo: nada
        self.assertEqual(len(self.enviados("reactivacion")), 15)

    def test_lote_sube_a_100_tras_7_dias_verdes_y_rojo_pausa(self):
        for d in range(7):
            base.set_estado(self.con, "calidad_fecha", "")
            tick.calidad_del_numero(self.con, self.cfg, local(2026, 10, 1 + d, 10, 0))
        self.assertEqual(tick.lote_del_dia(self.con, self.cfg, "GREEN"), 100)
        base.set_estado(self.con, "calidad_simulada", "RED")
        base.set_estado(self.con, "calidad_fecha", "")
        calidad = tick.calidad_del_numero(self.con, self.cfg, local(2026, 10, 9, 10, 0))
        self.assertEqual((calidad, tick.lote_del_dia(self.con, self.cfg, calidad)), ("RED", 0))
        self.assertEqual(base.get_estado(self.con, "dias_verde"), "0")
        aviso = self.con.execute("SELECT telefono FROM mensaje WHERE plantilla='aviso_equipo'").fetchall()
        self.assertEqual([a[0] for a in aviso], ["+528111111111"])

    def test_no_reactiva_a_quien_escribio_hace_poco_o_tiene_cita(self):
        self.importar(["Ana,8112340001,,si", "Beto,8112340002,,si"])
        self.escribir("hola", de="+528112340001")
        self.cita(local(2026, 10, 20, 10, 0), creado=self.t, contacto="+528112340002")
        self.tick_en(2026, 10, 7, 10, 0)
        self.assertEqual(self.enviados("reactivacion"), [])


class Avisos(Base3):
    def test_aviso_pendiente_sale_a_la_apertura(self):
        self.t = local(2026, 10, 10, 15, 0)                         # sábado tarde
        self.escribir("quiero hablar con una persona")
        self.tick_en(2026, 10, 11, 12, 0)                           # domingo
        self.assertEqual(self.enviados("aviso_equipo"), [])
        self.tick_en(2026, 10, 12, 9, 0)
        self.assertEqual(len(self.enviados("aviso_equipo")), 2)
        self.assertEqual(self.contacto()["aviso_pendiente"], 0)

    def test_escalamiento_a_15_min_de_horario_y_una_vez(self):
        self.escribir("quiero hablar con una persona")              # 10:00 → aviso inmediato (2)
        self.tick_en(2026, 10, 6, 10, 14)
        self.assertEqual(len(self.enviados("aviso_equipo")), 2)
        self.tick_en(2026, 10, 6, 10, 15)
        self.assertEqual(len(self.enviados("aviso_equipo")), 4)
        self.tick_en(2026, 10, 6, 10, 30)
        self.assertEqual(len(self.enviados("aviso_equipo")), 4)     # una vez por mensaje sin responder

    def test_escalamiento_cuenta_desde_la_apertura_y_se_apaga_si_responde_humano(self):
        self.t = local(2026, 10, 6, 13, 55)
        self.escribir("quiero hablar con una persona")
        self.tick_en(2026, 10, 6, 16, 10)                           # comida 14-16: solo 10 min de horario
        self.assertEqual(len(self.enviados("aviso_equipo")), 2)
        from rv import motor
        motor.responder(self.con, self.cfg, self.contacto(), "Hola, te atiendo", autor="humano:ana")
        self.tick_en(2026, 10, 6, 16, 30)
        self.assertEqual(len(self.enviados("aviso_equipo")), 2)

    def test_falla_de_un_paso_no_detiene_los_demas(self):
        from unittest import mock

        def recordatorios(con, cfg, t):
            raise RuntimeError("falla simulada")

        self.escribir("¿cuánto cuesta la limpieza?")
        pasos = [recordatorios if p.__name__ == "recordatorios" else p for p in tick.PASOS]
        with mock.patch.object(tick, "PASOS", pasos):
            r = self.tick_en(2026, 10, 8, 10, 0)
        self.assertEqual((r["recordatorios"], r["seguimiento"]), ("error", 1))

if __name__ == "__main__":
    unittest.main()
