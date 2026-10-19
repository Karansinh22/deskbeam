#!/usr/bin/env bash
# Gives the remote desktop a PERMANENT public https address using Tailscale Funnel (free account needed).
# Runs tailscale in user space: no sudo, no kernel changes, viewers need nothing installed.
set -euo pipefail
DEST="$HOME/.local/share/nmrd"; TS="$DEST/tailscale"; CONF="$HOME/.config/nmrd"; UNITS="$HOME/.config/systemd/user"
SOCK="$TS/tailscaled.sock"; mkdir -p "$TS/state" "$UNITS"
PORT="$(python3 -c "import json;print(json.load(open('$CONF/config.json')).get('port',8080))" 2>/dev/null || echo 8080)"

if [ ! -x "$TS/tailscaled" ]; then
  case "$(uname -m)" in aarch64|arm64) A=arm64;; x86_64) A=amd64;; armv7l) A=arm;; *) echo "Unsupported CPU"; exit 1;; esac
  VER="$(curl -s https://pkgs.tailscale.com/stable/ | grep -oE "tailscale_[0-9.]+_${A}\.tgz" | head -1)"
  curl -fL -o "$TS/ts.tgz" "https://pkgs.tailscale.com/stable/$VER"
  tar -xzf "$TS/ts.tgz" -C "$TS" --strip-components=1 && rm "$TS/ts.tgz"
fi

cat > "$UNITS/nmrd-tailscaled.service" <<UNIT
[Unit]
Description=Tailscale (user space) for nmrd
After=network-online.target
StartLimitIntervalSec=0
[Service]
ExecStart=$TS/tailscaled --tun=userspace-networking --statedir=$TS/state --socket=$SOCK
Restart=always
RestartSec=3
[Install]
WantedBy=default.target
UNIT
cat > "$UNITS/nmrd-funnel.service" <<UNIT
[Unit]
Description=Expose nmrd through Tailscale Funnel (permanent URL)
After=nmrd-tailscaled.service
Wants=nmrd-tailscaled.service
StartLimitIntervalSec=0
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'until $TS/tailscale --socket=$SOCK status >/dev/null 2>&1; do sleep 2; done; $TS/tailscale --socket=$SOCK funnel --bg $PORT'
Restart=on-failure
RestartSec=10
[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now nmrd-tailscaled.service
sleep 3

echo
echo "== Log in to Tailscale (free account). Open the link below in any browser and approve:"
"$TS/tailscale" --socket="$SOCK" up --hostname=nmrd-jetson
echo
echo "== Enabling Funnel. If it prints a link asking to enable HTTPS/Funnel for your tailnet, open it, approve, then re-run this script."
"$TS/tailscale" --socket="$SOCK" funnel --bg "$PORT"
systemctl --user enable --now nmrd-funnel.service

URL="$("$TS/tailscale" --socket="$SOCK" funnel status 2>/dev/null | grep -oE 'https://[a-zA-Z0-9.-]+\.ts\.net' | head -1 || true)"
if [ -n "$URL" ]; then
  echo "$URL" > "$CONF/stable_url"; echo "$URL" > "$CONF/url.txt"
  echo; echo "PERMANENT ADDRESS: $URL"
  [ -f "$CONF/ntfy_topic" ] && curl -s -H "Title: Remote desktop permanent address" -d "$URL" "https://ntfy.sh/$(cat "$CONF/ntfy_topic")" >/dev/null || true
else
  echo "Funnel not active yet - follow the message above, then run this script again."
fi
