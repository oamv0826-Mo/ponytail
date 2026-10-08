"""Correo saliente: avisos al dueño/equipo y reporte mensual. Por SMTP (Gmail, Outlook.com, Zoho, hosting) o por
Microsoft Graph (Microsoft 365). En modo prueba solo se registra en envios-prueba.log."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import base, ms


def configurado(cfg):
    return cfg["email"]["proveedor"] in ("smtp", "microsoft") and bool(cfg["email"]["remitente"])


def mensaje(cfg, para, asunto, texto, responde_a=None):
    m = EmailMessage()
    m["From"] = f"{cfg['nombre']} <{cfg['email']['remitente']}>"
    m["To"] = ", ".join(para)
    m["Subject"] = asunto
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
    m = mensaje(cfg, para, asunto, texto, responde_a)
    if cfg["modo_prueba"]:
        with open(cfg.carpeta / "envios-prueba.log", "a", encoding="utf-8") as f:
            f.write(f"{base.iso(base.ahora())}\tcorreo:{','.join(para)}\tsistema\t{asunto} | {' '.join(texto.split())}\n")
        return m["Message-ID"], None
    try:
        (_microsoft if cfg["email"]["proveedor"] == "microsoft" else _smtp)(cfg, m)
        return m["Message-ID"], None
    except (OSError, smtplib.SMTPException, ms.MSError) as e:
        base.log("correo fallido", ",".join(para), asunto, repr(e))
        return None, str(e)
