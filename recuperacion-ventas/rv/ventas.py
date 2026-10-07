"""Importación de clientes (reactivación), ventas, atribución, reporte mensual y página estática."""
import csv

from . import base

SI = {"si", "s", "1", "true", "x", "yes", "acepta", "acepto"}
NO = {"no", "n", "0", "false"}


def _columnas(fila):
    return {base.normalizar_texto(k).replace(" ", "_"): (v or "").strip() for k, v in fila.items() if k}


def importar_clientes(con, cfg, ruta):
    """CSV con columnas nombre, telefono, ultima_visita (AAAA-MM-DD, opcional), consentimiento (si/no).

    Normaliza a E.164, deduplica (en el archivo y contra la base) y excluye teléfonos del equipo.
    Si 'consentimiento' viene vacío no cambia el valor existente; 'no' lo revoca.
    """
    r = {"nuevos": 0, "actualizados": 0, "duplicados": 0, "equipo": 0, "rechazados": []}
    vistos = set()
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        for linea, fila in enumerate(csv.DictReader(f), start=2):
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
            visita = d.get("ultima_visita") or None
            if visita and not _es_fecha(visita):
                r["rechazados"].append((linea, f"ultima_visita no es AAAA-MM-DD: {visita!r}"))
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


def _es_fecha(s):
    import datetime as dt
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False
