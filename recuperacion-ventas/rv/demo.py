"""demo-ventas: recorrido de ~3 minutos para enseñar el sistema a un dueño, con cualquier archivo de nicho.

Corre en modo prueba con la IA simulada y un reloj simulado: nada sale a Meta ni a Anthropic."""
import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import agenda, base, motor, tick, ventas, wa, web
from .__main__ import payload_falso

EJEMPLO = base.RAIZ / "ejemplo" / "cliente.json"
USUARIO, CLAVE = "demo", "demo-ventas-2026"


def cliente_de_nicho(nicho, fecha_inicio):
    datos = json.loads(EJEMPLO.read_text(encoding="utf-8"))
    datos.update({k: nicho[k] for k in ("giro", "horario", "servicios", "faq")})
    datos.update({"nombre": nicho.get("nombre_demo") or f"Negocio de {nicho['nicho']} (demo)", "modo_prueba": True,
                  "dias_cerrados": [], "fecha_inicio": fecha_inicio.isoformat(),
                  "palabras": {"urgencia_medica": nicho.get("palabras_urgencia") or []}})
    return datos


class Demo:
    def __init__(self, nicho, carpeta, pausa):
        self.nicho, self.pausa = nicho, pausa
        self.lunes = lunes = primer_lunes(dt.datetime.now(ZoneInfo(base.DEFAULTS["zona_horaria"])).date())
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "cliente.json").write_text(json.dumps(cliente_de_nicho(nicho, lunes), ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        self.cfg = base.cargar_config(carpeta)
        self.con = base.abrir_db(self.cfg)
        self.ultimo_aviso = None
        s = self.cfg.servicios
        d = nicho.get("demo", {})
        self.s_noche = s.get(d.get("servicio_noche")) or list(s.values())[0]
        self.s_dia = s.get(d.get("servicio_dia")) or list(s.values())[-1]

    # --- reloj y salida ---
    def a(self, dia, hora, minuto=0):
        """Mueve el reloj simulado al día (0 = lunes) y hora locales."""
        self.t = dt.datetime.combine(self.lunes + dt.timedelta(days=dia), dt.time(hora, minuto), self.cfg.tz)
        self.t = self.t.astimezone(base.UTC)

    def decir(self, texto, seg=2.5):
        print(texto, flush=True)
        time.sleep(seg * self.pausa)

    def escena(self, titulo):
        estado = "abierto" if base.abierto(self.cfg, self.t) else "CERRADO"
        self.decir(f"\n━━ {titulo} · {base.fecha_humana(self.cfg, self.t)} · negocio {estado} ━━", 2)

    def mostrar(self, m):
        hora = base.de_iso(m["creado"]).astimezone(self.cfg.tz).strftime("%H:%M")
        texto = m["texto"]   # las plantillas ya se guardan con su texto real (wa.texto_plantilla)
        quien = {"bot": "IA", "sistema": "Sistema"}.get(m["autor"], m["autor"].replace("humano:", "Equipo: "))
        para = " (aviso al equipo)" if m["telefono"] in self.cfg.internos else ""
        if para and texto == self.ultimo_aviso:   # el mismo aviso va al dueño y a cada persona del equipo
            return
        self.ultimo_aviso = texto if para else None
        self.decir(f"   {hora} {quien}{para} → " + texto.replace("\n", "\n        "), 3.5)

    def salientes_desde(self, ultimo):
        for m in self.con.execute("SELECT * FROM mensaje WHERE id>? AND direccion='out' ORDER BY id", (ultimo,)):
            self.mostrar(m)

    def ultimo(self):
        return self.con.execute("SELECT COALESCE(MAX(id),0) FROM mensaje").fetchone()[0]

    def cliente(self, tel, nombre, texto):
        hora = self.t.astimezone(self.cfg.tz).strftime("%H:%M")
        self.decir(f"   {hora} {nombre} escribe → {texto}", 2.5)
        ultimo = self.ultimo()
        cuerpo = json.dumps(payload_falso(tel, texto, nombre)).encode()
        codigo, _ = web.recibir_webhook(self.con, self.cfg, cuerpo, wa.firmar(wa.app_secret(self.cfg), cuerpo))
        assert codigo == 200
        motor.procesar_pendientes(self.con, self.cfg)
        self.salientes_desde(ultimo)
        self.t += dt.timedelta(minutes=1)

    def cita_de(self, tel):
        return self.con.execute("SELECT ci.* FROM cita ci JOIN contacto co ON co.id=ci.contacto_id WHERE co.telefono=? "
                                "ORDER BY ci.id DESC", (tel,)).fetchone()

    # --- guion ---
    def correr(self):
        mariana, daniel, sofia = "+528100000101", "+528100000102", "+528100000103"
        sn, sd = self.s_noche, self.s_dia
        print(f"Demo: {self.cfg['nombre']} ({self.nicho['nicho']}). Nada sale a WhatsApp: todo es simulado.")

        self.a(0, 23, 5)
        self.escena("1. Mensaje de noche")
        self.cliente(mariana, "Mariana", f"Hola, buenas noches. ¿Cuánto cuesta el servicio de {sn['nombre'].lower()}?")
        self.cliente(mariana, "Mariana", f"Me interesa. Quiero agendar una cita de {sn['nombre'].lower()}")
        self.cliente(mariana, "Mariana", "1")
        self.decir("   ✓ Respondió en segundos, a las 11 de la noche, y la cita quedó en la agenda.", 3)

        self.a(1, 12, 10)
        self.escena("2. Pide precio y ya no contesta")
        self.cliente(daniel, "Daniel", f"Hola, ¿qué precio tiene el servicio de {sd['nombre'].lower()}?")
        self.decir("   … Daniel ya no responde. Aquí la mayoría de los negocios lo pierde.", 3)

        self.a(3, 12, 30)
        self.escena("3. Seguimiento del día 2")
        ultimo = self.ultimo()
        tick.correr(self.con, self.cfg)
        self.salientes_desde(ultimo)
        self.t += dt.timedelta(minutes=10)
        self.cliente(daniel, "Daniel", f"Sí, gracias. Quiero agendar una cita de {sd['nombre'].lower()}")
        self.cliente(daniel, "Daniel", "1")
        self.decir("   ✓ Una venta que se iba a perder, recuperada por el seguimiento.", 3)

        self.a(3, 13, 0)
        self.escena("4. Pasa a una persona del equipo")
        self.cliente(sofia, "Sofía", "Hola, quiero hablar con una persona, tengo una duda de mi tratamiento")
        ultimo = self.ultimo()
        c = self.con.execute("SELECT * FROM contacto WHERE telefono=?", (sofia,)).fetchone()
        self.t += dt.timedelta(minutes=3)
        motor.responder(self.con, self.cfg, c, "Hola Sofía, soy Ana de recepción. Cuéntame, ¿en qué te ayudo?",
                        autor="humano:Ana")
        self.salientes_desde(ultimo)
        self.decir("   ✓ La IA no contesta lo delicado: avisa al equipo y la conversación sigue en la bandeja.", 3)

        citas = [self.cita_de(mariana), self.cita_de(daniel)]
        self.t = max(base.de_iso(x["inicio"]) for x in citas) + dt.timedelta(hours=2)
        self.escena("5. Asistieron y se registra la venta")
        for ci, tel, nombre, s in ((citas[0], mariana, "Mariana", sn), (citas[1], daniel, "Daniel", sd)):
            agenda.marcar_cita(self.con, self.cfg, "Ana", {"cita": [str(ci["id"])], "estado": ["asistio"]})
            fecha = base.de_iso(ci["inicio"]).astimezone(self.cfg.tz).date()
            ventas.registrar_venta(self.con, self.cfg, ci["contacto_id"], s["precio_mxn"], fecha, "humano:Ana", ci["id"])
            v = self.con.execute("SELECT origen FROM venta WHERE cita_id=?", (ci["id"],)).fetchone()
            self.decir(f"   {nombre}: asistió · venta ${s['precio_mxn']:,} · origen: {ventas.NOMBRES_ORIGEN[v['origen']]}", 3)

        self.escena("6. Reporte mensual con la garantía")
        mes = self.t.astimezone(self.cfg.tz).strftime("%Y-%m")
        texto = ventas.reporte(self.con, self.cfg, mes)
        bloque = texto.split("## Para revisar")[0]
        for linea in bloque.strip().splitlines():
            if linea.strip():
                self.decir("   " + linea, 0.8)

        web.crear_usuario(self.con, USUARIO, CLAVE)
        print(f"\nPara enseñar la bandeja: python3 -m rv --cliente {self.cfg.carpeta} serve")
        print(f"y abre http://127.0.0.1:{self.cfg['puerto']}/bandeja (usuario {USUARIO}, contraseña {CLAVE}).")
        return texto




def primer_lunes(hoy):
    """Primer lunes del mes actual: la demo cabe en una semana y no cruza de mes."""
    d = hoy.replace(day=1)
    return d + dt.timedelta(days=(7 - d.weekday()) % 7)


def correr(nicho, carpeta=None, pausa=1.0):
    carpeta = Path(carpeta or tempfile.mkdtemp(prefix="rv-demo-"))
    if (carpeta / "datos.db").exists():
        raise SystemExit(f"{carpeta} ya tiene una demo: usa otra carpeta (la demo siempre empieza de cero)")
    reloj_real, clave = base.ahora, os.environ.pop("ANTHROPIC_API_KEY", None)   # siempre la IA simulada
    demo = Demo(nicho, carpeta, pausa)
    base.ahora = lambda: demo.t
    try:
        return demo.correr()
    finally:
        base.ahora = reloj_real
        demo.con.close()
        if clave:
            os.environ["ANTHROPIC_API_KEY"] = clave
