#!/usr/bin/env bash
# Installs nmrd for the current user: asks for a login, sets up auto-start, public tunnel.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/nmrd"
CONF="$HOME/.config/nmrd"
UNITS="$HOME/.config/systemd/user"
mkdir -p "$DEST" "$CONF" "$UNITS"
# X authority file: GDM autologin keeps it under /run/user/UID, other setups use ~/.Xauthority
if [ -r "/run/user/$(id -u)/gdm/Xauthority" ]; then XAUTH="/run/user/$(id -u)/gdm/Xauthority"; else XAUTH="$HOME/.Xauthority"; fi

echo "== Dependencies"
python3 -c "import aiohttp, bcrypt, Xlib, gi" 2>/dev/null || { echo "Missing: sudo apt install -y python3-aiohttp python3-bcrypt python3-xlib gstreamer1.0-nice"; exit 1; }
gst-inspect-1.0 nicesrc >/dev/null 2>&1 || { echo "Missing: sudo apt install -y gstreamer1.0-nice"; exit 1; }

echo "== Copying files"
cp -r "$SRC/server.py" "$SRC/web" "$SRC/tunnel.sh" "$SRC/tailscale-setup.sh" "$SRC/watchdog.sh" "$DEST/"
chmod +x "$DEST/tunnel.sh" "$DEST/tailscale-setup.sh" "$DEST/watchdog.sh"

if [ -s "$CONF/users.json" ]; then
  echo "== Existing login account(s) kept (add more with: python3 $DEST/server.py adduser NAME)"
else
  echo "== Create the remote-login account (used on the website)"
  read -rp "Username: " NAME
  python3 "$DEST/server.py" adduser "$NAME"
fi

[ -f "$CONF/config.json" ] || echo '{"bind":"127.0.0.1","port":8080}' > "$CONF/config.json"

echo "== Public access (free Cloudflare quick tunnel)"
if [ ! -x "$DEST/cloudflared" ]; then
  case "$(uname -m)" in aarch64|arm64) CFARCH=arm64;; x86_64) CFARCH=amd64;; armv7l) CFARCH=arm;; *) echo "Unsupported CPU: $(uname -m)"; exit 1;; esac
  curl -fL -o "$DEST/cloudflared" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$CFARCH"
  chmod +x "$DEST/cloudflared"
fi
[ -f "$CONF/ntfy_topic" ] || { echo "nmrd-$(head -c 12 /dev/urandom | base64 | tr -dc a-z0-9 | head -c 16)" > "$CONF/ntfy_topic"; chmod 600 "$CONF/ntfy_topic"; }

echo "== Auto-start (systemd user services: start at boot, restart forever)"
cat > "$UNITS/nmrd.service" <<UNIT
[Unit]
Description=nmrd remote desktop server
StartLimitIntervalSec=0
[Service]
Environment=DISPLAY=:0
Environment=XAUTHORITY=$XAUTH
# wait until the desktop's X server is up (autologin), so no click or manual start is needed
ExecStartPre=/bin/sh -c 'until [ -S /tmp/.X11-unix/X0 ] && [ -r "\$XAUTHORITY" ]; do sleep 1; done'
ExecStart=/usr/bin/python3 $DEST/server.py run
Restart=always
RestartSec=2
TimeoutStartSec=infinity
[Install]
WantedBy=default.target
UNIT
cat > "$UNITS/nmrd-tunnel.service" <<UNIT
[Unit]
Description=nmrd public tunnel (Cloudflare quick tunnel, backup/no-account address)
After=network-online.target
StartLimitIntervalSec=0
[Service]
ExecStart=$DEST/tunnel.sh
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
UNIT
cat > "$UNITS/nmrd-watchdog.service" <<UNIT
[Unit]
Description=nmrd self-healing watchdog for the public address
After=nmrd.service
StartLimitIntervalSec=0
[Service]
ExecStart=$DEST/watchdog.sh
Restart=always
RestartSec=10
[Install]
WantedBy=default.target
UNIT
# keep the user service manager running from boot, even before anyone logs in
loginctl enable-linger "$USER" 2>/dev/null || echo "NOTE: could not enable linger; run: sudo loginctl enable-linger $USER"
systemctl --user daemon-reload
systemctl --user enable nmrd.service nmrd-tunnel.service nmrd-watchdog.service
systemctl --user restart nmrd.service nmrd-tunnel.service nmrd-watchdog.service
sleep 8
echo
echo "Installed. Local address : http://localhost:8080"
echo "Public address           : $(cat "$CONF/url.txt" 2>/dev/null || echo '(starting - check ~/.config/nmrd/url.txt)')"
echo "For a PERMANENT address run: $DEST/tailscale-setup.sh"
echo "The quick-tunnel URL changes on every restart. To get it pushed to your phone, subscribe to"
echo "  https://ntfy.sh/$(cat "$CONF/ntfy_topic")   (ntfy app or browser)"
