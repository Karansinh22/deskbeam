#!/usr/bin/env bash
# Starts a free Cloudflare quick tunnel to the local web UI and announces the public URL.
# The URL is saved to ~/.config/nmrd/url.txt and (optionally) pushed to your ntfy.sh topic.
CONF="${NMRD_CONF:-$HOME/.config/nmrd}"
BIN="$HOME/.local/share/nmrd/cloudflared"
PORT="$(python3 -c "import json;print(json.load(open('$CONF/config.json')).get('port',8080))" 2>/dev/null || echo 8080)"
TOPIC="$(cat "$CONF/ntfy_topic" 2>/dev/null)"
"$BIN" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" 2>&1 | while read -r line; do
  echo "$line"
  url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' <<<"$line" | head -1)"
  if [ -n "$url" ]; then
    echo "$url" > "$CONF/quick_url.txt"; [ -s "$CONF/stable_url" ] || echo "$url" > "$CONF/url.txt"
    [ -n "$TOPIC" ] && [ ! -s "$CONF/stable_url" ] && curl -s -H "Title: Remote desktop is online" -d "$url" "https://ntfy.sh/$TOPIC" >/dev/null
  fi
done
