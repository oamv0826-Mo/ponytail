"""prueba-real: recorrido guiado con tu teléfono contra Meta, Anthropic y Google de verdad.

Cada paso dice qué hacer, revisa en la base lo que el sistema hizo, te pregunta qué viste en el teléfono y guarda
el resultado en prueba-real.json y prueba-real-resultados.md (en la carpeta del cliente)."""
import datetime as dt
import json
import time
import urllib.request

from . import base, tick, ventas, verificar, wa

OK_ENVIO = ("enviado", "sent", "delivered", "read", "prueba")


def _uno(con, sql, *p):
    return con.execute(sql, p).fetchone()


def _contacto(con, tel):
    return _uno(con, "SELECT * FROM contacto WHERE telefono=?", tel)


def _salientes(con, desde, tel=None, plantilla=None, autor=None):
    sql = "SELECT * FROM mensaje WHERE direccion='out' AND creado>=?"
    p = [desde]
    for campo, valor in (("telefono", tel), ("plantilla", plantilla)):
        if valor:
            sql += f" AND {campo}=?"
            p.append(valor)
    if autor:
        sql += " AND autor LIKE ?"
        p.append(autor)
    return con.execute(sql + " ORDER BY id", p).fetchall()


def _entrega(filas):
    """(ok, texto): ok si hay al menos uno y ninguno falló."""
    if not filas:
        return False, "no salió ningún mensaje"
    estados = [f["estado"] for f in filas]
    malos = [f for f in filas if f["estado"] not in OK_ENVIO]
    return not malos, "estados en Meta: " + ", ".join(estados) + (
        f" · error: {malos[0]['error']}" if malos and malos[0]["error"] else "")


# ---------- revisiones automáticas: (con, cfg, tel, desde) → (ok, detalle) ----------

def r_noche(con, cfg, tel, desde):
    c = _contacto(con, tel)
    entrada = c and _uno(con, "SELECT * FROM mensaje WHERE contacto_id=? AND direccion='in' AND creado>=? ORDER BY id",
                         c["id"], desde)
    if not entrada:
        return False, "todavía no llega tu mensaje"
    if base.abierto(cfg, base.de_iso(entrada["creado"])):
        return False, "tu mensaje llegó en horario abierto; repite este paso fuera del horario del cliente.json"
    fuera = _uno(con, "SELECT 1 FROM evento WHERE contacto_id=? AND tipo='fuera_horario'", c["id"])
    ok, txt = _entrega(_salientes(con, desde, tel))
    seg = _uno(con, "SELECT seg_activo FROM contacto WHERE id=?", c["id"])["seg_activo"]
    return ok and bool(fuera) and bool(seg), (f"respuesta: {txt} · fuera de horario registrado: {'sí' if fuera else 'NO'}"
                                              f" · seguimiento activado: {'sí' if seg else 'NO (¿preguntaste un precio?)'}")


def p_seguimiento(con, cfg, tel):
    """Simula que pasaron 2 días desde la pregunta de precio y corre el seguimiento."""
    c = _contacto(con, tel)
    if not c or not c["seg_activo"]:
        return "No hay seguimiento activo: en el paso 'noche' pregunta un precio y no agendes."
    if not base.en_ventana_envio(cfg, base.ahora()):
        return "Fuera de la ventana de envíos (9:00-20:00, no domingo): repite este paso dentro de ella."
    atras = base.iso(base.ahora() - dt.timedelta(days=2, minutes=1))
    con.execute("UPDATE contacto SET seg_inicio=?, seg_paso=0 WHERE id=?", (atras, c["id"]))
    print(f"seguimiento enviado: {tick.seguimiento(con, cfg, base.ahora())}")
    return None


def r_seguimiento(con, cfg, tel, desde):
    return _entrega(_salientes(con, desde, tel, plantilla="seguimiento_1"))


def r_cita(con, cfg, tel, desde):
    c = _contacto(con, tel)
    cita = c and _uno(con, "SELECT * FROM cita WHERE contacto_id=? AND creado>=? AND estado='agendada'", c["id"], desde)
    if not cita:
        return False, "todavía no hay cita agendada"
    externo = cfg["agenda"]["proveedor"] in ("google", "microsoft")
    ok, txt = _entrega(_salientes(con, desde, tel))
    detalle = f"cita {base.fecha_humana(cfg, base.de_iso(cita['inicio']))} · {txt}"
    if externo:
        detalle += f" · evento en el calendario ({cfg['agenda']['proveedor']}): {cita['evento_id'] or 'NO'}"
    return ok and (cita["evento_id"] is not None or not externo), detalle


def r_baja(con, cfg, tel, desde):
    baja = _uno(con, "SELECT 1 FROM optout WHERE telefono=? AND creado>=?", tel, desde)
    ok, txt = _entrega(_salientes(con, desde, tel))
    return bool(baja) and ok, f"baja registrada: {'sí' if baja else 'NO'} · confirmación: {txt}"


def r_urgencia(con, cfg, tel, desde):
    al_cliente = [m for m in _salientes(con, desde, tel) if m["texto"] == cfg.msg["emergencia"]]
    avisos = _salientes(con, desde, plantilla="aviso_equipo")
    ok1, t1 = _entrega(al_cliente)
    ok2, t2 = _entrega(avisos)
    return ok1 and ok2, f"mensaje de 911 al cliente: {t1} · aviso al equipo: {t2}"


def r_handoff(con, cfg, tel, desde):
    humano = _salientes(con, desde, tel, autor="humano:%")
    c = _contacto(con, tel)
    pidio = _uno(con, "SELECT 1 FROM evento WHERE contacto_id=? AND tipo='handoff' AND detalle LIKE 'pidio_humano%' "
                      "AND creado>=?", c["id"], desde)
    avisos = _salientes(con, desde, plantilla="aviso_equipo")
    ok1, t1 = _entrega(humano)
    ok2, t2 = _entrega(avisos)
    return ok1 and bool(pidio) and ok2, (f"respuesta desde la bandeja: {t1} · pediste persona: {'sí' if pidio else 'NO'}"
                                         f" · aviso al equipo: {t2}")


def p_asistencia(con, cfg, tel):
    """La asistencia se marca el día de la cita: si tu cita es otro día, se mueve a hace una hora (solo en la base)."""
    c = _contacto(con, tel)
    cita = c and _uno(con, "SELECT * FROM cita WHERE contacto_id=? AND estado='agendada' ORDER BY id DESC", c["id"])
    if not cita:
        return "No hay cita agendada: repite el paso 'cita'."
    hoy = base.ahora().astimezone(cfg.tz).date()
    if base.de_iso(cita["inicio"]).astimezone(cfg.tz).date() != hoy:
        dur = base.de_iso(cita["fin"]) - base.de_iso(cita["inicio"])
        inicio = base.ahora() - dt.timedelta(hours=1)
        con.execute("UPDATE cita SET inicio=?, fin=? WHERE id=?", (base.iso(inicio), base.iso(inicio + dur), cita["id"]))
        print("Tu cita no era de hoy: la moví a hace una hora en la base (Google Calendar no cambia).")
    return None


def r_asistencia(con, cfg, tel, desde):
    c = _contacto(con, tel)
    ok = c and _uno(con, "SELECT 1 FROM cita WHERE contacto_id=? AND estado='asistio' AND asistio_en>=?", c["id"], desde)
    return bool(ok), "cita marcada como Asistió" if ok else "la cita todavía no está marcada"


def r_venta(con, cfg, tel, desde):
    c = _contacto(con, tel)
    v = c and _uno(con, "SELECT * FROM venta WHERE contacto_id=? AND creado>=?", c["id"], desde)
    if not v:
        return False, "todavía no hay venta registrada"
    return v["origen"] in ventas.RECUPERADAS, (f"venta ${v['monto_centavos'] / 100:,.2f} · origen: "
                                              f"{ventas.NOMBRES_ORIGEN.get(v['origen'], v['origen'])}")


def r_reporte(con, cfg, tel, desde):
    mes = base.ahora().astimezone(cfg.tz).strftime("%Y-%m")
    ventas.guardar_reporte(cfg, mes, ventas.reporte(con, cfg, mes))
    d = ventas.datos_reporte(con, cfg, mes)
    return d["recuperado"] > 0 and d["citas_agendadas"] > 0, (
        f"reporte guardado en reportes/reporte-{mes}.md · recuperado ${d['recuperado'] / 100:,.2f} · "
        f"citas {d['citas_agendadas']} · garantía: {d['garantia']['estado']}")


def p_conexion(con, cfg, tel):
    """Chequeos, override del webhook (pide confirmación) y lectura de la configuración real de Meta."""
    if cfg["modo_prueba"]:
        return "cliente.json tiene modo_prueba: true. Ponlo en false para la prueba real."
    for nivel, msg in verificar.locales(con, cfg) + verificar.remotos(con, cfg):
        print(f"[{nivel:5}] {msg}")
    url = verificar.url_webhook(cfg)
    if input(f"¿Suscribo la app a tu WABA y apunto el webhook de tu número a {url}? [s/n] ").strip().lower() == "s":
        # requisito de Meta para el override: la app suscrita a la WABA
        print("Meta (subscribed_apps):", json.dumps(wa._graph(cfg, "POST", f"{cfg['whatsapp']['waba_id']}/subscribed_apps")))
        print("Meta (override):", json.dumps(verificar.configurar_override(cfg)))
    return None


def r_conexion(con, cfg, tel, desde):
    esperado = verificar.url_webhook(cfg)
    conf = wa.graph_get(cfg, f"{cfg['whatsapp']['phone_number_id']}?fields=webhook_configuration")
    (cfg.carpeta / "meta-webhook-configuration.json").write_text(json.dumps(conf, indent=2), encoding="utf-8")
    rutas = [r for r in _rutas(conf) if r[1] == esperado]
    try:
        with urllib.request.urlopen(cfg["url_publica"].rstrip("/") + "/salud", timeout=10) as r:
            salud = r.status
    except Exception as e:
        salud = e
    return (any(r[0] == "webhook_configuration.phone_number" for r in rutas) and salud == 200,
            f"respuesta real de Meta guardada en meta-webhook-configuration.json · la URL aparece en: "
            f"{', '.join(r[0] for r in rutas) or 'NINGÚN campo'} · túnel /salud: {salud}")


def _rutas(o, prefijo=""):
    """[(ruta.de.campo, valor)] de todas las hojas de un JSON."""
    if isinstance(o, dict):
        return [x for k, v in o.items() for x in _rutas(v, f"{prefijo}.{k}" if prefijo else k)]
    return [(prefijo, o)]


# (clave, título, qué haces tú, preparación automática o None, revisión automática)
PASOS = [
    ("conexion", "Conexión con Meta y el túnel",
     "Ten corriendo 'serve' y el túnel (docs/prueba-real.md). Este paso revisa cuentas, apunta el webhook de tu número "
     "y guarda la respuesta real de Meta.", p_conexion, r_conexion),
    ("noche", "Mensaje fuera de horario",
     "Desde tu teléfono, FUERA del horario del cliente.json, pregunta el precio de un servicio (p. ej. '¿cuánto cuesta "
     "la limpieza?'). No agendes todavía.", None, r_noche),
    ("seguimiento", "Seguimiento del día 2",
     "El comando simula que pasaron 2 días y manda la plantilla seguimiento_1. Debe llegarte a tu teléfono.",
     p_seguimiento, r_seguimiento),
    ("cita", "Cita agendada",
     "Escribe 'quiero agendar una cita de <servicio>' y responde con el número del horario. Si puedes, elige uno de HOY. "
     "Revisa que aparezca en Google Calendar.", None, r_cita),
    ("baja", "Baja de mensajes", "Escribe BAJA. Debe llegarte la confirmación.", None, r_baja),
    ("urgencia", "Urgencia",
     "Escribe 'tengo una urgencia, mucho dolor'. Te llega el mensaje del 911 y al teléfono del dueño o del equipo, "
     "el aviso.", None, r_urgencia),
    ("handoff", "Handoff a la bandeja",
     "1) En la bandeja (url_publica/bandeja) abre tu conversación, pulsa Tomar y responde algo; te debe llegar. "
     "2) Pulsa Devolver al bot. 3) Desde tu teléfono escribe 'quiero hablar con una persona'; al equipo le llega el aviso.",
     None, r_handoff),
    ("asistencia", "Asistencia",
     "En la bandeja → Citas, marca Asistió en tu cita.", p_asistencia, r_asistencia),
    ("venta", "Venta", "En la misma fila de la cita, registra una venta (p. ej. 800) y pulsa Registrar.", None, r_venta),
    ("reporte", "Reporte", "El comando genera el reporte del mes. Revisa los 5 números y la garantía.", None, r_reporte),
]


def _estado(cfg):
    ruta = cfg.carpeta / "prueba-real.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}


def _guardar(cfg, estado):
    (cfg.carpeta / "prueba-real.json").write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    filas = "\n".join(f"| {i} | {titulo} | {estado.get(k, {}).get('resultado', 'pendiente')} | "
                      f"{estado.get(k, {}).get('detalle', '')} | {estado.get(k, {}).get('nota', '')} |"
                      for i, (k, titulo, *_) in enumerate(PASOS))
    (cfg.carpeta / "prueba-real-resultados.md").write_text(
        f"# Prueba real: {cfg['nombre']}\n\n| # | Paso | Resultado | Lo que revisó el sistema | Tu nota |\n"
        f"|---|---|---|---|---|\n{filas}\n", encoding="utf-8")


def correr_paso(con, cfg, tel, clave, esperar_s=600, entrada=input):
    _, titulo, instruccion, preparar, revisar = next(p for p in PASOS if p[0] == clave)
    print(f"\n=== {titulo} ===\n{instruccion}")
    desde = base.iso(base.ahora())
    problema = preparar(con, cfg, tel) if preparar else None
    if problema:
        ok, detalle = False, problema
        print("✗", problema)
    else:
        print("Esperando… (Ctrl+C para dejar de esperar)")
        limite = time.monotonic() + esperar_s
        try:
            while True:
                ok, detalle = revisar(con, cfg, tel, desde)
                if ok or time.monotonic() > limite:
                    break
                time.sleep(3)
        except KeyboardInterrupt:
            pass
        except Exception as e:  # Meta o el túnel fallaron: se anota como falla, no rompe el recorrido
            ok, detalle = False, f"error: {e}"
        print(("✓ " if ok else "✗ ") + detalle)
    visto = entrada("¿En tu teléfono o en la bandeja pasó lo que se esperaba? [s/n] ").strip().lower() == "s"
    nota = entrada("Nota (opcional): ").strip()
    estado = _estado(cfg)
    estado[clave] = {"resultado": "pasó" if ok and visto else "falló", "detalle": detalle, "nota": nota,
                     "hora": base.iso(base.ahora())}
    _guardar(cfg, estado)
    return estado[clave]["resultado"]


def correr(con, cfg, tel, clave=None, entrada=input):
    tel = base.normalizar_tel(tel)
    if not tel:
        raise SystemExit("--tel inválido (usa tu número a 10 dígitos o +52...)")
    if tel in cfg.internos:
        raise SystemExit("tu teléfono está como dueño o equipo en cliente.json: el sistema ignora sus mensajes. "
                         "Usa otro número para dueño/equipo.")
    if clave:
        return correr_paso(con, cfg, tel, clave, entrada=entrada)
    estado = _estado(cfg)
    for k, *_ in PASOS:
        if estado.get(k, {}).get("resultado") == "pasó":
            continue
        correr_paso(con, cfg, tel, k, entrada=entrada)
        if entrada("¿Sigues con el siguiente paso? [Enter = sí, q = salir] ").strip().lower() == "q":
            break
    print(f"\nResultados en {cfg.carpeta / 'prueba-real-resultados.md'}")
