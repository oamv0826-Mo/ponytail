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
