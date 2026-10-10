"""Microsoft Graph con credenciales de aplicación (client credentials): correo y calendario de Microsoft 365.

Una app registrada en Microsoft Entra del negocio, con permisos de aplicación y consentimiento de administrador.
Secretos: MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET (el secreto vence: máximo 24 meses)."""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0/"
SECRETOS = ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET")
_token = {"valor": None, "expira": 0}
_lock = threading.Lock()


class MSError(RuntimeError):
    pass


def _pedir(metodo, url, cuerpo=None, cabeceras=None, form=False):
    datos = None
    if cuerpo is not None:
        datos = urllib.parse.urlencode(cuerpo).encode() if form else json.dumps(cuerpo).encode()
    req = urllib.request.Request(url, data=datos, method=metodo, headers={
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(cabeceras or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            contenido = r.read()   # sendMail responde 202 sin cuerpo
            return json.loads(contenido) if contenido else {}
    except urllib.error.HTTPError as e:
        if metodo == "DELETE" and e.code == 404:
            return {}
        raise MSError(f"Microsoft {e.code}: {e.read()[:300].decode(errors='replace')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MSError(f"red: {e}") from None


def token():
    with _lock:
        if _token["valor"] and _token["expira"] > time.time() + 60:
            return _token["valor"]
        faltan = [k for k in SECRETOS if not os.environ.get(k)]
        if faltan:
            raise MSError("faltan " + ", ".join(faltan))
        r = _pedir("POST", f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}/oauth2/v2.0/token", {
            "client_id": os.environ["MS_CLIENT_ID"], "client_secret": os.environ["MS_CLIENT_SECRET"],
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}, form=True)
        _token.update(valor=r["access_token"], expira=time.time() + int(r.get("expires_in", 3600)))
        return _token["valor"]


def graph(metodo, ruta, cuerpo=None, cabeceras=None):
    return _pedir(metodo, GRAPH + ruta, cuerpo, {"Authorization": f"Bearer {token()}", **(cabeceras or {})})


def buzon(direccion):
    return "users/" + urllib.parse.quote(direccion, safe="@")


def permisos():
    """Permisos de aplicación concedidos (claim 'roles' del token): un token puede salir bien sin el consentimiento."""
    carga = token().split(".")[1]
    return set(json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4))).get("roles", []))
