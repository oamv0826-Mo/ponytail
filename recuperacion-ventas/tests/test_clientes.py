"""Fichas de clientes, cotizaciones y cotejo, a través de la bandeja real (HTTP) y de sus funciones."""
import datetime as dt
import http.client
import socket
import threading
from urllib.parse import unquote, urlencode

from ayuda import Caso, local
from rv import base, web


class ConBandeja(Caso):
    """Servidor real en un puerto libre y una sesión iniciada (self.cookie)."""
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
        _, h, _ = self.pedir("POST", "/bandeja/login", {"usuario": "ana", "clave": "clave-segura-123"})
        self.cookie = h["Set-Cookie"].split(";")[0]

    def pedir(self, metodo, ruta, form=None, cookie=None, origen=True):
        h = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        cab = {"Content-Type": "application/x-www-form-urlencoded"}
        if origen:
            cab["Origin"] = f"http://127.0.0.1:{self.srv.server_address[1]}"
        if cookie:
            cab["Cookie"] = cookie
        h.request(metodo, ruta, body=urlencode(form or {}, doseq=True) if metodo == "POST" else None, headers=cab)
        r = h.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode()

    def post(self, ruta, form):
        codigo, h, _ = self.pedir("POST", ruta, form, cookie=self.cookie)
        self.assertEqual(codigo, 303)
        return unquote(h["Location"])

    def get(self, ruta):
        codigo, _, cuerpo = self.pedir("GET", ruta, cookie=self.cookie)
        self.assertEqual(codigo, 200)
        return cuerpo



class Fichas(ConBandeja):
    def alta(self, **kw):
        return self.post("/bandeja/clientes/nuevo", {"nombre": "Laura Pérez", "telefono": "81 1234 5678",
                                                     "email": "laura@gmail.com", "consentimiento": "si", **kw})

    def test_alta_y_duplicados_abren_la_misma_ficha(self):
        destino = self.alta(fecha_nacimiento="1990-05-04", como_nos_conocio="Instagram")
        cid = int(destino.rsplit("/", 1)[1])
        c = self.con.execute("SELECT * FROM contacto WHERE id=?", (cid,)).fetchone()
        self.assertEqual((c["telefono"], c["email"], c["origen"], c["consentimiento"], c["fecha_nacimiento"]),
                         ("+528112345678", "laura@gmail.com", "importado", 1, "1990-05-04"))
        self.assertIn("Laura Pérez", self.get(destino))
        for kw in ({"telefono": "+52 1 81 1234 5678", "email": ""}, {"telefono": "8100000077", "email": "LAURA@gmail.com"}):
            self.assertEqual(self.alta(**kw), f"/bandeja/clientes/{cid}?error=Ya estaba registrado: esta es su ficha.")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM contacto").fetchone()[0], 1)
        self.assertIn("Teléfono inválido", self.alta(telefono="123"))
        self.assertIn("no puede ser futura", self.alta(telefono="8100000088", email="", fecha_nacimiento="2999-01-01"))

    def test_datos_notas_busqueda_y_escape(self):
        cid = int(self.alta().rsplit("/", 1)[1])
        otro = int(self.alta(telefono="8100000099", email="otro@gmail.com", nombre="Otro").rsplit("/", 1)[1])
        self.assertIn("ya está en otra ficha", self.post(f"/bandeja/clientes/{cid}/datos",
                                                          {"nombre": "Laura", "email": "otro@gmail.com"}))
        self.post(f"/bandeja/clientes/{cid}/datos", {"nombre": "Laura P.", "email": "laura@gmail.com",
                                                     "como_nos_conocio": "recomendación", "consentimiento": "no"})
        c = self.con.execute("SELECT * FROM contacto WHERE id=?", (cid,)).fetchone()
        self.assertEqual((c["nombre"], c["como_nos_conocio"], c["consentimiento"]), ("Laura P.", "recomendación", 0))
        self.post(f"/bandeja/clientes/{cid}/nota", {"texto": "<script>x</script> prefiere las tardes"})
        self.post(f"/bandeja/clientes/{cid}/nota", {"texto": "segunda nota"})
        ficha = self.get(f"/bandeja/clientes/{cid}")
        self.assertIn("&lt;script&gt;x&lt;/script&gt; prefiere las tardes", ficha)
        self.assertNotIn("<script>x</script>", ficha)
        self.assertLess(ficha.index("segunda nota"), ficha.index("prefiere las tardes"))   # la más reciente arriba
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM nota_cliente").fetchone()[0], 2)
        for q in ("laura", "8112345678", "otro@gmail"):
            lista = self.get(f"/bandeja/clientes?q={q}")
            self.assertIn(f"/bandeja/clientes/{cid if q != 'otro@gmail' else otro}'", lista)
        self.assertNotIn(f"/bandeja/clientes/{otro}'", self.get("/bandeja/clientes?q=laura"))
        self.assertIn("Sin resultados", self.get("/bandeja/clientes?q=%25"))   # % es texto, no comodín de LIKE

    def test_ficha_muestra_citas_pasadas_y_futuras_y_enlaza_conversacion(self):
        cid = int(self.alta().rsplit("/", 1)[1])
        for inicio, estado in ((local(2026, 9, 1, 11, 0), "asistio"), (local(2026, 10, 20, 12, 0), "agendada")):
            self.con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, estado, creado, creado_por) "
                             "VALUES (?,?,?,?,?,?,?)", (cid, "limpieza", base.iso(inicio),
                                                        base.iso(inicio + dt.timedelta(hours=1)), estado,
                                                        base.iso(self.t), "bot"))
        ficha = self.get(f"/bandeja/clientes/{cid}")
        pasadas = ficha[ficha.index("Citas pasadas"):]
        self.assertIn("Asistió", pasadas)
        self.assertIn("20 de octubre", ficha[:ficha.index("Citas pasadas")])   # futura, con el formulario de agendar
        self.assertIn(f"/bandeja/c/{cid}/agendar", ficha)
        self.assertIn(f"/bandeja/c/{cid}'", ficha)
        self.assertIn(f"/bandeja/clientes/{cid}'", self.get(f"/bandeja/c/{cid}"))

    def test_sin_sesion_u_otro_origen_no_se_registra(self):
        codigo, h, _ = self.pedir("POST", "/bandeja/clientes/nuevo", {"nombre": "X", "telefono": "8100000001"})
        self.assertEqual((codigo, h.get("Location", "").endswith("/bandeja/login")), (303, True))
        codigo, _, _ = self.pedir("POST", "/bandeja/clientes/nuevo", {"nombre": "X", "telefono": "8100000001"},
                                  cookie=self.cookie, origen=False)
        self.assertEqual(codigo, 403)
        codigo, h, _ = self.pedir("GET", "/bandeja/clientes")
        self.assertEqual(codigo, 303)
        self.assertIsNone(self.con.execute("SELECT 1 FROM contacto").fetchone())


class Cotizaciones(ConBandeja):
    TEL = "+528100000055"

    def setUp(self):
        super().setUp()
        self.escribir("hola", de=self.TEL, nombre="Luis")   # abre la ventana de 24 h
        self.cid = self.contacto(self.TEL)["id"]

    def nueva(self, filas=(("limpieza", "", "1", ""), ("", "Radiografía", "2", "350.50")), cid=None):
        form = {k: [f[i] for f in filas] for i, k in enumerate(("servicio", "descripcion", "cantidad", "precio"))}
        return self.post(f"/bandeja/clientes/{cid or self.cid}/cotizacion", form)

    def q(self, folio="C-0001"):
        return self.con.execute("SELECT * FROM cotizacion WHERE folio=?", (folio,)).fetchone()

    def test_lineas_total_folios_y_errores(self):
        self.assertEqual(self.nueva(), f"/bandeja/cotizaciones/{self.q()['id']}")
        q = self.q()
        self.assertEqual((q["estado"], q["total_centavos"], q["vigencia_dias"]), ("borrador", 80000 + 70100, 15))
        lineas = self.con.execute("SELECT descripcion, cantidad, precio_centavos FROM cotizacion_linea ORDER BY id").fetchall()
        self.assertEqual([tuple(x) for x in lineas], [("Limpieza dental", 1, 80000), ("Radiografía", 2, 35050)])
        self.nueva()
        self.assertIsNotNone(self.q("C-0002"))
        self.assertIn("Falta el precio", self.nueva(filas=(("", "Algo", "1", ""),)))
        self.assertIn("Cantidad inválida", self.nueva(filas=(("limpieza", "", "0", ""),)))
        self.assertIn("al menos una línea", self.nueva(filas=(("", "", "1", ""),)))
        self.assertIn("monto inválido", self.nueva(filas=(("", "X", "1", "abc"),)))

    def test_enviar_por_la_conversacion_aceptar_y_editar_solo_borrador(self):
        self.nueva()
        qid = self.q()["id"]
        self.post(f"/bandeja/cotizaciones/{qid}/guardar", {"servicio": ["limpieza"], "descripcion": [""],
                                                           "cantidad": ["2"], "precio": [""]})
        self.assertEqual(self.q()["total_centavos"], 160000)
        self.assertEqual(self.post(f"/bandeja/cotizaciones/{qid}/enviar", {}), f"/bandeja/cotizaciones/{qid}")
        enviado = self.con.execute("SELECT * FROM mensaje WHERE direccion='out' ORDER BY id DESC").fetchone()
        self.assertIn("Cotización C-0001", enviado["texto"])
        self.assertIn("2 × Limpieza dental: $1,600.00 MXN", enviado["texto"])
        self.assertIn("Vigente hasta el miércoles 21 de octubre", enviado["texto"])
        self.assertEqual(self.q()["estado"], "enviada")
        self.assertIn("Solo un borrador", self.post(f"/bandeja/cotizaciones/{qid}/guardar", {"servicio": ["limpieza"],
                                                    "descripcion": [""], "cantidad": ["1"], "precio": [""]}))
        otro = self.con.execute("INSERT INTO contacto (telefono, nombre, creado) VALUES ('+528100000066', 'X', ?)",
                                (base.iso(self.t),)).lastrowid
        ajena = self.con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, creado, creado_por) VALUES "
                                 "(?, 'limpieza', '2026-10-20T16:00:00Z', '2026-10-20T17:00:00Z', ?, 'bot')",
                                 (otro, base.iso(self.t))).lastrowid
        self.assertIn("no es de este cliente", self.post(f"/bandeja/cotizaciones/{qid}/cita", {"cita": str(ajena)}))
        self.post(f"/bandeja/cotizaciones/{qid}/aceptar", {})
        q = self.q()
        self.assertEqual((q["estado"], q["respondida_en"]), ("aceptada", base.iso(self.t)))
        self.assertIn("Solo una cotización enviada", self.post(f"/bandeja/cotizaciones/{qid}/rechazar", {}))
        impresa = self.get(f"/bandeja/cotizaciones/{qid}/imprimir")
        self.assertIn("Cotización C-0001", impresa)
        self.assertNotIn("<header>", impresa)   # página limpia para imprimir
        self.assertIn("C-0001", self.get(f"/bandeja/clientes/{self.cid}"))

    def test_ventana_cerrada_correo_plantilla_o_baja(self):
        from rv import clientes
        self.nueva()
        self.t = self.t + dt.timedelta(days=2)   # ventana de 24 h cerrada; martes → jueves 10:00
        q = self.q()
        self.assertIsNone(clientes.enviar_cotizacion(self.con, self.cfg, q, "ana"))
        plantilla = self.con.execute("SELECT plantilla FROM mensaje ORDER BY id DESC").fetchone()[0]
        self.assertEqual(plantilla, "cotizacion")
        self.con.execute("UPDATE cotizacion SET estado='borrador', enviada_en=NULL")
        self.con.execute("UPDATE contacto SET email='luis@gmail.com' WHERE id=?", (self.cid,))
        self.cfg["email"].update(proveedor="smtp", remitente="citas@clinica.mx")
        self.cfg["email"]["smtp"]["host"] = "smtp.gmail.com"
        self.assertIsNone(clientes.enviar_cotizacion(self.con, self.cfg, self.q(), "ana"))
        self.assertIn("correo:luis@gmail.com\tsistema\tCotización C-0001", (self.dir / "envios-prueba.log").read_text())
        self.con.execute("UPDATE cotizacion SET estado='borrador', enviada_en=NULL")
        self.con.execute("UPDATE contacto SET email=NULL WHERE id=?", (self.cid,))
        self.con.execute("INSERT INTO optout (telefono, creado, origen) VALUES (?, ?, 'whatsapp')", (self.TEL, base.iso(self.t)))
        self.assertIn("dio de baja", clientes.enviar_cotizacion(self.con, self.cfg, self.q(), "ana"))

    def test_vence_en_el_tick_al_dia_siguiente_de_su_vigencia(self):
        from rv import tick
        self.nueva()
        self.post(f"/bandeja/cotizaciones/{self.q()['id']}/enviar", {})
        self.t = local(2026, 10, 21, 23, 0)   # último día de vigencia (enviada el 6, 15 días)
        tick.correr(self.con, self.cfg)
        self.assertEqual(self.q()["estado"], "enviada")
        self.t = local(2026, 10, 22, 0, 30)
        self.assertEqual(tick.correr(self.con, self.cfg)["vencer_cotizaciones"], 1)
        self.assertEqual(self.q()["estado"], "vencida")

    def test_la_ia_solo_menciona_montos_de_las_cotizaciones_de_ese_cliente(self):
        from rv import motor
        self.nueva()
        self.post(f"/bandeja/cotizaciones/{self.q()['id']}/enviar", {})
        self.escribir("hola", de="+528100000077", nombre="Otro")
        otro = self.contacto("+528100000077")
        r = {"accion": "responder", "texto": "Tu total es de $1,501.00 MXN.", "motivo": "", "intencion": "otro",
             "servicio_id": ""}
        motor.ejecutar(self.con, self.cfg, otro, r)
        self.assertEqual(self.contacto("+528100000077")["handoff_motivo"], "monto_no_config:1,501.00")
        motor.ejecutar(self.con, self.cfg, self.contacto(self.TEL), r)
        ultimo = self.con.execute("SELECT texto FROM mensaje WHERE contacto_id=? ORDER BY id DESC", (self.cid,)).fetchone()
        self.assertEqual(ultimo[0], "Tu total es de $1,501.00 MXN.")
        self.assertIn("C-0001 por $1,501.00 MXN", motor.datos_sistema(self.con, self.cfg, self.contacto(self.TEL)))

    def test_aceptar_por_mensaje_pasa_a_una_persona_sin_confirmar(self):
        from rv import motor
        self.nueva()
        self.post(f"/bandeja/cotizaciones/{self.q()['id']}/enviar", {})
        self.escribir("Sí, acepto la cotización", de=self.TEL)
        c = self.contacto(self.TEL)
        self.assertEqual((c["estado"], c["handoff_motivo"]), ("humano", "cotizacion:C-0001"))
        self.assertEqual(self.q()["estado"], "enviada")   # la confirma una persona
        self.assertIn("respondió a la cotización C-0001", motor.motivo_legible(c["handoff_motivo"]))
        self.nueva()
        self.post(f"/bandeja/cotizaciones/{self.q('C-0002')['id']}/enviar", {})
        self.assertIsNone(motor.motivo_cotizacion(self.con, self.cid, "cotizacion"))   # dos abiertas: no adivina
        self.assertEqual(motor.motivo_cotizacion(self.con, self.cid, "cotizacion:C-0002"), "cotizacion:C-0002")


class ConfigAislada(Caso):
    def test_cambiar_una_config_no_toca_los_valores_por_omision(self):
        self.cfg["email"]["smtp"]["host"] = "smtp.uno.mx"
        self.cfg["cotizaciones"]["vigencia_dias"] = 3
        otra = base.cargar_config(self.dir)
        self.assertEqual((otra["email"]["smtp"]["host"], otra["cotizaciones"]["vigencia_dias"]), ("", 15))


class SaldoYCotejo(ConBandeja):
    def setUp(self):
        super().setUp()
        from rv import clientes
        self.cl = clientes
        self.cid = int(self.post("/bandeja/clientes/nuevo", {"nombre": "Marta", "telefono": "8100000033"}).rsplit("/", 1)[1])

    def cotizacion(self, total="1000", estado="aceptada", cid=None):
        qid, error = self.cl.guardar_cotizacion(self.con, self.cfg, {"id": cid or self.cid}, "ana", {
            "servicio": [""], "descripcion": ["Tratamiento"], "cantidad": ["1"], "precio": [total]})
        self.assertIsNone(error)
        self.con.execute("UPDATE cotizacion SET estado=?, enviada_en=?, respondida_en=? WHERE id=?",
                         (estado, base.iso(self.t), base.iso(self.t), qid))
        return self.cl.cotizacion(self.con, qid)

    def venta(self, monto, cid=None, fecha="2026-10-06"):
        from rv import ventas
        vid, error = ventas.registrar_venta(self.con, self.cfg, cid or self.cid, monto, fecha, "humano:ana")
        self.assertIsNone(error)
        return self.con.execute("SELECT * FROM venta WHERE id=?", (vid,)).fetchone()

    def cotejo(self, k):
        return [detalle for _, _, detalle in self.cl.datos_cotejo(self.con, self.cfg, "2026-10")[k]]

    def test_una_cotizacion_abierta_se_liga_dos_no(self):
        q = self.cotizacion("1000")
        self.assertEqual(self.venta("400")["cotizacion_id"], q["id"])          # anticipo
        self.assertEqual(self.cl.saldo(self.con, q), 60000)
        self.assertIsNone(self.venta("700")["cotizacion_id"])                  # rebasa el saldo: no se adivina
        self.assertEqual(self.venta("600", fecha="2026-10-05")["cotizacion_id"], q["id"])
        self.assertEqual(self.cl.saldo(self.con, q), 0)
        self.assertIn("Saldo pendiente del cliente: <strong>$0.00 MXN</strong>", self.get(f"/bandeja/clientes/{self.cid}"))
        self.cotizacion("500")
        self.cotizacion("800")
        self.assertIsNone(self.venta("300", fecha="2026-10-04")["cotizacion_id"])   # dos abiertas: no se liga
        self.assertEqual(len(self.cotejo("venta_sin_ligar")), 2)   # la de 700 y la de 300, con cotizaciones abiertas

    def test_cada_categoria_del_cotejo(self):
        q = self.cotizacion("1000")
        self.assertEqual(self.cotejo("aceptada_sin_cita"), [f"{q['folio']} por $1,000.00 MXN"])
        self.venta("400")
        self.assertEqual(self.cotejo("saldo_pendiente"), [f"{q['folio']}: total $1,000.00 MXN, falta $600.00 MXN"])
        inicio = local(2026, 10, 2, 11, 0)
        self.con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, estado, creado, creado_por) VALUES "
                         "(?, 'limpieza', ?, ?, 'asistio', ?, 'bot')",
                         (self.cid, base.iso(inicio), base.iso(inicio + dt.timedelta(hours=1)), base.iso(self.t)))
        self.assertEqual(self.cotejo("aceptada_sin_cita"), [])   # ya hay una cita creada después de aceptarla
        otro = int(self.post("/bandeja/clientes/nuevo", {"nombre": "Pepe", "telefono": "8100000044"}).rsplit("/", 1)[1])
        self.con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, estado, creado, creado_por) VALUES "
                         "(?, 'limpieza', ?, ?, 'asistio', ?, 'bot')",
                         (otro, base.iso(inicio), base.iso(inicio + dt.timedelta(hours=1)), base.iso(self.t)))
        self.assertEqual(self.cotejo("asistio_sin_venta"), ["Limpieza dental el 2026-10-02"])   # Marta sí pagó después
        v = self.cotizacion("200", estado="vencida", cid=otro)
        self.assertEqual(self.cotejo("vencida"), [f"{v['folio']} venció el 2026-10-21"])
        (self.dir / "pagos-sin-contacto.csv").write_text(
            "telefono,fecha,monto,email,nombre,proveedor,pago\n+525500000000,2026-10-03,250.00,x@y.mx,Desconocido,stripe,cs_9\n"
            "+525500000000,2026-09-03,99.00,x@y.mx,Viejo,stripe,cs_8\n", encoding="utf-8")
        self.assertEqual(self.cotejo("pago_sin_cliente"), ["$250.00 el 2026-10-03 · stripe cs_9 · tel +525500000000"])
        pagina = self.get("/bandeja/cotejo?mes=2026-10")
        self.assertIn(f"/bandeja/clientes/{otro}'>Pepe</a>", pagina)
        self.assertIn("mes inválido", self.get("/bandeja/cotejo?mes=octubre"))

    def test_de_punta_a_punta_saldo_cero_y_cotejo_limpio(self):
        from rv.__main__ import main
        form = {"servicio": ["limpieza", ""], "descripcion": ["", "Radiografía"], "cantidad": ["1", "1"],
                "precio": ["", "350"]}
        qid = int(self.post(f"/bandeja/clientes/{self.cid}/cotizacion", form).rsplit("/", 1)[1])
        self.post(f"/bandeja/cotizaciones/{qid}/enviar", {})   # sin ventana ni correo: plantilla 'cotizacion'
        self.assertEqual(self.con.execute("SELECT plantilla FROM mensaje ORDER BY id DESC").fetchone()[0], "cotizacion")
        self.post(f"/bandeja/cotizaciones/{qid}/aceptar", {})
        self.post(f"/bandeja/c/{self.cid}/agendar", {"servicio": "limpieza", "fecha": "2026-10-08", "hora": "11:00"})
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM cita WHERE contacto_id=?", (self.cid,)).fetchone())
        self.post(f"/bandeja/c/{self.cid}/venta", {"monto": "1150", "fecha": "2026-10-06"})
        q = self.cl.cotizacion(self.con, qid)
        self.assertEqual((q["estado"], self.cl.saldo(self.con, q)), ("aceptada", 0))
        d = self.cl.datos_cotejo(self.con, self.cfg, "2026-10")
        self.assertEqual([x for filas in d.values() for x in filas if x[0] == self.cid], [])
        main(["--cliente", str(self.dir), "cotejo", "2026-10"])
        self.assertIn("Sin diferencias.", (self.dir / "reportes" / "cotejo-2026-10.md").read_text())
