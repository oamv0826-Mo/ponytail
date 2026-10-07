"""Chequeos de instalación y salud de una instancia. Locales siempre; contra Meta/Anthropic/Google con --remoto."""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

from . import agenda, base, ia, wa

PLANTILLAS = ["aviso_equipo", "retomar_contacto", "cita_confirmada", "recordatorio_cita", "seguimiento_1",
              "seguimiento_2", "seguimiento_3", "reactivacion", "resena"]


def locales(con, cfg):
    r = []
    ok = lambda m: r.append(("OK", m))        # noqa: E731
    aviso = lambda m: r.append(("AVISO", m))  # noqa: E731
    error = lambda m: r.append(("ERROR", m))  # noqa: E731
    prueba = cfg["modo_prueba"]
    ok(f"config válido: {cfg['nombre']} · {len(cfg.servicios)} servicios")
    (aviso if prueba else ok)("modo prueba ACTIVADO: no se envía nada a Meta" if prueba else "modo producción")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    ultima = base.secciones_esquema()[-1][0]
    (ok if version == ultima else error)(f"esquema de base de datos v{version} (esperado v{ultima})")
    n_usuarios = con.execute("SELECT COUNT(*) FROM usuario").fetchone()[0]
    (ok if n_usuarios else error)(f"usuarios de la bandeja: {n_usuarios}")
    faltan = [k for k in ("WA_TOKEN", "WA_APP_SECRET", "WA_VERIFY_TOKEN", "ANTHROPIC_API_KEY") if not os.environ.get(k)]
    if cfg["agenda"]["proveedor"] == "google":
        sa = os.environ.get("GOOGLE_SA_FILE", "")
        if not sa or not os.path.exists(sa):
            faltan.append("GOOGLE_SA_FILE")
    if faltan:
        (aviso if prueba else error)("faltan secretos: " + ", ".join(faltan))
    else:
        ok("secretos presentes")
    if not prueba:
        (ok if cfg["url_publica"].startswith("https://") else error)(f"url_publica: {cfg['url_publica']}")
        for k in ("phone_number_id", "waba_id"):
            (ok if cfg["whatsapp"].get(k) else error)(f"whatsapp.{k}: {cfg['whatsapp'].get(k) or 'vacío'}")
        if cfg["agenda"]["proveedor"] == "google" and not cfg["agenda"].get("calendar_id"):
            error("agenda.calendar_id vacío")
    if not cfg.internos:
        error("no hay dueño ni equipo con teléfono: nadie recibiría los avisos de handoff")
    try:
        ia.precios(cfg)
        ok(f"modelo de IA: {cfg['ia']['modelo']} · tope ${cfg['ia']['tope_mensual_usd']} USD/mes "
           f"· gastado este mes ${ia.costo_mes_usd(con, cfg):.2f}")
    except ia.IAError as e:
        error(str(e))
    (ok if cfg["resenas"].get("link") else aviso)(f"link de reseñas: {cfg['resenas'].get('link') or 'vacío (no se piden reseñas)'}")
    rotas = con.execute("SELECT COUNT(*) FROM entrada WHERE error IS NOT NULL").fetchone()[0]
    (aviso if rotas else ok)(f"entradas del webhook con error: {rotas}")
    hace7 = base.iso(base.ahora() - dt.timedelta(days=7))
    fallidos = con.execute("SELECT COUNT(*) FROM mensaje WHERE estado IN ('error','failed') AND creado>?", (hace7,)).fetchone()[0]
    (aviso if fallidos else ok)(f"envíos fallidos en 7 días: {fallidos}")
    pend = con.execute("SELECT COUNT(*) FROM entrada WHERE terminado IS NULL").fetchone()[0]
    (aviso if pend > 20 else ok)(f"cola pendiente: {pend}")
    ultimo = base.get_estado(con, "ultimo_tick")
    if not prueba:
        atrasado = not ultimo or base.ahora() - base.de_iso(ultimo) > dt.timedelta(minutes=15)
        (error if atrasado else ok)(f"último tick: {ultimo or 'nunca'}")
    return r


def _anthropic_modelo(cfg):
    req = urllib.request.Request(f"https://api.anthropic.com/v1/models/{cfg['ia']['modelo']}", headers={
        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def remotos(con, cfg):
    """Llamadas de solo lectura (no envían mensajes ni cuestan): Meta, Anthropic (Models API) y Google freeBusy."""
    r = []
    if cfg["modo_prueba"]:
        return [("AVISO", "modo prueba: chequeos remotos omitidos")]
    w = cfg["whatsapp"]
    try:
        n = wa.graph_get(cfg, f"{w['phone_number_id']}?fields=display_phone_number,verified_name,quality_rating")
        r.append(("OK", f"Meta: {n.get('display_phone_number')} · {n.get('verified_name')} · calidad {n.get('quality_rating')}"))
    except Exception as e:
        r.append(("ERROR", f"Meta: no se pudo leer el número: {e}"))
    esperado = cfg["url_publica"].rstrip("/") + "/webhook"
    try:
        # ponytail: se busca la URL en toda la respuesta en lugar de depender del nombre exacto del campo.
        conf = wa.graph_get(cfg, f"{w['phone_number_id']}?fields=webhook_configuration")
        r.append(("OK" if esperado in json.dumps(conf) else "ERROR",
                  f"override de webhook del número → {esperado}" + ("" if esperado in json.dumps(conf) else
                                                                     f" NO configurado (Meta respondió {json.dumps(conf)[:300]})")))
    except Exception as e:
        r.append(("ERROR", f"override de webhook: {e}"))
    try:
        datos = wa.graph_get(cfg, f"{w['waba_id']}/message_templates?fields=name,status,language&limit=200").get("data", [])
        estado = {(t["name"], t.get("language")): t.get("status") for t in datos}
        for p in PLANTILLAS:
            st = estado.get((p, cfg["plantillas_idioma"]))
            r.append(("OK" if st == "APPROVED" else "ERROR", f"plantilla {p} ({cfg['plantillas_idioma']}): {st or 'no existe'}"))
    except Exception as e:
        r.append(("ERROR", f"plantillas: {e}"))
    try:
        m = _anthropic_modelo(cfg)
        r.append(("OK", f"Anthropic: clave válida, modelo {m.get('id')}"))
    except urllib.error.HTTPError as e:
        r.append(("ERROR", f"Anthropic: {e.code} (clave o modelo inválido)"))
    except Exception as e:
        r.append(("ERROR", f"Anthropic: {e}"))
    if cfg["agenda"]["proveedor"] == "google":
        try:
            agenda.ocupado_google(cfg, base.ahora(), base.ahora() + dt.timedelta(days=1))
            r.append(("OK", "Google Calendar: acceso al calendario"))
        except Exception as e:
            r.append(("ERROR", f"Google Calendar: {e} (¿se compartió el calendario con la cuenta de servicio?)"))
    return r


def configurar_override(cfg):
    """Apunta el webhook del número de este cliente a su URL (paso de instalación)."""
    if cfg["modo_prueba"]:
        raise RuntimeError("en modo prueba no se configura Meta")
    url = cfg["url_publica"].rstrip("/") + "/webhook"
    return wa._graph(cfg, "POST", cfg["whatsapp"]["phone_number_id"], {
        "webhook_configuration": {"override_callback_uri": url, "verify_token": os.environ["WA_VERIFY_TOKEN"]}})
