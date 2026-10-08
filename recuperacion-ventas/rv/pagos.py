"""Pagos en línea → ventas registradas solas: webhooks de Stripe y Mercado Pago (firma verificada), a la misma cola
que WhatsApp; el trabajador busca al cliente por teléfono o correo y llama a ventas.registrar_venta (misma atribución
que una venta capturada a mano). Clip: sin webhooks documentados de pagos; sus ventas entran por importar-ventas."""
import csv
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import base, ventas

TOLERANCIA_S = 300   # Stripe: una firma de hace más de 5 min se rechaza (evita reenvíos de un evento capturado)


def _hmac(secreto, datos):
    return hmac.new(secreto.encode(), datos, hashlib.sha256).hexdigest()


def _partes(cabecera):
    return [p.strip().split("=", 1) for p in (cabecera or "").split(",") if "=" in p]


def firma_stripe_valida(cuerpo, cabecera, secreto, ahora_s=None):
    """Stripe-Signature: t=<ts>,v1=<hex>[,v1=...]; firma = HMAC-SHA256(secreto, '<t>.' + cuerpo crudo).
    Primero la firma, después la antigüedad: antes de verificarla, t lo puede poner cualquiera."""
    partes = _partes(cabecera)
    t = next((v for k, v in partes if k == "t"), "")
    firmas = [v for k, v in partes if k == "v1"]
    if not secreto or not t.isdigit() or not firmas:
        return False
    esperada = _hmac(secreto, t.encode() + b"." + cuerpo).encode()
    if not any(hmac.compare_digest(esperada, f.encode("utf-8", "replace")) for f in firmas):
        return False
    return abs((ahora_s or base.ahora().timestamp()) - int(t)) <= TOLERANCIA_S


def firma_mercadopago_valida(x_signature, x_request_id, data_id, secreto):
    """x-signature: ts=<ts>,v1=<hex>; firma = HMAC-SHA256(secreto, 'id:<data.id>;request-id:<x-request-id>;ts:<ts>;'),
    omitiendo las partes que no vengan; data.id alfanumérico va en minúsculas."""
    partes = dict(_partes(x_signature))
    ts, v1 = partes.get("ts"), partes.get("v1")
    if not secreto or not ts or not v1:
        return False
    plantilla = (f"id:{str(data_id).lower()};" if data_id else "") + \
        (f"request-id:{x_request_id};" if x_request_id else "") + f"ts:{ts};"
    return hmac.compare_digest(_hmac(secreto, plantilla.encode()).encode(), v1.encode("utf-8", "replace"))


def recibir(con, cfg, proveedor, cuerpo, cabeceras, query):
    """Webhook de pagos → (código HTTP, motivo). Solo verifica y encola: el trabajador procesa."""
    p = cfg["pagos"].get(proveedor, {})
    if not p.get("activo"):
        return 404, "proveedor de pagos no activo"
    if proveedor == "stripe":
        if not firma_stripe_valida(cuerpo, cabeceras.get("Stripe-Signature"), os.environ.get("STRIPE_WEBHOOK_SECRET", "")):
            return 401, "firma inválida"
        try:
            evento = json.loads(cuerpo)
        except ValueError:
            return 400, "JSON inválido"
        if evento.get("type") != "checkout.session.completed":
            return 200, "evento ignorado"   # 200 para que Stripe no lo reintente
        clave, item = f"p:stripe:{evento['id']}", {"tipo": "pago", "proveedor": "stripe", "objeto": evento["data"]["object"]}
    else:
        data_id = (query.get("data.id") or [""])[0]
        if not firma_mercadopago_valida(cabeceras.get("x-signature"), cabeceras.get("x-request-id"), data_id,
                                        os.environ.get("MP_WEBHOOK_SECRET", "")):
            return 401, "firma inválida"
        if (query.get("type") or [""])[0] != "payment" or not data_id:
            return 200, "evento ignorado"
        if not cabeceras.get("x-request-id"):
            return 400, "falta x-request-id"
        # un pago puede notificarse varias veces (creado, actualizado): cada aviso (x-request-id, firmado) se revisa
        # una vez; repetir un aviso capturado no vuelve a encolarlo, y la venta tampoco se duplica
        clave = f"p:mp:{data_id}:{cabeceras['x-request-id']}"
        item = {"tipo": "pago", "proveedor": "mercadopago", "id": data_id}
    con.execute("INSERT OR IGNORE INTO entrada (clave, payload, recibido) VALUES (?,?,?)",
                (clave, json.dumps(item, ensure_ascii=False), base.iso(base.ahora())))
    return 200, "ok"


def pago_mercadopago(pago_id):
    req = urllib.request.Request(f"https://api.mercadopago.com/v1/payments/{urllib.parse.quote(str(pago_id), safe='')}",
                                 headers={"Authorization": f"Bearer {os.environ.get('MP_ACCESS_TOKEN', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Mercado Pago {e.code}: {e.read()[:300].decode(errors='replace')}") from None


def normalizar(item):
    """Pago de cualquier proveedor → {id, aprobado, centavos, moneda, fecha (UTC), telefono, email, nombre}."""
    if item["proveedor"] == "stripe":
        s = item["objeto"]
        cliente = s.get("customer_details") or {}
        return {"id": s["id"], "aprobado": s.get("payment_status") == "paid", "centavos": s.get("amount_total") or 0,
                "moneda": (s.get("currency") or "").upper(), "fecha": dt.datetime.fromtimestamp(s["created"], base.UTC),
                "telefono": cliente.get("phone"), "email": cliente.get("email"), "nombre": cliente.get("name")}
    p = pago_mercadopago(item["id"])
    payer, tel = p.get("payer") or {}, (p.get("payer") or {}).get("phone") or {}
    numero = f"{tel.get('area_code') or ''}{tel.get('number') or ''}"
    fecha = dt.datetime.fromisoformat(p.get("date_approved") or p["date_created"])
    return {"id": str(p["id"]), "aprobado": p.get("status") == "approved",
            "centavos": round(float(p.get("transaction_amount") or 0) * 100), "moneda": p.get("currency_id") or "",
            "fecha": fecha.astimezone(base.UTC), "telefono": numero or None, "email": payer.get("email"),
            "nombre": " ".join(x for x in (payer.get("first_name"), payer.get("last_name")) if x)}


def _celda(x):
    """El nombre y el correo los escribe el pagador: sin saltos de línea (filas falsas que importar-ventas tomaría
    como ventas) y sin fórmulas de Excel."""
    s = " ".join(str(x or "").split())
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"\+\d{8,15}", s) else s


def procesar(con, cfg, item):
    """Registra la venta si el pago está aprobado, es en MXN y el cliente existe; si no lo encuentra, lo deja en
    pagos-sin-contacto.csv para capturarlo con importar-ventas."""
    p = normalizar(item)
    if not p["aprobado"] or p["centavos"] <= 0:
        return None
    if p["moneda"] != "MXN":
        base.log("pago en otra moneda ignorado:", item["proveedor"], p["id"], p["moneda"])
        return None
    tel = base.normalizar_tel(p["telefono"]) if p["telefono"] else None
    email = (p["email"] or "").strip().lower() or None
    c = con.execute("SELECT id FROM contacto WHERE (telefono=? AND ? IS NOT NULL) OR (email=? AND ? IS NOT NULL) "
                    "ORDER BY telefono=? DESC LIMIT 1", (tel, tel, email, email, tel)).fetchone()
    fecha = p["fecha"].astimezone(cfg.tz).date().isoformat()
    monto = f"{p['centavos'] / 100:.2f}"
    if not c:
        nuevo = not (cfg.carpeta / "pagos-sin-contacto.csv").exists()
        with open(cfg.carpeta / "pagos-sin-contacto.csv", "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(["telefono", "fecha", "monto", "email", "nombre", "proveedor", "pago"])
            w.writerow([_celda(x) for x in (tel, fecha, monto, email, p["nombre"], item["proveedor"], p["id"])])
        base.log("pago sin contacto:", item["proveedor"], p["id"])
        return None
    vid, error = ventas.registrar_venta(con, cfg, c["id"], monto, fecha, f"pago:{item['proveedor']}:{p['id']}")
    if error and not error.startswith("esa venta ya"):
        raise RuntimeError(f"venta de {item['proveedor']} {p['id']} no registrada: {error}")
    return vid
