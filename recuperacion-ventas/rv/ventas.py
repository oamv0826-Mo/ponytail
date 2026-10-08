"""Importación de clientes (reactivación), ventas, atribución, reporte mensual y página estática."""
import csv
import datetime as dt
import html
import io
import re
import statistics
from collections import Counter
import urllib.parse

from . import base, motor

SI = {"si", "s", "1", "true", "x", "yes", "acepta", "acepto"}
NO = {"no", "n", "0", "false"}


ALIAS = {"celular": "telefono", "whatsapp": "telefono", "movil": "telefono", "tel": "telefono", "numero": "telefono",
         "telefono_celular": "telefono", "numero_de_telefono": "telefono", "numero_de_celular": "telefono",
         "ultima_cita": "ultima_visita", "fecha_ultima_visita": "ultima_visita", "fecha_de_ultima_visita": "ultima_visita",
         "acepta_mensajes": "consentimiento", "importe": "monto", "total": "monto"}


def _columnas(fila):
    out = {}
    for k, v in fila.items():
        if k:
            k = base.normalizar_texto(k).replace(" ", "_")
            out.setdefault(ALIAS.get(k, k), (v or "").strip())
    return out


def leer_csv(ruta):
    """Filas de un CSV como lo guarda Excel en México: UTF-8 o Windows-1252, separado por coma, punto y coma o tab."""
    datos = open(ruta, "rb").read()
    try:
        texto = datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = datos.decode("cp1252")
    primera = texto.split("\n", 1)[0]
    sep = max(",;\t", key=primera.count)
    return csv.DictReader(io.StringIO(texto, newline=""), delimiter=sep)


def fecha_csv(s):
    """AAAA-MM-DD o DD/MM/AAAA (formato de Excel en México). Devuelve 'AAAA-MM-DD' o None si no es fecha."""
    s = (s or "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, formato).date().isoformat()
        except ValueError:
            pass
    return None


def importar_clientes(con, cfg, ruta):
    """CSV con columnas nombre, telefono, ultima_visita (AAAA-MM-DD, opcional), consentimiento (si/no).

    Normaliza a E.164, deduplica (en el archivo y contra la base) y excluye teléfonos del equipo.
    Si 'consentimiento' viene vacío no cambia el valor existente; 'no' lo revoca.
    """
    r = {"nuevos": 0, "actualizados": 0, "duplicados": 0, "equipo": 0, "rechazados": []}
    vistos = set()
    for linea, fila in enumerate(leer_csv(ruta), start=2):
        d = _columnas(fila)
        tel = base.normalizar_tel(d.get("telefono"))
        if not tel:
            r["rechazados"].append((linea, f"teléfono inválido: {d.get('telefono', '')!r}"))
            continue
        if tel in cfg.internos:
            r["equipo"] += 1
            continue
        if tel in vistos:
            r["duplicados"] += 1
            continue
        vistos.add(tel)
        consent_txt = base.normalizar_texto(d.get("consentimiento", ""))
        if consent_txt and consent_txt not in SI | NO:
            r["rechazados"].append((linea, f"consentimiento no reconocido: {d.get('consentimiento')!r}"))
            continue
        consent = 1 if consent_txt in SI else 0 if consent_txt in NO else None
        crudo, visita = d.get("ultima_visita"), fecha_csv(d.get("ultima_visita"))
        if crudo and not visita:
            r["rechazados"].append((linea, f"ultima_visita no es fecha (AAAA-MM-DD o DD/MM/AAAA): {crudo!r}"))
            continue
        nombre = d.get("nombre", "")[:60]
        existe = con.execute("SELECT * FROM contacto WHERE telefono=?", (tel,)).fetchone()
        if existe:
            con.execute("UPDATE contacto SET nombre=CASE WHEN nombre='' THEN ? ELSE nombre END, "
                        "consentimiento=COALESCE(?, consentimiento), "
                        "ultima_visita=MAX(COALESCE(ultima_visita,''), COALESCE(?,'')) WHERE id=?",
                        (nombre, consent, visita, existe["id"]))
            r["actualizados"] += 1
        else:
            con.execute("INSERT INTO contacto (telefono, wa_id, nombre, origen, consentimiento, ultima_visita, creado) "
                        "VALUES (?,?,?,?,?,?,?)", (tel, tel.lstrip("+"), nombre, "importado", consent or 0, visita,
                                                   base.iso(base.ahora())))
            r["nuevos"] += 1
    con.execute("UPDATE contacto SET ultima_visita=NULL WHERE ultima_visita=''")
    return r



# ---------- ventas y atribución ----------

VENTANA_ATRIBUCION = dt.timedelta(days=60)
RECUPERADAS = ("reactivacion", "seguimiento", "fuera_horario")
NOMBRES_ORIGEN = {"reactivacion": "Reactivación", "seguimiento": "Seguimiento", "fuera_horario": "Fuera de horario",
                  "respuesta_rapida": "Respuesta rápida (no cuenta)", "sin_atribucion": "Sin atribución (no cuenta)"}


def fin_del_dia(cfg, fecha):
    return dt.datetime.combine(fecha + dt.timedelta(days=1), dt.time(0), cfg.tz).astimezone(base.UTC)


def atribucion(con, cfg, contacto_id, fecha):
    """Origen de una venta según docs/anexo-contrato.md (primera regla que aplica)."""
    t = base.iso(fin_del_dia(cfg, fecha))
    t60 = base.iso(fin_del_dia(cfg, fecha) - VENTANA_ATRIBUCION)
    q = lambda sql, *a: con.execute(sql, a).fetchone() is not None  # noqa: E731
    if q("SELECT 1 FROM evento e WHERE e.contacto_id=? AND e.tipo='reactivacion' AND e.creado BETWEEN ? AND ? "
         "AND EXISTS (SELECT 1 FROM mensaje m WHERE m.contacto_id=e.contacto_id AND m.direccion='in' "
         "AND m.creado>e.creado AND m.creado<?)", contacto_id, t60, t, t):
        return "reactivacion"
    if q("SELECT 1 FROM evento e WHERE e.contacto_id=? AND e.tipo='seguimiento' AND e.creado BETWEEN ? AND ? "
         "AND (EXISTS (SELECT 1 FROM mensaje m WHERE m.contacto_id=e.contacto_id AND m.direccion='in' "
         "AND m.creado>e.creado AND m.creado<?) OR EXISTS (SELECT 1 FROM cita c WHERE c.contacto_id=e.contacto_id "
         "AND c.creado>e.creado AND c.creado<?))", contacto_id, t60, t, t, t):
        return "seguimiento"
    if q("SELECT 1 FROM evento WHERE contacto_id=? AND tipo='fuera_horario' AND creado BETWEEN ? AND ?",
         contacto_id, t60, t):
        return "fuera_horario"
    if q("SELECT 1 FROM contacto WHERE id=? AND origen='entrante'", contacto_id):
        return "respuesta_rapida"
    return "sin_atribucion"


def a_centavos(monto):
    s = str(monto).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise ValueError(f"monto inválido: {monto!r}")
    centavos = round(float(s) * 100)
    if centavos <= 0:
        raise ValueError("el monto debe ser mayor a 0")
    return centavos


def registrar_venta(con, cfg, contacto_id, monto, fecha, por, cita_id=None):
    """Devuelve (venta_id, None) o (None, error)."""
    try:
        centavos = a_centavos(monto)
        f = dt.date.fromisoformat(str(fecha))
    except ValueError as e:
        return None, str(e)
    if f > base.ahora().astimezone(cfg.tz).date():
        return None, "la fecha no puede ser futura"
    origen = atribucion(con, cfg, contacto_id, f)
    cur = con.execute("INSERT OR IGNORE INTO venta (contacto_id, cita_id, monto_centavos, fecha, origen, registrado_por, "
                      "creado) VALUES (?,?,?,?,?,?,?)", (contacto_id, cita_id, centavos, f.isoformat(), origen, por,
                                                         base.iso(base.ahora())))
    if cur.rowcount == 0:
        return None, ("esa venta ya estaba registrada (misma cita, fecha y monto)" if cita_id else
                      "esa venta ya estaba registrada (mismo contacto, fecha y monto)")
    return cur.lastrowid, None


def importar_ventas(con, cfg, ruta, por="importacion"):
    """CSV con columnas telefono, fecha (AAAA-MM-DD), monto."""
    r = {"registradas": 0, "duplicadas": 0, "rechazadas": []}
    for linea, fila in enumerate(leer_csv(ruta), start=2):
        d = _columnas(fila)
        tel = base.normalizar_tel(d.get("telefono"))
        c = tel and con.execute("SELECT id FROM contacto WHERE telefono=?", (tel,)).fetchone()
        if not c:
            r["rechazadas"].append((linea, f"contacto no encontrado: {d.get('telefono', '')!r}"))
            continue
        vid, error = registrar_venta(con, cfg, c["id"], d.get("monto", ""), fecha_csv(d.get("fecha")) or d.get("fecha", ""),
                                     por)
        if vid:
            r["registradas"] += 1
        elif error.startswith("esa venta ya"):
            r["duplicadas"] += 1
        else:
            r["rechazadas"].append((linea, error))
    return r


# ---------- reporte mensual ----------

def _rango_mes(cfg, mes):
    try:
        a = dt.datetime.strptime(mes, "%Y-%m").replace(tzinfo=cfg.tz)
    except ValueError:
        raise ValueError(f"mes inválido: {mes!r} (usa AAAA-MM, p. ej. 2026-10)") from None
    b = (a + dt.timedelta(days=32)).replace(day=1)
    return a, b


def _pesos(centavos):
    return f"${centavos / 100:,.2f} MXN"


def garantia(con, cfg):
    inicio = dt.date.fromisoformat(cfg["fecha_inicio"])
    fin = inicio + VENTANA_ATRIBUCION
    marcadores = ",".join("?" * len(RECUPERADAS))
    suma = con.execute(f"SELECT COALESCE(SUM(monto_centavos),0) FROM venta WHERE origen IN ({marcadores}) "
                       "AND fecha>=? AND fecha<?", (*RECUPERADAS, inicio.isoformat(), fin.isoformat())).fetchone()[0]
    meta = round(float(cfg["mensualidad_mxn"]) * 100)
    hoy = base.ahora().astimezone(cfg.tz).date()
    if hoy < fin:
        estado = f"EN CURSO: {_pesos(suma)} de {_pesos(meta)}; el periodo termina el {(fin - dt.timedelta(days=1)).isoformat()}"
    elif suma >= meta:
        estado = f"CUMPLE: {_pesos(suma)} recuperados ≥ {_pesos(meta)}"
    else:
        estado = f"NO CUMPLE: {_pesos(suma)} recuperados < {_pesos(meta)}. El siguiente mes no se cobra."
    return {"inicio": inicio, "fin": fin, "recuperado": suma, "meta": meta, "estado": estado}


def datos_reporte(con, cfg, mes):
    a, b = _rango_mes(cfg, mes)
    A, B = base.iso(a), base.iso(b)
    # 1. consultas: mensaje entrante sin otro entrante del mismo contacto en las 24 h previas
    consultas = con.execute(
        "SELECT contacto_id, creado FROM (SELECT contacto_id, creado, LAG(creado) OVER (PARTITION BY contacto_id "
        "ORDER BY creado) AS previo FROM mensaje WHERE direccion='in' AND " + base.NO_REACCION + ") "
        "WHERE creado>=? AND creado<? AND "
        "(previo IS NULL OR julianday(creado)-julianday(previo)>1)", (A, B)).fetchall()
    # 2. tiempo de respuesta: primer mensaje saliente al cliente después del inicio de cada consulta
    tiempos, sin_respuesta = [], 0
    for cid, creado in consultas:
        r = con.execute("SELECT MIN(creado) FROM mensaje WHERE contacto_id=? AND direccion='out' AND creado>=?",
                        (cid, creado)).fetchone()[0]
        if r:
            tiempos.append((base.de_iso(r) - base.de_iso(creado)).total_seconds())
        else:
            sin_respuesta += 1
    ventas = {o: (n, s) for o, n, s in con.execute(
        "SELECT origen, COUNT(*), SUM(monto_centavos) FROM venta WHERE fecha>=? AND fecha<? GROUP BY origen",
        (a.date().isoformat(), b.date().isoformat()))}
    cuenta = lambda sql, *p: con.execute(sql, p).fetchone()[0]  # noqa: E731
    ev = dict(con.execute("SELECT tipo, COUNT(*) FROM evento WHERE creado>=? AND creado<? GROUP BY tipo", (A, B)).fetchall())
    motivos = Counter(motor.motivo_legible(d) for (d,) in con.execute(
        "SELECT detalle FROM evento WHERE tipo='handoff' AND creado>=? AND creado<?", (A, B)))
    return {
        "mes": mes, "consultas": len(consultas),
        "fuera_horario": ev.get("fuera_horario", 0),
        "mediana_s": statistics.median(tiempos) if tiempos else None,
        "pct_5min": (100 * sum(1 for x in tiempos if x <= 300) / len(tiempos)) if tiempos else None,
        "sin_respuesta": sin_respuesta,
        # una cita reprogramada deja la anterior cancelada: solo cuentan las que siguen en pie
        "citas_agendadas": cuenta("SELECT COUNT(*) FROM cita WHERE creado>=? AND creado<? AND estado<>'cancelada'", A, B),
        "citas_canceladas": cuenta("SELECT COUNT(*) FROM cita WHERE creado>=? AND creado<? AND estado='cancelada'", A, B),
        "citas_asistidas": cuenta("SELECT COUNT(*) FROM cita WHERE estado='asistio' AND inicio>=? AND inicio<?", A, B),
        "citas_no_asistio": cuenta("SELECT COUNT(*) FROM cita WHERE estado='no_asistio' AND inicio>=? AND inicio<?", A, B),
        "citas_sin_cerrar": cuenta("SELECT COUNT(*) FROM cita WHERE estado='agendada' AND inicio>=? AND inicio<? "
                                   "AND inicio<?", A, B, base.iso(base.ahora())),
        "asistio_sin_venta": cuenta("SELECT COUNT(*) FROM cita c WHERE estado='asistio' AND inicio>=? AND inicio<? AND "
                                    "NOT EXISTS (SELECT 1 FROM venta v WHERE v.cita_id=c.id)", A, B),
        "ventas": ventas,
        "recuperado": sum(ventas.get(o, (0, 0))[1] or 0 for o in RECUPERADAS),
        "recuperadas_n": sum(ventas.get(o, (0, 0))[0] for o in RECUPERADAS),
        "resenas": ev.get("resena", 0), "seguimientos": ev.get("seguimiento", 0),
        "reactivaciones": ev.get("reactivacion", 0),
        "motivos_handoff": motivos.most_common(),
        "costo_ia_usd": cuenta("SELECT COALESCE(SUM(costo_micro_usd),0) FROM ia_uso WHERE creado>=? AND creado<?",
                               A, B) / 1_000_000,
        "garantia": garantia(con, cfg),
    }


def _duracion(seg):
    if seg is None:
        return "sin datos"
    return f"{seg:.0f} s" if seg < 60 else f"{seg / 60:.1f} min" if seg < 3600 else f"{seg / 3600:.1f} h"


def guardar_reporte(cfg, mes, texto):
    carpeta = cfg.carpeta / "reportes"
    carpeta.mkdir(exist_ok=True)
    (carpeta / f"reporte-{mes}.md").write_text(texto, encoding="utf-8")
    print(texto)


def reporte(con, cfg, mes, resenas_google=None):
    d = datos_reporte(con, cfg, mes)
    a, _ = _rango_mes(cfg, mes)
    titulo = f"{base.MESES_ES[a.month - 1].capitalize()} {a.year}"
    filas_ventas = "\n".join(f"| {NOMBRES_ORIGEN.get(o, o)} | {n} | {_pesos(s or 0)} |"
                             for o, (n, s) in sorted(d["ventas"].items(), key=lambda x: NOMBRES_ORIGEN.get(x[0], x[0])))
    resenas = f"{d['resenas']} {'solicitud enviada' if d['resenas'] == 1 else 'solicitudes enviadas'}" + (f"; reseñas nuevas en Google: {resenas_google}"
                                                         if resenas_google is not None else "")
    motivos = "\n".join(f"- {k}: {n}" for k, n in d["motivos_handoff"]) or "- ninguno"
    g = d["garantia"]
    pct = f"{d['pct_5min']:.0f}% en menos de 5 min" if d["pct_5min"] is not None else "sin datos"
    return f"""# Reporte de {cfg['nombre']}: {titulo}

## Los 5 números

| | |
|---|---|
| 1. Consultas recibidas | **{d['consultas']}** ({d['fuera_horario']} fuera de horario) |
| 2. Tiempo de respuesta (mediana) | **{_duracion(d['mediana_s'])}** ({pct}) |
| 3. Citas agendadas | **{d['citas_agendadas']}** (asistieron {d['citas_asistidas']}, no asistieron {d['citas_no_asistio']}; canceladas: {d['citas_canceladas']}) |
| 4. Ventas recuperadas | **{_pesos(d['recuperado'])}** en {d['recuperadas_n']} {'venta' if d['recuperadas_n'] == 1 else 'ventas'} |
| 5. Reseñas | **{resenas}** |

## Ventas del mes por origen

| Origen | Ventas | Monto |
|---|---|---|
{filas_ventas or '| (sin ventas registradas) | 0 | $0.00 MXN |'}

Reglas de atribución: docs/anexo-contrato.md. Solo cuentan como recuperadas: reactivación, seguimiento y fuera de horario.

## Garantía de 60 días ({g['inicio'].isoformat()} a {(g['fin'] - dt.timedelta(days=1)).isoformat()})

**{g['estado']}**

## Para revisar

- Citas pasadas sin marcar asistencia: {d['citas_sin_cerrar']}
- Citas con asistencia pero sin venta registrada: {d['asistio_sin_venta']}
- Consultas sin ninguna respuesta: {d['sin_respuesta']}
- Seguimientos enviados: {d['seguimientos']} · Reactivaciones enviadas: {d['reactivaciones']}
- Costo de IA del mes: ${d['costo_ia_usd']:.2f} USD

### Conversaciones pasadas a humano por motivo
{motivos}
"""


# ---------- página estática ----------

def pagina(cfg):
    e = html.escape
    tel = re.sub(r"\D", "", cfg.get("telefono_negocio", ""))
    wa = f"https://wa.me/{tel}?text=" + urllib.parse.quote(f"Hola, quiero información de {cfg['nombre']}")
    mapa = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(cfg.get("direccion", ""))
    servicios = "".join(f"<li><strong>{e(s['nombre'])}</strong> · ${s['precio_mxn']:,} MXN<br>"
                        f"<span>{e(s.get('descripcion', ''))}</span></li>" for s in cfg.servicios.values())
    horario = "".join(f"<li>{base.DIAS_ES[i].capitalize()}: "
                      f"{e(', '.join(f'{x}–{y}' for x, y in cfg['horario'].get(d, [])) or 'cerrado')}</li>"
                      for i, d in enumerate(base.DIAS))
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(cfg['nombre'])}</title><meta name="description" content="{e(cfg.get('giro', ''))} en {e(cfg.get('direccion', ''))}">
<style>
body{{font:17px/1.5 system-ui,sans-serif;margin:0;color:#1d2433;background:#f7f8fa}}
main{{max-width:640px;margin:0 auto;padding:24px 16px}}h1{{margin:.2em 0}}
.wa{{display:block;text-align:center;background:#128c4a;color:#fff;font-weight:700;padding:16px;border-radius:12px;
text-decoration:none;font-size:20px;margin:20px 0}}.wa:focus,.wa:hover{{background:#0b6b38}}
ul{{padding-left:1.1em}}li{{margin:.4em 0}}li span{{color:#556}}a{{color:#0b5cad}}
</style></head><body><main>
<h1>{e(cfg['nombre'])}</h1><p>{e(cfg.get('giro', ''))}</p>
<a class="wa" href="{e(wa)}">Escríbenos por WhatsApp</a>
<h2>Servicios</h2><ul>{servicios}</ul>
<h2>Horario</h2><ul>{horario}</ul>
<h2>Dónde estamos</h2><p><a href="{e(mapa)}">{e(cfg.get('direccion', ''))}</a></p>
<a class="wa" href="{e(wa)}">Agenda tu cita por WhatsApp</a>
</main></body></html>
"""


# ---------- bandeja ----------

def html_ventas_conversacion(con, cfg, c):
    e = html.escape
    bp = e(cfg.base_path)
    ventas = con.execute("SELECT * FROM venta WHERE contacto_id=? ORDER BY fecha DESC", (c["id"],)).fetchall()
    lista = "".join(f"<li>{e(v['fecha'])}: {_pesos(v['monto_centavos'])} · {e(NOMBRES_ORIGEN.get(v['origen'], v['origen']))}"
                    f"</li>" for v in ventas)
    hoy = base.ahora().astimezone(cfg.tz).date().isoformat()
    return (f"<h2>Ventas</h2>{'<ul>' + lista + '</ul>' if lista else '<p>Sin ventas registradas.</p>'}"
            f"<form method='post' action='{bp}/bandeja/c/{c['id']}/venta'><label for='mo'>Monto (MXN)</label> "
            f"<input id='mo' name='monto' inputmode='decimal' required pattern='[0-9,]+([.][0-9]{{1,2}})?'> "
            f"<label for='fv'>Fecha</label> <input id='fv' type='date' name='fecha' value='{hoy}' max='{hoy}' required> "
            f"<button>Registrar venta</button></form>")


def accion_venta(con, cfg, c, usuario, form):
    cita = (form.get("cita") or [""])[0]
    cita_id = int(cita) if cita.isdigit() else None
    if cita_id and not con.execute("SELECT 1 FROM cita WHERE id=? AND contacto_id=?", (cita_id, c["id"])).fetchone():
        return "La cita no es de este contacto."
    _, error = registrar_venta(con, cfg, c["id"], (form.get("monto") or [""])[0], (form.get("fecha") or [""])[0],
                               f"humano:{usuario}", cita_id)
    return error


def html_venta_en_cita(con, cfg, f):
    """Formulario corto en cada cita con 'Asistió' y sin venta registrada."""
    if f["estado"] != "asistio" or con.execute("SELECT 1 FROM venta WHERE cita_id=?", (f["id"],)).fetchone():
        return ""
    bp = html.escape(cfg.base_path)
    fecha = min(base.de_iso(f["inicio"]).astimezone(cfg.tz).date(), base.ahora().astimezone(cfg.tz).date()).isoformat()
    return (f"<form class='inline' method='post' action='{bp}/bandeja/c/{f['contacto_id']}/venta'>"
            f"<input type='hidden' name='cita' value='{f['id']}'><input type='hidden' name='fecha' value='{fecha}'>"
            f"<label>Venta $ <input name='monto' size='7' inputmode='decimal' required "
            f"pattern='[0-9,]+([.][0-9]{{1,2}})?'></label> <button>Registrar</button></form>")
