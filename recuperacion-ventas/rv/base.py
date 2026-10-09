"""Config, tiempo/horarios, teléfonos, texto y base de datos. Lo que todos los módulos comparten."""
import contextlib
import copy
import datetime as dt
import fcntl
import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
ESQUEMA = RAIZ / "docs" / "esquema.sql"
UTC = dt.timezone.utc

DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
            "septiembre", "octubre", "noviembre", "diciembre"]

MENSAJES = {
    "emergencia": "Si es una emergencia médica, llama al 911 o acude de inmediato a urgencias del hospital más cercano.",
    "handoff_abierto": "Gracias por escribir. En unos minutos te atiende una persona del equipo.",
    "handoff_cerrado": "Gracias por escribir. Te contacta una persona del equipo {apertura}.",
    "no_texto": "Por ahora solo puedo leer mensajes de texto.",
    "baja": "Listo, ya no te enviaremos mensajes. Si nos escribes, con gusto te atendemos.",
    "propuesta": "Para {servicio} tengo estos horarios:\n{opciones}\nResponde con el número que prefieras.",
    "sin_horarios": "Por ahora no tengo horarios disponibles en línea.",
    "horario_ocupado": "Ese horario ya no está disponible.",
    "cita_confirmada": "Listo, tu cita de {servicio} quedó para el {fecha}. Te enviaremos un recordatorio.",
    "confirmar_cancelacion": "¿Confirmas que cancelamos tu cita de {servicio} del {fecha}? Responde SÍ para cancelar.",
    "cita_cancelada": "Tu cita del {fecha} quedó cancelada. Si quieres otro horario, dime y te propongo opciones.",
}

DEFAULTS = {
    "modo_prueba": True,
    "zona_horaria": "America/Monterrey",
    "url_publica": "http://127.0.0.1:8080",
    "puerto": 8080,
    "equipo": [],
    "dias_cerrados": [],
    "faq": [],
    "palabras": {
        "urgencia_medica": ["urgencia", "emergencia", "dolor fuerte", "mucho dolor", "sangrado", "sangra",
                            "desmayo", "no puedo respirar", "inflamacion fuerte", "fiebre alta"],
        "handoff": ["hablar con una persona", "hablar con alguien", "persona real", "asesor", "humano",
                    "queja", "reclamo", "abogado", "demanda", "factura"],
        "baja": ["baja", "no gracias", "ya no", "stop", "detener promociones", "stop promotions"],
        # frases inequívocas que piden la baja dentro de un mensaje más largo
        "baja_frases": ["darme de baja", "dame de baja", "denme de baja", "dar de baja", "no me manden", "no me envien",
                        "no me escriban", "dejen de enviar", "deja de enviar", "dejen de mandar", "deja de mandar",
                        "no quiero recibir", "no quiero mas mensajes"],
    },
    "ia": {"modelo": "claude-haiku-4-5", "tope_mensual_usd": 30, "max_contexto": 20, "timeout_s": 20},
    "agenda": {"proveedor": "local", "calendar_id": "", "dias_adelante": 14, "anticipacion_min_horas": 2,
               "paso_min": 30, "margen_min": 10, "cambio_min_horas": 2, "separacion_propuestas_horas": 3},
    "envios": {"ventana": ["09:00", "20:00"]},
    "escalamiento_min": 15,
    "seguimiento_dias": [2, 5, 10],
    "reactivacion": {"lote_base": 50, "lote_max": 100, "dias_verde_para_subir": 7, "sin_contacto_dias": 30},
    "resenas": {"link": "", "horas_despues": 2, "cada_dias": 90},
    "plantillas_idioma": "es_MX",
    "whatsapp": {"phone_number_id": "", "waba_id": "", "graph_version": "v23.0"},
    # correo: "" (sin correo) | "smtp" | "microsoft"; avisos_a recibe los mismos avisos que el WhatsApp del equipo
    "email": {"proveedor": "", "remitente": "", "avisos_a": [], "reporte_a": [],
              "smtp": {"host": "", "puerto": 587, "seguridad": "starttls"},
              # canal de clientes: leer el buzón del remitente y contestar (IMAP con smtp; Graph con microsoft)
              "entrada": {"activa": False, "imap": {"host": "", "puerto": 993}, "por_tick": 25,
                          "servidor_autenticacion": ""}},   # id del Authentication-Results de tu servidor (Gmail: solo)
    # pagos en línea que registran la venta solos (webhooks en url_publica/pagos/<proveedor>)
    "pagos": {"stripe": {"activo": False}, "mercadopago": {"activo": False}},
    "cotizaciones": {"vigencia_dias": 15},
    "mensajes": {},
}


# ---------- config ----------

def _fusionar(base, encima):
    out = dict(base)
    for k, v in encima.items():
        out[k] = _fusionar(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def cargar_secretos(carpeta):
    """KEY=VALOR por línea desde secretos.env; no pisa variables ya definidas (systemd las pone)."""
    p = Path(carpeta) / "secretos.env"
    if p.exists():
        for linea in p.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Config(dict):
    """cliente.json fusionado sobre DEFAULTS, con atajos de uso común."""

    def __init__(self, datos, carpeta="."):
        super().__init__(_fusionar(copy.deepcopy(DEFAULTS), datos))   # copia: nadie modifica los valores por omisión
        self.carpeta = Path(carpeta)
        self.tz = ZoneInfo(self["zona_horaria"])
        self.msg = {**MENSAJES, **self["mensajes"]}
        self.servicios = {s["id"]: s for s in self.get("servicios", [])}
        internos = [self.get("dueno", {}).get("telefono", "")] + [p.get("telefono", "") for p in self["equipo"]]
        self.internos = {normalizar_tel(t) for t in internos if t}
        self.internos.discard(None)
        self.palabras = {k: [normalizar_texto(p) for p in v] for k, v in self["palabras"].items()}
        self.validar()

    def nombre_servicio(self, sid, defecto=None):
        return self.servicios.get(sid, {}).get("nombre", sid if defecto is None else defecto)

    def validar(self):
        errores = []
        for campo in ("nombre", "horario", "servicios", "fecha_inicio", "mensualidad_mxn"):
            if not self.get(campo):
                errores.append(f"falta '{campo}'")
        for dia, rangos in self.get("horario", {}).items():
            if dia not in DIAS:
                errores.append(f"día de horario inválido: {dia}")
            for r in rangos:
                if len(r) != 2 or not all(re.fullmatch(r"\d\d:\d\d", h) for h in r) or r[0] >= r[1]:
                    errores.append(f"rango inválido en {dia}: {r}")
        for s in self.get("servicios", []):
            precio = s.get("precio_mxn")
            if not s.get("id") or not s.get("nombre") or not isinstance(s.get("duracion_min"), int) \
                    or s["duracion_min"] <= 0 or isinstance(precio, bool) or not isinstance(precio, (int, float)) or precio < 0:
                errores.append(f"servicio incompleto (id, nombre, precio_mxn numérico ≥ 0, duracion_min entero > 0): {s}")
        if self["agenda"]["proveedor"] not in ("local", "google", "microsoft"):
            errores.append(f"agenda.proveedor inválido: {self['agenda']['proveedor']!r} (local, google o microsoft)")
        elif self["agenda"]["proveedor"] == "microsoft" and "@" not in self["agenda"]["calendar_id"]:
            errores.append("con agenda microsoft, calendar_id es el correo del buzón o sala (p. ej. citas@negocio.mx)")
        em = self["email"]
        if em["proveedor"] not in ("", "smtp", "microsoft"):
            errores.append(f"email.proveedor inválido: {em['proveedor']!r} (vacío, smtp o microsoft)")
        elif em["proveedor"] and "@" not in em["remitente"]:
            errores.append("email.remitente debe ser un correo")
        elif em["proveedor"] == "smtp" and (not em["smtp"]["host"] or em["smtp"]["seguridad"] not in ("starttls", "ssl")):
            errores.append("email.smtp necesita host y seguridad starttls o ssl")
        elif em["entrada"]["activa"] and em["proveedor"] == "smtp" and not em["entrada"]["imap"]["host"]:
            errores.append("email.entrada activa con smtp necesita entrada.imap.host (p. ej. imap.gmail.com)")
        if errores:
            raise ValueError("cliente.json inválido: " + "; ".join(errores))

    @property
    def base_path(self):
        from urllib.parse import urlparse
        return urlparse(self["url_publica"]).path.rstrip("/")


def cargar_config(carpeta):
    carpeta = Path(carpeta)
    cargar_secretos(carpeta)
    with open(carpeta / "cliente.json", encoding="utf-8") as f:
        return Config(json.load(f), carpeta)


# ---------- tiempo ----------

def ahora():
    """Punto único de reloj (las pruebas lo sustituyen)."""
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def de_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if s else None


def _hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def intervalos_dia(cfg, fecha):
    """Rangos de apertura (datetimes locales) de una fecha local; [] si está cerrado."""
    if fecha.isoformat() in cfg["dias_cerrados"]:
        return []
    return [(dt.datetime.combine(fecha, _hm(a), cfg.tz), dt.datetime.combine(fecha, _hm(b), cfg.tz))
            for a, b in cfg["horario"].get(DIAS[fecha.weekday()], [])]


def inicio_apertura_actual(cfg, t):
    local = t.astimezone(cfg.tz)
    for a, b in intervalos_dia(cfg, local.date()):
        if a <= local < b:
            return a.astimezone(UTC)
    return None


def abierto(cfg, t):
    return inicio_apertura_actual(cfg, t) is not None


def proxima_apertura(cfg, t):
    """Primer inicio de horario estrictamente posterior a t (busca hasta 60 días)."""
    local = t.astimezone(cfg.tz)
    for d in range(61):
        for a, _ in intervalos_dia(cfg, local.date() + dt.timedelta(days=d)):
            if a > local:
                return a.astimezone(UTC)
    return None


def en_ventana_envio(cfg, t):
    """Proactivos: 9:00-20:00 locales, nunca domingo ni día cerrado del config."""
    local = t.astimezone(cfg.tz)
    if local.weekday() == 6 or not intervalos_dia(cfg, local.date()):
        return False
    a, b = (_hm(x) for x in cfg["envios"]["ventana"])
    return a <= local.time() < b


def ultimo_momento_envio_antes(cfg, t, holgura=dt.timedelta(minutes=30)):
    """t si cae en la ventana de envío (sin contar sus últimos 'holgura' minutos); si no, el último momento
    permitido anterior, que es el cierre de la ventana menos 'holgura' (el tick corre cada 5 min y necesita
    margen para alcanzarlo). None si no hay ninguno en 30 días."""
    local = t.astimezone(cfg.tz)
    a, b = (_hm(x) for x in cfg["envios"]["ventana"])
    for d in range(31):
        fecha = local.date() - dt.timedelta(days=d)
        fin = dt.datetime.combine(fecha, b, cfg.tz) - holgura
        candidato = min(local, fin) if d == 0 else fin
        if en_ventana_envio(cfg, candidato) and candidato.time() >= a:
            return candidato.astimezone(UTC)
    return None


def fecha_humana(cfg, t, con_hora=True):
    """'martes 7 de octubre a las 9:00' en hora local."""
    l = t.astimezone(cfg.tz)
    s = f"{DIAS_ES[l.weekday()]} {l.day} de {MESES_ES[l.month - 1]}"
    return s + (f" a las {l.hour}:{l.minute:02d}" if con_hora else "")


def apertura_humana(cfg, t):
    p = proxima_apertura(cfg, t)
    if p is None:
        return "en cuanto el negocio abra"
    l, hoy = p.astimezone(cfg.tz), t.astimezone(cfg.tz).date()
    if l.date() == hoy:
        return f"hoy a las {l.hour}:{l.minute:02d}"
    return "el " + fecha_humana(cfg, p)


# ---------- teléfonos y texto ----------

def normalizar_tel(tel, pais="52"):
    """E.164. México: 10 dígitos → +52; 521+10 (formato antiguo de móvil) → +52+10. None si no es válido."""
    if tel is None:
        return None
    s = str(tel).strip()
    digitos = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digitos = digitos[2:]
    if len(digitos) == 10 and not s.startswith("+"):
        if digitos.startswith("0"):   # ningún número nacional de México empieza con 0
            return None
        digitos = pais + digitos
    if len(digitos) == 13 and digitos[:3] in ("044", "045"):   # prefijo antiguo de celular: 044/045 + 10 dígitos
        digitos = pais + digitos[3:]
    elif len(digitos) == 12 and digitos.startswith("01"):       # prefijo antiguo de larga distancia: 01 + 10
        digitos = pais + digitos[2:]
    if digitos.startswith("521") and len(digitos) == 13:
        digitos = "52" + digitos[3:]
    if digitos.startswith("0"):
        return None
    if digitos.startswith("52") and len(digitos) != 12:
        return None
    return "+" + digitos if 8 <= len(digitos) <= 15 else None


def normalizar_texto(s):
    """minúsculas, sin acentos, sin puntuación, espacios simples."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def contiene_frase(texto_norm, frases):
    t = f" {texto_norm} "
    return next((f for f in frases if f and f" {f} " in t), None)


# ---------- base de datos ----------

def conectar(ruta):
    con = sqlite3.connect(ruta, timeout=10, isolation_level=None)  # autocommit; transacciones explícitas
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def secciones_esquema():
    partes = re.split(r"^-- version: (\d+)\s*$", ESQUEMA.read_text(encoding="utf-8"), flags=re.M)
    return [(int(partes[i]), partes[i + 1]) for i in range(1, len(partes), 2)]


def migrar(con, ruta_db=None):
    """Aplica las secciones pendientes. Con ruta_db, bajo un candado de archivo: serve y tick arrancan juntos tras
    una actualización y sin candado el segundo intentaría aplicar la misma migración ("duplicate column")."""
    if ruta_db is None:
        return _migrar(con, None)
    with open(f"{ruta_db}.migrar.lock", "w") as candado:
        fcntl.flock(candado, fcntl.LOCK_EX)
        return _migrar(con, ruta_db)


def _migrar(con, ruta_db):
    actual = con.execute("PRAGMA user_version").fetchone()[0]   # se lee ya con el candado tomado
    pendientes = [(v, sql) for v, sql in secciones_esquema() if v > actual]
    if pendientes and actual > 0 and ruta_db:
        respaldar(con, Path(ruta_db).parent / "respaldos")
    for v, sql in pendientes:
        try:
            con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={v};\nCOMMIT;")
        except sqlite3.Error:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise


@contextlib.contextmanager
def transaccion(con):
    """Una sola transacción (un solo fsync) para importaciones de miles de filas; si algo falla no queda a medias."""
    con.execute("BEGIN")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def db(cfg):
    return contextlib.closing(conectar(cfg.carpeta / "datos.db"))


def abrir_db(cfg):
    ruta = cfg.carpeta / "datos.db"
    con = conectar(ruta)
    migrar(con, ruta)
    return con


def respaldar(con, destino):
    """Copia consistente (API de backup de SQLite) comprimida con gzip. Devuelve la ruta."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="tmp-respaldo-", suffix=".db", dir=destino)  # único: respaldos simultáneos
    os.close(fd)
    try:
        copia = sqlite3.connect(tmp)
        con.backup(copia)
        copia.close()
        salida = destino / f"datos-{ahora().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.db.gz"
        with open(tmp, "rb") as f, gzip.open(salida, "wb") as g:
            shutil.copyfileobj(f, g)
    finally:
        os.unlink(tmp)
    return salida


def evento(con, contacto_id, tipo, detalle="", creado=None):
    con.execute("INSERT INTO evento (contacto_id, tipo, creado, detalle) VALUES (?,?,?,?)",
                (contacto_id, tipo, iso(creado or ahora()), detalle))


# Las reacciones (👍) se guardan como mensaje entrante pero no son consultas ni piden respuesta.
NO_REACCION = "texto NOT LIKE '[reacción%'"


def dio_baja(con, telefono):
    return con.execute("SELECT 1 FROM optout WHERE telefono=?", (telefono,)).fetchone() is not None


def es_correo(c):
    """Contacto que escribe por correo: su 'telefono' es la dirección (ver esquema v7)."""
    return "@" in (c["telefono"] or "")


def ventana_abierta(c):
    """Ventana de 24 h de Meta para texto libre: abierta si el contacto escribió hace menos de 24 h.
    Por correo no hay ventana: siempre se le puede contestar."""
    if es_correo(c):
        return True
    u = de_iso(c["ultimo_entrante"])
    return bool(u) and ahora() - u < dt.timedelta(hours=24)


def get_estado(con, clave, defecto=None):
    r = con.execute("SELECT valor FROM estado WHERE clave=?", (clave,)).fetchone()
    return r[0] if r else defecto


def set_estado(con, clave, valor):
    con.execute("INSERT INTO estado (clave, valor) VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (clave, str(valor)))


def log(*partes):
    print(iso(ahora()), *partes, file=sys.stderr, flush=True)
