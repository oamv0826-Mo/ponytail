"""Servidor HTTP: webhook de Meta (+ hilo trabajador de la cola) y bandeja web del equipo."""
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import base, motor, wa

MAX_WEBHOOK = 1_000_000
MAX_FORM = 64_000
SESION_HORAS = 12
VERIFY_PRUEBA = "verificar-prueba"


# ---------- núcleo sin HTTP (lo usan el handler y el comando simular) ----------

def recibir_webhook(con, cfg, cuerpo, firma):
    """Verifica firma y encola. Devuelve (status_http, entradas_nuevas)."""
    if not wa.firma_valida(cfg, cuerpo, firma):
        return 401, 0
    try:
        payload = json.loads(cuerpo)
    except ValueError:
        return 400, 0
    return 200, motor.encolar(con, payload)


def verify_token(cfg):
    return os.environ.get("WA_VERIFY_TOKEN") or (VERIFY_PRUEBA if cfg["modo_prueba"] else "")


# ---------- usuarios y sesiones ----------

def _hash(clave, sal):
    return hashlib.scrypt(clave.encode(), salt=bytes.fromhex(sal), n=2 ** 14, r=8, p=1, dklen=32).hex()


def crear_usuario(con, nombre, clave):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{2,40}", nombre) or len(clave) < 10:
        raise ValueError("usuario: 2-40 caracteres [a-zA-Z0-9_.-]; contraseña: mínimo 10 caracteres")
    sal = secrets.token_hex(16)
    con.execute("INSERT INTO usuario (nombre, hash, sal, creado) VALUES (?,?,?,?) "
                "ON CONFLICT(nombre) DO UPDATE SET hash=excluded.hash, sal=excluded.sal",
                (nombre, _hash(clave, sal), sal, base.iso(base.ahora())))
    con.execute("DELETE FROM sesion WHERE usuario=?", (nombre,))


def verificar_clave(con, nombre, clave):
    u = con.execute("SELECT hash, sal FROM usuario WHERE nombre=?", (nombre,)).fetchone()
    calculado = _hash(clave, u["sal"] if u else "00" * 16)  # mismo costo exista o no el usuario
    return bool(u) and hmac.compare_digest(calculado, u["hash"])


def _sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def abrir_sesion(con, usuario):
    token = secrets.token_urlsafe(32)
    expira = base.ahora() + dt.timedelta(hours=SESION_HORAS)
    con.execute("DELETE FROM sesion WHERE expira<?", (base.iso(base.ahora()),))
    con.execute("INSERT INTO sesion (token_sha256, usuario, expira) VALUES (?,?,?)",
                (_sha(token), usuario, base.iso(expira)))
    return token


def usuario_de_sesion(con, token):
    if not token:
        return None
    r = con.execute("SELECT usuario FROM sesion WHERE token_sha256=? AND expira>?",
                    (_sha(token), base.iso(base.ahora()))).fetchone()
    return r["usuario"] if r else None


class Limitador:
    """5 intentos fallidos → bloqueo de 15 min, por clave (usuario o IP).

    ponytail: en memoria; se reinicia con el proceso. Suficiente para una bandeja de un negocio;
    si hiciera falta persistirlo, mover a la tabla estado.
    """

    def __init__(self, max_fallos=5, bloqueo_s=900, reloj=time.monotonic):
        self.max, self.bloqueo, self.reloj = max_fallos, bloqueo_s, reloj
        self.datos, self.lock = {}, threading.Lock()

    def bloqueado(self, *claves):
        with self.lock:
            return any(self.datos.get(k, (0, 0))[1] > self.reloj() for k in claves)

    def fallo(self, *claves):
        with self.lock:
            for k in claves:
                n, hasta = self.datos.get(k, (0, 0))
                n = 0 if hasta and hasta <= self.reloj() else n
                n += 1
                self.datos[k] = (0, self.reloj() + self.bloqueo) if n >= self.max else (n, 0)

    def exito(self, clave):
        with self.lock:
            self.datos.pop(clave, None)


# ---------- HTML ----------

e = html.escape

CSS = """
body{font:15px/1.45 system-ui,sans-serif;margin:0;color:#1d2433;background:#f4f6f8}
header{background:#123;color:#fff;padding:10px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header a{color:#fff}main{max-width:900px;margin:0 auto;padding:16px}
table{width:100%;border-collapse:collapse;background:#fff}td,th{padding:8px;border-bottom:1px solid #dde;text-align:left;vertical-align:top}
.humano{background:#fff4e5}.msg{padding:8px 12px;margin:6px 0;border-radius:8px;max-width:80%;white-space:pre-wrap}
.in{background:#fff}.out{background:#dcf3e4;margin-left:auto}.meta{font-size:12px;color:#556}
.err{color:#a00}form.inline{display:inline}button{padding:6px 12px;margin:2px}textarea{width:100%;min-height:70px}
.aviso{background:#fff4e5;padding:8px;border-left:4px solid #e90}nav a{margin-right:12px}
"""


def pagina(cfg, titulo, cuerpo, usuario=None):
    bp = e(cfg.base_path)
    cab = ""
    if usuario:
        cab = (f"<header><strong>{e(cfg['nombre'])}</strong><nav><a href='{bp}/bandeja'>Conversaciones</a>"
               f"<a href='{bp}/bandeja/citas'>Citas</a></nav><span>{e(usuario)}</span>"
               f"<form class='inline' method='post' action='{bp}/bandeja/salir'><button>Salir</button></form></header>")
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{e(titulo)}</title><style>{CSS}</style></head>"
            f"<body>{cab}<main>{cuerpo}</main></body></html>")


def hora_local(cfg, s):
    t = base.de_iso(s)
    return t.astimezone(cfg.tz).strftime("%d/%m %H:%M") if t else ""


def ventana_abierta(c):
    u = base.de_iso(c["ultimo_entrante"])
    return bool(u) and base.ahora() - u < dt.timedelta(hours=24)


def html_lista(con, cfg, usuario, filtro):
    where = {"pendientes": "WHERE c.estado='humano' AND c.asignado_a IS NULL",
             "mias": "WHERE c.asignado_a=?", "todas": ""}.get(filtro, "WHERE c.estado='humano'")
    params = (usuario,) if filtro == "mias" else ()
    filas = con.execute(
        "SELECT c.*, (SELECT texto FROM mensaje m WHERE m.contacto_id=c.id ORDER BY m.id DESC LIMIT 1) AS ultimo, "
        "(SELECT creado FROM mensaje m WHERE m.contacto_id=c.id ORDER BY m.id DESC LIMIT 1) AS cuando "
        f"FROM contacto c {where} ORDER BY c.estado='humano' DESC, cuando DESC LIMIT 200", params).fetchall()
    bp = e(cfg.base_path)
    if not filas:
        return "<p>No hay conversaciones aquí.</p>"
    rows = "".join(
        f"<tr class='{'humano' if f['estado'] == 'humano' else ''}'><td><a href='{bp}/bandeja/c/{f['id']}'>"
        f"{e(f['nombre'] or f['telefono'])}</a><div class='meta'>{e(f['telefono'])}</div></td>"
        f"<td>{'Humano' if f['estado'] == 'humano' else 'Bot'}<div class='meta'>"
        f"{e(motor.motivo_legible(f['handoff_motivo'])) if f['estado'] == 'humano' and f['handoff_motivo'] else ''}"
        f"</div></td><td>{e(f['asignado_a'] or '')}</td><td>{e((f['ultimo'] or '')[:80])}"
        f"<div class='meta'>{hora_local(cfg, f['cuando'])}</div></td></tr>" for f in filas)
    return f"<table><tr><th>Contacto</th><th>Atiende</th><th>Tomada por</th><th>Último mensaje</th></tr>{rows}</table>"


MEDIA_EN_LINEA = {"image/jpeg", "image/png", "image/webp", "audio/ogg", "audio/mpeg", "audio/aac", "audio/mp4",
                  "audio/amr", "video/mp4", "video/3gpp", "application/pdf"}


def html_media(cfg, m):
    if not m["media_id"]:
        return ""
    url = f"{e(cfg.base_path)}/bandeja/media/{m['id']}"
    mime = (m["media_mime"] or "").split(";")[0]
    if mime.startswith("audio/") and mime in MEDIA_EN_LINEA:
        return f"<br><audio controls preload='none' src='{url}'></audio>"
    if mime.startswith("image/") and mime in MEDIA_EN_LINEA:
        return f"<br><a href='{url}'><img src='{url}' alt='foto del cliente' style='max-width:240px'></a>"
    return f"<br><a href='{url}'>Abrir archivo</a>"


def html_mensajes(con, cfg, cid):
    filas = con.execute("SELECT * FROM (SELECT * FROM mensaje WHERE contacto_id=? ORDER BY id DESC LIMIT 200) "
                        "ORDER BY id", (cid,)).fetchall()
    return "".join(
        f"<div class='msg {m['direccion']}'>{e(m['texto'])}{html_media(cfg, m)}<div class='meta'>{e(m['autor'])} · "
        f"{hora_local(cfg, m['creado'])} · {e(m['estado'])}"
        f"{' · <span class=err>' + e(m['error']) + '</span>' if m['error'] else ''}</div></div>" for m in filas)


def html_conversacion(con, cfg, c, usuario):
    bp, cid = e(cfg.base_path), c["id"]
    optout = base.dio_baja(con, c["telefono"])
    abierta = ventana_abierta(c)
    accion = lambda ruta, texto: (f"<form class='inline' method='post' action='{bp}/bandeja/c/{cid}/{ruta}'>"  # noqa: E731
                                  f"<button>{texto}</button></form>")
    info = (f"<h1>{e(c['nombre'] or c['telefono'])}</h1><p>{e(c['telefono'])} · Atiende: "
            f"<strong>{'Humano' if c['estado'] == 'humano' else 'Bot'}</strong>"
            f"{' (' + e(motor.motivo_legible(c['handoff_motivo'])) + ')' if c['estado'] == 'humano' and c['handoff_motivo'] else ''}"
            f" · Tomada por: {e(c['asignado_a'] or 'nadie')}{' · <strong>Dio de baja</strong>' if optout else ''}</p>")
    botones = accion("tomar", "Tomar") + (accion("devolver", "Devolver al bot") if c["estado"] == "humano" else
                                          accion("pasar", "Pasar a humano"))
    if abierta:
        hasta = base.de_iso(c["ultimo_entrante"]) + dt.timedelta(hours=24)
        resp = (f"<form method='post' action='{bp}/bandeja/c/{cid}/responder'><label for='t'>Responder "
                f"(ventana abierta hasta {e(hora_local(cfg, base.iso(hasta)))})</label>"
                f"<textarea id='t' name='texto' required maxlength='2000'></textarea><button>Enviar</button></form>")
    elif optout:
        resp = "<p class='aviso'>Ventana de 24 h cerrada y el contacto dio de baja: no se le puede escribir.</p>"
    elif not base.en_ventana_envio(cfg, base.ahora()):
        resp = "<p class='aviso'>Ventana de 24 h cerrada. La plantilla para retomar solo sale de 9:00 a 20:00, lunes a sábado.</p>"
    else:
        resp = ("<p class='aviso'>Ventana de 24 h cerrada: solo se puede enviar la plantilla de retomar contacto.</p>"
                + accion("retomar", "Enviar plantilla de retomar contacto"))
    extra = "".join(f(con, cfg, c) for f in EXTRAS_CONVERSACION)
    script = (f"<script>setInterval(()=>fetch('{bp}/bandeja/c/{cid}/mensajes').then(r=>r.ok&&r.text())"
              f".then(h=>{{if(h)document.getElementById('msgs').innerHTML=h}}),10000)</script>")
    return f"{info}<p>{botones}</p><div id='msgs'>{html_mensajes(con, cfg, cid)}</div>{resp}{extra}{script}"


# Etapas siguientes agregan secciones a la conversación (agendar, registrar venta) y rutas POST.
EXTRAS_CONVERSACION = []
ACCIONES_EXTRA = {}   # nombre → función(con, cfg, c, usuario, form) → mensaje de error o None
RUTAS_POST_EXTRA = {}  # ruta → función(con, cfg, usuario, form) → ruta a la que redirigir
PAGINAS_EXTRA = {}    # ruta → función(con, cfg, usuario, query) → html


def accion_conversacion(con, cfg, c, usuario, nombre, form):
    """Ejecuta una acción de la bandeja. Devuelve mensaje de error o None."""
    cid = c["id"]
    if nombre == "tomar":
        con.execute("UPDATE contacto SET asignado_a=? WHERE id=?", (usuario, cid))
    elif nombre == "devolver":
        con.execute("UPDATE contacto SET estado='bot', asignado_a=NULL, aviso_pendiente=0, escalado=NULL WHERE id=?",
                    (cid,))
    elif nombre == "pasar":
        con.execute("UPDATE contacto SET estado='humano', handoff_desde=?, handoff_motivo=?, asignado_a=?, seg_activo=0 "
                    "WHERE id=?", (base.iso(base.ahora()), f"manual:{usuario}", usuario, cid))
    elif nombre == "responder":
        texto = (form.get("texto") or [""])[0].strip()
        if not texto:
            return "Escribe un mensaje."
        if not ventana_abierta(c):
            return "La ventana de 24 h está cerrada."
        con.execute("UPDATE contacto SET asignado_a=COALESCE(asignado_a, ?) WHERE id=?", (usuario, cid))
        _, ok = motor.responder(con, cfg, c, texto[:2000], autor=f"humano:{usuario}")
        return None if ok else "No se pudo enviar (ver el error en el mensaje)."
    elif nombre == "retomar":
        if base.dio_baja(con, c["telefono"]):
            return "El contacto dio de baja."
        if not base.en_ventana_envio(cfg, base.ahora()):
            return "Fuera del horario de envío (9:00 a 20:00, lunes a sábado)."
        wa.enviar(con, cfg, c["telefono"], plantilla="retomar_contacto", params=[c["nombre"], cfg["nombre"]],
                  contacto_id=cid, autor=f"humano:{usuario}", destino=c["wa_id"])
    elif nombre in ACCIONES_EXTRA:
        return ACCIONES_EXTRA[nombre](con, cfg, c, usuario, form)
    else:
        return "Acción desconocida."
    return None


# ---------- handler ----------

def crear_servidor(cfg, host="127.0.0.1", puerto=None):
    limitador = Limitador()
    despertar = threading.Event()
    origen = "{0.scheme}://{0.netloc}".format(urlparse(cfg["url_publica"]))
    seguro = cfg["url_publica"].startswith("https://")
    bp = cfg.base_path

    class H(BaseHTTPRequestHandler):
        timeout = 15
        server_version = "rv"
        sys_version = ""

        def log_message(self, fmt, *args):
            base.log(self.address_string(), fmt % args)

        def ip(self):
            ip = self.client_address[0]
            if ip in ("127.0.0.1", "::1") and self.headers.get("X-Forwarded-For"):
                return self.headers["X-Forwarded-For"].split(",")[0].strip()
            return ip

        def enviar(self, codigo, cuerpo="", tipo="text/html; charset=utf-8", extra=()):
            datos = cuerpo.encode() if isinstance(cuerpo, str) else cuerpo
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(datos)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            # same-origin (no no-referrer): con no-referrer el navegador manda "Origin: null" en los POST y la
            # revisión anti-CSRF rechazaría todos los formularios.
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline'; "
                             "form-action 'self'; frame-ancestors 'none'")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(datos)

        def redirigir(self, ruta, extra=()):
            self.enviar(303, "", extra=[("Location", bp + ruta), *extra])

        def leer(self, maximo):
            n = int(self.headers.get("Content-Length") or 0)
            if n > maximo:
                raise ValueError("cuerpo demasiado grande")
            return self.rfile.read(n)

        def token(self):
            for parte in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = parte.strip().partition("=")
                if k == "rv_sesion":
                    return v
            return None

        def cookie(self, valor, max_age):
            return ("Set-Cookie", f"rv_sesion={valor}; Path={bp or '/'}; HttpOnly; SameSite=Strict; Max-Age={max_age}"
                    + ("; Secure" if seguro else ""))

        # --- GET ---
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/webhook":
                ok = q.get("hub.mode") == ["subscribe"] and verify_token(cfg) and \
                    hmac.compare_digest((q.get("hub.verify_token") or [""])[0], verify_token(cfg))
                return self.enviar(200, (q.get("hub.challenge") or [""])[0], "text/plain") if ok else self.enviar(403)
            if u.path == "/salud":
                return self.enviar(200, "ok", "text/plain")
            if u.path == "/bandeja/login":
                return self.enviar(200, pagina(cfg, "Entrar", form_login(cfg)))
            with base.db(cfg) as con:
                usuario = usuario_de_sesion(con, self.token())
                if not usuario:
                    return self.redirigir("/bandeja/login")
                if u.path in ("/bandeja", "/bandeja/"):
                    filtro = (q.get("f") or ["pendientes"])[0]
                    tabs = " ".join(f"<a href='{e(bp)}/bandeja?f={k}'>{'<strong>' + t + '</strong>' if k == filtro else t}</a>"
                                    for k, t in (("pendientes", "Pendientes de humano"), ("mias", "Mías"), ("todas", "Todas")))
                    script = (f"<script>setInterval(()=>fetch('{e(bp)}/bandeja/lista?f={e(filtro)}').then(r=>r.ok&&r.text())"
                              f".then(h=>{{if(h)document.getElementById('lista').innerHTML=h}}),10000)</script>")
                    return self.enviar(200, pagina(cfg, "Bandeja", f"<nav>{tabs}</nav><div id='lista'>"
                                                   f"{html_lista(con, cfg, usuario, filtro)}</div>{script}", usuario))
                if u.path == "/bandeja/lista":
                    return self.enviar(200, html_lista(con, cfg, usuario, (q.get("f") or ["pendientes"])[0]))
                m = re.fullmatch(r"/bandeja/c/(\d+)(/mensajes)?", u.path)
                if m:
                    c = motor.contacto(con, int(m.group(1)))
                    if not c:
                        return self.enviar(404, pagina(cfg, "No existe", "<p>No existe.</p>", usuario))
                    if m.group(2):
                        return self.enviar(200, html_mensajes(con, cfg, c["id"]))
                    error = (q.get("error") or [""])[0]
                    aviso = f"<p class='aviso err'>{e(error)}</p>" if error else ""
                    return self.enviar(200, pagina(cfg, c["nombre"] or c["telefono"],
                                                   aviso + html_conversacion(con, cfg, c, usuario), usuario))
                m = re.fullmatch(r"/bandeja/media/(\d+)", u.path)
                if m:
                    return self.media(con, int(m.group(1)))
                if u.path in PAGINAS_EXTRA:
                    return self.enviar(200, pagina(cfg, "Bandeja", PAGINAS_EXTRA[u.path](con, cfg, usuario, q), usuario))
            self.enviar(404, "no encontrado", "text/plain")

        # --- POST ---
        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/webhook":
                try:
                    cuerpo = self.leer(MAX_WEBHOOK)
                except ValueError:
                    return self.enviar(413)
                with base.db(cfg) as con:
                    codigo, _ = recibir_webhook(con, cfg, cuerpo, self.headers.get("X-Hub-Signature-256"))
                self.enviar(codigo, "ok" if codigo == 200 else "", "text/plain")
                if codigo == 200:
                    despertar.set()
                return
            if self.headers.get("Origin") != origen:  # anti-CSRF: todo POST de la bandeja debe venir de su origen
                return self.enviar(403, "origen no permitido", "text/plain")
            try:
                form = parse_qs(self.leer(MAX_FORM).decode("utf-8", "replace"))
            except ValueError:
                return self.enviar(413)
            with base.db(cfg) as con:
                if u.path == "/bandeja/login":
                    return self.login(con, form)
                usuario = usuario_de_sesion(con, self.token())
                if u.path == "/bandeja/salir":
                    if self.token():
                        con.execute("DELETE FROM sesion WHERE token_sha256=?", (_sha(self.token()),))
                    return self.redirigir("/bandeja/login", [self.cookie("", 0)])
                if not usuario:
                    return self.redirigir("/bandeja/login")
                m = re.fullmatch(r"/bandeja/c/(\d+)/([a-z_]+)", u.path)
                if m:
                    c = motor.contacto(con, int(m.group(1)))
                    if not c:
                        return self.enviar(404)
                    error = accion_conversacion(con, cfg, c, usuario, m.group(2), form)
                    destino = f"/bandeja/c/{c['id']}" + (f"?error={_q(error)}" if error else "")
                    return self.redirigir(destino)
                if u.path in RUTAS_POST_EXTRA:
                    destino = RUTAS_POST_EXTRA[u.path](con, cfg, usuario, form)
                    return self.redirigir(destino or "/bandeja")
            self.enviar(404, "no encontrado", "text/plain")

        def media(self, con, mensaje_id):
            fila = con.execute("SELECT media_id FROM mensaje WHERE id=?", (mensaje_id,)).fetchone()
            if not fila or not fila["media_id"]:
                return self.enviar(404, "no hay archivo", "text/plain")
            if cfg["modo_prueba"]:
                return self.enviar(404, "en modo prueba no hay archivos reales", "text/plain")
            try:
                datos, mime = wa.descargar_media(cfg, fila["media_id"])
            except Exception as ex:  # vencido en Meta (≈30 días), red, demasiado grande
                base.log("media:", ex)
                return self.enviar(502, "no se pudo obtener el archivo de WhatsApp", "text/plain")
            mime = mime.split(";")[0].strip()
            extra = [] if mime in MEDIA_EN_LINEA else [("Content-Disposition", "attachment")]  # p. ej. SVG/HTML: nunca en línea
            return self.enviar(200, datos, mime if mime in MEDIA_EN_LINEA else "application/octet-stream", extra)

        def login(self, con, form):
            nombre = (form.get("usuario") or [""])[0].strip()
            clave = (form.get("clave") or [""])[0]
            claves = (f"u:{nombre}", f"ip:{self.ip()}")
            if limitador.bloqueado(*claves):
                return self.enviar(429, pagina(cfg, "Entrar", form_login(cfg, "Demasiados intentos. Espera 15 minutos.")))
            if not verificar_clave(con, nombre, clave):
                limitador.fallo(*claves)
                return self.enviar(401, pagina(cfg, "Entrar", form_login(cfg, "Usuario o contraseña incorrectos.")))
            limitador.exito(claves[0])
            return self.redirigir("/bandeja", [self.cookie(abrir_sesion(con, nombre), SESION_HORAS * 3600)])

    srv = ThreadingHTTPServer((host, puerto or cfg["puerto"]), H)
    srv.daemon_threads = True
    srv.despertar = despertar
    srv.limitador = limitador
    return srv


def _q(s):
    from urllib.parse import quote
    return quote(s)


def form_login(cfg, error=""):
    bp = e(cfg.base_path)
    return (f"<h1>{e(cfg['nombre'])}: bandeja</h1>{'<p class=err>' + e(error) + '</p>' if error else ''}"
            f"<form method='post' action='{bp}/bandeja/login'><p><label for='u'>Usuario</label><br>"
            f"<input id='u' name='usuario' autocomplete='username' required></p><p><label for='c'>Contraseña</label><br>"
            f"<input id='c' name='clave' type='password' autocomplete='current-password' required></p>"
            f"<button>Entrar</button></form>")


def trabajador(cfg, despertar, parar):
    """Hilo único que vacía la cola en orden."""
    con = base.conectar(cfg.carpeta / "datos.db")
    while not parar.is_set():
        despertar.wait(timeout=5)
        despertar.clear()
        try:
            while motor.procesar_pendientes(con, cfg):
                pass
        except Exception as ex:  # el hilo nunca debe morir
            base.log("trabajador:", repr(ex))


def servir(cfg):
    con = base.abrir_db(cfg)  # migra antes de atender
    con.close()
    srv = crear_servidor(cfg)
    parar = threading.Event()
    threading.Thread(target=trabajador, args=(cfg, srv.despertar, parar), daemon=True).start()
    base.log(f"rv escuchando en 127.0.0.1:{srv.server_address[1]} ({'MODO PRUEBA' if cfg['modo_prueba'] else 'producción'})")
    try:
        srv.serve_forever()
    finally:
        parar.set()


# ---------- registro de secciones de etapas posteriores ----------
from . import agenda, ventas  # noqa: E402

EXTRAS_CONVERSACION.append(agenda.html_agendar)
ACCIONES_EXTRA["agendar"] = agenda.accion_agendar
PAGINAS_EXTRA["/bandeja/citas"] = agenda.pagina_citas
RUTAS_POST_EXTRA["/bandeja/citas/marcar"] = agenda.marcar_cita
EXTRAS_CONVERSACION.append(ventas.html_ventas_conversacion)
ACCIONES_EXTRA["venta"] = ventas.accion_venta
agenda.EXTRAS_FILA_CITA.append(ventas.html_venta_en_cita)
