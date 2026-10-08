"""WhatsApp Cloud API: firma del webhook, lectura del payload y envíos (o registro en modo prueba)."""
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
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
    # en bytes: compare_digest con texto no ASCII lanza TypeError y tiraría la conexión
    return bool(secreto and cabecera) and hmac.compare_digest(firmar(secreto, cuerpo).encode(),
                                                              cabecera.encode("utf-8", "replace"))


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


TIPOS_MEDIA = {"image": "foto", "audio": "nota de voz", "video": "video", "document": "documento", "sticker": "sticker"}


def no_texto(msg):
    """(texto para mostrar, media_id, mime) de un mensaje que no es texto. Conserva el texto de la foto/documento."""
    t = msg.get("type", "desconocido")
    datos = msg.get(t) if isinstance(msg.get(t), dict) else {}
    if t in TIPOS_MEDIA:
        extra = datos.get("caption") or datos.get("filename") or ""
        return f"[{TIPOS_MEDIA[t]}] {extra}".strip(), datos.get("id"), datos.get("mime_type")
    if t == "location":
        partes = [datos.get("name"), datos.get("address"), f"{datos.get('latitude')},{datos.get('longitude')}"]
        return "[ubicación] " + " · ".join(str(x) for x in partes if x and x != "None,None"), None, None
    if t == "reaction":
        return f"[reacción {datos.get('emoji') or 'quitada'}]", None, None
    return f"[{t}]", None, None


MAX_MEDIA = 25_000_000


def descargar_media(cfg, media_id):
    """Bytes y tipo de un archivo del cliente (la URL de Meta dura minutos: se pide cada vez)."""
    info = _graph(cfg, "GET", urllib.parse.quote(media_id, safe=""))
    req = urllib.request.Request(info["url"], headers={"Authorization": f"Bearer {os.environ.get('WA_TOKEN', '')}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        datos = r.read(MAX_MEDIA + 1)
    if len(datos) > MAX_MEDIA:
        raise RuntimeError("archivo demasiado grande")
    return datos, info.get("mime_type") or "application/octet-stream"


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


_PLANTILLAS = None


def texto_plantilla(nombre, params):
    """Texto que recibe el cliente, armado desde docs/plantillas.md (única fuente de los textos)."""
    global _PLANTILLAS
    if _PLANTILLAS is None:
        doc = (base.RAIZ / "docs" / "plantillas.md").read_text(encoding="utf-8")
        _PLANTILLAS = dict(re.findall(r"^### (\w+) \([^)]*\)\n```\n(.*?)\n```", doc, re.M | re.S))
    t = _PLANTILLAS.get(nombre)
    if t is None:
        return f"[{nombre}] " + " | ".join(params)
    return re.sub(r"\{\{(\d+)\}\}", lambda m: params[int(m.group(1)) - 1] if int(m.group(1)) <= len(params)
                  else m.group(0), t)


def _limpiar_param(p):
    # Meta: sin saltos de línea/tabs ni más de 4 espacios seguidos, y no vacío.
    return re.sub(r"\s+", " ", str(p)).strip() or "cliente"


def enviar(con, cfg, telefono, *, texto=None, plantilla=None, params=(), contacto_id=None,
           autor="bot"):
    """Envía texto libre o plantilla. Guarda el mensaje; devuelve (mensaje_id, ok).

    Siempre al teléfono E.164 sin '+', no al wa_id: en México Meta manda 521 + 10 dígitos, pero la lista de
    destinatarios del número de prueba y la marcación actual son 52 + 10 (con 521 falla con 131030)."""
    params = [_limpiar_param(p) for p in params]
    mostrado = texto if texto is not None else texto_plantilla(plantilla, params)
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
    cuerpo = {"messaging_product": "whatsapp", "to": telefono.lstrip("+")}
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
