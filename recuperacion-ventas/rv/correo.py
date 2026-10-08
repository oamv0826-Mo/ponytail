"""Correo: avisos al dueño/equipo, reporte mensual y canal de clientes (leer el buzón y contestar en el mismo hilo).
Por SMTP/IMAP (Gmail, Zoho, hosting) o por Microsoft Graph (Microsoft 365). En modo prueba no sale nada."""
import email
import email.policy
import html
import imaplib
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime

from . import base, ms


def configurado(cfg):
    return cfg["email"]["proveedor"] in ("smtp", "microsoft") and bool(cfg["email"]["remitente"])


def mensaje(cfg, para, asunto, texto, responde_a=None):
    m = EmailMessage()
    m["From"] = f"{cfg['nombre']} <{cfg['email']['remitente']}>"
    m["To"] = ", ".join(para)
    m["Subject"] = " ".join(asunto.split())   # sin saltos de línea: un CR/LF en una cabecera es inválido (o inyección)
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=cfg["email"]["remitente"].rsplit("@", 1)[-1])
    if responde_a:   # para que el cliente vea la respuesta en el mismo hilo
        m["In-Reply-To"] = m["References"] = responde_a
    m.set_content(texto)
    return m


def usuario_smtp(cfg):
    return os.environ.get("SMTP_USUARIO") or cfg["email"]["remitente"]


def conectar_smtp(cfg):
    """Conexión cifrada y con sesión iniciada (Gmail pide contraseña de aplicación, no la normal)."""
    s = cfg["email"]["smtp"]
    contexto = ssl.create_default_context()
    con = (smtplib.SMTP_SSL(s["host"], s["puerto"], context=contexto, timeout=20) if s["seguridad"] == "ssl"
           else smtplib.SMTP(s["host"], s["puerto"], timeout=20))
    try:
        if s["seguridad"] == "starttls":
            con.starttls(context=contexto)   # nunca en claro: sin STARTTLS falla en vez de mandar la clave
        con.login(usuario_smtp(cfg), os.environ.get("SMTP_CLAVE", ""))
    except BaseException:
        con.close()
        raise
    return con


def _smtp(cfg, m):
    with conectar_smtp(cfg) as con:
        con.send_message(m)


def _microsoft(cfg, m):
    cuerpo = {"subject": m["Subject"], "body": {"contentType": "Text", "content": m.get_content()},
              "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in m["To"].split(",")]}
    ms.graph("POST", ms.buzon(cfg["email"]["remitente"]) + "/sendMail", {"message": cuerpo})


def enviar(cfg, para, asunto, texto, responde_a=None):
    """Devuelve (message_id, error). Nunca lanza: un aviso por correo que falla no detiene nada."""
    para = [p.strip() for p in para if p and p.strip()]
    if not para or not configurado(cfg):
        return None, "correo no configurado"
    try:
        m = mensaje(cfg, para, asunto, texto, responde_a)
        if cfg["modo_prueba"]:
            with open(cfg.carpeta / "envios-prueba.log", "a", encoding="utf-8") as f:
                f.write(f"{base.iso(base.ahora())}\tcorreo:{','.join(para)}\tsistema\t{m['Subject']} | "
                        f"{' '.join(texto.split())}\n")
        else:
            (_microsoft if cfg["email"]["proveedor"] == "microsoft" else _smtp)(cfg, m)
        return m["Message-ID"], None
    except Exception as e:  # cabecera inválida, red, SMTP, Graph: se registra y sigue
        base.log("correo fallido", ",".join(para), asunto, repr(e))
        return None, str(e)


# ---------- canal de clientes: correo entrante ----------

MAX_TEXTO = 4000
NO_RESPONDER = re.compile(r"(no-?reply|noreply|mailer-daemon|postmaster|bounce|notificaciones?|notifications?)@", re.I)
CITA = re.compile(r"^(?:El .{0,200}escribi[óo]:|On .{0,200}wrote:|-{2,}\s*(?:Original Message|Mensaje original)\s*-{2,}"
                  r"|De: .+|From: .+)\s*$", re.M | re.I)


def texto_util(cuerpo, es_html=False):
    """Lo que escribió el cliente: sin HTML, sin el historial citado ni la firma de '--'."""
    if es_html:
        cuerpo = html.unescape(re.sub(r"<(script|style)\b.*?</\1>|<[^>]+>", " ", cuerpo, flags=re.S | re.I))
    m = CITA.search(cuerpo)
    cuerpo = cuerpo[:m.start()] if m else cuerpo
    lineas = [x for x in cuerpo.splitlines() if not x.lstrip().startswith(">")]
    if "-- " in lineas:
        lineas = lineas[:lineas.index("-- ")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(x.rstrip() for x in lineas)).strip()[:MAX_TEXTO]


def ignorar(cfg, de, cabeceras):
    """Motivo para no contestar (robots, listas, el propio buzón, el equipo) o None. Evita bucles con autorespuestas."""
    em = cfg["email"]
    c = {k.lower(): str(v).lower() for k, v in cabeceras.items()}
    if not de or "@" not in de:
        return "sin remitente"
    if de == em["remitente"].lower() or de in {x.lower() for x in em["avisos_a"] + em["reporte_a"]}:
        return "propio o del equipo"
    if NO_RESPONDER.search(de):
        return "remitente automático"
    if c.get("auto-submitted", "no") != "no" or "x-autoreply" in c or "x-autorespond" in c:
        return "respuesta automática"
    if c.get("precedence") in ("bulk", "list", "junk") or "list-id" in c or "list-unsubscribe" in c:
        return "lista o boletín"
    return None


def _item(cfg, de, nombre, asunto, msg_id, texto, fecha, graph_id=None, cabeceras=None):
    de = (de or "").strip().lower()
    motivo = ignorar(cfg, de, cabeceras or {})
    return {"tipo": "correo", "de": de, "nombre": nombre or "", "asunto": " ".join((asunto or "").split())[:200],
            "id": msg_id or f"<sin-id-{graph_id or fecha}>", "texto": texto, "fecha": fecha, "graph_id": graph_id,
            "ignorar": motivo}


def de_mime(cfg, crudo):
    """Bytes de un correo (IMAP) → item para la cola."""
    m = email.message_from_bytes(crudo, policy=email.policy.default)
    nombre, de = parseaddr(str(m.get("From", "")))
    parte = m.get_body(preferencelist=("plain", "html"))
    texto = texto_util(parte.get_content(), parte.get_content_type() == "text/html") if parte else ""
    try:
        fecha = parsedate_to_datetime(m["Date"]).astimezone(base.UTC)
    except (TypeError, ValueError):
        fecha = base.ahora()
    return _item(cfg, de, nombre, m.get("Subject", ""), (m.get("Message-ID") or "").strip(), texto, base.iso(fecha),
                 cabeceras=dict(m.items()))


def de_graph(cfg, g):
    """Mensaje de Microsoft Graph (cuerpo pedido como texto) → item para la cola."""
    remitente = (g.get("from") or {}).get("emailAddress", {})
    cab = {h["name"]: h["value"] for h in g.get("internetMessageHeaders") or []}
    fecha = base.de_iso((g.get("receivedDateTime") or "")[:19] + "Z") or base.ahora()
    return _item(cfg, remitente.get("address"), remitente.get("name"), g.get("subject"), g.get("internetMessageId"),
                 texto_util(g.get("body", {}).get("content", ""), g.get("body", {}).get("contentType") == "html"),
                 base.iso(fecha), graph_id=g["id"], cabeceras=cab)


def leer_entrada(cfg, guardar):
    """Lee los correos no leídos del buzón; llama guardar(item) y solo después lo marca como leído
    (si el proceso cae a la mitad, el correo se vuelve a leer y la cola lo descarta por su Message-ID)."""
    em = cfg["email"]
    n = int(em["entrada"]["por_tick"])
    if em["proveedor"] == "microsoft":
        r = ms.graph("GET", ms.buzon(em["remitente"]) + "/mailFolders/inbox/messages?$filter=isRead%20eq%20false"
                     f"&$top={n}&$select=id,internetMessageId,subject,from,body,receivedDateTime,internetMessageHeaders",
                     cabeceras={"Prefer": 'outlook.body-content-type="text"'})
        for g in r.get("value", []):
            guardar(de_graph(cfg, g))
            ms.graph("PATCH", ms.buzon(em["remitente"]) + f"/messages/{g['id']}", {"isRead": True})
        return len(r.get("value", []))
    i = em["entrada"]["imap"]
    con = imaplib.IMAP4_SSL(i["host"], int(i["puerto"]), ssl_context=ssl.create_default_context(), timeout=30)
    try:
        con.login(usuario_smtp(cfg), os.environ.get("SMTP_CLAVE", ""))
        con.select("INBOX")
        uids = con.uid("SEARCH", None, "UNSEEN")[1][0].split()[:n]
        for uid in uids:
            datos = con.uid("FETCH", uid, "(BODY.PEEK[])")[1]   # PEEK: no lo marca leído todavía
            guardar(de_mime(cfg, next(x[1] for x in datos if isinstance(x, tuple))))
            con.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return len(uids)
    finally:
        try:
            con.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def responder(cfg, c, texto):
    """Respuesta a un cliente de correo en el mismo hilo. Devuelve (message_id, error)."""
    hilo = json.loads(c["email_hilo"] or "{}")
    asunto = hilo.get("asunto") or f"Tu mensaje a {cfg['nombre']}"
    asunto = asunto if asunto.lower().startswith(("re:", "rv:")) else f"Re: {asunto}"
    if cfg["email"]["proveedor"] == "microsoft" and hilo.get("graph_id") and not cfg["modo_prueba"]:
        try:   # reply de Graph conserva el hilo (Graph no deja fijar In-Reply-To en sendMail)
            ms.graph("POST", ms.buzon(cfg["email"]["remitente"]) + f"/messages/{hilo['graph_id']}/reply",
                     {"comment": texto})
            return None, None   # 202 sin cuerpo: Graph no devuelve el id del mensaje enviado
        except ms.MSError as e:
            base.log("correo fallido", c["email"], repr(e))
            return None, str(e)
    return enviar(cfg, [c["email"]], asunto, texto, responde_a=hilo.get("id"))
