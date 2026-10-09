"""Tick (cada 5 min): avisos y escalamientos, recordatorios, reseñas, seguimiento 2/5/10 y reactivación."""
import datetime as dt

from . import base, clientes, correo, motor, wa

POR_TICK_REACTIVACION = 10   # reparte el lote diario para no saturar al equipo con respuestas simultáneas


MAX_INTENTOS = 3   # fallas del mismo envío en 24 h antes de rendirse


def puede_proactivo(con, cfg, c, por_correo=False):
    """Opt-out global y exclusión de equipo/dueño: se revisa antes de cada envío proactivo. Los contactos de correo
    solo reciben lo transaccional (recordatorio de su cita, por_correo=True); seguimiento, reactivación y reseñas son
    de WhatsApp, con plantillas aprobadas y consentimiento."""
    if base.es_correo(c) and not por_correo:
        return False
    return c["telefono"] not in cfg.internos and not base.dio_baja(con, c["telefono"])


def _plantilla(con, cfg, c, nombre, params):
    """'ok' si salió; 'reintentar' si falló (se intenta en el siguiente tick); 'agotado' tras MAX_INTENTOS fallas.

    ponytail: si Meta aceptó el envío pero la respuesta se perdió (timeout), el reintento puede duplicarlo;
    es preferible a no enviar un recordatorio."""
    if base.es_correo(c):   # mismo texto que la plantilla, en el hilo del cliente
        ok = motor.responder(con, cfg, motor.contacto(con, c["id"]), wa.texto_plantilla(nombre, params), autor="sistema")[1]
    else:
        ok = wa.enviar(con, cfg, c["telefono"], plantilla=nombre, params=params, contacto_id=c["id"], autor="sistema")[1]
    if ok:
        return "ok"
    fallas = con.execute("SELECT COUNT(*) FROM mensaje WHERE contacto_id=? AND plantilla=? AND estado='error' "
                         "AND creado>?", (c["id"], nombre, base.iso(base.ahora() - dt.timedelta(hours=24)))).fetchone()[0]
    return "agotado" if fallas >= MAX_INTENTOS else "reintentar"


# ---------- avisos al equipo ----------

def avisos_pendientes(con, cfg, t):
    if not base.abierto(cfg, t):
        return 0
    filas = con.execute("SELECT * FROM contacto WHERE aviso_pendiente=1").fetchall()
    for c in filas:
        con.execute("UPDATE contacto SET aviso_pendiente=0 WHERE id=?", (c["id"],))
        if c["estado"] == "humano":
            motor.avisar_equipo(con, cfg, c, c["handoff_motivo"] or "handoff")
    return len(filas)


def escalamientos(con, cfg, t):
    """Mensaje del cliente sin respuesta humana por más de N minutos de horario abierto → nuevo aviso."""
    apertura = base.inicio_apertura_actual(cfg, t)
    if not apertura:
        return 0
    limite = dt.timedelta(minutes=int(cfg["escalamiento_min"]))
    n = 0
    for c in con.execute("SELECT * FROM contacto WHERE estado='humano'").fetchall():
        ult_in = con.execute("SELECT MAX(creado) FROM mensaje WHERE contacto_id=? AND direccion='in' AND "
                             + base.NO_REACCION, (c["id"],)).fetchone()[0]
        ult_humano = con.execute("SELECT MAX(creado) FROM mensaje WHERE contacto_id=? AND autor LIKE 'humano:%'",
                                 (c["id"],)).fetchone()[0]
        if not ult_in or (ult_humano and ult_humano >= ult_in) or (c["escalado"] and c["escalado"] >= ult_in):
            continue
        # se cuenta desde lo último entre: su mensaje, la apertura y el paso a humano (uno manual es reciente)
        desde = max(base.de_iso(ult_in), apertura, base.de_iso(c["handoff_desde"]) or apertura)
        if t - desde >= limite:
            motor.avisar_equipo(con, cfg, c, "escalamiento")
            con.execute("UPDATE contacto SET escalado=? WHERE id=?", (base.iso(t), c["id"]))
            n += 1
    return n


# ---------- recordatorios de cita ----------

def recordatorios(con, cfg, t):
    n = 0
    if not base.en_ventana_envio(cfg, t):
        return 0
    filas = con.execute("SELECT ci.*, co.telefono, co.nombre FROM cita ci JOIN contacto co "
                        "ON co.id=ci.contacto_id WHERE ci.estado='agendada' AND ci.inicio>? AND "
                        "(ci.rec24 IS NULL OR ci.rec2 IS NULL)", (base.iso(t),)).fetchall()
    for f in filas:
        inicio, creado = base.de_iso(f["inicio"]), base.de_iso(f["creado"])
        c = {"id": f["contacto_id"], "telefono": f["telefono"], "nombre": f["nombre"]}
        for campo, horas in (("rec24", 24), ("rec2", 2)):
            if f[campo]:
                continue
            objetivo = inicio - dt.timedelta(hours=horas)
            # creada dentro del plazo (ya recibió confirmación), dio de baja, o el de 2 h cae fuera de la ventana
            if (creado >= objetivo or not puede_proactivo(con, cfg, c, por_correo=True)
                    or (horas == 2 and not base.en_ventana_envio(cfg, objetivo))):
                con.execute(f"UPDATE cita SET {campo}='omitido' WHERE id=?", (f["id"],))
                continue
            momento = base.ultimo_momento_envio_antes(cfg, objetivo) if horas == 24 else objetivo
            if momento is None or t < momento:
                continue
            servicio = cfg.nombre_servicio(f["servicio_id"])
            r = _plantilla(con, cfg, c, "recordatorio_cita",
                           [c["nombre"], servicio, base.fecha_humana(cfg, inicio),
                            cfg.get("direccion", cfg["nombre"]).rstrip(". ")])   # la plantilla ya pone el punto
            if r != "reintentar":
                con.execute(f"UPDATE cita SET {campo}=? WHERE id=?", (base.iso(t) if r == "ok" else "error", f["id"]))
            n += r == "ok"
    return n


# ---------- reseñas ----------

def resenas(con, cfg, t):
    r = cfg["resenas"]
    if not r.get("link") or not base.en_ventana_envio(cfg, t):
        return 0
    limite = base.iso(t - dt.timedelta(hours=float(r["horas_despues"])))
    n = 0
    for f in con.execute("SELECT ci.id AS cita_id, ci.asistio_en, co.* FROM cita ci JOIN contacto co "
                         "ON co.id=ci.contacto_id WHERE ci.estado='asistio' AND ci.resena_enviada IS NULL "
                         "AND ci.asistio_en<=?", (limite,)).fetchall():
        reciente = con.execute("SELECT 1 FROM evento WHERE contacto_id=? AND tipo='resena' AND creado>?",
                               (f["id"], base.iso(t - dt.timedelta(days=int(r["cada_dias"]))))).fetchone()
        viejo = base.de_iso(f["asistio_en"]) < t - dt.timedelta(days=7)
        if reciente or viejo or not puede_proactivo(con, cfg, f):
            con.execute("UPDATE cita SET resena_enviada='omitida' WHERE id=?", (f["cita_id"],))
            continue
        res = _plantilla(con, cfg, f, "resena", [f["nombre"], cfg["nombre"], r["link"]])
        if res != "reintentar":
            con.execute("UPDATE cita SET resena_enviada=? WHERE id=?", (base.iso(t) if res == "ok" else "error",
                                                                      f["cita_id"]))
        if res == "ok":
            base.evento(con, f["id"], "resena")
            n += 1
    return n


# ---------- seguimiento 2/5/10 ----------

def seguimiento(con, cfg, t):
    if not base.en_ventana_envio(cfg, t):
        return 0
    dias = [int(x) for x in cfg["seguimiento_dias"]]
    n = 0
    for c in con.execute("SELECT * FROM contacto WHERE seg_activo=1 AND seg_paso<?", (len(dias),)).fetchall():
        if c["estado"] != "bot" or motor.tiene_cita_futura(con, c["id"]) or not puede_proactivo(con, cfg, c):
            con.execute("UPDATE contacto SET seg_activo=0 WHERE id=?", (c["id"],))
            continue
        paso = c["seg_paso"]
        vence = base.de_iso(c["seg_inicio"]) + dt.timedelta(days=dias[paso])
        anterior = con.execute("SELECT MAX(creado) FROM evento WHERE contacto_id=? AND tipo='seguimiento'",
                               (c["id"],)).fetchone()[0]
        if anterior and base.de_iso(anterior) >= base.de_iso(c["seg_inicio"]):
            vence = max(vence, base.de_iso(anterior) + dt.timedelta(days=1))   # nunca dos en el mismo día
        if t < vence:
            continue
        servicio = cfg.nombre_servicio(c["seg_servicio"] or "", "nuestros servicios")
        params = [c["nombre"], cfg["nombre"], servicio] if paso == 1 else [c["nombre"], servicio]
        res = _plantilla(con, cfg, c, f"seguimiento_{paso + 1}", params)
        if res == "reintentar":
            continue
        con.execute("UPDATE contacto SET seg_paso=?, seg_activo=? WHERE id=?",
                    (paso + 1, int(paso + 1 < len(dias)), c["id"]))
        if res == "ok":
            base.evento(con, c["id"], "seguimiento", str(paso + 1))
            n += 1
    return n


# ---------- reactivación ----------

def calidad_del_numero(con, cfg, t):
    """Lee la calidad del número una vez al día y cuenta días seguidos en GREEN."""
    hoy = t.astimezone(cfg.tz).date().isoformat()
    if base.get_estado(con, "calidad_fecha") == hoy:
        return base.get_estado(con, "calidad", "UNKNOWN")
    if cfg["modo_prueba"]:
        calidad = base.get_estado(con, "calidad_simulada", "GREEN")
    else:
        try:
            calidad = wa.graph_get(cfg, f"{cfg['whatsapp']['phone_number_id']}?fields=quality_rating").get(
                "quality_rating", "UNKNOWN")
        except Exception as e:  # sin dato de calidad: lote base, nunca el máximo
            base.log("calidad:", e)
            calidad = "UNKNOWN"
    verdes = int(base.get_estado(con, "dias_verde", "0"))
    base.set_estado(con, "dias_verde", verdes + 1 if calidad == "GREEN" else 0)
    base.set_estado(con, "calidad", calidad)
    base.set_estado(con, "calidad_fecha", hoy)
    if calidad == "RED":
        motor.avisar_equipo(con, cfg, None, "calidad_roja", solo_dueno=True)
    return calidad


def lote_del_dia(con, cfg, calidad):
    r = cfg["reactivacion"]
    if calidad == "RED":
        return 0
    if calidad == "GREEN" and int(base.get_estado(con, "dias_verde", "0")) >= int(r["dias_verde_para_subir"]):
        return int(r["lote_max"])
    return int(r["lote_base"])


def reactivacion(con, cfg, t):
    if not base.en_ventana_envio(cfg, t):
        return 0
    lote = lote_del_dia(con, cfg, calidad_del_numero(con, cfg, t))
    inicio_dia = t.astimezone(cfg.tz).replace(hour=0, minute=0, second=0)
    hoy = con.execute("SELECT COUNT(*) FROM evento WHERE tipo='reactivacion' AND creado>=?",
                      (base.iso(inicio_dia),)).fetchone()[0]
    cupo = min(lote - hoy, POR_TICK_REACTIVACION)
    if cupo <= 0:
        return 0
    sin_contacto = base.iso(t - dt.timedelta(days=int(cfg["reactivacion"]["sin_contacto_dias"])))
    candidatos = con.execute(
        "SELECT * FROM contacto c WHERE consentimiento=1 AND reactivacion_enviada IS NULL AND estado='bot' "
        "AND (ultimo_entrante IS NULL OR ultimo_entrante<?) "
        "AND NOT EXISTS (SELECT 1 FROM optout o WHERE o.telefono=c.telefono) "
        "AND NOT EXISTS (SELECT 1 FROM cita ci WHERE ci.contacto_id=c.id AND ci.estado='agendada' AND ci.inicio>?) "
        "ORDER BY ultima_visita IS NULL, ultima_visita DESC, id LIMIT ?",
        (sin_contacto, base.iso(t), cupo * 2)).fetchall()
    n = 0
    for c in candidatos:
        if n >= cupo:
            break
        if not puede_proactivo(con, cfg, c):
            continue
        res = _plantilla(con, cfg, c, "reactivacion", [c["nombre"], cfg["nombre"]])
        if res != "reintentar":
            con.execute("UPDATE contacto SET reactivacion_enviada=? WHERE id=?",
                        (base.iso(t) if res == "ok" else "error", c["id"]))
        if res == "ok":
            base.evento(con, c["id"], "reactivacion")
            n += 1
    return n


# ---------- correo entrante (canal de clientes) ----------

def correo_entrante(con, cfg, t):
    """Lee el buzón y deja cada correo en la cola; el trabajador del servidor lo contesta en segundos (un solo
    trabajador mantiene el orden por cliente). A cualquier hora: contestar no es un envío proactivo."""
    em = cfg["email"]
    if not em["entrada"]["activa"] or not correo.configurado(cfg) or cfg["modo_prueba"]:
        return 0
    return correo.leer_entrada(cfg, lambda item: motor.encolar_correo(con, item))


PASOS = [correo_entrante, avisos_pendientes, escalamientos, recordatorios, resenas, seguimiento, reactivacion,
         clientes.vencer_cotizaciones]


def correr(con, cfg):
    """Ejecuta cada paso; la falla de uno no detiene a los demás. Devuelve {paso: enviados | 'error'}."""
    t = base.ahora()
    base.set_estado(con, "ultimo_tick", base.iso(t))
    resumen = {}
    for paso in PASOS:
        try:
            resumen[paso.__name__] = paso(con, cfg, t)
        except Exception as e:
            base.log(f"tick {paso.__name__}:", repr(e))
            resumen[paso.__name__] = "error"
    return resumen

