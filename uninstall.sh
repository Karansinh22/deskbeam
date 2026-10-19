#!/usr/bin/env bash
systemctl --user disable --now nmrd.service nmrd-tunnel.service 2>/dev/null
rm -f ~/.config/systemd/user/nmrd.service ~/.config/systemd/user/nmrd-tunnel.service
systemctl --user daemon-reload
rm -rf ~/.local/share/nmrd
echo "Removed. Credentials remain in ~/.config/nmrd (delete it to wipe them)."
