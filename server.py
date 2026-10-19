#!/usr/bin/env python3
"""nmrd - tiny remote-desktop server: web login + WebRTC screen + input injection (X11)."""
import asyncio, getpass, json, os, secrets, subprocess, sys, threading, time, argparse
from pathlib import Path

import bcrypt
from aiohttp import web, WSMsgType

CONF_DIR = Path(os.environ.get("NMRD_CONF", Path.home() / ".config" / "nmrd"))
USERS = CONF_DIR / "users.json"
CONFIG = CONF_DIR / "config.json"
WEB = Path(__file__).parent / "web"

DEFAULT_CONFIG = {
    "bind": "127.0.0.1",
    "port": 8080,
    "fps": 30,
    "bitrate_kbps": 8000,
    "ice_servers": [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}],
    # optional TURN fallback: {"urls": ["turn:host:3478"], "username": "u", "credential": "p"}
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text()))
    return cfg


# ---------------------------------------------------------------- users
def load_users():
    return json.loads(USERS.read_text()) if USERS.exists() else {}


def add_user(name, password):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    users = load_users()
    users[name] = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    USERS.write_text(json.dumps(users))
    USERS.chmod(0o600)


def check_user(name, password):
    h = load_users().get(name)
    # always run a hash check so timing doesn't reveal valid usernames
    h = (h or "$2b$12$" + "." * 53).encode()
    try:
        ok = bcrypt.checkpw(password.encode(), h)
    except ValueError:
        ok = False
    return ok and name in load_users()


# ---------------------------------------------------------------- input (X11 / XTest)
class Input:
    SPECIAL = {
        "Enter": "Return", "Backspace": "BackSpace", "Tab": "Tab", "Escape": "Escape", " ": "space",
        "ArrowLeft": "Left", "ArrowRight": "Right", "ArrowUp": "Up", "ArrowDown": "Down",
        "Shift": "Shift_L", "Control": "Control_L", "Alt": "Alt_L", "AltGraph": "ISO_Level3_Shift",
        "Meta": "Super_L", "OS": "Super_L", "Delete": "Delete", "Home": "Home", "End": "End",
        "PageUp": "Prior", "PageDown": "Next", "Insert": "Insert", "CapsLock": "Caps_Lock",
        "ContextMenu": "Menu", "PrintScreen": "Print", "NumLock": "Num_Lock", "ScrollLock": "Scroll_Lock",
        "Pause": "Pause",
    }

    def __init__(self):
        from Xlib import display, X, XK
        from Xlib.ext import xtest
        self.X, self.XK, self.xtest = X, XK, xtest
        self._display = display
        self.lock = threading.Lock()
        self._connect()

    def _connect(self):
        self.d = self._display.Display()
        self.root = self.d.screen().root
        g = self.root.get_geometry()
        self.w, self.h = g.width, g.height
        self.down = set()

    def _keycode(self, key, code):
        name = self.SPECIAL.get(key)
        if key in ("Shift", "Control", "Alt", "Meta") and code.endswith("Right"):
            name = {"Shift": "Shift_R", "Control": "Control_R", "Alt": "Alt_R", "Meta": "Super_R"}[key]
        if name is None and len(key) >= 1 and key.startswith("F") and key[1:].isdigit():
            name = key
        if name:
            ks = self.XK.string_to_keysym(name)
        elif len(key) == 1:
            o = ord(key)
            ks = o if o < 0x100 else 0x01000000 + o
        else:
            return 0
        return self.d.keysym_to_keycode(ks) if ks else 0

    def handle(self, m):
        try:
            self._handle(m)
        except Exception as e:  # X server restarted / connection lost: reconnect once
            print("X error, reconnecting:", e, file=sys.stderr)
            with self.lock:
                try:
                    self._connect()
                except Exception as e2:
                    print("X reconnect failed:", e2, file=sys.stderr)

    def _handle(self, m):
        X, t = self.X, m.get("t")
        with self.lock:
            if t == "mv":
                x, y = int(m["x"] * self.w), int(m["y"] * self.h)
                self.xtest.fake_input(self.d, X.MotionNotify, x=x, y=y)
            elif t in ("md", "mu"):
                b = {0: 1, 1: 2, 2: 3}.get(m["b"])
                if b:
                    self.xtest.fake_input(self.d, X.ButtonPress if t == "md" else X.ButtonRelease, b)
            elif t == "wh":
                for _ in range(min(abs(int(m["n"])), 10)):
                    b = 4 if m["n"] < 0 else 5
                    self.xtest.fake_input(self.d, X.ButtonPress, b)
                    self.xtest.fake_input(self.d, X.ButtonRelease, b)
            elif t in ("kd", "ku"):
                kc = self._keycode(m.get("k", ""), m.get("c", ""))
                if kc:
                    if t == "kd":
                        self.down.add(kc)
                    else:
                        self.down.discard(kc)
                    self.xtest.fake_input(self.d, X.KeyPress if t == "kd" else X.KeyRelease, kc)
            elif t == "ra":  # release everything (focus lost)
                for kc in list(self.down):
                    self.xtest.fake_input(self.d, X.KeyRelease, kc)
                self.down.clear()
            self.d.flush()


# ---------------------------------------------------------------- remote cursor shape -> browser CSS cursor
CURSOR_CSS = {
    "left_ptr": "default", "arrow": "default", "default": "default", "top_left_arrow": "default",
    "xterm": "text", "text": "text", "ibeam": "text",
    "hand1": "pointer", "hand2": "pointer", "pointer": "pointer", "pointing_hand": "pointer",
    "watch": "wait", "wait": "wait", "left_ptr_watch": "progress", "progress": "progress",
    "fleur": "move", "move": "move", "all-scroll": "all-scroll", "size_all": "move",
    "crosshair": "crosshair", "cross": "crosshair", "tcross": "crosshair",
    "sb_h_double_arrow": "ew-resize", "h_double_arrow": "ew-resize", "ew-resize": "ew-resize", "col-resize": "col-resize",
    "left_side": "ew-resize", "right_side": "ew-resize", "split_h": "col-resize", "size_hor": "ew-resize",
    "sb_v_double_arrow": "ns-resize", "v_double_arrow": "ns-resize", "ns-resize": "ns-resize", "row-resize": "row-resize",
    "top_side": "ns-resize", "bottom_side": "ns-resize", "split_v": "row-resize", "size_ver": "ns-resize",
    "top_left_corner": "nwse-resize", "bottom_right_corner": "nwse-resize", "nwse-resize": "nwse-resize", "size_fdiag": "nwse-resize",
    "top_right_corner": "nesw-resize", "bottom_left_corner": "nesw-resize", "nesw-resize": "nesw-resize", "size_bdiag": "nesw-resize",
    "question_arrow": "help", "help": "help", "whats_this": "help",
    "not-allowed": "not-allowed", "crossed_circle": "not-allowed", "forbidden": "not-allowed", "no-drop": "no-drop",
    "grab": "grab", "openhand": "grab", "grabbing": "grabbing", "closedhand": "grabbing", "dnd-none": "grabbing",
    "copy": "copy", "dnd-copy": "copy", "alias": "alias", "dnd-link": "alias", "context-menu": "context-menu",
    "zoom-in": "zoom-in", "zoom-out": "zoom-out", "vertical-text": "vertical-text", "cell": "cell",
}
CURSOR = {"css": "default"}   # latest shape; anything unknown or hidden falls back to a visible arrow


def cursor_watcher(on_change):
    """Poll the X server's current cursor (XFixes) and report its CSS equivalent when it changes."""
    import ctypes
    try:
        x11, xf = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXfixes.so.3")

        class Img(ctypes.Structure):
            _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort),
                        ("xhot", ctypes.c_ushort), ("yhot", ctypes.c_ushort), ("serial", ctypes.c_ulong),
                        ("pixels", ctypes.c_void_p), ("atom", ctypes.c_ulong), ("name", ctypes.c_char_p)]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        xf.XFixesGetCursorImage.restype = ctypes.POINTER(Img)
        xf.XFixesGetCursorImage.argtypes = [ctypes.c_void_p]
        x11.XFree.argtypes = [ctypes.c_void_p]
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            return
    except OSError as e:
        print("cursor sync unavailable:", e, file=sys.stderr)
        return
    last = None
    while True:
        time.sleep(0.05)
        try:
            img = xf.XFixesGetCursorImage(dpy)
            if not img:
                continue
            name = (img.contents.name or b"").decode("latin-1").lower()
            x11.XFree(img)
            css = CURSOR_CSS.get(name, "default")
            if css != last:
                last, CURSOR["css"] = css, css
                on_change(css)
        except Exception as e:
            print("cursor watcher error:", e, file=sys.stderr)
            time.sleep(1)


# ---------------------------------------------------------------- WebRTC session (GStreamer)
import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstWebRTC, GstSdp, GstVideo, GLib

Gst.init(None)
threading.Thread(target=GLib.MainLoop().run, daemon=True).start()


class Session:
    def __init__(self, ws, loop, cfg, inp):
        self.ws, self.loop, self.cfg, self.inp = ws, loop, cfg, inp
        fps = cfg["fps"]
        self.pipe = Gst.parse_launch(
            f"ximagesrc use-damage=false show-pointer=false do-timestamp=true ! "
            f"video/x-raw,framerate={fps}/1 ! videoconvert n-threads=2 ! video/x-raw,format=I420 ! queue max-size-buffers=2 leaky=downstream ! "
            f"x264enc name=enc tune=zerolatency speed-preset=ultrafast bitrate={cfg['bitrate_kbps']} "
            f"key-int-max={fps*2} bframes=0 threads=4 sliced-threads=true vbv-buf-capacity=300 ! "
            f"video/x-h264,profile=constrained-baseline ! h264parse config-interval=-1 ! "
            f"rtph264pay config-interval=-1 aggregate-mode=zero-latency pt=96 ! "
            f"application/x-rtp,media=video,encoding-name=H264,payload=96 ! "
            f"webrtcbin name=wb bundle-policy=max-bundle latency=0"
        )
        self.wb = self.pipe.get_by_name("wb")
        self.enc = self.pipe.get_by_name("enc")
        self.wb.connect("on-negotiation-needed", self.on_negotiation)
        self.wb.connect("on-ice-candidate", self.on_ice)
        for s in cfg["ice_servers"]:
            for u in s["urls"]:
                if u.startswith("stun:"):
                    self.wb.set_property("stun-server", "stun://" + u[5:])
                elif u.startswith("turn:"):
                    self.wb.emit("add-turn-server", f"turn://{s['username']}:{s['credential']}@{u[5:]}")
        self.pipe.set_state(Gst.State.PLAYING)  # webrtcbin refuses data channels while closed (NULL)
        opt = lambda **kw: Gst.Structure.new_from_string("o," + ",".join(f"{k.replace('_','-')}={v}" for k, v in kw.items()))
        self.chans = [
            self.wb.emit("create-data-channel", "mouse", opt(ordered="false", max_retransmits=0)),
            self.wb.emit("create-data-channel", "key", opt(ordered="true")),
        ]
        for c in self.chans:
            c.connect("on-message-string", self.on_msg)

    # signaling out
    def send(self, obj):
        asyncio.run_coroutine_threadsafe(self.ws.send_json(obj), self.loop)

    def on_negotiation(self, wb):
        p = Gst.Promise.new_with_change_func(self._offer_ready, wb, None)
        wb.emit("create-offer", None, p)

    def _offer_ready(self, promise, wb, _):
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        if offer is None or offer.sdp is None:
            print("create-offer failed:", reply.to_string(), file=sys.stderr)
            return
        wb.emit("set-local-description", offer, Gst.Promise.new())
        self.send({"type": "offer", "sdp": offer.sdp.as_text()})

    def on_ice(self, wb, mline, cand):
        self.send({"type": "ice", "mline": mline, "candidate": cand})

    # signaling in
    def on_answer(self, sdp):
        _, msg = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(sdp.encode(), msg)
        ans = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, msg)
        self.wb.emit("set-remote-description", ans, Gst.Promise.new())

    def on_remote_ice(self, mline, cand):
        if cand:
            self.wb.emit("add-ice-candidate", mline, cand)

    def on_msg(self, chan, text):
        try:
            self.handle_input(json.loads(text))
        except Exception as e:
            print("input error:", e, file=sys.stderr)

    def handle_input(self, m):
        if m.get("t") == "q":  # quality change
            self.enc.set_property("bitrate", max(500, min(30000, int(m["kbps"]))))
        else:
            self.inp.handle(m)

    def close(self):
        self.inp.handle({"t": "ra"})
        self.pipe.set_state(Gst.State.NULL)


class WsSession:
    """Fallback for networks where WebRTC can't connect: H.264 over the WebSocket (TCP/HTTPS)."""

    def __init__(self, ws, loop, cfg, inp):
        self.ws, self.loop, self.inp = ws, loop, inp
        self.pending, self.need_key, self.closed = 0, True, False
        fps = min(cfg["fps"], 30)
        self.pipe = Gst.parse_launch(
            f"ximagesrc use-damage=false show-pointer=false do-timestamp=true ! "
            f"video/x-raw,framerate={fps}/1 ! videoconvert n-threads=2 ! video/x-raw,format=I420 ! queue max-size-buffers=2 leaky=downstream ! "
            f"x264enc name=enc tune=zerolatency speed-preset=ultrafast bitrate=4000 "
            f"key-int-max={fps*2} bframes=0 threads=4 sliced-threads=true vbv-buf-capacity=300 ! "
            f"video/x-h264,profile=constrained-baseline,stream-format=byte-stream,alignment=au ! "
            f"appsink name=sink emit-signals=true sync=false max-buffers=2 drop=true"
        )
        self.enc = self.pipe.get_by_name("enc")
        self.pipe.get_by_name("sink").connect("new-sample", self.on_sample)
        self.pipe.set_state(Gst.State.PLAYING)

    def on_sample(self, sink):
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        key = not buf.has_flags(Gst.BufferFlags.DELTA_UNIT)
        if self.closed:
            return Gst.FlowReturn.OK
        if self.pending > 3 and not key:       # link is slower than the video: drop, resync on next key frame
            self.need_key = True
            return Gst.FlowReturn.OK
        if self.need_key and not key:
            return Gst.FlowReturn.OK
        if key:
            self.need_key = False
        data = (b"\x01" if key else b"\x00") + buf.extract_dup(0, buf.get_size())
        self.pending += 1
        fut = asyncio.run_coroutine_threadsafe(self.ws.send_bytes(data), self.loop)
        fut.add_done_callback(lambda _: setattr(self, "pending", max(0, self.pending - 1)))
        if self.pending > 3:
            self.enc.send_event(GstVideo.video_event_new_upstream_force_key_unit(Gst.CLOCK_TIME_NONE, True, 0))
        return Gst.FlowReturn.OK

    def handle_input(self, m):
        if m.get("t") == "q":
            self.enc.set_property("bitrate", max(500, min(30000, int(m["kbps"]))))
        else:
            self.inp.handle(m)

    def close(self):
        self.closed = True
        self.inp.handle({"t": "ra"})
        self.pipe.set_state(Gst.State.NULL)


# ---------------------------------------------------------------- web app
SESSIONS = {}          # token -> (expiry, username)
FAILS = {}             # ip -> (count, locked_until)
ACTIVE = {"s": None}   # one viewer at a time
VIEWER = {"info": None, "ws": None, "token": None}   # who is connected right now (shown by the desktop overlay)
STATE = {"paused": False}                            # owner switch: refuse all new viewers
LOCAL_TOKEN = CONF_DIR / "local_token"               # secret shared only with the desktop overlay (same user)


def client_ip(req):
    if req.remote in ("127.0.0.1", "::1"):
        fwd = req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        return req.headers.get("CF-Connecting-IP") or fwd or req.remote
    return req.remote


def authed(req):
    ent = SESSIONS.get(req.cookies.get("nmrd"))
    return bool(ent and ent[0] > time.time())


def session_user(req):
    return SESSIONS[req.cookies["nmrd"]][1]


def notify(title, body):
    try:
        subprocess.Popen(["notify-send", "-a", "nmrd", "-u", "critical", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def local_ok(req):
    """Owner-only endpoints: need the secret token file readable only by the desktop user."""
    try:
        return secrets.compare_digest(req.headers.get("X-Local-Token", ""), LOCAL_TOKEN.read_text().strip())
    except OSError:
        return False


async def local_status(req):
    if not local_ok(req):
        raise web.HTTPForbidden()
    return web.json_response({"viewer": VIEWER["info"], "paused": STATE["paused"]})


async def local_kick(req):
    if not local_ok(req):
        raise web.HTTPForbidden()
    info, ws, tok = VIEWER["info"], VIEWER["ws"], VIEWER["token"]
    if ws is None:
        return web.json_response({"ok": True, "kicked": None})
    SESSIONS.pop(tok, None)               # their login is revoked: they must sign in again
    await ws.close()
    return web.json_response({"ok": True, "kicked": info and info["user"]})


async def local_pause(req):
    if not local_ok(req):
        raise web.HTTPForbidden()
    STATE["paused"] = bool((await req.json()).get("paused"))
    if STATE["paused"] and VIEWER["ws"] is not None:
        SESSIONS.pop(VIEWER["token"], None)
        await VIEWER["ws"].close()
    return web.json_response({"ok": True, "paused": STATE["paused"]})


async def api_me(req):
    return web.json_response({"ok": True}) if authed(req) else web.json_response({"ok": False}, status=401)


async def index(req):
    if not authed(req):
        raise web.HTTPFound("/login")
    return web.FileResponse(WEB / "index.html")


async def login_page(req):
    return web.FileResponse(WEB / "login.html")


async def login(req):
    ip = client_ip(req)
    cnt, until = FAILS.get(ip, (0, 0))
    if until > time.time():
        return web.json_response({"error": "Too many attempts, wait a bit."}, status=429)
    data = await req.json()
    ok = await asyncio.get_running_loop().run_in_executor(None, check_user, str(data.get("user", "")), str(data.get("password", "")))
    if not ok:
        cnt += 1
        FAILS[ip] = (cnt, time.time() + min(2 ** cnt, 300) if cnt >= 3 else 0)
        await asyncio.sleep(1)
        return web.json_response({"error": "Wrong username or password."}, status=401)
    FAILS.pop(ip, None)
    tok = secrets.token_urlsafe(32)
    SESSIONS[tok] = (time.time() + 12 * 3600, str(data.get('user')))
    r = web.json_response({"ok": True})
    r.set_cookie("nmrd", tok, httponly=True, samesite="Strict", max_age=12 * 3600,
                 secure=req.headers.get("X-Forwarded-Proto", req.scheme) == "https")
    return r


async def logout(req):
    SESSIONS.pop(req.cookies.get("nmrd"), None)
    r = web.json_response({"ok": True})
    r.del_cookie("nmrd")
    return r


async def ws_handler(req):
    if not authed(req):
        raise web.HTTPUnauthorized()
    origin = req.headers.get("Origin")
    if origin and origin.split("://", 1)[-1] != req.host:
        raise web.HTTPForbidden()
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(req)
    if STATE["paused"]:
        await ws.send_json({"type": "paused"})
        await ws.close()
        return ws
    cfg, loop = req.app["cfg"], asyncio.get_running_loop()
    me = {"user": session_user(req), "ip": client_ip(req), "mode": None, "since": int(time.time()),
          "agent": req.headers.get("User-Agent", "")[:120]}
    if ACTIVE["s"]:                      # newest viewer wins
        ACTIVE["s"].close()
        ACTIVE["s"] = None
    sess = None
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m["type"] == "start":
                if sess:
                    sess.close()
                inp = req.app["inp"]
                await ws.send_json({"type": "config", "mode": m.get("mode", "webrtc"),
                                    "ice_servers": cfg["ice_servers"], "w": inp.w, "h": inp.h})
                cls = WsSession if m.get("mode") == "ws" else Session
                sess = ACTIVE["s"] = cls(ws, loop, cfg, inp)
                first = VIEWER["ws"] is not ws
                me["mode"] = "compatibility" if m.get("mode") == "ws" else "direct"
                if first:
                    me["since"] = int(time.time())
                    notify("Remote viewer connected", f"{me['user']} from {me['ip']}")
                VIEWER.update(info=me, ws=ws, token=req.cookies.get("nmrd"))
                await ws.send_json({"type": "cursor", "css": CURSOR["css"]})
            elif m["type"] == "answer" and isinstance(sess, Session):
                sess.on_answer(m["sdp"])
            elif m["type"] == "ice" and isinstance(sess, Session):
                sess.on_remote_ice(m["mline"], m["candidate"])
            elif m["type"] == "in" and sess:
                sess.handle_input(m["m"])
    finally:
        if sess:
            sess.close()
            if ACTIVE["s"] is sess:
                ACTIVE["s"] = None
        if VIEWER["ws"] is ws:
            VIEWER.update(info=None, ws=None, token=None)
            notify("Remote viewer disconnected", me["user"])
    return ws


def push_cursor(css):
    sess = ACTIVE["s"]
    if sess:
        asyncio.run_coroutine_threadsafe(sess.ws.send_json({"type": "cursor", "css": css}), sess.loop)


def make_app(cfg):
    threading.Thread(target=cursor_watcher, args=(push_cursor,), daemon=True).start()
    app = web.Application()
    app["cfg"], app["inp"] = cfg, Input()
    app.add_routes([web.get("/", index), web.get("/login", login_page), web.post("/api/login", login),
                    web.post("/api/logout", logout), web.get("/api/me", api_me), web.get("/ws", ws_handler),
                    web.get("/local/status", local_status), web.post("/local/kick", local_kick),
                    web.post("/local/pause", local_pause)])
    return app


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("adduser"); a.add_argument("name"); a.add_argument("--password")
    sub.add_parser("run")
    r = sub.choices["run"]; r.add_argument("--bind"); r.add_argument("--port", type=int)
    args = ap.parse_args()
    if args.cmd == "adduser":
        pw = args.password or getpass.getpass("Password: ")
        if not args.password and pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords differ")
        if len(pw) < 8:
            sys.exit("Use at least 8 characters")
        add_user(args.name, pw)
        print(f"User '{args.name}' saved.")
        return
    cfg = load_config()
    if args.cmd == "run":
        cfg["bind"] = args.bind or cfg["bind"]
        cfg["port"] = args.port or cfg["port"]
    if not load_users():
        sys.exit("No users yet. Run: server.py adduser <name>")
    if not LOCAL_TOKEN.exists():
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_TOKEN.write_text(secrets.token_urlsafe(32))
        LOCAL_TOKEN.chmod(0o600)
    web.run_app(make_app(cfg), host=cfg["bind"], port=cfg["port"], print=lambda *_: print(f"nmrd listening on {cfg['bind']}:{cfg['port']}", flush=True))


if __name__ == "__main__":
    main()
