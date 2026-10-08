"""Agenda: horarios libres, propuestas 1/2/3, reservas sin doble cita, cancelar/reprogramar y Google Calendar."""
import base64
import datetime as dt
import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import base, ia, motor, wa

LOCK = threading.Lock()  # reserva = consultar disponibilidad + crear, sin intercalarse con otra reserva del proceso
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar"
_token = {"valor": None, "expira": 0.0}
_token_lock = threading.Lock()


class AgendaError(Exception):
    pass


# ---------- RSA PKCS#1 v1.5 + SHA-256 en Python puro (para el JWT de la cuenta de servicio) ----------

def _der(datos, i=0):
    """Lee un elemento DER en datos[i:]. Devuelve (tag, contenido, siguiente_indice)."""
    tag, largo = datos[i], datos[i + 1]
    i += 2
    if largo & 0x80:
        n = largo & 0x7F
        largo = int.from_bytes(datos[i:i + n], "big")
        i += n
    return tag, datos[i:i + largo], i + largo


def _enteros_secuencia(contenido):
    out, i = [], 0
    while i < len(contenido):
        tag, valor, i = _der(contenido, i)
        out.append((tag, valor))
    return out


def clave_rsa(pem):
    """PEM PKCS#8 ('BEGIN PRIVATE KEY', el de Google) o PKCS#1 ('BEGIN RSA PRIVATE KEY') → (n, d)."""
    cuerpo = "".join(l for l in pem.strip().splitlines() if not l.startswith("-----"))
    der = base64.b64decode(cuerpo)
    _, seq, _ = _der(der)
    partes = _enteros_secuencia(seq)
    if "RSA PRIVATE KEY" not in pem:            # PKCS#8: version, algoritmo, OCTET STRING con la clave PKCS#1
        _, seq, _ = _der(partes[2][1])
        partes = _enteros_secuencia(seq)
    enteros = [int.from_bytes(v, "big") for t, v in partes if t == 0x02]
    return enteros[1], enteros[3]               # version, n, e, d, ...


_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


def firmar_rs256(pem, mensaje):
    # ponytail: pow() de Python no es de tiempo constante. Aceptable aquí: firmamos un JWT por hora, en
    # nuestro servidor, sin exponer un oráculo de firma. Si se firmara por petición de terceros, usar una biblioteca.
    n, d = clave_rsa(pem)
    k = (n.bit_length() + 7) // 8
    t = _DIGEST_INFO_SHA256 + hashlib.sha256(mensaje).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt_cuenta_servicio(sa, ahora_s):
    cab = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    datos = _b64url(json.dumps({"iss": sa["client_email"], "scope": GOOGLE_SCOPE, "aud": sa["token_uri"],
                                "iat": ahora_s, "exp": ahora_s + 3600}).encode())
    firma = firmar_rs256(sa["private_key"], f"{cab}.{datos}".encode())
    return f"{cab}.{datos}.{_b64url(firma)}"


# ---------- Google Calendar ----------

def _http(metodo, url, cuerpo=None, cabeceras=None, form=False):
    datos = None
    if cuerpo is not None:
        datos = urllib.parse.urlencode(cuerpo).encode() if form else json.dumps(cuerpo).encode()
    req = urllib.request.Request(url, data=datos, method=metodo, headers={
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(cabeceras or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            contenido = r.read()
            return json.loads(contenido) if contenido else {}
    except urllib.error.HTTPError as e:
        if metodo == "DELETE" and e.code in (404, 410):
            return {}
        raise AgendaError(f"Google {e.code}: {e.read()[:300].decode(errors='replace')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AgendaError(f"red: {e}") from None


def _token_google():
    with _token_lock:
        if _token["valor"] and _token["expira"] > time.time() + 60:
            return _token["valor"]
        ruta = os.environ.get("GOOGLE_SA_FILE", "")
        if not ruta:
            raise AgendaError("falta GOOGLE_SA_FILE")
        with open(ruta, encoding="utf-8") as f:
            sa = json.load(f)
        r = _http("POST", sa["token_uri"], {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                            "assertion": jwt_cuenta_servicio(sa, int(time.time()))}, form=True)
        _token.update(valor=r["access_token"], expira=time.time() + int(r.get("expires_in", 3600)))
        return _token["valor"]


def _gcal(metodo, ruta, cuerpo=None):
    return _http(metodo, "https://www.googleapis.com/calendar/v3/" + ruta, cuerpo,
                 {"Authorization": f"Bearer {_token_google()}"})


def _cal_id(cfg):
    return urllib.parse.quote(cfg["agenda"]["calendar_id"], safe="")


def ocupado_google(cfg, t0, t1):
    if cfg["agenda"]["proveedor"] != "google":
        return []
    r = _gcal("POST", "freeBusy", {"timeMin": base.iso(t0), "timeMax": base.iso(t1),
                                   "items": [{"id": cfg["agenda"]["calendar_id"]}]})
    cal = r.get("calendars", {}).get(cfg["agenda"]["calendar_id"], {})
    if cal.get("errors"):
        raise AgendaError(f"freeBusy: {cal['errors']}")
    return [(dt.datetime.fromisoformat(b["start"]), dt.datetime.fromisoformat(b["end"])) for b in cal.get("busy", [])]


def crear_evento(cfg, inicio, fin, titulo, descripcion):
    if cfg["agenda"]["proveedor"] != "google":
        return None
    r = _gcal("POST", f"calendars/{_cal_id(cfg)}/events", {
        "summary": titulo, "description": descripcion,
        "start": {"dateTime": base.iso(inicio)}, "end": {"dateTime": base.iso(fin)}})
    return r["id"]


def borrar_evento(cfg, evento_id):
    if cfg["agenda"]["proveedor"] == "google" and evento_id:
        _gcal("DELETE", f"calendars/{_cal_id(cfg)}/events/{urllib.parse.quote(evento_id, safe='')}")


# ---------- disponibilidad ----------

def ocupados(con, cfg, t0, t1, excluir_cita=None):
    """Intervalos ocupados (UTC) entre t0 y t1, ya ampliados por el margen entre citas."""
    margen = dt.timedelta(minutes=int(cfg["agenda"]["margen_min"]))
    filas = con.execute("SELECT id, inicio, fin FROM cita WHERE estado='agendada' AND fin>? AND inicio<?",
                        (base.iso(t0 - margen), base.iso(t1 + margen))).fetchall()
    propios = [(base.de_iso(f["inicio"]), base.de_iso(f["fin"])) for f in filas if f["id"] != excluir_cita]
    externos = ocupado_google(cfg, t0 - margen, t1 + margen)
    if excluir_cita:
        # Google recorta los bloques a la ventana consultada y une los contiguos: se resta el intervalo de la
        # cita que se reprograma en vez de compararlo exacto.
        r = con.execute("SELECT inicio, fin FROM cita WHERE id=?", (excluir_cita,)).fetchone()
        if r:
            externos = restar(externos, base.de_iso(r["inicio"]), base.de_iso(r["fin"]))
    return [(a - margen, b + margen) for a, b in propios + externos]


def restar(intervalos, x, y):
    """Intervalos menos [x, y)."""
    out = []
    for a, b in intervalos:
        if b <= x or a >= y:
            out.append((a, b))
            continue
        if a < x:
            out.append((a, x))
        if b > y:
            out.append((y, b))
    return out


def libre(intervalos, inicio, fin):
    return all(fin <= a or inicio >= b for a, b in intervalos)


def duracion(cfg, servicio_id):
    return dt.timedelta(minutes=int(cfg.servicios[servicio_id]["duracion_min"]))


def horarios_libres(con, cfg, servicio_id, n=3, excluir_cita=None):
    """Hasta n horarios: el primero libre y los siguientes separados al menos 'separacion_propuestas_horas'."""
    ag = cfg["agenda"]
    desde = base.ahora() + dt.timedelta(hours=float(ag["anticipacion_min_horas"]))
    hasta = base.ahora() + dt.timedelta(days=int(ag["dias_adelante"]))
    dur, paso = duracion(cfg, servicio_id), dt.timedelta(minutes=int(ag["paso_min"]))
    sep = dt.timedelta(hours=float(ag["separacion_propuestas_horas"]))
    busy = ocupados(con, cfg, desde, hasta, excluir_cita)
    actual = con.execute("SELECT inicio FROM cita WHERE id=?", (excluir_cita,)).fetchone() if excluir_cita else None
    if actual:  # al reprogramar no se ofrece el mismo horario que ya tiene
        busy.append((base.de_iso(actual["inicio"]), base.de_iso(actual["inicio"]) + dt.timedelta(seconds=1)))
    elegidos = []
    fecha = desde.astimezone(cfg.tz).date()
    while fecha <= hasta.astimezone(cfg.tz).date() and len(elegidos) < n:
        for a, b in base.intervalos_dia(cfg, fecha):
            s = a
            while s + dur <= b and len(elegidos) < n:
                su = s.astimezone(base.UTC)
                if su >= desde and su + dur <= hasta and libre(busy, su, su + dur) and \
                        (not elegidos or su - elegidos[-1] >= sep):
                    elegidos.append(su)
                s += paso
        fecha += dt.timedelta(days=1)
    return elegidos


# ---------- acciones ----------

def citas_futuras(con, cid):
    return con.execute("SELECT * FROM cita WHERE contacto_id=? AND estado='agendada' AND inicio>? ORDER BY inicio",
                       (cid, base.iso(base.ahora()))).fetchall()


def _guardar_propuesta(con, cid, datos):
    datos["expira"] = base.iso(base.ahora() + dt.timedelta(hours=24))
    con.execute("UPDATE contacto SET propuesta=? WHERE id=?", (json.dumps(datos), cid))


def proponer(con, cfg, c, servicio_id, reprograma=None):
    try:
        slots = horarios_libres(con, cfg, servicio_id, excluir_cita=reprograma)
    except AgendaError as e:
        base.log("agenda:", e)
        motor.handoff(con, cfg, c["id"], "agenda_error")
        return
    if not slots:
        motor.responder(con, cfg, c, cfg.msg["sin_horarios"])
        motor.handoff(con, cfg, c["id"], "sin_horarios")
        return
    _guardar_propuesta(con, c["id"], {"tipo": "horarios", "servicio_id": servicio_id,
                                      "slots": [base.iso(s) for s in slots], "reprograma": reprograma})
    opciones = "\n".join(f"{i}) {base.fecha_humana(cfg, s)}" for i, s in enumerate(slots, 1))
    motor.responder(con, cfg, c, cfg.msg["propuesta"].format(servicio=cfg.servicios[servicio_id]["nombre"],
                                                              opciones=opciones))


def reservar(con, cfg, c, servicio_id, inicio, creado_por="bot", reprograma=None):
    """Revisa disponibilidad justo antes de crear. Devuelve id de cita, o None si el horario ya no sirve
    (ocupado, en el pasado, o para el bot, con menos anticipación que la configurada)."""
    fin = inicio + duracion(cfg, servicio_id)
    minimo = base.ahora() + (dt.timedelta(hours=float(cfg["agenda"]["anticipacion_min_horas"]))
                             if creado_por == "bot" else dt.timedelta(0))
    if inicio < minimo:   # mismo límite que horarios_libres (>=)
        return None
    with LOCK:
        if not libre(ocupados(con, cfg, inicio, fin, excluir_cita=reprograma), inicio, fin):
            return None
        titulo = f"{cfg.servicios[servicio_id]['nombre']} - {c['nombre'] or 'cliente'} ({c['telefono']})"
        evento_id = crear_evento(cfg, inicio, fin, titulo, f"Agendada por: {creado_por}. WhatsApp: {c['telefono']}")
        cid = con.execute("INSERT INTO cita (contacto_id, servicio_id, inicio, fin, evento_id, creado, creado_por) "
                          "VALUES (?,?,?,?,?,?,?)", (c["id"], servicio_id, base.iso(inicio), base.iso(fin), evento_id,
                                                     base.iso(base.ahora()), creado_por)).lastrowid
    con.execute("UPDATE contacto SET seg_activo=0, propuesta=NULL WHERE id=?", (c["id"],))
    base.evento(con, c["id"], "cita", str(cid))
    if reprograma:
        anterior = con.execute("SELECT estado FROM cita WHERE id=?", (reprograma,)).fetchone()
        if anterior and anterior["estado"] == "agendada":   # si ya se marcó Asistió/No asistió, no se toca
            try:
                cancelar(con, cfg, reprograma)
            except AgendaError as e:
                # la cita nueva ya existe: se cancela la anterior en la base y se pide al equipo borrar el evento
                base.log("agenda: no se pudo borrar el evento anterior:", e)
                con.execute("UPDATE cita SET estado='cancelada' WHERE id=?", (reprograma,))
                motor.avisar_equipo(con, cfg, c, "evento_huerfano")
    return cid


def cancelar(con, cfg, cita_id):
    cita = con.execute("SELECT * FROM cita WHERE id=?", (cita_id,)).fetchone()
    borrar_evento(cfg, cita["evento_id"])
    con.execute("UPDATE cita SET estado='cancelada' WHERE id=?", (cita_id,))
    return cita


def _cambio_simple(cfg, citas):
    limite = base.ahora() + dt.timedelta(hours=float(cfg["agenda"]["cambio_min_horas"]))
    return len(citas) == 1 and base.de_iso(citas[0]["inicio"]) > limite


def ejecutar(con, cfg, c, r):
    """Acciones de agenda que decidió la IA."""
    if r["accion"] == "proponer_cita":
        sid = r["servicio_id"] or (next(iter(cfg.servicios)) if len(cfg.servicios) == 1 else "")
        if sid:
            return proponer(con, cfg, c, sid)
        if r["texto"] and not ia.problema_texto(cfg, r["texto"]):
            return motor.responder(con, cfg, c, r["texto"])   # la IA pregunta qué servicio
        return motor.handoff(con, cfg, c["id"], "ia:proponer_cita sin servicio")
    citas = citas_futuras(con, c["id"])
    if not _cambio_simple(cfg, citas):
        return motor.handoff(con, cfg, c["id"], "cambio_cita_complejo")
    cita = citas[0]
    if r["accion"] == "cancelar_cita":
        _guardar_propuesta(con, c["id"], {"tipo": "cancelar", "cita_id": cita["id"]})
        return motor.responder(con, cfg, c, cfg.msg["confirmar_cancelacion"].format(
            servicio=cfg.servicios.get(cita["servicio_id"], {}).get("nombre", cita["servicio_id"]),
            fecha=base.fecha_humana(cfg, base.de_iso(cita["inicio"]))))
    return proponer(con, cfg, c, cita["servicio_id"], reprograma=cita["id"])


_ELECCION = re.compile(r"^(?:(?:la|el|opcion|numero|quiero la|quiero el)\s+)?([1-9])$")
_SI = {"si", "si por favor", "si cancela", "si cancelala", "si gracias", "confirmo", "claro", "ok", "de acuerdo"}


def responder_propuesta(con, cfg, c, tn):
    """True si el mensaje respondió a la propuesta pendiente (y ya se atendió)."""
    p = json.loads(c["propuesta"])
    if base.de_iso(p["expira"]) < base.ahora():
        con.execute("UPDATE contacto SET propuesta=NULL WHERE id=?", (c["id"],))
        return False
    if p["tipo"] == "cancelar":
        if tn in _SI:
            cita = con.execute("SELECT * FROM cita WHERE id=?", (p["cita_id"],)).fetchone()
            if not cita or cita["estado"] != "agendada":
                con.execute("UPDATE contacto SET propuesta=NULL WHERE id=?", (c["id"],))
                return False
            try:
                cancelar(con, cfg, cita["id"])
            except AgendaError as e:
                base.log("agenda:", e)
                motor.handoff(con, cfg, c["id"], "agenda_error")
                return True
            con.execute("UPDATE contacto SET propuesta=NULL WHERE id=?", (c["id"],))
            motor.responder(con, cfg, c, cfg.msg["cita_cancelada"].format(
                fecha=base.fecha_humana(cfg, base.de_iso(cita["inicio"]))))
            return True
        if tn == "no":
            con.execute("UPDATE contacto SET propuesta=NULL WHERE id=?", (c["id"],))
            motor.responder(con, cfg, c, "De acuerdo, tu cita sigue en pie.")
            return True
        return False
    m = _ELECCION.match(tn)
    if not m or not 1 <= int(m.group(1)) <= len(p["slots"]):
        return False
    inicio = base.de_iso(p["slots"][int(m.group(1)) - 1])
    try:
        cita_id = reservar(con, cfg, c, p["servicio_id"], inicio, reprograma=p.get("reprograma"))
    except AgendaError as e:
        base.log("agenda:", e)
        motor.handoff(con, cfg, c["id"], "agenda_error")
        return True
    if cita_id is None:
        motor.responder(con, cfg, c, cfg.msg["horario_ocupado"])
        proponer(con, cfg, motor.contacto(con, c["id"]), p["servicio_id"], reprograma=p.get("reprograma"))
        return True
    motor.responder(con, cfg, c, cfg.msg["cita_confirmada"].format(
        servicio=cfg.servicios[p["servicio_id"]]["nombre"], fecha=base.fecha_humana(cfg, inicio)))
    return True


# ---------- bandeja ----------

e = html.escape


def html_agendar(con, cfg, c):
    bp = e(cfg.base_path)
    citas = citas_futuras(con, c["id"])
    lista = "".join(f"<li>{e(cfg.servicios.get(x['servicio_id'], {}).get('nombre', x['servicio_id']))}: "
                    f"{e(base.fecha_humana(cfg, base.de_iso(x['inicio'])))}</li>" for x in citas)
    opciones = "".join(f"<option value='{e(s['id'])}'>{e(s['nombre'])}</option>" for s in cfg.servicios.values())
    return (f"<h2>Citas</h2>{'<ul>' + lista + '</ul>' if lista else '<p>Sin citas próximas.</p>'}"
            f"<form method='post' action='{bp}/bandeja/c/{c['id']}/agendar'><label for='sv'>Servicio</label> "
            f"<select id='sv' name='servicio'>{opciones}</select> <label for='fe'>Fecha</label> "
            f"<input id='fe' type='date' name='fecha' required> <label for='ho'>Hora</label> "
            f"<input id='ho' type='time' name='hora' required step='300'> <button>Agendar</button></form>")


def accion_agendar(con, cfg, c, usuario, form):
    sid = (form.get("servicio") or [""])[0]
    try:
        local = dt.datetime.strptime(f"{form['fecha'][0]} {form['hora'][0]}", "%Y-%m-%d %H:%M").replace(tzinfo=cfg.tz)
    except (KeyError, ValueError):
        return "Fecha u hora inválida."
    if sid not in cfg.servicios:
        return "Servicio inválido."
    inicio = local.astimezone(base.UTC)
    if inicio <= base.ahora():
        return "La cita debe ser en el futuro."
    try:
        cita_id = reservar(con, cfg, c, sid, inicio, creado_por=f"humano:{usuario}")
    except AgendaError as ex:
        return f"Error de calendario: {ex}"
    if cita_id is None:
        return "Ese horario está ocupado."
    fecha = base.fecha_humana(cfg, inicio)
    nombre = cfg.servicios[sid]["nombre"]
    from .web import ventana_abierta
    if ventana_abierta(c):
        motor.responder(con, cfg, c, cfg.msg["cita_confirmada"].format(servicio=nombre, fecha=fecha),
                        autor=f"humano:{usuario}")
    elif not base.dio_baja(con, c["telefono"]):
        wa.enviar(con, cfg, c["telefono"], plantilla="cita_confirmada", params=[c["nombre"], nombre, cfg["nombre"], fecha],
                  contacto_id=c["id"], autor=f"humano:{usuario}")
    return None


ESTADOS_CITA = {"agendada": "Agendada", "asistio": "Asistió", "no_asistio": "No asistió", "cancelada": "Cancelada"}
EXTRAS_FILA_CITA = []  # la etapa 4 agrega el registro de venta


def pagina_citas(con, cfg, usuario, q):
    bp = e(cfg.base_path)
    hoy = base.ahora().astimezone(cfg.tz).replace(hour=0, minute=0, second=0)
    filas = con.execute("SELECT ci.*, co.nombre, co.telefono FROM cita ci JOIN contacto co ON co.id=ci.contacto_id "
                        "WHERE ci.inicio>=? AND ci.inicio<? ORDER BY ci.inicio",
                        (base.iso(hoy - dt.timedelta(days=1)), base.iso(hoy + dt.timedelta(days=8)))).fetchall()
    boton = lambda cid, estado, txt: (f"<form class='inline' method='post' action='{bp}/bandeja/citas/marcar'>"  # noqa: E731
                                      f"<input type='hidden' name='cita' value='{cid}'><input type='hidden' "
                                      f"name='estado' value='{estado}'><button>{txt}</button></form>")
    rows = "".join(
        f"<tr><td>{e(base.fecha_humana(cfg, base.de_iso(f['inicio'])))}</td><td><a href='{bp}/bandeja/c/{f['contacto_id']}'>"
        f"{e(f['nombre'] or f['telefono'])}</a></td><td>{e(cfg.servicios.get(f['servicio_id'], {}).get('nombre', f['servicio_id']))}"
        f"</td><td>{ESTADOS_CITA[f['estado']]}</td><td>"
        + (((boton(f["id"], "asistio", "Asistió") + boton(f["id"], "no_asistio", "No asistió")
             if ya_es_el_dia(cfg, f) else "") + boton(f["id"], "cancelada", "Cancelar")) if f["estado"] == "agendada" else "")
        + "".join(x(con, cfg, f) for x in EXTRAS_FILA_CITA) + "</td></tr>" for f in filas)
    error = (q.get("error") or [""])[0]
    return (f"<h1>Citas (ayer a 7 días)</h1>{'<p class=err>' + e(error) + '</p>' if error else ''}"
            f"<table><tr><th>Cuándo</th><th>Cliente</th><th>Servicio</th><th>Estado</th><th></th></tr>{rows}</table>")


def ya_es_el_dia(cfg, cita):
    """La asistencia solo se marca el día de la cita o después."""
    return base.de_iso(cita["inicio"]).astimezone(cfg.tz).date() <= base.ahora().astimezone(cfg.tz).date()


def marcar_cita(con, cfg, usuario, form):
    try:
        cita_id, estado = int(form["cita"][0]), form["estado"][0]
    except (KeyError, ValueError):
        return "/bandeja/citas?error=" + urllib.parse.quote("Solicitud inválida.")
    cita = con.execute("SELECT * FROM cita WHERE id=?", (cita_id,)).fetchone()
    if not cita or cita["estado"] != "agendada" or estado not in ("asistio", "no_asistio", "cancelada"):
        return "/bandeja/citas?error=" + urllib.parse.quote("La cita ya no está agendada.")
    if estado != "cancelada" and not ya_es_el_dia(cfg, cita):
        return "/bandeja/citas?error=" + urllib.parse.quote("La asistencia se marca el día de la cita o después.")
    if estado == "cancelada":
        try:
            cancelar(con, cfg, cita_id)
        except AgendaError as ex:
            return "/bandeja/citas?error=" + urllib.parse.quote(f"Error de calendario: {ex}")
    else:
        con.execute("UPDATE cita SET estado=?, asistio_en=? WHERE id=?",
                    (estado, base.iso(base.ahora()) if estado == "asistio" else None, cita_id))
    return "/bandeja/citas"
