"""Chequeos de instalación y salud de una instancia. Locales siempre; contra Meta/Anthropic/Google con --remoto."""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

from . import agenda, base, correo, ia, ms, wa

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
    em = cfg["email"]
    if em["proveedor"] == "smtp" and not os.environ.get("SMTP_CLAVE"):
        faltan.append("SMTP_CLAVE")
    if "microsoft" in (em["proveedor"], cfg["agenda"]["proveedor"]):
        faltan += [k for k in ms.SECRETOS if not os.environ.get(k) and k not in faltan]
    for proveedor, claves in (("stripe", ("STRIPE_WEBHOOK_SECRET",)), ("mercadopago", ("MP_WEBHOOK_SECRET", "MP_ACCESS_TOKEN"))):
        if cfg["pagos"][proveedor]["activo"]:
            faltan += [k for k in claves if not os.environ.get(k)]
            ok(f"pagos {proveedor}: webhook en {cfg['url_publica'].rstrip('/')}/pagos/{proveedor}")
    if faltan:
        (aviso if prueba else error)("faltan secretos: " + ", ".join(faltan))
    else:
        ok("secretos presentes")
    if not prueba:
        (ok if cfg["url_publica"].startswith("https://") else error)(f"url_publica: {cfg['url_publica']}")
        for k in ("phone_number_id", "waba_id"):
            (ok if cfg["whatsapp"].get(k) else error)(f"whatsapp.{k}: {cfg['whatsapp'].get(k) or 'vacío'}")
        if cfg["agenda"]["proveedor"] != "local" and not cfg["agenda"].get("calendar_id"):
            error("agenda.calendar_id vacío")
    if not cfg.internos:
        error("no hay dueño ni equipo con teléfono: nadie recibiría los avisos de handoff")
    try:
        ia.precios(cfg)
        ok(f"modelo de IA: {cfg['ia']['modelo']} · tope ${cfg['ia']['tope_mensual_usd']} USD/mes "
           f"· gastado este mes ${ia.costo_mes_usd(con, cfg):.2f}")
    except ia.IAError as e:
        error(str(e))
    if em["entrada"]["activa"] and em["proveedor"] == "smtp" and not correo.servidor_autenticacion(cfg):
        error("email.entrada.servidor_autenticacion vacío: sin él ningún correo se considera verificado y todos pasan "
              "a humano (pon el id que tu servidor escribe en Authentication-Results)")
    if correo.configurado(cfg):
        (ok if em["avisos_a"] else aviso)(f"correo ({em['proveedor']}, desde {em['remitente']}): avisos a "
                                          f"{', '.join(em['avisos_a']) or 'nadie'}")
    else:
        aviso("correo sin configurar: los avisos solo llegan por WhatsApp y el reporte no se manda por correo")
    aviso("reseñas nuevas de Google: dato manual en el reporte (--resenas-google); ver arranque-cuentas.md §12")
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


def _mercadopago_yo():
    req = urllib.request.Request("https://api.mercadopago.com/users/me",
                                 headers={"Authorization": f"Bearer {os.environ.get('MP_ACCESS_TOKEN', '')}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _anthropic_modelo(cfg):
    req = urllib.request.Request(f"https://api.anthropic.com/v1/models/{cfg['ia']['modelo']}", headers={
        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


PERMISOS_WA = {"whatsapp_business_messaging", "whatsapp_business_management"}


def token_meta(d):
    """Resultado de GET /debug_token → (nivel, mensaje). expires_at 0 = no vence (token de usuario del sistema)."""
    if not d.get("is_valid"):
        return "ERROR", f"token de Meta inválido o revocado: {d.get('error', {}).get('message', 'sin detalle')}"
    faltan = PERMISOS_WA - set(d.get("scopes", []))
    if faltan:
        return "ERROR", "al token de Meta le faltan permisos: " + ", ".join(sorted(faltan))
    vence = d.get("expires_at") or 0
    if not vence:
        return "OK", "token de Meta válido, no vence"
    dias = (vence - base.ahora().timestamp()) / 86400
    return ("OK" if dias > 14 else "ERROR"), (f"token de Meta vence en {dias:.0f} días: usa un token de usuario del "
                                              "sistema (no vence) para producción")


def remotos(con, cfg):
    """Llamadas de solo lectura (no envían mensajes ni cuestan): Meta, Anthropic (Models API) y Google freeBusy."""
    r = []
    if cfg["modo_prueba"]:
        return [("AVISO", "modo prueba: chequeos remotos omitidos")]
    w = cfg["whatsapp"]
    r.append(("OK", f"Meta: Graph API {w['graph_version']}"))
    try:
        n = wa.graph_get(cfg, f"{w['phone_number_id']}?fields=display_phone_number,verified_name,quality_rating")
        r.append(("OK", f"Meta: {n.get('display_phone_number')} · {n.get('verified_name')} · calidad {n.get('quality_rating')}"))
    except Exception as e:
        r.append(("ERROR", f"Meta: no se pudo leer el número: {e}"))
    try:
        r.append(token_meta(wa.graph_get(cfg, "debug_token?input_token=" + os.environ.get("WA_TOKEN", "")).get("data", {})))
    except Exception as e:
        r.append(("AVISO", f"token de Meta: no se pudo revisar ({e})"))
    try:
        apps = wa.graph_get(cfg, f"{w['waba_id']}/subscribed_apps").get("data", [])
        r.append(("OK" if apps else "ERROR", "app suscrita a la WABA" if apps else
                  "ninguna app suscrita a la WABA: sin esto Meta no manda webhooks (prueba-real lo configura)"))
    except Exception as e:
        r.append(("ERROR", f"suscripción de la app a la WABA: {e}"))
    esperado = url_webhook(cfg)
    try:
        # Formato documentado por Meta: {"webhook_configuration": {"phone_number": url, "application": url}}.
        # prueba-real guarda la respuesta real para confirmarlo.
        conf = wa.graph_get(cfg, f"{w['phone_number_id']}?fields=webhook_configuration")
        ok = conf.get("webhook_configuration", {}).get("phone_number") == esperado
        r.append(("OK" if ok else "ERROR", f"override de webhook del número → {esperado}" +
                  ("" if ok else f" NO configurado (Meta respondió {json.dumps(conf)[:300]})")))
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
    if cfg["pagos"]["mercadopago"]["activo"]:
        try:
            yo = _mercadopago_yo()
            r.append(("OK", f"Mercado Pago: token válido ({yo.get('nickname') or yo.get('id')}, {yo.get('site_id')})"))
        except Exception as e:
            r.append(("ERROR", f"Mercado Pago: {e} (revisa MP_ACCESS_TOKEN de producción)"))
    if cfg["pagos"]["stripe"]["activo"]:
        r.append(("AVISO", "Stripe: sin llamada de lectura posible con solo el secreto del webhook; manda un evento de "
                           "prueba desde el panel de Stripe y revisa el log"))
    if correo.configurado(cfg):
        try:
            r.append(("OK", probar_correo(cfg)))
        except Exception as e:
            r.append(("ERROR", f"correo: {e}"))
    if cfg["agenda"]["proveedor"] == "google":
        try:
            agenda.ocupado_externo(cfg, base.ahora(), base.ahora() + dt.timedelta(days=1))
            r.append(("OK", "Google Calendar: lectura de disponibilidad (freeBusy)"))
        except Exception as e:
            r.append(("ERROR", f"Google Calendar: {e} (comparte el calendario con {_correo_cuenta_servicio()} "
                               "con permiso «Hacer cambios en eventos»)"))
    if cfg["agenda"]["proveedor"] == "microsoft":
        try:
            faltan = PERMISOS_MS["calendario"] - ms.permisos()
            if faltan:
                raise RuntimeError(f"a la app le falta el permiso de aplicación {', '.join(faltan)} con consentimiento")
            agenda.ocupado_externo(cfg, base.ahora(), base.ahora() + dt.timedelta(days=1))
            r.append(("OK", f"calendario Microsoft 365 de {cfg['agenda']['calendar_id']}: lectura (getSchedule) y permiso "
                            "de escritura"))
        except Exception as e:
            r.append(("ERROR", f"calendario Microsoft 365: {e}"))
    return r


def url_webhook(cfg):
    return cfg["url_publica"].rstrip("/") + "/webhook"


PERMISOS_MS = {"correo": {"Mail.Send"}, "entrada": {"Mail.ReadWrite", "Mail.Send"}, "calendario": {"Calendars.ReadWrite"}}


def probar_correo(cfg):
    """Inicia sesión sin mandar nada (SMTP) o revisa que la app de Microsoft tenga Mail.Send concedido."""
    em = cfg["email"]
    if em["proveedor"] == "microsoft":
        faltan = PERMISOS_MS["correo"] - ms.permisos()
        if faltan:
            raise RuntimeError(f"a la app de Microsoft le falta el permiso de aplicación {', '.join(faltan)} "
                               "con consentimiento de administrador")
        if em["entrada"]["activa"]:
            faltan = PERMISOS_MS["entrada"] - ms.permisos()
            if faltan:
                raise RuntimeError(f"para leer y contestar el buzón falta el permiso de aplicación {', '.join(faltan)}")
            ms.graph("GET", ms.buzon(em["remitente"]) + "/mailFolders/inbox?$select=unreadItemCount")
        return (f"correo Microsoft 365: token y permisos para {em['remitente']}"
                + (" (envío y lectura del buzón)" if em["entrada"]["activa"] else " (envío)"))
    with correo.conectar_smtp(cfg):
        pass
    s = em["smtp"]
    msg = f"correo SMTP: sesión iniciada en {s['host']}:{s['puerto']} como {correo.usuario_smtp(cfg)}"
    if em["entrada"]["activa"]:
        import imaplib
        import ssl
        i = em["entrada"]["imap"]
        con = imaplib.IMAP4_SSL(i["host"], int(i["puerto"]), ssl_context=ssl.create_default_context(), timeout=15)
        try:
            con.login(correo.usuario_smtp(cfg), os.environ.get("SMTP_CLAVE", ""))
            con.select("INBOX", readonly=True)   # solo lectura: no marca nada como leído
        finally:
            con.logout()
        msg += f"; buzón IMAP {i['host']} abierto (solo lectura)"
    return msg


def _correo_cuenta_servicio():
    try:
        with open(os.environ.get("GOOGLE_SA_FILE", ""), encoding="utf-8") as f:
            return json.load(f)["client_email"]
    except (OSError, ValueError, KeyError):
        return "la cuenta de servicio"


def configurar_override(cfg):
    """Apunta el webhook del número de este cliente a su URL (paso de instalación)."""
    if cfg["modo_prueba"]:
        raise RuntimeError("en modo prueba no se configura Meta")
    return wa._graph(cfg, "POST", cfg["whatsapp"]["phone_number_id"], {
        "webhook_configuration": {"override_callback_uri": url_webhook(cfg), "verify_token": os.environ["WA_VERIFY_TOKEN"]}})
