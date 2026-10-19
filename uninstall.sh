#!/usr/bin/env bash
systemctl --user disable --now nmrd.service nmrd-tunnel.service nmrd-watchdog.service nmrd-overlay.service nmrd-funnel.service nmrd-tailscaled.service 2>/dev/null
rm -f ~/.config/systemd/user/nmrd.service ~/.config/systemd/user/nmrd-tunnel.service ~/.config/systemd/user/nmrd-watchdog.service ~/.config/systemd/user/nmrd-overlay.service ~/.config/systemd/user/nmrd-funnel.service ~/.config/systemd/user/nmrd-tailscaled.service
systemctl --user daemon-reload
rm -rf ~/.local/share/nmrd
echo "Removed. Credentials remain in ~/.config/nmrd (delete it to wipe them)."
