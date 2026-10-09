"""Fichas de clientes en la bandeja (alta a mano, datos, notas, citas, ventas) y cotizaciones."""
import datetime as dt
import html
import re
from urllib.parse import quote

from . import agenda, base, correo, motor, ventas, wa

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


# ---------- cotizaciones ----------

ESTADOS_COT = {"borrador": "Borrador", "enviada": "Enviada", "aceptada": "Aceptada", "rechazada": "Rechazada",
               "vencida": "Vencida"}
FILAS_FORM = 5


def cotizacion(con, qid):
    return con.execute("SELECT * FROM cotizacion WHERE id=?", (qid,)).fetchone()


def lineas(con, qid):
    return con.execute("SELECT * FROM cotizacion_linea WHERE cotizacion_id=? ORDER BY id", (qid,)).fetchall()


def lineas_de_form(cfg, form):
    """Filas del formulario → ([(servicio_id, descripcion, cantidad, precio_centavos)], error). Una fila con servicio
    y sin precio toma el precio del config; una fila libre necesita descripción y precio."""
    campos = [form.get(k) or [] for k in ("servicio", "descripcion", "cantidad", "precio")]
    filas = []
    for servicio, desc, cant, precio in zip(*[x + [""] * (max(map(len, campos)) - len(x)) for x in campos]):
        servicio, desc, cant, precio = (x.strip() for x in (servicio, desc, cant, precio))
        if not servicio and not desc:
            continue
        s = cfg.servicios.get(servicio)
        if servicio and not s:
            return None, f"Servicio desconocido: {servicio}"
        if not cant.isdigit() or not 1 <= int(cant) <= 999:
            return None, f"Cantidad inválida en «{desc or s['nombre']}» (1 a 999)."
        try:
            centavos = ventas.a_centavos(precio) if precio else round(float(s["precio_mxn"]) * 100) if s else None
        except ValueError as ex:
            return None, f"{ex} en «{desc or s['nombre']}»."
        if not centavos:
            return None, f"Falta el precio de «{desc}»."
        filas.append((servicio or None, (desc or s["nombre"])[:120], int(cant), centavos))
    return (filas, None) if filas else (None, "Agrega al menos una línea.")


def guardar_cotizacion(con, cfg, c, usuario, form, qid=None):
    """Crea un borrador (qid=None) o reemplaza las líneas de uno. Devuelve (qid, error)."""
    filas, error = lineas_de_form(cfg, form)
    if error:
        return qid, error
    total = sum(cant * precio for _, _, cant, precio in filas)
    if qid is None:
        qid = con.execute("INSERT INTO cotizacion (contacto_id, vigencia_dias, total_centavos, creado_por, creado) "
                          "VALUES (?,?,?,?,?)", (c["id"], int(cfg["cotizaciones"]["vigencia_dias"]), total, usuario,
                                                  base.iso(base.ahora()))).lastrowid
        # folio consecutivo del negocio a partir del id (sin carreras entre dos personas del equipo)
        con.execute("UPDATE cotizacion SET folio=printf('C-%04d', id) WHERE id=?", (qid,))
    else:
        con.execute("DELETE FROM cotizacion_linea WHERE cotizacion_id=?", (qid,))   # líneas de un borrador
        con.execute("UPDATE cotizacion SET total_centavos=? WHERE id=?", (total, qid))
    con.executemany("INSERT INTO cotizacion_linea (cotizacion_id, servicio_id, descripcion, cantidad, precio_centavos) "
                    "VALUES (?,?,?,?,?)", [(qid, *f) for f in filas])
    return qid, None


def vigente_hasta(cfg, q):
    """Último día (local) en que vale la cotización; None si no se ha enviado."""
    if not q["enviada_en"]:
        return None
    return base.de_iso(q["enviada_en"]).astimezone(cfg.tz).date() + dt.timedelta(days=q["vigencia_dias"])


def texto_cotizacion(con, cfg, q):
    filas = "\n".join(f"- {l['cantidad']} × {l['descripcion']}: {ventas._pesos(l['cantidad'] * l['precio_centavos'])}"
                      for l in lineas(con, q["id"]))
    hasta = vigente_hasta(cfg, q) or base.ahora().astimezone(cfg.tz).date() + dt.timedelta(days=q["vigencia_dias"])
    return (f"Cotización {q['folio']} de {cfg['nombre']}:\n{filas}\nTotal: {ventas._pesos(q['total_centavos'])}\n"
            f"Vigente hasta el {base.fecha_humana(cfg, dt.datetime.combine(hasta, dt.time(12), cfg.tz), con_hora=False)}."
            " Responde a este mensaje si quieres agendar o tienes dudas.")


def enviar_cotizacion(con, cfg, q, usuario):
    """Por la conversación si se le puede escribir (ventana de 24 h, o correo); si no, por correo si tiene; si no,
    con la plantilla 'cotizacion' (requiere aprobación de Meta). Devuelve error o None."""
    if q["estado"] not in ("borrador", "enviada"):
        return f"La cotización está {ESTADOS_COT[q['estado']].lower()}: no se puede enviar."
    c = motor.contacto(con, q["contacto_id"])
    q = dict(q) | {"enviada_en": q["enviada_en"] or base.iso(base.ahora())}
    texto = texto_cotizacion(con, cfg, q)
    if base.ventana_abierta(c):
        ok = motor.responder(con, cfg, c, texto, autor=f"humano:{usuario}")[1]
        canal = "conversación"
    elif c["email"] and correo.configurado(cfg):
        ok = correo.enviar(cfg, [c["email"]], f"Cotización {q['folio']} · {cfg['nombre']}", texto)[1] is None
        canal = "correo"
    elif base.dio_baja(con, c["telefono"]):
        return "El contacto dio de baja y no tiene correo: no se le puede enviar."
    elif not base.en_ventana_envio(cfg, base.ahora()):
        return "Ventana de 24 h cerrada y fuera del horario de envío (9:00 a 20:00, lunes a sábado)."
    else:
        hasta = vigente_hasta(cfg, q)
        ok = wa.enviar(con, cfg, c["telefono"], plantilla="cotizacion", contacto_id=c["id"], autor=f"humano:{usuario}",
                       params=[c["nombre"], cfg["nombre"], q["folio"], ventas._pesos(q["total_centavos"]),
                               hasta.strftime("%d/%m/%Y")])[1]
        canal = "plantilla"
    if not ok:
        return "No se pudo enviar (ver el error en la conversación o en el log)."
    con.execute("UPDATE cotizacion SET estado='enviada', enviada_en=? WHERE id=?", (q["enviada_en"], q["id"]))
    base.evento(con, c["id"], "cotizacion", f"{q['folio']}:{canal}")
    return None


def marcar(con, q, estado):
    if q["estado"] not in ("enviada", "vencida"):
        return "Solo una cotización enviada (o vencida) se marca como aceptada o rechazada."
    con.execute("UPDATE cotizacion SET estado=?, respondida_en=? WHERE id=?", (estado, base.iso(base.ahora()), q["id"]))
    return None


def ligar_cita(con, q, form):
    cita = _campo(form, "cita")
    if cita and not (cita.isdigit() and con.execute("SELECT 1 FROM cita WHERE id=? AND contacto_id=?",
                                                    (int(cita), q["contacto_id"])).fetchone()):
        return "La cita no es de este cliente."
    con.execute("UPDATE cotizacion SET cita_id=? WHERE id=?", (int(cita) if cita else None, q["id"]))
    return None


def vencer_cotizaciones(con, cfg, t):
    """Paso del tick: una cotización enviada pasa a 'vencida' el día después de su vigencia."""
    hoy, n = t.astimezone(cfg.tz).date(), 0
    for q in con.execute("SELECT * FROM cotizacion WHERE estado='enviada'").fetchall():
        if hoy > vigente_hasta(cfg, q):
            n += con.execute("UPDATE cotizacion SET estado='vencida' WHERE id=? AND estado='enviada'", (q["id"],)).rowcount
    return n


def abiertas(con, cid):
    """Enviadas y no vencidas: las que el cliente puede estar contestando."""
    return con.execute("SELECT * FROM cotizacion WHERE contacto_id=? AND estado='enviada' ORDER BY id", (cid,)).fetchall()


def montos_cotizados(con, cid):
    """Montos (pesos) que la IA puede mencionar a ESTE cliente: totales, precios y subtotales de sus cotizaciones
    abiertas. A nadie más."""
    montos = set()
    for q in abiertas(con, cid):
        montos.add(q["total_centavos"] / 100)
        for l in lineas(con, q["id"]):
            montos |= {l["precio_centavos"] / 100, l["cantidad"] * l["precio_centavos"] / 100}
    return montos


def contexto_ia(con, cfg, cid):
    qs = abiertas(con, cid)
    if not qs:
        return ""
    lista = "; ".join(f"{q['folio']} por {ventas._pesos(q['total_centavos'])}, vigente hasta "
                      f"{vigente_hasta(cfg, q).isoformat()}" for q in qs)
    return (f" Cotizaciones enviadas al cliente: {lista}. Si el cliente acepta una cotización o quiere cambiarla, "
            f"usa accion=humano con motivo 'cotizacion:<folio>'; no la confirmes tú.")


def _form_lineas(cfg, filas=()):
    opciones = lambda sel: "<option value=''>— libre —</option>" + "".join(   # noqa: E731
        f"<option value='{e(s['id'])}' {'selected' if s['id'] == sel else ''}>{e(s['nombre'])} "
        f"(${s['precio_mxn']:,})</option>" for s in cfg.servicios.values())
    filas = list(filas) + [None] * max(FILAS_FORM - len(filas), 2)
    return "<table><tr><th>Servicio</th><th>Descripción</th><th>Cant.</th><th>Precio c/u (vacío = el del servicio)</th></tr>" + "".join(
        f"<tr><td><select name='servicio'>{opciones(l['servicio_id'] if l else '')}</select></td>"
        f"<td><input name='descripcion' maxlength='120' value='{e(l['descripcion']) if l else ''}'></td>"
        f"<td><input name='cantidad' size='3' inputmode='numeric' value='{l['cantidad'] if l else 1}'></td>"
        f"<td><input name='precio' size='9' inputmode='decimal' value='{l['precio_centavos'] / 100 if l else ''}'></td></tr>"
        for l in filas) + "</table>"


def html_cotizaciones(con, cfg, c):
    bp = e(cfg.base_path)
    qs = con.execute("SELECT * FROM cotizacion WHERE contacto_id=? ORDER BY id DESC", (c["id"],)).fetchall()
    filas = "".join(f"<tr><td><a href='{bp}/bandeja/cotizaciones/{q['id']}'>{e(q['folio'])}</a></td>"
                    f"<td>{ESTADOS_COT[q['estado']]}</td><td>{ventas._pesos(q['total_centavos'])}</td>"
                    f"<td>{e(str(vigente_hasta(cfg, q) or '—'))}</td>{html_saldo_fila(con, q)}</tr>" for q in qs)
    return (f"<h2>Cotizaciones</h2>{html_saldo_cliente(con, c)}"
            + (f"<table><tr><th>Folio</th><th>Estado</th><th>Total</th><th>Vigente hasta</th>{html_saldo_cab()}</tr>"
               f"{filas}</table>" if qs else "<p>Sin cotizaciones.</p>")
            + f"<details><summary>Nueva cotización</summary><form method='post' "
              f"action='{bp}/bandeja/clientes/{c['id']}/cotizacion'>{_form_lineas(cfg)}<button>Guardar borrador</button>"
              f"</form></details>")


# ---------- saldo ----------

def saldo(con, q):
    """Lo que falta pagar de una cotización aceptada: total − ventas ligadas (centavos)."""
    pagado = con.execute("SELECT COALESCE(SUM(monto_centavos),0) FROM venta WHERE cotizacion_id=?", (q["id"],)).fetchone()[0]
    return q["total_centavos"] - pagado


def con_saldo(con, cid):
    return [q for q in con.execute("SELECT * FROM cotizacion WHERE contacto_id=? AND estado='aceptada' ORDER BY id",
                                   (cid,)).fetchall() if saldo(con, q) > 0]


def ligar_venta(con, venta_id, cid, centavos):
    """Liga la venta a la cotización que paga solo si no hay duda: el cliente tiene exactamente una aceptada con
    saldo y el monto no la rebasa. Si no, queda sin ligar y el cotejo la muestra."""
    pendientes = con_saldo(con, cid)
    if len(pendientes) == 1 and centavos <= saldo(con, pendientes[0]):
        con.execute("UPDATE venta SET cotizacion_id=? WHERE id=?", (pendientes[0]["id"], venta_id))
        return pendientes[0]["id"]
    return None


def html_saldo_cab():
    return "<th>Saldo</th>"


def html_saldo_fila(con, q):
    return f"<td>{ventas._pesos(saldo(con, q)) if q['estado'] == 'aceptada' else '—'}</td>"


def html_saldo_cliente(con, c):
    total = sum(saldo(con, q) for q in con_saldo(con, c["id"]))
    return f"<p>Saldo pendiente del cliente: <strong>{ventas._pesos(total)}</strong></p>"


# ---------- cotejo: cotización → cita → venta/pago ----------

CATEGORIAS_COTEJO = {
    "aceptada_sin_cita": "Cotizaciones aceptadas sin cita",
    "asistio_sin_venta": "Citas con «Asistió» sin venta",
    "venta_sin_ligar": "Ventas sin ligar de clientes con cotización abierta",
    "saldo_pendiente": "Ventas ligadas que no cubren el total de la cotización",
    "vencida": "Cotizaciones enviadas sin respuesta que vencieron",
    "pago_sin_cliente": "Pagos en línea sin cliente (pagos-sin-contacto.csv)",
}


def datos_cotejo(con, cfg, mes):
    """{categoría: [(contacto_id | None, nombre, detalle)]} del mes AAAA-MM (hora local)."""
    a, b = ventas._rango_mes(cfg, mes)
    A, B, da, db = base.iso(a), base.iso(b), a.date().isoformat(), b.date().isoformat()
    r = {k: [] for k in CATEGORIAS_COTEJO}
    nombre = lambda f: f["nombre"] or f["telefono"]   # noqa: E731
    for q in con.execute("SELECT q.*, c.nombre, c.telefono FROM cotizacion q JOIN contacto c ON c.id=q.contacto_id "
                         "WHERE q.estado='aceptada' AND q.respondida_en>=? AND q.respondida_en<? AND q.cita_id IS NULL "
                         "AND NOT EXISTS (SELECT 1 FROM cita ci WHERE ci.contacto_id=q.contacto_id AND "
                         "ci.estado<>'cancelada' AND ci.creado>=q.respondida_en) ORDER BY q.id", (A, B)):
        r["aceptada_sin_cita"].append((q["contacto_id"], nombre(q), f"{q['folio']} por {ventas._pesos(q['total_centavos'])}"))
    for ci in con.execute("SELECT ci.*, c.nombre, c.telefono FROM cita ci JOIN contacto c ON c.id=ci.contacto_id "
                          "WHERE ci.estado='asistio' AND ci.inicio>=? AND ci.inicio<? ORDER BY ci.inicio", (A, B)):
        dia = base.de_iso(ci["inicio"]).astimezone(cfg.tz).date().isoformat()
        if not con.execute("SELECT 1 FROM venta WHERE cita_id=? OR (contacto_id=? AND fecha>=?)",
                           (ci["id"], ci["contacto_id"], dia)).fetchone():
            r["asistio_sin_venta"].append((ci["contacto_id"], nombre(ci), f"{cfg.nombre_servicio(ci['servicio_id'])} el {dia}"))
    for v in con.execute("SELECT v.*, c.nombre, c.telefono FROM venta v JOIN contacto c ON c.id=v.contacto_id "
                         "WHERE v.fecha>=? AND v.fecha<? AND v.cotizacion_id IS NULL ORDER BY v.fecha", (da, db)):
        abiertas_ = con_saldo(con, v["contacto_id"])
        if abiertas_:
            r["venta_sin_ligar"].append((v["contacto_id"], nombre(v), f"{ventas._pesos(v['monto_centavos'])} el {v['fecha']}; "
                                         f"abiertas: {', '.join(q['folio'] for q in abiertas_)}"))
    for q in con.execute("SELECT q.*, c.nombre, c.telefono FROM cotizacion q JOIN contacto c ON c.id=q.contacto_id "
                         "WHERE q.estado='aceptada' AND EXISTS (SELECT 1 FROM venta v WHERE v.cotizacion_id=q.id "
                         "AND v.fecha>=? AND v.fecha<?) ORDER BY q.id", (da, db)):
        if saldo(con, q):
            r["saldo_pendiente"].append((q["contacto_id"], nombre(q), f"{q['folio']}: total {ventas._pesos(q['total_centavos'])}"
                                         f", falta {ventas._pesos(saldo(con, q))}"))
    for q in con.execute("SELECT q.*, c.nombre, c.telefono FROM cotizacion q JOIN contacto c ON c.id=q.contacto_id "
                         "WHERE q.estado='vencida' ORDER BY q.id"):
        hasta = vigente_hasta(cfg, q)
        if da <= hasta.isoformat() < db:
            r["vencida"].append((q["contacto_id"], nombre(q), f"{q['folio']} venció el {hasta.isoformat()}"))
    ruta = cfg.carpeta / "pagos-sin-contacto.csv"
    if ruta.exists():
        for fila in ventas.leer_csv(ruta):
            if da <= (fila.get("fecha") or "") < db:
                r["pago_sin_cliente"].append((None, fila.get("nombre") or fila.get("email") or fila.get("telefono") or "?",
                                              f"${fila.get('monto')} el {fila.get('fecha')} · {fila.get('proveedor')} "
                                              f"{fila.get('pago')} · tel {fila.get('telefono') or '—'}"))
    return r


def cotejo_md(cfg, mes, d):
    partes = [f"# Cotejo {mes} · {cfg['nombre']}", ""]
    for k, titulo in CATEGORIAS_COTEJO.items():
        partes.append(f"## {titulo} ({len(d[k])})")
        partes += [f"- {nombre}: {detalle}" for _, nombre, detalle in d[k]] or ["- Sin diferencias."]
        partes.append("")
    return "\n".join(partes)


def html_cotejo(con, cfg, mes):
    bp = e(cfg.base_path)
    d = datos_cotejo(con, cfg, mes)
    enlace = lambda cid, nombre: f"<a href='{bp}/bandeja/clientes/{cid}'>{e(nombre)}</a>" if cid else e(nombre)  # noqa: E731
    secciones = "".join(
        f"<h2>{titulo} ({len(d[k])})</h2>" + ("<ul>" + "".join(f"<li>{enlace(cid, nombre)}: {e(detalle)}</li>"
                                                              for cid, nombre, detalle in d[k]) + "</ul>"
                                              if d[k] else "<p>Sin diferencias.</p>")
        for k, titulo in CATEGORIAS_COTEJO.items())
    return (f"<h1>Cotejo</h1><form method='get' action='{bp}/bandeja/cotejo'><label>Mes <input type='month' name='mes' "
            f"value='{e(mes)}'></label> <button>Ver</button></form>{secciones}")


def html_cotizacion(con, cfg, q, error=""):
    bp, qid = e(cfg.base_path), q["id"]
    c = motor.contacto(con, q["contacto_id"])
    boton = lambda accion, texto: (f"<form class='inline' method='post' action='{bp}/bandeja/cotizaciones/{qid}/{accion}'>"  # noqa: E731
                                   f"<button>{texto}</button></form>")
    detalle = "".join(f"<tr><td>{l['cantidad']}</td><td>{e(l['descripcion'])}</td><td>{ventas._pesos(l['precio_centavos'])}"
                      f"</td><td>{ventas._pesos(l['cantidad'] * l['precio_centavos'])}</td></tr>" for l in lineas(con, qid))
    if q["estado"] == "borrador":
        acciones = (f"<h2>Editar</h2><form method='post' action='{bp}/bandeja/cotizaciones/{qid}/guardar'>"
                    f"{_form_lineas(cfg, lineas(con, qid))}<button>Guardar</button></form><p>{boton('enviar', 'Enviar al cliente')}</p>")
    elif q["estado"] in ("enviada", "vencida"):
        acciones = (f"<p>{boton('aceptar', 'Marcar aceptada')}{boton('rechazar', 'Marcar rechazada')}"
                    + (boton("enviar", "Reenviar") if q["estado"] == "enviada" else "") + "</p>")
    else:
        acciones = ""
    citas = con.execute("SELECT * FROM cita WHERE contacto_id=? AND estado<>'cancelada' ORDER BY inicio DESC LIMIT 20",
                        (c["id"],)).fetchall()
    opciones = "<option value=''>— ninguna —</option>" + "".join(
        f"<option value='{x['id']}' {'selected' if x['id'] == q['cita_id'] else ''}>"
        f"{e(base.fecha_humana(cfg, base.de_iso(x['inicio'])))} · {e(cfg.nombre_servicio(x['servicio_id']))}</option>"
        for x in citas)
    return (f"<h1>Cotización {e(q['folio'])}</h1>{'<p class=err>' + e(error) + '</p>' if error else ''}"
            f"<p><a href='{bp}/bandeja/clientes/{c['id']}'>{e(c['nombre'] or c['telefono'])}</a> · "
            f"{ESTADOS_COT[q['estado']]} · vigente hasta {e(str(vigente_hasta(cfg, q) or '(al enviarse)'))} · "
            f"<a href='{bp}/bandeja/cotizaciones/{qid}/imprimir'>Imprimir</a></p>"
            f"<table><tr><th>Cant.</th><th>Descripción</th><th>Precio</th><th>Importe</th></tr>{detalle}"
            f"<tr><th colspan='3'>Total</th><th>{ventas._pesos(q['total_centavos'])}</th></tr></table>{acciones}"
            f"<form method='post' action='{bp}/bandeja/cotizaciones/{qid}/cita'><label>Cita de esta cotización "
            f"<select name='cita'>{opciones}</select></label> <button>Guardar</button></form>")


def html_imprimir(con, cfg, q):
    c = motor.contacto(con, q["contacto_id"])
    filas = "".join(f"<tr><td>{l['cantidad']}</td><td>{e(l['descripcion'])}</td><td class=n>{ventas._pesos(l['precio_centavos'])}"
                    f"</td><td class=n>{ventas._pesos(l['cantidad'] * l['precio_centavos'])}</td></tr>" for l in lineas(con, q["id"]))
    hoy = base.ahora().astimezone(cfg.tz).date()
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Cotización {e(q['folio'])}</title>"
            "<style>body{font:14px/1.5 system-ui,sans-serif;max-width:720px;margin:32px auto;color:#111}"
            "table{width:100%;border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid #ccc;text-align:left}"
            ".n{text-align:right}@media print{a{display:none}}</style></head><body>"
            f"<h1>{e(cfg['nombre'])}</h1><p>{e(cfg.get('direccion', ''))}</p>"
            f"<h2>Cotización {e(q['folio'])}</h2><p>Para: {e(c['nombre'] or c['telefono'])} · Fecha: {hoy.isoformat()}"
            f" · Vigente hasta: {e(str(vigente_hasta(cfg, q) or hoy + dt.timedelta(days=q['vigencia_dias'])))}</p>"
            f"<table><tr><th>Cant.</th><th>Descripción</th><th class=n>Precio</th><th class=n>Importe</th></tr>{filas}"
            f"<tr><th colspan='3'>Total (MXN)</th><th class=n>{ventas._pesos(q['total_centavos'])}</th></tr></table>"
            "<p><a href='javascript:print()'>Imprimir o guardar como PDF</a></p></body></html>")


# ---------- rutas (las llama web.py ya con sesión iniciada; los POST ya pasaron el chequeo de Origin) ----------

def get(con, cfg, usuario, ruta, q):
    """(título, cuerpo[, crudo]) de una página de clientes, o None si la ruta no es de este módulo. crudo=True: la
    página ya está completa (la de imprimir), sin el encabezado de la bandeja."""
    error = (q.get("error") or [""])[0]
    if ruta == "/bandeja/clientes":
        return "Clientes", html_lista(con, cfg, (q.get("q") or [""])[0].strip()[:100])
    if ruta == "/bandeja/clientes/nuevo":
        return "Registrar cliente", html_nuevo(cfg, error)
    m = re.fullmatch(r"/bandeja/clientes/(\d+)", ruta)
    if m:
        c = motor.contacto(con, int(m.group(1)))
        return (c["nombre"] or c["telefono"], html_ficha(con, cfg, c, usuario, error)) if c else ("No existe", "<p>No existe.</p>")
    if ruta == "/bandeja/cotejo":
        mes = (q.get("mes") or [""])[0] or base.ahora().astimezone(cfg.tz).strftime("%Y-%m")
        try:
            return "Cotejo", html_cotejo(con, cfg, mes)
        except ValueError as ex:
            return "Cotejo", f"<p class=err>{e(str(ex))}</p>"
    m = re.fullmatch(r"/bandeja/cotizaciones/(\d+)(/imprimir)?", ruta)
    if m:
        cot = cotizacion(con, int(m.group(1)))
        if not cot:
            return "No existe", "<p>No existe.</p>"
        if m.group(2):
            return cot["folio"], html_imprimir(con, cfg, cot), True
        return f"Cotización {cot['folio']}", html_cotizacion(con, cfg, cot, error)
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
    m = re.fullmatch(r"/bandeja/clientes/(\d+)/cotizacion", ruta)
    if m:
        c = motor.contacto(con, int(m.group(1)))
        if not c:
            return "/bandeja/clientes"
        qid, error = guardar_cotizacion(con, cfg, c, usuario, form)
        return _ruta_ficha(c["id"], error) if error else f"/bandeja/cotizaciones/{qid}"
    m = re.fullmatch(r"/bandeja/cotizaciones/(\d+)/(guardar|enviar|aceptar|rechazar|cita)", ruta)
    if m:
        q = cotizacion(con, int(m.group(1)))
        if not q:
            return "/bandeja/clientes"
        accion = m.group(2)
        if accion == "guardar":
            error = (guardar_cotizacion(con, cfg, motor.contacto(con, q["contacto_id"]), usuario, form, q["id"])[1]
                     if q["estado"] == "borrador" else "Solo un borrador se puede editar.")
        elif accion == "enviar":
            error = enviar_cotizacion(con, cfg, q, usuario)
        elif accion == "cita":
            error = ligar_cita(con, q, form)
        else:
            error = marcar(con, q, "aceptada" if accion == "aceptar" else "rechazada")
        return f"/bandeja/cotizaciones/{q['id']}" + (f"?error={quote(error)}" if error else "")
    return None
