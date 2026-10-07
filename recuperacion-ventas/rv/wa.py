"""WhatsApp Cloud API: firma del webhook, lectura del payload y envíos (o registro en modo prueba)."""
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request
import uuid

from . import base

SECRETO_PRUEBA = "secreto-de-prueba"


def app_secret(cfg):
    s = os.environ.get("WA_APP_SECRET", "")
    if not s and cfg["modo_prueba"]:
        return SECRETO_PRUEBA
    return s


def firmar(secreto, cuerpo):
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def firma_valida(cfg, cuerpo, cabecera):
    secreto = app_secret(cfg)
    return bool(secreto and cabecera) and hmac.compare_digest(firmar(secreto, cuerpo), cabecera)


def separar(payload):
    """Payload de Meta → [(clave_unica, item)]. Un item por mensaje o por cambio de estado."""
    items = []
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            if ch.get("field") != "messages":
                continue
            v = ch.get("value", {})
            pnid = v.get("metadata", {}).get("phone_number_id", "")
            nombres = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in v.get("contacts", [])}
            for m in v.get("messages", []):
                items.append((f"m:{m['id']}", {"tipo": "mensaje", "pnid": pnid, "msg": m,
                                                "nombre": nombres.get(m.get("from"), "")}))
            for s in v.get("statuses", []):
                items.append((f"s:{s['id']}:{s['status']}", {"tipo": "estado", "pnid": pnid, "estado": s}))
    return items


def texto_de(msg):
    """Texto legible de un mensaje entrante; None si no es texto (audio, imagen, etc.)."""
    t = msg.get("type")
    if t == "text":
        return msg.get("text", {}).get("body", "")
    if t == "button":
        return msg.get("button", {}).get("text", "")
    if t == "interactive":
        i = msg.get("interactive", {})
        return (i.get("button_reply") or i.get("list_reply") or {}).get("title", "")
    return None


def _graph(cfg, metodo, ruta, cuerpo=None, timeout=15):
    w = cfg["whatsapp"]
    url = f"https://graph.facebook.com/{w['graph_version']}/{ruta}"
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo, headers={
        "Authorization": f"Bearer {os.environ.get('WA_TOKEN', '')}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Graph {e.code}: {e.read()[:500].decode(errors='replace')}") from None


def graph_get(cfg, ruta):
    return _graph(cfg, "GET", ruta)


def _limpiar_param(p):
    # Meta: sin saltos de línea/tabs ni más de 4 espacios seguidos, y no vacío.
    return re.sub(r"\s+", " ", str(p)).strip() or "cliente"


def enviar(con, cfg, telefono, *, texto=None, plantilla=None, params=(), contacto_id=None,
           autor="bot", destino=None):
    """Envía texto libre o plantilla. Guarda el mensaje; devuelve (mensaje_id, ok).

    destino: wa_id exacto de Meta si se conoce; si no, el teléfono E.164 sin '+'.
    """
    params = [_limpiar_param(p) for p in params]
    mostrado = texto if texto is not None else f"[{plantilla}] " + " | ".join(params)
    cur = con.execute(
        "INSERT INTO mensaje (contacto_id, telefono, direccion, tipo, texto, plantilla, autor, estado, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (contacto_id, telefono, "out", "texto" if texto is not None else "plantilla", mostrado, plantilla,
         autor, "pendiente", base.iso(base.ahora())))
    mid = cur.lastrowid
    if cfg["modo_prueba"]:
        wid = f"prueba-{uuid.uuid4().hex[:12]}"
        con.execute("UPDATE mensaje SET estado='prueba', wa_id=? WHERE id=?", (wid, mid))
        with open(cfg.carpeta / "envios-prueba.log", "a", encoding="utf-8") as f:
            f.write(f"{base.iso(base.ahora())}\t{telefono}\t{autor}\t{mostrado}\n")
        return mid, True
    cuerpo = {"messaging_product": "whatsapp", "to": destino or telefono.lstrip("+")}
    if texto is not None:
        cuerpo |= {"type": "text", "text": {"body": texto, "preview_url": False}}
    else:
        comp = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else []
        cuerpo |= {"type": "template", "template": {"name": plantilla, "language": {"code": cfg["plantillas_idioma"]},
                                                    "components": comp}}
    try:
        r = _graph(cfg, "POST", f"{cfg['whatsapp']['phone_number_id']}/messages", cuerpo)
        con.execute("UPDATE mensaje SET estado='enviado', wa_id=? WHERE id=?", (r["messages"][0]["id"], mid))
        return mid, True
    except Exception as e:  # red, 4xx/5xx: queda visible en la bandeja, sin reintento ciego
        con.execute("UPDATE mensaje SET estado='error', error=? WHERE id=?", (str(e)[:500], mid))
        base.log("envío fallido", telefono, plantilla or "texto", e)
        return mid, False
