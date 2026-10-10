"""Historia de un cliente de punta a punta con el sistema real (modo prueba, reloj fijo, sin cuentas).

Uso (desde recuperacion-ventas/): python3 ejemplo/historia.py
"""
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from rv import agenda, base, motor, tick, ventas, wa, web  # noqa: E402
from rv.__main__ import payload_falso  # noqa: E402

MTY = dt.timezone(dt.timedelta(hours=-6))
reloj = [None]
carpeta = Path(tempfile.mkdtemp())
shutil.copy(RAIZ / "ejemplo" / "cliente.json", carpeta / "cliente.json")


def en(*a):
    reloj[0] = dt.datetime(*a, tzinfo=MTY).astimezone(base.UTC)
    l = reloj[0].astimezone(MTY)
    print(f"\n──── {base.DIAS_ES[l.weekday()].capitalize()} {l.day} de {base.MESES_ES[l.month-1]}, {l:%H:%M} ────")


with mock.patch.object(base, "ahora", lambda: reloj[0]):
    en(2026, 10, 13, 22, 40)
    cfg = base.cargar_config(carpeta)
    con = base.abrir_db(cfg)
    TEL, NOMBRE = "+528112223344", "Mariana"
    visto = [0]

    def salidas():
        filas = con.execute("SELECT * FROM mensaje WHERE id>? AND direccion='out' ORDER BY id", (visto[0],)).fetchall()
        visto[0] = con.execute("SELECT MAX(id) FROM mensaje").fetchone()[0] or 0
        for m in filas:
            if m["telefono"] == TEL:
                quien = "🤖 Clínica" if m["autor"] in ("bot", "sistema") else f"👩 {m['autor'].split(':')[1].capitalize()} (equipo)"
                tipo = f"  [plantilla {m['plantilla']}]" if m["plantilla"] else ""
                print(f"   {quien}{tipo}:\n      " + m["texto"].replace("\n", "\n      "))
            else:
                import re as _re
                motivo = _re.search(r"Motivo: (.*?)\. Ábrela", m["texto"])
                print(f"   🔔 Aviso por WhatsApp al equipo ({m['telefono']}): {motivo.group(1) if motivo else m['texto']}")

    def cliente(texto):
        print(f"   📱 {NOMBRE}: {texto}")
        cuerpo = json.dumps(payload_falso(TEL, texto, NOMBRE)).encode()
        web.recibir_webhook(con, cfg, cuerpo, wa.firmar(wa.app_secret(cfg), cuerpo))
        motor.procesar_pendientes(con, cfg)
        salidas()

    def sistema(nota):
        print(f"   ⚙️  {nota}")

    # 1. Escribe de noche (nadie en la clínica)
    cliente("Hola buenas noches")
    cliente("¿Cuánto cuesta el blanqueamiento?")
    cliente("¿Tienen estacionamiento?")
    cliente("ok gracias, lo pienso")

    # 2. No vuelve a escribir: seguimiento a los 2 días de su último mensaje
    en(2026, 10, 15, 10, 0)
    sistema("tick automático: todavía no se cumplen 2 días desde su último mensaje (martes 22:40)")
    tick.correr(con, cfg)
    salidas()
    en(2026, 10, 16, 10, 0)
    sistema("tick automático: seguimiento día 2")
    tick.correr(con, cfg)
    salidas()

    # 3. Responde al seguimiento al día siguiente y agenda sola
    en(2026, 10, 17, 11, 0)
    cliente("Sí, quiero agendar el blanqueamiento")
    cliente("2")

    # 4. Recordatorios automáticos
    cita = con.execute("SELECT * FROM cita").fetchone()
    ini = base.de_iso(cita["inicio"])
    en(2026, 10, 17, 19, 30)
    sistema("tick automático: 24 h antes sería domingo (no se envía en domingo) → se adelanta al sábado 19:30")
    tick.correr(con, cfg)
    salidas()
    en(*(ini - dt.timedelta(hours=2)).astimezone(MTY).timetuple()[:5])
    sistema("tick automático: 2 h antes son las 7:00, antes de la ventana de envío → se omite (ya tuvo el de 24 h)")
    tick.correr(con, cfg)
    salidas()

    # 5. Pregunta delicada → pasa a una persona
    en(*(ini - dt.timedelta(minutes=50)).astimezone(MTY).timetuple()[:5])
    cliente("¿El blanqueamiento me va a causar sensibilidad? ¿qué tomo después?")
    c = con.execute("SELECT * FROM contacto WHERE telefono=?", (TEL,)).fetchone()
    en(*(ini).astimezone(MTY).timetuple()[:5])
    sistema("tick automático: abre la clínica → sale el aviso que quedó pendiente")
    tick.correr(con, cfg)
    salidas()
    sistema("Ana abre la bandeja, toma la conversación y responde")
    web.accion_conversacion(con, cfg, c, "ana", "tomar", {})
    web.accion_conversacion(con, cfg, motor.contacto(con, c["id"]), "ana", "responder",
                            {"texto": ["Hola Mariana, soy Ana. La doctora te explica todo en tu cita y te da indicaciones personalizadas. ¡Te esperamos!"]})
    salidas()
    web.accion_conversacion(con, cfg, motor.contacto(con, c["id"]), "ana", "devolver", {})

    # 6. Asiste, se registra la venta, se pide reseña
    en(*(ini + dt.timedelta(minutes=70)).astimezone(MTY).timetuple()[:5])
    sistema("Ana marca 'Asistió' y registra la venta de $3,500 en la bandeja")
    agenda.marcar_cita(con, cfg, "ana", {"cita": [str(cita["id"])], "estado": ["asistio"]})
    vid, _ = ventas.registrar_venta(con, cfg, c["id"], "3500", reloj[0].astimezone(MTY).date().isoformat(), "humano:ana", cita["id"])
    origen = con.execute("SELECT origen FROM venta WHERE id=?", (vid,)).fetchone()[0]
    sistema(f"origen asignado a la venta: {ventas.NOMBRES_ORIGEN[origen]} → cuenta como venta recuperada")
    en(*(ini + dt.timedelta(minutes=70 + 125)).astimezone(MTY).timetuple()[:5])
    sistema("tick automático: solicitud de reseña 2 h después")
    tick.correr(con, cfg)
    salidas()

    # 7. Reporte
    print("\n──── Reporte de fin de mes (extracto) ────")
    texto = ventas.reporte(con, cfg, "2026-10")
    print("\n".join(l for l in texto.splitlines() if l.startswith("| ") and ("." in l[:6]) or "EN CURSO" in l or "CUMPLE" in l))
    con.close()
shutil.rmtree(carpeta)
