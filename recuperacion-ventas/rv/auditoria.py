"""Auditoría de fugas: registro de las pruebas a negocios del nicho (CSV), puntaje de 100 y reporte HTML por negocio."""
import csv
import datetime as dt
import html
import json
import re
import statistics
from pathlib import Path

from . import base, ventas

COLUMNAS = ["negocio", "whatsapp", "envio_habil", "respuesta_habil", "envio_no_habil", "respuesta_no_habil",
            "dio_precio", "ofrecio_agendar", "seguimiento_2d", "seguimiento_5d", "resenas_google", "contesta_resenas",
            "boton_whatsapp", "horario_visible", "consultas_mes", "ticket_promedio", "notas"]
EJEMPLO = ["Clínica Ejemplo (borra esta fila)", "8112345678", "2026-10-13 11:00", "2026-10-13 12:40",
           "2026-10-17 20:00", "", "si", "no", "no", "no", "45", "no", "si", "no", "", "", "contestó con audio"]

# Tabla del plan de negocio (100 puntos). Cada métrica: (clave, nombre, puntos máximos).
METRICAS = [("habil", "Tiempo de primera respuesta en horario hábil", 25),
            ("no_habil", "Respuesta fuera de horario", 15),
            ("calidad", "Calidad de la respuesta", 20),
            ("seguimiento", "Seguimiento", 20),
            ("resenas", "Reseñas en Google", 10),
            ("contacto", "Facilidad de contacto", 10)]
ARREGLO = {"habil": "Respuesta inmediata por WhatsApp con IA, con los datos que tú apruebas.",
           "no_habil": "La IA contesta de noche y en fin de semana, y agenda aunque no haya nadie.",
           "calidad": "Cada respuesta da el precio y ofrece horarios para agendar en el mismo mensaje.",
           "seguimiento": "Seguimiento automático a los días 2, 5 y 10 a quien pidió precio y no agendó.",
           "resenas": "Solicitud automática de reseña en Google después de cada visita.",
           "contacto": "Página sencilla con botón directo a WhatsApp, horario y perfil de Google al día."}


def cargar_nicho(ruta):
    with open(ruta, encoding="utf-8") as f:
        n = json.load(f)
    if not n.get("nicho"):
        raise ValueError(f"{ruta}: falta 'nicho'")
    return n


def _fecha(s):
    s = (s or "").strip()
    if not s:
        return None
    for formato in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(s, formato)
        except ValueError:
            pass
    raise ValueError(f"fecha y hora inválida (usa AAAA-MM-DD HH:MM): {s!r}")


def _si(s):
    t = base.normalizar_texto(s)
    if t and t not in ventas.SI | ventas.NO:
        raise ValueError(f"esperaba si/no: {s!r}")
    return t in ventas.SI


def _entero(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not s:
        return None
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ValueError(f"número inválido: {s!r}")
    return float(s)


def _demora(envio, respuesta):
    if envio and respuesta and respuesta < envio:
        raise ValueError(f"la respuesta ({respuesta}) es anterior al envío ({envio})")
    return (respuesta - envio) if envio and respuesta else None


def leer(ruta):
    """Devuelve (negocios, errores). Cada negocio: dict con los datos ya interpretados."""
    negocios, errores = [], []
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        for linea, fila in enumerate(csv.DictReader(f), start=2):
            d = {k: (v or "").strip() for k, v in fila.items() if k}
            if not d.get("negocio") or "(borra esta fila)" in d["negocio"]:
                continue
            try:
                eh, rh = _fecha(d.get("envio_habil")), _fecha(d.get("respuesta_habil"))
                en, rn = _fecha(d.get("envio_no_habil")), _fecha(d.get("respuesta_no_habil"))
                negocios.append({
                    "negocio": d["negocio"], "notas": d.get("notas", ""),
                    "envio_habil": eh, "demora_habil": _demora(eh, rh),
                    "envio_no_habil": en, "demora_no_habil": _demora(en, rn),
                    "precio": _si(d.get("dio_precio")), "agendar": _si(d.get("ofrecio_agendar")),
                    "seg2": _si(d.get("seguimiento_2d")), "seg5": _si(d.get("seguimiento_5d")),
                    "resenas": int(_entero(d.get("resenas_google")) or 0), "contesta": _si(d.get("contesta_resenas")),
                    "boton": _si(d.get("boton_whatsapp")), "horario": _si(d.get("horario_visible")),
                    "consultas_mes": _entero(d.get("consultas_mes")), "ticket": _entero(d.get("ticket_promedio"))})
            except ValueError as e:
                errores.append((linea, f"{d['negocio']}: {e}"))
    return negocios, errores


def _txt(td):
    m = round(td.total_seconds() / 60)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min" if m < 48 * 60 else f"{m // 1440} días"


def puntaje(n):
    """{clave: (puntos, máximo, resultado legible)}; una prueba no hecha (sin hora de envío) no cuenta."""
    p = {}
    if n["envio_habil"]:
        d = n["demora_habil"]
        pts = 0 if d is None else 25 if d < dt.timedelta(minutes=5) else 15 if d < dt.timedelta(hours=1) else 5
        p["habil"] = (pts, 25, f"respondió en {_txt(d)}" if d is not None else "no respondió")
    if n["envio_no_habil"]:
        d = n["demora_no_habil"]
        pts = 0 if d is None else 15 if d < dt.timedelta(hours=1) else 5
        p["no_habil"] = (pts, 15, f"respondió en {_txt(d)}" if d is not None else "no respondió")
    p["calidad"] = (10 * (n["precio"] + n["agendar"]), 20,
                    {2: "dio precio y ofreció agendar", 1: "dio precio pero no ofreció agendar" if n["precio"] else
                     "ofreció agendar pero no dio precio", 0: "no dio precio ni ofreció agendar"}[n["precio"] + n["agendar"]])
    seg = n["seg2"] or n["seg5"]
    p["seguimiento"] = (20 if seg else 0, 20, "volvió a escribir sin que respondieras" if seg else
                        "no volvió a escribir en 5 días")
    # ponytail: más de 100 reseñas sin contestarlas cae al nivel de 30-100 (la tabla no define ese caso).
    r = n["resenas"]
    p["resenas"] = (10 if r > 100 and n["contesta"] else 5 if r >= 30 else 0, 10,
                    f"{r} reseñas" + (", las contesta" if n["contesta"] else ", no las contesta"))
    faltan = [x for x, ok in (("botón directo a WhatsApp", n["boton"]), ("horario visible", n["horario"])) if not ok]
    p["contacto"] = (10 - 5 * len(faltan), 10, "sin " + " ni ".join(faltan) if faltan else
                     "botón directo a WhatsApp y horario visible")
    return p


def total(p):
    return round(100 * sum(x[0] for x in p.values()) / sum(x[1] for x in p.values()))


def hallazgos(n, p):
    """Las 3 métricas con más puntos perdidos, redactadas con su evidencia."""
    perdidas = sorted((m for m in p if p[m][0] < p[m][1]), key=lambda m: p[m][0] - p[m][1])[:3]
    hora = lambda t: t.strftime("%d/%m %H:%M")  # noqa: E731
    texto = {
        "habil": lambda: f"En horario hábil escribimos el {hora(n['envio_habil'])} y {p['habil'][2]}.",
        "no_habil": lambda: f"Fuera de horario escribimos el {hora(n['envio_no_habil'])} y {p['no_habil'][2]}.",
        "calidad": lambda: f"En la respuesta {p['calidad'][2]}.",
        "seguimiento": lambda: "Después de pedir precio no respondimos, y nadie volvió a escribir en 5 días.",
        "resenas": lambda: f"En Google tiene {p['resenas'][2]}.",
        "contacto": lambda: f"Su página o perfil está {p['contacto'][2]}.",
    }
    return [(m, texto[m]()) for m in perdidas]


def _fraccion(p):
    return f"1 de cada {round(1 / p)}" if abs(1 / p - round(1 / p)) < 0.05 else f"el {p:.0%}"


def en_riesgo(n, perdida):
    if not n["consultas_mes"] or not n["ticket"]:
        return None
    perdidos = n["consultas_mes"] * perdida
    return perdidos, perdidos * n["ticket"]


CSS = """@page{size:letter;margin:14mm}body{font:12.5px/1.45 system-ui,-apple-system,sans-serif;color:#1d2433;margin:0}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:21px;margin:.2em 0}h2{font-size:14px;margin:1.1em 0 .3em;
text-transform:uppercase;letter-spacing:.04em;color:#3b4660}p{margin:.35em 0}.sub{color:#556;margin:0}
.barras div{display:flex;align-items:center;gap:8px;margin:4px 0}.barras span{width:150px}.barras b{display:block;height:14px;
background:#9aa7bd;border-radius:3px}.barras .tu b{background:#c2410c}table{width:100%;border-collapse:collapse}
td,th{text-align:left;padding:4px 6px;border-bottom:1px solid #e3e6ec;vertical-align:top}th{font-size:11.5px;color:#556}
td.n{text-align:right;white-space:nowrap}.caja{background:#fff4ec;border-left:4px solid #c2410c;padding:8px 10px;margin:6px 0}
.nota{color:#667;font-size:11px}@media print{main{padding:0}}"""


def reporte_html(n, p, grupo, nicho, fecha):
    e = html.escape
    tu, prom, mejor = total(p), round(statistics.mean(grupo)), max(grupo)
    ciudad = nicho.get("ciudad", "")
    barras = "".join(f"<div class='{c}'><span>{t}</span><b style='width:{v * 4}px'></b> {v}</div>"
                     for c, t, v in (("tu", "Tu negocio", tu), ("", "Promedio del grupo", prom), ("", "Mejor del grupo", mejor)))
    filas = "".join(f"<tr><td>{e(nombre)}</td><td>{e(p[k][2])}</td><td class=n>{p[k][0]} / {p[k][1]}</td></tr>"
                    for k, nombre, _ in METRICAS if k in p)
    if "no_habil" not in p:
        filas += "<tr><td>Respuesta fuera de horario</td><td>no se probó</td><td class=n>—</td></tr>"
    lista = hallazgos(n, p)
    hall = "".join(f"<li>{e(t)}</li>" for _, t in lista) or "<li>Sin fugas importantes en esta prueba.</li>"
    arreglo = "".join(f"<li>{e(ARREGLO[m])}</li>" for m, _ in lista)
    perdida = float(nicho.get("auditoria", {}).get("perdida_estimada", 0.2))
    r = en_riesgo(n, perdida)
    if r:
        cuesta = (f"<div class=caja>Si recibes {n['consultas_mes']:.0f} consultas al mes y pierdes {_fraccion(perdida)} por "
                  f"responder tarde o no dar seguimiento, son {r[0]:.0f} clientes al mes. Con tu "
                  f"ticket promedio de ${n['ticket']:,.0f} son hasta <strong>${r[1]:,.0f} MXN al mes en ventas en riesgo"
                  f"</strong>.</div><p class=nota>Estimación con tus propios números; no es una promesa de resultado.</p>")
    else:
        cuesta = ("<p>Pendiente: con tus consultas al mes y tu ticket promedio calculamos cuánto representa en ventas.</p>")
    sin_no_habil = "" if "no_habil" in p else " La prueba fuera de horario no se hizo; el puntaje se calculó sobre las demás."
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Auditoría · {e(n['negocio'])}</title>
<style>{CSS}</style></head><body><main>
<p class=sub>Auditoría de atención por WhatsApp · {e(nicho['nicho'])}{' en ' + e(ciudad) if ciudad else ''} · {e(fecha)}</p>
<h1>{e(n['negocio'])} obtuvo {tu}/100</h1>
<p>El promedio del grupo ({len(grupo)} {e(nicho['nicho'])}{' de ' + e(ciudad) if ciudad else ''}) es {prom} y el mejor obtuvo {mejor}.{sin_no_habil}</p>
<div class=barras>{barras}</div>
<h2>Cómo se midió</h2>
<p>Escribimos como cliente con una consulta real y corta, sin agendar citas falsas.</p>
<table><tr><th>Métrica</th><th>Resultado</th><th class=n>Puntos</th></tr>{filas}</table>
<h2>Tres hallazgos</h2><ul>{hall}</ul>
<h2>Lo que cuesta</h2>{cuesta}
<h2>Cómo se arregla</h2><ul>{arreglo or '<li>Mantener lo que ya funciona y medirlo cada mes.</li>'}</ul>
<h2>Siguiente paso</h2><p>Una reunión de 20 minutos para revisar estas fugas con tus números.</p>
<p class=nota>Los demás negocios evaluados no se identifican: solo se muestran el promedio y el mejor puntaje.</p>
</main></body></html>
"""


def _slug(s):
    return base.normalizar_texto(s).replace(" ", "-")[:60] or "negocio"


def generar(carpeta, nicho, fecha=None):
    """Lee negocios.csv y escribe reportes/<negocio>.html y resumen.html. Devuelve (resultados, errores)."""
    carpeta = Path(carpeta)
    negocios, errores = leer(carpeta / "negocios.csv")
    resultados = [(n, puntaje(n)) for n in negocios]
    if not resultados:
        return [], errores
    grupo = [total(p) for _, p in resultados]
    fecha = fecha or base.ahora().strftime("%d/%m/%Y")
    salida = carpeta / "reportes"
    salida.mkdir(exist_ok=True)
    usados = set()
    filas = []
    for n, p in sorted(resultados, key=lambda x: -total(x[1])):
        nombre = _slug(n["negocio"])
        while nombre in usados:
            nombre += "-2"
        usados.add(nombre)
        (salida / f"{nombre}.html").write_text(reporte_html(n, p, grupo, nicho, fecha), encoding="utf-8")
        filas.append(f"<tr><td><a href='reportes/{nombre}.html'>{html.escape(n['negocio'])}</a></td>"
                     f"<td class=n>{total(p)}</td><td>{html.escape(n['notas'])}</td></tr>")
    (carpeta / "resumen.html").write_text(
        f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Resumen de la auditoría</title>"
        f"<style>{CSS}</style></head><body><main><h1>Resumen interno: {html.escape(nicho['nicho'])}</h1>"
        f"<p>{len(grupo)} negocios · promedio {round(statistics.mean(grupo))} · mejor {max(grupo)} · peor {min(grupo)}. "
        f"Solo para ti: lleva nombres de todos los negocios.</p><table><tr><th>Negocio</th><th class=n>Puntaje</th>"
        f"<th>Notas</th></tr>{''.join(filas)}</table></main></body></html>", encoding="utf-8")
    return resultados, errores


def crear_plantilla(carpeta):
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    with open(carpeta / "negocios.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNAS)
        w.writerow(EJEMPLO)
