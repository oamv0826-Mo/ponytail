"""Línea de comandos: python3 -m rv --cliente DIR <comando> ..."""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.request

from . import base, motor, tick, ventas, wa, web


def payload_falso(telefono, texto=None, nombre="", tipo="text", msg_id=None):
    """Payload con la forma exacta del webhook de Meta (para simular sin cuenta)."""
    wa_id = telefono.lstrip("+")
    m = {"from": wa_id, "id": msg_id or f"wamid.SIM{time.time_ns()}", "timestamp": str(int(base.ahora().timestamp())),
         "type": tipo}
    if tipo == "text":
        m["text"] = {"body": texto or ""}
    else:
        m[tipo] = {"id": "media-simulada"}
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA-SIM", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "", "phone_number_id": ""},
        "contacts": [{"profile": {"name": nombre}, "wa_id": wa_id}], "messages": [m]}}]}]}


def simular_uno(cfg, args, texto, tipo="text"):
    cuerpo = json.dumps(payload_falso(args.de, texto, args.nombre, tipo)).encode()
    firma = wa.firmar(wa.app_secret(cfg), cuerpo)
    if args.url:
        req = urllib.request.Request(args.url.rstrip("/") + "/webhook", data=cuerpo, method="POST",
                                     headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma})
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"servidor respondió {r.status}; el hilo trabajador lo procesa en segundos")
        return
    with base.db(cfg) as con:
        ultimo = con.execute("SELECT COALESCE(MAX(id),0) FROM mensaje").fetchone()[0]
        codigo, _ = web.recibir_webhook(con, cfg, cuerpo, firma)
        if codigo != 200:
            sys.exit(f"webhook respondió {codigo}")
        motor.procesar_pendientes(con, cfg)
        for m in con.execute("SELECT * FROM mensaje WHERE id>? AND direccion='out' ORDER BY id", (ultimo,)):
            print(f"  → {m['telefono']} [{m['autor']}] {m['texto']}")


def cmd_simular(cfg, args):
    if not cfg["modo_prueba"] and not args.url:
        sys.exit("simular solo funciona con modo_prueba=true (para no enviar mensajes reales)")
    if args.interactivo:
        print(f"Escribe como el cliente {args.de}. Línea vacía o Ctrl+D para salir. '/audio' simula un audio.")
        for linea in sys.stdin:
            linea = linea.rstrip("\n")
            if not linea:
                break
            simular_uno(cfg, args, None if linea == "/audio" else linea, "audio" if linea == "/audio" else "text")
        return
    simular_uno(cfg, args, args.texto, args.tipo)


def cmd_usuario(cfg, args):
    clave = sys.stdin.readline().rstrip("\n") if args.clave_stdin else getpass.getpass("Contraseña (mín. 10): ")
    with base.db(cfg) as con:
        web.crear_usuario(con, args.nombre, clave)
    print(f"usuario '{args.nombre}' listo")


def cmd_tick(cfg, args):
    with base.db(cfg) as con:
        resumen = tick.correr(con, cfg)
    print(" ".join(f"{k}={v}" for k, v in resumen.items()))
    if "error" in resumen.values():
        sys.exit(1)


def cmd_importar_clientes(cfg, args):
    with base.db(cfg) as con:
        r = ventas.importar_clientes(con, cfg, args.csv)
    print(f"nuevos={r['nuevos']} actualizados={r['actualizados']} duplicados={r['duplicados']} "
          f"equipo_excluidos={r['equipo']} rechazados={len(r['rechazados'])}")
    for linea, motivo in r["rechazados"]:
        print(f"  línea {linea}: {motivo}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="rv", description="Sistema de Recuperación de Ventas")
    p.add_argument("--cliente", default=os.environ.get("RV_CLIENTE", "."), help="carpeta del cliente")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="webhook + bandeja web")
    s = sub.add_parser("simular", help="inyecta un mensaje falso de WhatsApp (modo prueba)")
    s.add_argument("--de", default="+528100000001")
    s.add_argument("--nombre", default="Cliente de prueba")
    s.add_argument("--texto", default="Hola")
    s.add_argument("--tipo", default="text", choices=["text", "audio", "image"])
    s.add_argument("--interactivo", action="store_true")
    s.add_argument("--url", help="enviar al servidor en marcha (p. ej. http://127.0.0.1:8080)")
    sub.add_parser("tick", help="envíos programados (correr cada 5 min)")
    i = sub.add_parser("importar-clientes", help="CSV: nombre,telefono,ultima_visita,consentimiento")
    i.add_argument("csv")
    u = sub.add_parser("usuario", help="crea o cambia la contraseña de un usuario de la bandeja")
    u.add_argument("nombre")
    u.add_argument("--clave-stdin", action="store_true")
    args = p.parse_args(argv)

    cfg = base.cargar_config(args.cliente)
    base.abrir_db(cfg).close()  # migraciones
    {"serve": lambda: web.servir(cfg), "simular": lambda: cmd_simular(cfg, args),
     "usuario": lambda: cmd_usuario(cfg, args), "tick": lambda: cmd_tick(cfg, args),
     "importar-clientes": lambda: cmd_importar_clientes(cfg, args)}[args.cmd]()


if __name__ == "__main__":
    main()
