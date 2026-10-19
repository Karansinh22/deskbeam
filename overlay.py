#!/usr/bin/env python3
"""nmrd desktop overlay: a small always-on-top floater showing who is connected, with Disconnect / Pause."""
import json, os, signal, threading, time, urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

CONF = Path(os.environ.get("NMRD_CONF", Path.home() / ".config" / "nmrd"))


def port():
    try:
        return json.loads((CONF / "config.json").read_text()).get("port", 8080)
    except (OSError, ValueError):
        return 8080


def call(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port()}{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Local-Token": (CONF / "local_token").read_text().strip()})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read())


CSS = b"""
window { background: rgba(24,26,32,0.93); border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); }
label { color: #e8eaed; font-size: 13px; }
button.dot { background: transparent; border: none; padding: 0 4px; box-shadow: none; }
button.dot label { font-size: 18px; }
button.dot-idle label { color: #3ecf6b; } button.dot-live label { color: #ff5252; }
button.dot-pause label { color: #f5b942; } button.dot-off label { color: #888; }
button { padding: 3px 10px; border-radius: 8px; background: #2c303a; color: #e8eaed; border: 1px solid #444; }
button.danger { background: #b3261e; border-color: #b3261e; color: white; }
"""


class Overlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)       # no decorations, no taskbar entry
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_accept_focus(False)
        self.stick()
        prov = Gtk.CssProvider(); prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(spacing=10, margin=8)
        self.dot = Gtk.Button(label="●")
        self.dot.get_style_context().add_class("dot")
        self.dot.set_relief(Gtk.ReliefStyle.NONE)
        self.dot.set_tooltip_text("Click to hide / show the panel (SIGUSR1 also toggles)")
        self.dot.connect("clicked", lambda *_: self.set_collapsed(not self.collapsed))
        self.collapsed = (CONF / "overlay_collapsed").exists()
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: self.set_collapsed(not self.collapsed) or True)
        self.text = Gtk.Label(label="…")
        self.kick = Gtk.Button(label="Disconnect"); self.kick.get_style_context().add_class("danger")
        self.pause = Gtk.Button(label="Pause")
        self.kick.connect("clicked", lambda *_: self.act("/local/kick", {}))
        self.pause.connect("clicked", self.toggle_pause)
        handle = Gtk.EventBox(); handle.add(Gtk.Box(spacing=8))
        handle.get_child().pack_start(self.text, 0, 0, 0)
        handle.connect("button-press-event", lambda w, e: self.begin_move_drag(e.button, int(e.x_root), int(e.y_root), e.time))
        box.pack_start(self.dot, 0, 0, 0); box.pack_start(handle, 0, 0, 0); box.pack_start(self.kick, 0, 0, 0); box.pack_start(self.pause, 0, 0, 0)
        self.add(box)
        self.paused = False
        self.state = None
        geo = self.screen_box()
        self.right, self.top, self._lock = geo.x + geo.width - 20, geo.y + 52, True
        self.connect("configure-event", self.on_configure)
        self.show_all()
        self.place()
        GLib.timeout_add(1000, self.keep_visible)
        GLib.timeout_add(1000, self.poll)
        GLib.timeout_add(1000, self.tick)

    def screen_box(self, x=None, y=None):
        """Geometry of the monitor the panel is on (primary if unknown)."""
        disp = Gdk.Display.get_default()
        mon = disp.get_monitor_at_point(x, y) if x is not None else None
        return (mon or disp.get_primary_monitor() or disp.get_monitor(0)).get_geometry()

    def clamp(self, x, y, w, h):
        g = self.screen_box(x + w // 2, y)
        return (max(g.x, min(x, g.x + g.width - w)), max(g.y + 32, min(y, g.y + g.height - h - 8)))

    def place(self):
        """Anchor: right edge + top. Window grows/shrinks leftwards and is always clamped onto the screen."""
        w, h = self.get_preferred_width()[1], self.get_preferred_height()[1]
        self._lock = True
        self.resize(1, 1)
        x, y = self.clamp(self.right - w, self.top, w, h)
        self.right, self.top = x + w, y
        self.move(x, y)
        GLib.timeout_add(300, self._unlock)

    def keep_visible(self):
        """Safety net: if the panel ever ends up off-screen (drag, monitor change), pull it back."""
        if not self._lock:
            x, y = self.get_position(); w, h = self.get_size()
            nx, ny = self.clamp(x, y, w, h)
            if (nx, ny) != (x, y):
                self.right, self.top = nx + w, ny
                self.move(nx, ny)
        return True

    def _unlock(self):
        self._lock = False
        return False

    def on_configure(self, win, ev):
        if not self._lock:                      # user dragged it: remember the new anchor (kept on-screen)
            x, y = self.clamp(ev.x, ev.y, ev.width, ev.height)
            self.right, self.top = x + ev.width, y

    def set_collapsed(self, value):
        self.collapsed = value
        flag = CONF / "overlay_collapsed"
        try:
            flag.touch() if value else flag.unlink(missing_ok=True)
        except OSError:
            pass
        self.render()

    def act(self, path, body):
        threading.Thread(target=lambda: self._call(path, body), daemon=True).start()

    def _call(self, path, body):
        try:
            call(path, body)
        except Exception:
            pass

    def toggle_pause(self, *_):
        self.act("/local/pause", {"paused": not self.paused})

    def poll(self):
        def work():
            try:
                st = call("/local/status")
            except Exception:
                st = None
            GLib.idle_add(self.apply, st)
        threading.Thread(target=work, daemon=True).start()
        return True

    def tick(self):
        self.render()
        return True

    def apply(self, st):
        self.state = st
        self.paused = bool(st and st["paused"])
        self.render()

    def render(self):
        st, ctx = self.state, self.dot.get_style_context()
        for c in ("dot-idle", "dot-live", "dot-pause", "dot-off"):
            ctx.remove_class(c)
        if st is None:
            ctx.add_class("dot-off"); self.text.set_text("Remote server not running")
            self.kick.hide(); self.pause.hide()
        elif st["viewer"]:
            v = st["viewer"]; secs = max(0, int(time.time()) - v["since"])
            ctx.add_class("dot-live")
            self.text.set_text(f"{v['user']} is viewing / controlling · {v['ip']} · {v.get('mode') or '…'} · {secs//60}:{secs%60:02d}")
            self.kick.show(); self.pause.show(); self.pause.set_label("Pause")
        elif st["paused"]:
            ctx.add_class("dot-pause"); self.text.set_text("Remote access paused")
            self.kick.hide(); self.pause.show(); self.pause.set_label("Resume")
        else:
            ctx.add_class("dot-idle"); self.text.set_text("Remote access on · nobody connected")
            self.kick.hide(); self.pause.show(); self.pause.set_label("Pause")
        if self.collapsed:                       # only the coloured dot stays: red = someone is connected
            self.text.hide(); self.kick.hide(); self.pause.hide()
        else:
            self.text.show()
        label = str(self.collapsed) + self.text.get_text().rsplit(" · ", 1)[0] + str(self.kick.get_visible()) + self.pause.get_label()
        if label != getattr(self, "_label", None):
            self._label = label
            GLib.idle_add(self.place)


if __name__ == "__main__":
    Overlay()
    Gtk.main()
