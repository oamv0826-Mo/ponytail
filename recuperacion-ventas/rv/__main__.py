"""Línea de comandos: python3 -m rv --cliente DIR <comando> ..."""
import argparse
import datetime as dt
import getpass
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from . import base, correo, motor, tick, ventas, verificar, wa, web


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


def simular_correo(cfg, args, texto):
    from email.utils import make_msgid
    if not correo.configurado(cfg):
        sys.exit("para simular el canal de correo pon en cliente.json email.proveedor (smtp o microsoft) y email.remitente")
    item = correo._item(cfg, args.de, args.nombre, args.asunto, make_msgid(domain="simulado.mx"), texto,
                        base.iso(base.ahora()), autenticacion=f"dmarc=pass header.from={args.de.rpartition('@')[2]}")
    with base.db(cfg) as con:
        ultimo = con.execute("SELECT COALESCE(MAX(id),0) FROM mensaje").fetchone()[0]
        motor.encolar_correo(con, item)
        motor.procesar_pendientes(con, cfg)
        for m in con.execute("SELECT * FROM mensaje WHERE id>? AND direccion='out' ORDER BY id", (ultimo,)):
            print(f"  → {m['telefono']} [{m['autor']}] {m['texto']}")


def simular_uno(cfg, args, texto, tipo="text"):
    if "@" in args.de:   # --de cliente@correo.mx: simula un correo en vez de un WhatsApp
        return simular_correo(cfg, args, texto)
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
    with base.db(cfg) as con, base.transaccion(con):
        r = ventas.importar_clientes(con, cfg, args.csv)
    print(f"nuevos={r['nuevos']} actualizados={r['actualizados']} duplicados={r['duplicados']} "
          f"equipo_excluidos={r['equipo']} rechazados={len(r['rechazados'])}")
    for linea, motivo in r["rechazados"]:
        print(f"  línea {linea}: {motivo}")


def cmd_importar_ventas(cfg, args):
    with base.db(cfg) as con, base.transaccion(con):
        r = ventas.importar_ventas(con, cfg, args.csv)
    print(f"registradas={r['registradas']} duplicadas={r['duplicadas']} rechazadas={len(r['rechazadas'])}")
    for linea, motivo in r["rechazadas"]:
        print(f"  línea {linea}: {motivo}")


def cmd_reporte(cfg, args):
    mes = args.mes or (base.ahora().astimezone(cfg.tz).replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    with base.db(cfg) as con:
        try:
            texto = ventas.reporte(con, cfg, mes, args.resenas_google)
        except ValueError as e:
            sys.exit(str(e))
    ventas.guardar_reporte(cfg, mes, texto)
    if args.enviar:
        para = cfg["email"]["reporte_a"] or cfg["email"]["avisos_a"]
        _, error = correo.enviar(cfg, para, f"Reporte de {mes} · {cfg['nombre']}", texto)
        if error:
            sys.exit(f"no se envió el reporte por correo: {error}")
        print(f"Reporte enviado a {', '.join(para)}")


def cmd_pagina(cfg, args):
    salida = args.salida or str(cfg.carpeta / "pagina" / "index.html")
    os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
    with open(salida, "w", encoding="utf-8") as f:
        f.write(ventas.pagina(cfg))
    print(f"página escrita en {salida}")


def cmd_verificar(cfg, args):
    with base.db(cfg) as con:
        resultados = verificar.locales(con, cfg) + (verificar.remotos(con, cfg) if args.remoto else [])
    for nivel, msg in resultados:
        print(f"[{nivel:5}] {msg}")
    if any(n == "ERROR" for n, _ in resultados):
        sys.exit(1)


def cmd_respaldo(cfg, args):
    destino = Path(args.destino or cfg.carpeta / "respaldos")
    with base.db(cfg) as con:
        ruta = base.respaldar(con, destino)
    limite = time.time() - args.dias * 86400
    for viejo in destino.glob("datos-*.db.gz"):
        if viejo.stat().st_mtime < limite:
            viejo.unlink()
    print(ruta)


def cmd_configurar_webhook(cfg, args):
    print(json.dumps(verificar.configurar_override(cfg)))


def cmd_auditoria(args):
    from . import auditoria
    nicho = auditoria.cargar_nicho(args.nicho)
    carpeta = Path(args.carpeta)
    if not (carpeta / "negocios.csv").exists():
        auditoria.crear_plantilla(carpeta)
        print(f"Creé {carpeta / 'negocios.csv'}: una fila por negocio (ábrelo en Excel o Numbers).")
        print(f"Mensaje de prueba para {nicho['nicho']}: {nicho['auditoria']['mensaje_prueba']}")
        print("Cuando lo llenes, corre el mismo comando para generar los reportes.")
        return
    resultados, errores = auditoria.generar(carpeta, nicho)
    for linea, motivo in errores:
        print(f"  línea {linea}: {motivo}")
    for n, p in sorted(resultados, key=lambda x: -auditoria.total(x[1])):
        print(f"{auditoria.total(p):>4}  {n['negocio']}")
    if resultados:
        print(f"Reportes en {carpeta / 'reportes'} · resumen interno en {carpeta / 'resumen.html'}")
    if errores:
        sys.exit(1)


def cmd_demo_ventas(args):
    from . import auditoria, demo
    demo.correr(auditoria.cargar_nicho(args.nicho), args.carpeta, 0 if args.rapido else 1.8)  # ~3 min


def cmd_prueba_real(cfg, args):
    from . import prueba
    with base.db(cfg) as con:
        prueba.correr(con, cfg, args.tel, args.paso)


def main(argv=None):
    p = argparse.ArgumentParser(prog="rv", description="Agorá: sistema de recuperación de ventas")
    p.add_argument("--cliente", default=os.environ.get("RV_CLIENTE", "."), help="carpeta del cliente")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="webhook + bandeja web")
    s = sub.add_parser("simular", help="inyecta un mensaje falso de WhatsApp (modo prueba)")
    s.add_argument("--de", default="+528100000001", help="teléfono, o un correo para simular el canal de correo")
    s.add_argument("--asunto", default="Consulta", help="asunto del correo simulado")
    s.add_argument("--nombre", default="Cliente de prueba")
    s.add_argument("--texto", default="Hola")
    s.add_argument("--tipo", default="text", choices=["text", "audio", "image"])
    s.add_argument("--interactivo", action="store_true")
    s.add_argument("--url", help="enviar al servidor en marcha (p. ej. http://127.0.0.1:8080)")
    sub.add_parser("tick", help="envíos programados (correr cada 5 min)")
    i = sub.add_parser("importar-clientes", help="CSV: nombre,telefono,ultima_visita,consentimiento")
    i.add_argument("csv")
    v = sub.add_parser("importar-ventas", help="CSV: telefono,fecha,monto")
    v.add_argument("csv")
    r = sub.add_parser("reporte", help="reporte mensual (por defecto, el mes anterior)")
    r.add_argument("mes", nargs="?", help="AAAA-MM")
    r.add_argument("--resenas-google", type=int, help="reseñas nuevas en Google en el mes (dato manual)")
    r.add_argument("--enviar", action="store_true", help="mandarlo por correo a email.reporte_a (o email.avisos_a)")
    pg = sub.add_parser("pagina", help="página estática que lleva a WhatsApp")
    pg.add_argument("--salida")
    vf = sub.add_parser("verificar", help="chequeos de instalación y salud")
    vf.add_argument("--remoto", action="store_true", help="también consulta Meta, Anthropic y Google (solo lectura)")
    rs = sub.add_parser("respaldo", help="copia comprimida de la base; borra copias locales viejas")
    rs.add_argument("--destino")
    rs.add_argument("--dias", type=int, default=30)
    sub.add_parser("configurar-webhook", help="apunta el webhook del número a url_publica/webhook (instalación)")
    u = sub.add_parser("usuario", help="crea o cambia la contraseña de un usuario de la bandeja")
    u.add_argument("nombre")
    u.add_argument("--clave-stdin", action="store_true")
    au = sub.add_parser("auditoria", help="auditoría de fugas: crea negocios.csv o genera los reportes (no usa --cliente)")
    au.add_argument("--nicho", required=True, help="archivo de nicho, p. ej. nichos/clinica-estetica.json")
    au.add_argument("carpeta", help="carpeta de la auditoría (ahí vive negocios.csv)")
    dv = sub.add_parser("demo-ventas", help="demo de ~3 min para dueños, sin cuentas (no usa --cliente)")
    dv.add_argument("--nicho", required=True)
    dv.add_argument("--carpeta", help="dónde guardar el cliente de la demo (por defecto, una carpeta temporal)")
    dv.add_argument("--rapido", action="store_true", help="sin pausas")
    pr = sub.add_parser("prueba-real", help="recorrido guiado con tu teléfono (docs/prueba-real.md)")
    pr.add_argument("--tel", required=True, help="tu número de WhatsApp (el que escribe como cliente)")
    pr.add_argument("paso", nargs="?", help="repetir un solo paso: conexion, noche, seguimiento, cita, baja, urgencia, "
                                           "handoff, asistencia, venta, reporte")
    args = p.parse_args(argv)
    if args.cmd in ("auditoria", "demo-ventas"):
        return {"auditoria": cmd_auditoria, "demo-ventas": cmd_demo_ventas}[args.cmd](args)

    cfg = base.cargar_config(args.cliente)
    base.abrir_db(cfg).close()  # migraciones
    {"serve": lambda: web.servir(cfg), "simular": lambda: cmd_simular(cfg, args),
     "usuario": lambda: cmd_usuario(cfg, args), "tick": lambda: cmd_tick(cfg, args),
     "importar-clientes": lambda: cmd_importar_clientes(cfg, args),
     "importar-ventas": lambda: cmd_importar_ventas(cfg, args), "reporte": lambda: cmd_reporte(cfg, args),
     "pagina": lambda: cmd_pagina(cfg, args), "verificar": lambda: cmd_verificar(cfg, args),
     "respaldo": lambda: cmd_respaldo(cfg, args), "prueba-real": lambda: cmd_prueba_real(cfg, args),
     "configurar-webhook": lambda: cmd_configurar_webhook(cfg, args)}[args.cmd]()


if __name__ == "__main__":
    main()
