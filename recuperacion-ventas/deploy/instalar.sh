#!/usr/bin/env bash
# Instalación de Agorá en un VPS Ubuntu 24.04 en dos órdenes (ver docs/runbook.md para el detalle de cada paso).
#
#   1) Servidor, una vez (desde la máquina recién creada, como root):
#        curl -fsSL https://raw.githubusercontent.com/oamv0826-Mo/ponytail/main/recuperacion-ventas/deploy/instalar.sh -o instalar.sh
#        bash instalar.sh servidor --dominio rv.tudominio.mx [--rama main]
#
#   2) Cada cliente:
#        bash /srv/rv/app/recuperacion-ventas/deploy/instalar.sh cliente <id> [--nicho nichos/<giro>.json] [--google-sa archivo.json]
#
# Mientras el PR #1 no esté fusionado, en las dos URL/opciones de arriba usa la rama claude/festive-ride-3kldx0
# en vez de main (--rama claude/festive-ride-3kldx0).
#
# Las dos se pueden repetir sin romper nada: lo que ya existe se respeta (no pisa cliente.json ni secretos.env).
# El cliente queda en modo_prueba; la salida a producción (verificar --remoto, configurar-webhook) sigue siendo manual.
set -euo pipefail

REPO=https://github.com/oamv0826-Mo/ponytail
APP=/srv/rv/app
RV=$APP/recuperacion-ventas
CLIENTES=/srv/rv/clientes
CADDY_CLIENTES=/etc/caddy/rv-clientes

paso() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
falla() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
como_rv() { sudo -u rv -H "$@"; }

uso() {
	sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
	exit 1
}

[ "$(id -u)" -eq 0 ] || falla "corre este script como root (sudo bash $0 ...)"

servidor() {
	local dominio="" rama="main"
	while [ $# -gt 0 ]; do
		case "$1" in
			--dominio) dominio="${2:-}"; shift 2 ;;
			--rama) rama="${2:-}"; shift 2 ;;
			*) uso ;;
		esac
	done
	[ -n "$dominio" ] || falla "falta --dominio (por ejemplo --dominio rv.tudominio.mx)"

	paso "Revisando el sistema"
	# shellcheck source=/dev/null
	. /etc/os-release
	[ "${ID:-}" = ubuntu ] || falla "este script es para Ubuntu (encontré ${PRETTY_NAME:-desconocido})"
	command -v python3 >/dev/null || apt-get install -y python3
	python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
		|| falla "Agorá necesita Python 3.11 o mayor; usa Ubuntu 24.04 LTS"

	paso "Instalando paquetes"
	export DEBIAN_FRONTEND=noninteractive
	apt-get update -q
	apt-get install -y -q python3 tzdata git rclone ufw curl gnupg debian-keyring debian-archive-keyring apt-transport-https
	if ! command -v caddy >/dev/null; then
		curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
			| gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
		curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
		apt-get update -q
		apt-get install -y -q caddy
	fi

	paso "Zona horaria y firewall (SSH, 80, 443)"
	timedatectl set-timezone America/Monterrey || true
	ufw allow OpenSSH >/dev/null && ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
	ufw --force enable >/dev/null

	paso "Usuario rv y carpetas"
	id rv >/dev/null 2>&1 || adduser --system --group --home /home/rv rv
	mkdir -p "$CLIENTES" /etc/rv "$CADDY_CLIENTES"
	chown rv:rv /srv/rv "$CLIENTES"

	paso "Código (rama $rama)"
	if [ -d "$APP/.git" ]; then
		como_rv git -C "$APP" fetch -q origin "$rama"
		como_rv git -C "$APP" checkout -q "$rama"
		como_rv git -C "$APP" pull -q --ff-only origin "$rama"
	else
		install -d -o rv -g rv "$APP"
		como_rv git clone -q -b "$rama" "$REPO" "$APP"
	fi

	paso "Pruebas"
	local salida
	salida=$(cd "$RV" && como_rv python3 -m unittest discover -s tests 2>&1) \
		|| { echo "$salida" | tail -30; falla "las pruebas fallaron; no sigo"; }
	echo "$salida" | grep -E '^(Ran |OK|FAILED)'

	paso "Servicios de systemd"
	cp "$RV"/deploy/*.service "$RV"/deploy/*.timer /etc/systemd/system/
	systemctl daemon-reload

	paso "Caddy (HTTPS para $dominio)"
	if [ -f /etc/caddy/Caddyfile ] && grep -q 'rv-clientes' /etc/caddy/Caddyfile; then
		echo "ya configurado; no lo toco"
	else
		[ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.antes-de-agora.$(date +%s)"
		cat > /etc/caddy/Caddyfile <<-EOF
		# Agorá. Cada cliente agrega su archivo en $CADDY_CLIENTES/<id>.caddy (lo crea instalar.sh cliente).
		$dominio {
			encode gzip
			request_body {
				max_size 2MB
			}
			import $CADDY_CLIENTES/*.caddy
			handle {
				respond 404
			}
			log {
				output file /var/log/caddy/rv.log
			}
		}
		EOF
	fi
	caddy fmt --overwrite /etc/caddy/Caddyfile
	echo "$dominio" > /etc/rv/dominio
	caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
	systemctl enable -q caddy
	systemctl reload-or-restart caddy

	paso "Listo"
	local ip
	ip=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || echo "<IP del servidor>")
	cat <<-EOF
	Servidor instalado. Falta:
	  1. Que $dominio apunte a $ip (registro A). Revisa: dig +short $dominio
	     Caddy saca el certificado solo en cuanto el dominio resuelva (journalctl -u caddy -n 30).
	  2. Respaldo externo (una vez):
	       sudo -u rv -H rclone config            # remoto llamado "respaldo" (Backblaze B2 o Cloudflare R2)
	       echo 'RV_RCLONE_DESTINO=respaldo:rv-respaldos/rv' > /etc/rv/respaldo.env
	  3. Primer cliente:
	       bash $RV/deploy/instalar.sh cliente <id> --google-sa /root/google-sa.json
	EOF
}

siguiente_puerto() {
	python3 - "$CLIENTES" <<-'EOF'
	import json, sys
	from pathlib import Path
	usados = set()
	for f in Path(sys.argv[1]).glob("*/cliente.json"):
	    try:
	        usados.add(int(json.loads(f.read_text(encoding="utf-8")).get("puerto", 0)))
	    except (ValueError, OSError):
	        pass
	p = 8081
	while p in usados:
	    p += 1
	print(p)
	EOF
}

preguntar_secreto() {   # nombre, texto, oculto(1/0); escribe NOMBRE=valor solo si falta en secretos.env
	local nombre=$1 texto=$2 oculto=$3 archivo=$4 valor=""
	grep -q "^$nombre=" "$archivo" && return 0
	if [ -t 0 ]; then
		if [ "$oculto" = 1 ]; then read -rsp "  $texto (Enter = después): " valor; echo
		else read -rp "  $texto (Enter = después): " valor; fi
	fi
	echo "$nombre=$valor" >> "$archivo"
}

cliente() {
	local id="${1:-}" nicho="" sa=""
	[ -n "$id" ] || uso
	shift
	[[ "$id" =~ ^[a-z0-9][a-z0-9-]*$ ]] || falla "el id solo lleva minúsculas, números y guiones (ej. dental-garza)"
	while [ $# -gt 0 ]; do
		case "$1" in
			--nicho) nicho="${2:-}"; shift 2 ;;
			--google-sa) sa="${2:-}"; shift 2 ;;
			*) uso ;;
		esac
	done
	[ -d "$RV" ] || falla "primero instala el servidor: bash instalar.sh servidor --dominio ..."
	[ -f /etc/rv/dominio ] || falla "no encuentro /etc/rv/dominio; corre de nuevo instalar.sh servidor"
	local dominio c puerto
	dominio=$(cat /etc/rv/dominio)
	c=$CLIENTES/$id

	paso "Carpeta $c"
	install -d -o rv -g rv "$c"
	if [ -f "$c/cliente.json" ]; then
		puerto=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["puerto"])' "$c/cliente.json")
		echo "cliente.json ya existe; no lo toco (puerto $puerto)"
	else
		puerto=$(siguiente_puerto)
		[ -z "$nicho" ] || [ -f "$nicho" ] || [ -f "$RV/$nicho" ] || falla "no encuentro el nicho $nicho"
		[ -z "$nicho" ] || [ -f "$nicho" ] || nicho=$RV/$nicho
		(cd "$RV" && python3 - "$c/cliente.json" "$id" "$dominio" "$puerto" "$nicho" <<-'EOF'
		import datetime as dt, json, sys
		from pathlib import Path
		from rv import demo
		destino, id_, dominio, puerto, nicho = sys.argv[1:]
		if nicho:
		    datos = demo.cliente_de_nicho(json.loads(Path(nicho).read_text(encoding="utf-8")), dt.date.today())
		else:
		    datos = json.loads(demo.EJEMPLO.read_text(encoding="utf-8"))
		datos.update({"modo_prueba": True, "puerto": int(puerto), "url_publica": f"https://{dominio}/c/{id_}",
		              "fecha_inicio": dt.date.today().isoformat()})
		Path(destino).write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
		EOF
		)
		chown rv:rv "$c/cliente.json"
		echo "cliente.json creado (puerto $puerto, modo_prueba). Los datos del negocio son de ejemplo: edítalo."
	fi

	if [ -n "$sa" ]; then
		[ -f "$sa" ] || falla "no encuentro $sa"
		install -m 600 -o rv -g rv "$sa" "$c/google-sa.json"
	fi

	paso "Secretos ($c/secretos.env)"
	touch "$c/secretos.env"; chown rv:rv "$c/secretos.env"; chmod 600 "$c/secretos.env"
	grep -q '^WA_VERIFY_TOKEN=' "$c/secretos.env" \
		|| echo "WA_VERIFY_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" >> "$c/secretos.env"
	grep -q '^GOOGLE_SA_FILE=' "$c/secretos.env" || echo "GOOGLE_SA_FILE=$c/google-sa.json" >> "$c/secretos.env"
	preguntar_secreto WA_TOKEN "Token permanente de Meta (WA_TOKEN)" 1 "$c/secretos.env"
	preguntar_secreto WA_APP_SECRET "Secreto de la app de Meta (WA_APP_SECRET)" 1 "$c/secretos.env"
	preguntar_secreto ANTHROPIC_API_KEY "Clave de Anthropic (ANTHROPIC_API_KEY)" 1 "$c/secretos.env"

	paso "Ruta en Caddy"
	cat > "$CADDY_CLIENTES/$id.caddy" <<-EOF
	handle_path /c/$id/* {
		reverse_proxy 127.0.0.1:$puerto
	}
	handle_path /p/$id/* {
		root * $c/pagina
		file_server
	}
	EOF
	caddy fmt --overwrite "$CADDY_CLIENTES/$id.caddy"
	caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
		|| { caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; falla "Caddy rechazó la configuración"; }
	systemctl reload caddy

	paso "Servicios"
	systemctl enable -q --now "rv@$id" "rv-tick@$id.timer"
	if [ -s /etc/rv/respaldo.env ]; then
		systemctl enable -q --now "rv-respaldo@$id.timer"
	else
		echo "Respaldo externo sin configurar (/etc/rv/respaldo.env): rv-respaldo@$id.timer queda apagado."
	fi
	sleep 2
	systemctl is-active -q "rv@$id" || { journalctl -u "rv@$id" -n 20 --no-pager; falla "rv@$id no arrancó"; }

	if [ -t 0 ] && ! (cd "$RV" && como_rv python3 -m rv --cliente "$c" verificar 2>/dev/null | grep -q 'usuarios de la bandeja: [1-9]'); then
		paso "Usuario de la bandeja"
		local nombre=""
		read -rp "  Nombre de usuario para la bandeja (Enter = después): " nombre
		[ -z "$nombre" ] || (cd "$RV" && como_rv python3 -m rv --cliente "$c" usuario "$nombre")
	fi

	paso "Verificación"
	(cd "$RV" && como_rv python3 -m rv --cliente "$c" verificar) || true

	cat <<-EOF

	Cliente $id instalado en modo prueba. Falta:
	  1. Datos del negocio:   sudo -u rv nano $c/cliente.json   y luego   systemctl restart rv@$id
	  2. Usuario de la bandeja: cd $RV && sudo -u rv -H python3 -m rv --cliente $c usuario <nombre>
	  3. Bandeja: https://$dominio/c/$id/bandeja
	  4. Producción: docs/checklist-instalacion.md, "Salida a producción" (verificar --remoto, modo_prueba false,
	     configurar-webhook). WA_VERIFY_TOKEN para Meta: grep WA_VERIFY_TOKEN $c/secretos.env
	EOF
}

case "${1:-}" in
	servidor) shift; servidor "$@" ;;
	cliente) shift; cliente "$@" ;;
	*) uso ;;
esac
