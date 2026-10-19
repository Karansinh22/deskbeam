# nmrd – web remote desktop for Linux (free, low latency)

Control your Linux desktop from **any browser, on any network**, with a username and password you choose.
Think of a self-hosted NoMachine / AnyDesk that is just a small Python service plus a web page.

- **Smooth, low latency**: your screen is encoded to H.264 and sent over **WebRTC**; mouse and keyboard go back over WebRTC data channels.
- **Works from anywhere**: the machine only makes *outgoing* connections. No router setup, no port forwarding.
- **Automatic fallback**: if the network blocks WebRTC (strict NAT, campus WiFi), the page switches to *compatibility mode* (H.264 over the HTTPS WebSocket).
- **Starts by itself**: systemd user services start at boot, restart on failure, and a watchdog repairs the public address.
- **Free**: Cloudflare quick tunnel (no account) or Tailscale Funnel (free account, permanent address).
- **No client install**: viewers only need a modern browser (Chrome, Edge, Firefox).

```
 Linux machine (this repo)                       Internet                    You
 ┌──────────────────────────┐   outbound only   ┌─────────────────┐   ┌────────────────┐
 │ server.py: login + WebRTC │ ───────────────▶ │ Cloudflare /     │ ◀─│ browser: login, │
 │ captures screen (X11),    │                  │ Tailscale Funnel │   │ see + control   │
 │ injects mouse/keyboard    │ ◀── P2P video ───────────────────────────│ (WebRTC)        │
 └──────────────────────────┘                   └─────────────────┘   └────────────────┘
```

## Requirements

| | |
|---|---|
| OS | Linux with an **X11 session** (Ubuntu/Debian tested: Jetson Orin Nano, Ubuntu 22.04). **Wayland is not supported** (log in with "Ubuntu on Xorg"/"X11"). |
| CPU | x86-64 or ARM64. Video is encoded in software (x264); ~1.5–2 CPU cores at 1080p30. |
| Auto-login | For "no clicks after boot", enable desktop auto-login so an X session exists. |

### Install dependencies (Debian/Ubuntu)

```bash
sudo apt install -y python3-aiohttp python3-bcrypt python3-xlib python3-gi gir1.2-gtk-3.0 libnotify-bin \
  gir1.2-gst-plugins-bad-1.0 gstreamer1.0-tools gstreamer1.0-x gstreamer1.0-nice \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly curl
```

(`gstreamer1.0-nice` is the one people forget: without it WebRTC cannot connect.)

## Install

```bash
git clone https://github.com/Karansinh22/nmrd-remote-desktop.git
cd nmrd-remote-desktop
./install.sh
```

The installer:
1. checks dependencies,
2. asks you to create the **website login** (username + password, stored as a bcrypt hash in `~/.config/nmrd/users.json`),
3. downloads `cloudflared` for a free public address,
4. installs and starts systemd **user** services (`nmrd`, `nmrd-tunnel`, `nmrd-watchdog`), enables linger so they start at boot.

At the end it prints a public `https://….trycloudflare.com` address. Open it, log in, done.
Locally it is also available at `http://localhost:8080`.

> The quick-tunnel address **changes on every restart**. The installer creates a random private topic on [ntfy.sh](https://ntfy.sh) (shown at the end) and posts the new address there; subscribe to it in the ntfy app or browser. For an address that never changes, do the next step.

## Permanent address (optional, free): Tailscale Funnel

1. Create a **free personal** Tailscale account. Use a personal login (Gmail/GitHub); a school/company login may need its admin's approval.
2. Run:
   ```bash
   ~/.local/share/nmrd/tailscale-setup.sh
   ```
3. Open the login link it prints and approve. If it asks you to enable **HTTPS / Funnel** for your tailnet, open that link, approve, and run the script again.
4. It ends with `PERMANENT ADDRESS: https://nmrd-jetson.<your-tailnet>.ts.net`. That address survives reboots and network changes; the watchdog re-announces it if a network change breaks it.

Tailscale runs in user space (no sudo, no kernel changes). Viewers install nothing.

## Using it

- Open the address, sign in, and the desktop appears. Click the video to give it keyboard focus.
- Toolbar: quality (3 / 8 / 15 Mbps), connection mode (Auto / Direct only / Compatibility), fullscreen, log out.
- The mouse pointer is drawn by your own browser (zero lag, never disappears); its shape (arrow, text, hand, resize…) follows the remote machine.
- Only one viewer at a time; a new viewer replaces the old one.
- Add another login: `python3 ~/.local/share/nmrd/server.py adduser NAME`
  (users live in `~/.config/nmrd/users.json`; delete a user by removing its entry).

## Who is connected? (desktop overlay)

A small floating pill sits at the top-right of the machine's desktop (`overlay.py`, started automatically):

- 🟢 **Remote access on · nobody connected**: idle.
- 🔴 **`name` is viewing / controlling · IP · mode · time** with a red **Disconnect** button: the viewer is kicked **and their login is revoked**, so they land on the sign-in page.
- **Pause**: blocks all new viewers (and disconnects the current one) until you press **Resume**.
- You also get a desktop notification whenever someone connects or disconnects.
- **Hide / show:** click the coloured dot to collapse the panel to just the dot (it turns red while someone is connected); click it again to expand. The choice is remembered across restarts. You can also toggle from a terminal or a keyboard shortcut with `systemctl --user kill -s USR1 nmrd-overlay`.
- Drag the panel by its text to move it; stop it entirely with `systemctl --user disable --now nmrd-overlay`.

The overlay talks to the server over `127.0.0.1` using a secret token in `~/.config/nmrd/local_token` (mode 600), so people connecting over the internet can't use these controls. Note that a viewer who controls your desktop can of course click the overlay too.

## Configuration

`~/.config/nmrd/config.json` (all optional):

```json
{
  "bind": "127.0.0.1",
  "port": 8080,
  "fps": 30,
  "bitrate_kbps": 8000,
  "ice_servers": [
    {"urls": ["stun:stun.l.google.com:19302"]},
    {"urls": ["turn:your.turn.host:3478"], "username": "user", "credential": "pass"}
  ]
}
```

Restart after changes: `systemctl --user restart nmrd`.
Add your own TURN server in `ice_servers` if you need direct mode on very strict networks (compatibility mode already covers most cases).

### Run without installing (for testing)

```bash
python3 server.py adduser me          # creates ~/.config/nmrd/users.json
python3 server.py run --port 8080     # then open http://localhost:8080
```
Use `NMRD_CONF=/some/dir` to keep test credentials elsewhere.

## Managing the services

```bash
systemctl --user status nmrd nmrd-tunnel nmrd-watchdog      # health
systemctl --user restart nmrd                               # restart
journalctl --user -u nmrd -f                                # logs (if journald is enabled)
cat ~/.config/nmrd/url.txt                                  # current public address
./uninstall.sh                                              # remove services and files
```

## Security: please read

This gives **full control of your computer** to anyone who can log in. Treat it accordingly.

- Use a **long, unique password**. Failed logins are throttled per client address, but there is no 2-factor login yet.
- Traffic is TLS-encrypted (HTTPS/WSS via the tunnel; WebRTC uses DTLS-SRTP). Passwords are bcrypt-hashed; sessions are HttpOnly, SameSite=Strict cookies.
- The server listens on `127.0.0.1` only by default. If you change `bind` to `0.0.0.0` for LAN use, the login page is **plain HTTP** on that network.
- A quick-tunnel address is secret-by-obscurity only; the password is what protects you.
- Anything shown on the screen (including other people's data) is visible to whoever is logged in.
- Credentials and the ntfy topic are stored in `~/.config/nmrd/` (mode 600). Never commit that folder.
- Report vulnerabilities privately via GitHub's "Report a vulnerability" (Security tab).

## How it works

| File | Role |
|---|---|
| `server.py` | aiohttp web app: login, WebSocket signalling, GStreamer pipelines (`ximagesrc → x264enc → webrtcbin`, or `appsink` for compatibility mode), X11 input injection via XTest |
| `web/index.html`, `web/login.html` | Browser viewer (WebRTC / WebCodecs) and login page |
| `install.sh`, `uninstall.sh` | Per-user install via systemd user services |
| `tunnel.sh` | Cloudflare quick tunnel, writes/announces the address |
| `tailscale-setup.sh` | Optional permanent address with Tailscale Funnel |
| `watchdog.sh` | Checks the public address every minute and repairs it |

## Limitations / ideas

Linux + X11 only · no audio · no clipboard or file transfer yet · one viewer at a time · no 2FA yet.
Pull requests welcome.

## License

MIT, see [LICENSE](LICENSE).
