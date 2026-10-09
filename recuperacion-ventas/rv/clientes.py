"""Fichas de clientes en la bandeja: alta a mano, datos, notas del equipo, citas pasadas y futuras, ventas."""
import datetime as dt
import html
import re
from urllib.parse import quote

from . import agenda, base, motor, ventas

e = html.escape


def _campo(form, k):
    return (form.get(k) or [""])[0].strip()


def _ruta_ficha(cid, error=None):
    return f"/bandeja/clientes/{cid}" + (f"?error={quote(error)}" if error else "")


def buscar(con, q, limite=100):
    if not q:
        return con.execute("SELECT * FROM contacto ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    patron = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    tel = base.normalizar_tel(q)
    return con.execute("SELECT * FROM contacto WHERE nombre LIKE ? ESCAPE '\\' OR telefono LIKE ? ESCAPE '\\' "
                       "OR email LIKE ? ESCAPE '\\' OR telefono=? ORDER BY id DESC LIMIT ?",
                       (patron, patron, patron, tel or "", limite)).fetchall()


def _datos_validos(form):
    """Campos editables de la ficha → (dict, error)."""
    d = {"nombre": _campo(form, "nombre")[:60], "email": _campo(form, "email").lower() or None,
         "fecha_nacimiento": _campo(form, "fecha_nacimiento") or None,
         "como_nos_conocio": _campo(form, "como_nos_conocio")[:100] or None,
         "consentimiento": 1 if _campo(form, "consentimiento") == "si" else 0}
    if d["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d["email"]):
        return None, "Correo inválido."
    if d["fecha_nacimiento"]:
        try:
            if dt.date.fromisoformat(d["fecha_nacimiento"]) > base.ahora().date():
                return None, "La fecha de nacimiento no puede ser futura."
        except ValueError:
            return None, "Fecha de nacimiento inválida."
    return d, None


def registrar(con, cfg, usuario, form):
    """Alta a mano. Si el teléfono o el correo ya existen, se abre esa ficha (nunca se duplica)."""
    tel = base.normalizar_tel(_campo(form, "telefono"))
    if not tel:
        return "/bandeja/clientes/nuevo?error=" + quote("Teléfono inválido (10 dígitos de México o +código de país).")
    if tel in cfg.internos:
        return "/bandeja/clientes/nuevo?error=" + quote("Ese teléfono es del equipo.")
    d, error = _datos_validos(form)
    if error:
        return "/bandeja/clientes/nuevo?error=" + quote(error)
    existe = con.execute("SELECT id FROM contacto WHERE telefono=? OR (email=? AND ? IS NOT NULL) "
                         "ORDER BY telefono=? DESC LIMIT 1", (tel, d["email"], d["email"], tel)).fetchone()
    if existe:
        return _ruta_ficha(existe["id"], "Ya estaba registrado: esta es su ficha.")
    # origen 'importado': lo registró el negocio, no llegó escribiendo (no cuenta como respuesta rápida en la garantía)
    cid = con.execute("INSERT INTO contacto (telefono, wa_id, nombre, origen, consentimiento, email, fecha_nacimiento, "
                      "como_nos_conocio, creado) VALUES (?,?,?,?,?,?,?,?,?)",
                      (tel, tel.lstrip("+"), d["nombre"], "importado", d["consentimiento"], d["email"],
                       d["fecha_nacimiento"], d["como_nos_conocio"], base.iso(base.ahora()))).lastrowid
    base.evento(con, cid, "alta_manual", usuario)
    return _ruta_ficha(cid)


def guardar_datos(con, cfg, c, form):
    d, error = _datos_validos(form)
    if error:
        return error
    if d["email"] and con.execute("SELECT 1 FROM contacto WHERE (email=? OR telefono=?) AND id<>?",
                                  (d["email"], d["email"], c["id"])).fetchone():
        return "Ese correo ya está en otra ficha."
    if base.es_correo(c):
        d["email"] = c["email"]   # el correo es la identidad de un contacto de correo: no se cambia aquí
    con.execute("UPDATE contacto SET nombre=?, email=?, fecha_nacimiento=?, como_nos_conocio=?, consentimiento=? "
                "WHERE id=?", (d["nombre"], d["email"], d["fecha_nacimiento"], d["como_nos_conocio"],
                               d["consentimiento"], c["id"]))
    return None


def agregar_nota(con, c, usuario, form):
    texto = _campo(form, "texto")[:2000]
    if not texto:
        return "Escribe la nota."
    con.execute("INSERT INTO nota_cliente (contacto_id, texto, autor, creado) VALUES (?,?,?,?)",
                (c["id"], texto, usuario, base.iso(base.ahora())))
    return None


# ---------- HTML ----------

def html_lista(con, cfg, q):
    bp = e(cfg.base_path)
    filas = buscar(con, q)
    rows = "".join(f"<tr><td><a href='{bp}/bandeja/clientes/{c['id']}'>{e(c['nombre'] or '(sin nombre)')}</a></td>"
                   f"<td>{e(c['telefono'])}</td><td>{e(c['email'] or '')}</td></tr>" for c in filas)
    return (f"<h1>Clientes</h1><form method='get' action='{bp}/bandeja/clientes'><label for='q'>Buscar</label> "
            f"<input id='q' name='q' value='{e(q)}' placeholder='nombre, teléfono o correo'> <button>Buscar</button> "
            f"<a href='{bp}/bandeja/clientes/nuevo'>Registrar cliente</a></form>"
            + (f"<table><tr><th>Nombre</th><th>Teléfono</th><th>Correo</th></tr>{rows}</table>" if filas
               else "<p>Sin resultados.</p>"))


def _campos(c=None):
    v = lambda k: e(c[k] or "") if c else ""   # noqa: E731
    si = "selected" if c and c["consentimiento"] else ""
    return (f"<p><label>Nombre<br><input name='nombre' maxlength='60' value='{v('nombre')}' required></label></p>"
            f"<p><label>Correo<br><input name='email' type='email' value='{v('email')}'"
            f"{' readonly' if c and base.es_correo(c) else ''}></label></p>"
            f"<p><label>Cumpleaños<br><input name='fecha_nacimiento' type='date' value='{v('fecha_nacimiento')}'>"
            f"</label></p><p><label>¿Cómo nos conoció?<br><input name='como_nos_conocio' maxlength='100' "
            f"value='{v('como_nos_conocio')}'></label></p><p><label>Aceptó recibir promociones<br>"
            f"<select name='consentimiento'><option value='no'>No</option><option value='si' {si}>Sí</option>"
            f"</select></label></p>")


def html_nuevo(cfg, error=""):
    bp = e(cfg.base_path)
    return (f"<h1>Registrar cliente</h1>{'<p class=err>' + e(error) + '</p>' if error else ''}"
            f"<form method='post' action='{bp}/bandeja/clientes/nuevo'><p><label>Teléfono (WhatsApp)<br>"
            f"<input name='telefono' required inputmode='tel'></label></p>{_campos()}<button>Registrar</button></form>")


def html_ficha(con, cfg, c, usuario, error=""):
    bp, cid = e(cfg.base_path), c["id"]
    ahora = base.iso(base.ahora())
    pasadas = con.execute("SELECT * FROM cita WHERE contacto_id=? AND (inicio<=? OR estado<>'agendada') "
                          "ORDER BY inicio DESC LIMIT 50", (cid, ahora)).fetchall()
    lista_pasadas = "".join(f"<li>{e(base.fecha_humana(cfg, base.de_iso(x['inicio'])))}: "
                            f"{e(cfg.nombre_servicio(x['servicio_id']))} · {agenda.ESTADOS_CITA[x['estado']]}</li>"
                            for x in pasadas)
    notas = con.execute("SELECT * FROM nota_cliente WHERE contacto_id=? ORDER BY id DESC", (cid,)).fetchall()
    lista_notas = "".join(f"<li>{e(n['texto'])} <span class='meta'>{e(n['autor'])} · "
                          f"{e(base.fecha_humana(cfg, base.de_iso(n['creado'])))}</span></li>" for n in notas)
    baja = " · <strong>Dio de baja</strong>" if base.dio_baja(con, c["telefono"]) else ""
    return (f"<h1>{e(c['nombre'] or c['telefono'])}</h1>{'<p class=err>' + e(error) + '</p>' if error else ''}"
            f"<p>{e(c['telefono'])}{baja} · <a href='{bp}/bandeja/c/{cid}'>Ver conversación</a></p>"
            f"<details><summary>Datos</summary><form method='post' action='{bp}/bandeja/clientes/{cid}/datos'>"
            f"{_campos(c)}<button>Guardar</button></form></details>"
            + agenda.html_agendar(con, cfg, c)
            + f"<h2>Citas pasadas</h2>{'<ul>' + lista_pasadas + '</ul>' if lista_pasadas else '<p>Sin citas pasadas.</p>'}"
            + ventas.html_ventas_conversacion(con, cfg, c)
            + html_cotizaciones(con, cfg, c)
            + f"<h2>Notas del equipo</h2><form method='post' action='{bp}/bandeja/clientes/{cid}/nota'>"
              f"<textarea name='texto' required maxlength='2000'></textarea><button>Agregar nota</button></form>"
            + (f"<ul>{lista_notas}</ul>" if lista_notas else ""))


def html_cotizaciones(con, cfg, c):
    return "<h2>Cotizaciones y saldo</h2><p>Próximamente.</p>"


# ---------- rutas (las llama web.py ya con sesión iniciada; los POST ya pasaron el chequeo de Origin) ----------

def get(con, cfg, usuario, ruta, q):
    """(título, cuerpo) de una página de clientes, o None si la ruta no es de este módulo."""
    error = (q.get("error") or [""])[0]
    if ruta == "/bandeja/clientes":
        return "Clientes", html_lista(con, cfg, (q.get("q") or [""])[0].strip()[:100])
    if ruta == "/bandeja/clientes/nuevo":
        return "Registrar cliente", html_nuevo(cfg, error)
    m = re.fullmatch(r"/bandeja/clientes/(\d+)", ruta)
    if m:
        c = motor.contacto(con, int(m.group(1)))
        return (c["nombre"] or c["telefono"], html_ficha(con, cfg, c, usuario, error)) if c else ("No existe", "<p>No existe.</p>")
    return None


def post(con, cfg, usuario, ruta, form):
    """Ruta a la que redirigir tras un POST de clientes, o None si la ruta no es de este módulo."""
    if ruta == "/bandeja/clientes/nuevo":
        return registrar(con, cfg, usuario, form)
    m = re.fullmatch(r"/bandeja/clientes/(\d+)/(datos|nota)", ruta)
    if m:
        c = motor.contacto(con, int(m.group(1)))
        if not c:
            return "/bandeja/clientes"
        error = guardar_datos(con, cfg, c, form) if m.group(2) == "datos" else agregar_nota(con, c, usuario, form)
        return _ruta_ficha(c["id"], error)
    return None
