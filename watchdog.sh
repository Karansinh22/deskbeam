#!/usr/bin/env bash
# Self-heal: every minute, check that the permanent public address answers; if not, repair step by step.
CONF="${NMRD_CONF:-$HOME/.config/nmrd}"
TS="$HOME/.local/share/nmrd/tailscale"; SOCK="$TS/tailscaled.sock"
URL="$(cat "$CONF/stable_url" 2>/dev/null)"; [ -z "$URL" ] && exit 0   # nothing to watch until tailscale-setup.sh was run
PORT="$(python3 -c "import json;print(json.load(open('$CONF/config.json')).get('port',8080))" 2>/dev/null || echo 8080)"
ok() { n=0; for i in 1 2 3 4; do [ "$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$URL/login")" = 200 ] && n=$((n+1)); done; [ $n -ge 2 ]; }  # >=2/4: ingress nodes are flaky right after a network change
fails=0
while sleep 60; do
  if curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/login && ok; then fails=0; continue; fi
  fails=$((fails+1)); echo "$(date) public check failed ($fails)"
  if [ $fails -eq 2 ]; then       # re-announce the funnel
    "$TS/tailscale" --socket="$SOCK" funnel reset; "$TS/tailscale" --socket="$SOCK" funnel --bg "$PORT"
  elif [ $fails -eq 4 ]; then     # restart tailscale (new network not picked up)
    systemctl --user restart nmrd-tailscaled; sleep 10; "$TS/tailscale" --socket="$SOCK" funnel --bg "$PORT"
  elif [ $fails -ge 7 ]; then     # last resort: restart server too
    systemctl --user restart nmrd nmrd-tailscaled; sleep 10; "$TS/tailscale" --socket="$SOCK" funnel --bg "$PORT"; fails=3
  fi
done
