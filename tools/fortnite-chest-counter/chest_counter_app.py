"""
Chest Counter: desktop app.

Start it with   python chest_counter_app.py
or double-click ChestCounter.pyw (no console window), or the built .exe.

All counting/detection logic lives in chest_counter.py (Engine); this file is
only the user interface.
"""
from __future__ import annotations

import math
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

import chest_counter as cc

APP_NAME = "Chest Counter"
FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"

C = {
    "bg": "#0d0f14",
    "side": "#12151c",
    "card": "#181c25",
    "card2": "#202532",
    "hover": "#262c3a",
    "border": "#272d3b",
    "text": "#e9ecf2",
    "muted": "#8a93a6",
    "faint": "#596174",
    "gold": "#ffc94d",
    "gold_dark": "#c99a2e",
    "blue": "#5cc8ff",
    "green": "#4fe0a0",
    "green_dim": "#1f4f3c",
    "red": "#ff6b6b",
    "amber": "#ffb347",
}


def F(size: int, weight: str = "normal"):
    return (FONT, size, weight)


def fmt_int(n) -> str:
    return f"{int(round(n)):,}"


def ago(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%d %b  %H:%M")


def duration(a, b) -> str:
    if not a or not b or b < a:
        return "-"
    secs = int(b - a)
    return f"{secs // 60}:{secs % 60:02d}"


def make_icon(size: int = 64) -> Image.Image:
    """A small treasure-chest icon drawn in code (no asset files needed)."""
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = lambda *v: [round(x * s) for x in v]  # noqa: E731
    d.rounded_rectangle(r(4, 4, 60, 60), radius=round(14 * s), fill="#1a1e27")
    d.rounded_rectangle(r(12, 18, 52, 33), radius=round(7 * s), fill="#ffc94d")   # lid
    d.rectangle(r(12, 30, 52, 50), fill="#e0a930")                               # body
    d.rectangle(r(12, 33, 52, 36), fill="#8a5a12")                               # seam
    d.rectangle(r(28, 29, 36, 41), fill="#5cc8ff")                               # lock
    d.rectangle(r(31, 34, 33, 38), fill="#12151c")
    return img


# --------------------------------------------------------------------------- #
# Small reusable widgets
# --------------------------------------------------------------------------- #
class Card(ctk.CTkFrame):
    def __init__(self, master, **kw):
        kw.setdefault("fg_color", C["card"])
        kw.setdefault("corner_radius", 14)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", C["border"])
        super().__init__(master, **kw)


class StatCard(Card):
    def __init__(self, master, title: str, color: str, value_size: int = 34):
        super().__init__(master)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=title.upper(), font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        self.value = ctk.CTkLabel(self, text="-", font=F(value_size, "bold"), text_color=color, anchor="w")
        self.value.grid(row=1, column=0, sticky="ew", padx=18)
        self.sub = ctk.CTkLabel(self, text="", font=F(12), text_color=C["muted"], anchor="w", justify="left")
        self.sub.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))

    def set(self, value: str, sub: str = ""):
        if self.value.cget("text") != value:
            self.value.configure(text=value)
        if self.sub.cget("text") != sub:
            self.sub.configure(text=sub)


class Pill(ctk.CTkFrame):
    """Status line in the sidebar: coloured dot + label + value."""

    def __init__(self, master, label: str):
        super().__init__(master, fg_color="transparent")
        self.dot = ctk.CTkLabel(self, text="●", font=F(12), text_color=C["faint"], width=14)
        self.dot.pack(side="left")
        ctk.CTkLabel(self, text=label, font=F(12), text_color=C["muted"]).pack(side="left", padx=(4, 0))
        self.value = ctk.CTkLabel(self, text="", font=F(12, "bold"), text_color=C["text"])
        self.value.pack(side="right")

    def set(self, text: str, color: str):
        self.dot.configure(text_color=color)
        if self.value.cget("text") != text:
            self.value.configure(text=text)


class Page(ctk.CTkFrame):
    title = ""
    subtitle = ""

    def __init__(self, master, app: "ChestCounterApp"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=self.title, font=F(26, "bold"), text_color=C["text"], anchor="w").grid(
            row=0, column=0, sticky="w")
        self.subtitle_label = ctk.CTkLabel(head, text=self.subtitle, font=F(13), text_color=C["muted"], anchor="w")
        self.subtitle_label.grid(row=1, column=0, sticky="w")
        self.head_right = ctk.CTkFrame(head, fg_color="transparent", width=1, height=1)
        self.head_right.grid(row=0, column=1, rowspan=2, sticky="e")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.grid_rowconfigure(1, weight=1)

    def refresh(self, state: dict):  # called ~4x per second while visible
        pass


def center_over(win, parent, w: int | None = None, h: int | None = None):
    win.update_idletasks()
    w = w or win.winfo_reqwidth()
    h = h or win.winfo_reqheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


def button(master, text, command, kind="secondary", **kw):
    styles = {
        "primary": dict(fg_color=C["gold"], hover_color=C["gold_dark"], text_color="#1a1406"),
        "secondary": dict(fg_color=C["card2"], hover_color=C["hover"], text_color=C["text"]),
        "danger": dict(fg_color="transparent", hover_color="#3a1e22", text_color=C["red"],
                       border_width=1, border_color="#5a2a30"),
        "ghost": dict(fg_color="transparent", hover_color=C["hover"], text_color=C["muted"]),
    }
    opts = dict(height=38, corner_radius=10, font=F(13, "bold"))
    opts.update(styles[kind])
    opts.update(kw)
    return ctk.CTkButton(master, text=text, command=command, **opts)


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Live chest count, current match and token chance"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.pause_btn = button(self.head_right, "Pause detection", app.toggle_auto, width=160)
        self.pause_btn.pack(side="right")

        b = self.body
        for i in range(4):
            b.grid_columnconfigure(i, weight=1, uniform="stat")
        b.grid_rowconfigure(2, weight=1)

        self.c_total = StatCard(b, "Your chests", C["gold"])
        self.c_squad = StatCard(b, "Squad estimate", C["green"])
        self.c_match = StatCard(b, "This match", C["blue"])
        self.c_session = StatCard(b, "This session", C["text"])
        for i, c in enumerate((self.c_total, self.c_squad, self.c_match, self.c_session)):
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0), pady=(0, 12))

        # Token chance card
        tok = Card(b)
        tok.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 12))
        tok.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tok, text="TOKEN CHANCE  ·  TEAM", font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 0))
        self.tok_value = ctk.CTkLabel(tok, text="-", font=F(44, "bold"), text_color=C["green"], anchor="w")
        self.tok_value.grid(row=1, column=0, sticky="w", padx=18)
        self.tok_bar = ctk.CTkProgressBar(tok, height=10, corner_radius=5, progress_color=C["green"],
                                          fg_color=C["card2"])
        self.tok_bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(4, 8))
        self.tok_own = ctk.CTkLabel(tok, text="", font=F(12), text_color=C["muted"], anchor="w")
        self.tok_own.grid(row=3, column=0, sticky="w", padx=18)
        self.tok_next = ctk.CTkLabel(tok, text="", font=F(12), text_color=C["text"], anchor="w", justify="left")
        self.tok_next.grid(row=4, column=0, sticky="w", padx=18, pady=(2, 16))

        # Controls card
        ctl = Card(b)
        ctl.grid(row=1, column=2, columnspan=2, sticky="nsew", padx=(8, 0), pady=(0, 12))
        ctl.grid_columnconfigure((0, 1), weight=1, uniform="ctl")
        ctk.CTkLabel(ctl, text="CONTROLS", font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        hk = app.cfg["hotkeys"]
        self.ctl_buttons = {
            "plus": button(ctl, f"+1 chest   {hk['increment'].upper()}", lambda: app.engine.count("manual", +1), "primary"),
            "minus": button(ctl, f"−1 chest   {hk['decrement'].upper()}", lambda: app.engine.count("manual", -1)),
            "new": button(ctl, f"New match   {hk['new_match'].upper()}", lambda: app.engine.new_match("button")),
            "end": button(ctl, "End match", lambda: app.engine.finish_match("button")),
            "reset": button(ctl, f"Reset session   {hk['reset_session'].upper()}", app.engine.reset_session, "ghost"),
            "past": button(ctl, "Add past chests…", app.open_add_dialog),
        }
        order = ["plus", "minus", "new", "end", "past", "reset"]
        for i, key in enumerate(order):
            self.ctl_buttons[key].grid(row=1 + i // 2, column=i % 2, sticky="ew",
                                       padx=(18, 4) if i % 2 == 0 else (4, 18), pady=4)
        ctk.CTkLabel(ctl, text="Hotkeys work in-game, even when this window isn't focused.",
                     font=F(11), text_color=C["faint"], anchor="w").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=18, pady=(6, 14))

        # Activity feed
        feed = Card(b)
        feed.grid(row=2, column=0, columnspan=4, sticky="nsew")
        feed.grid_columnconfigure(0, weight=1)
        feed.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(feed, text="ACTIVITY", font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, sticky="w", padx=18, pady=(14, 4))
        self.feed = ctk.CTkTextbox(feed, fg_color=C["card"], text_color=C["text"], font=F(12),
                                   border_width=0, wrap="word", activate_scrollbars=True)
        self.feed.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        tb = self.feed._textbox
        for tag, color in (("count", C["gold"]), ("match", C["blue"]), ("warn", C["red"]),
                           ("info", C["muted"]), ("time", C["faint"])):
            tb.tag_config(tag, foreground=color)
        self.feed.configure(state="disabled")

    def log(self, kind: str, text: str):
        self.feed.configure(state="normal")
        self.feed._textbox.insert("1.0", f"{text}\n", (kind,))
        self.feed._textbox.insert("1.0", f"{time.strftime('%H:%M:%S')}   ", ("time",))
        # keep the feed short
        lines = int(self.feed._textbox.index("end-1c").split(".")[0])
        if lines > 300:
            self.feed._textbox.delete("300.0", "end")
        self.feed.configure(state="disabled")

    def refresh(self, s: dict):
        mult = s["squad_multiplier"]
        detected = s["opened"] + s["broken"]
        self.c_total.set(fmt_int(s["total"]), f"detected {detected:,} · added {s['imported'] + s['manual']:,}")
        self.c_squad.set(f"~{fmt_int(s['squad_total'])}", f"your chests × {mult:g}")
        if s["match_chests"] is None:
            self.c_match.set("Lobby", "next chest or match start begins one")
        else:
            self.c_match.set(str(s["match_chests"]),
                             f"match #{s['match_number']} · squad ~{fmt_int(s['squad_match_chests'])}")
        avg = "-" if s["avg_per_match"] is None else f"{s['avg_per_match']:.1f}"
        self.c_session.set(str(s["session"]), f"avg {avg} per match · best {s['best_match']}")

        p = s["squad_chance"]
        self.tok_value.configure(text=cc.format_chance(p))
        self.tok_bar.set(min(1.0, p))
        odds = self.app.cfg["token_odds"]
        self.tok_own.configure(text=f"Own chests only: {cc.format_chance(s['token_chance'])}   ·   1 in {odds:,} per chest")
        nxt = next((t for t in (0.10, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99) if t > p), None)
        if nxt is None or mult <= 0:
            self.tok_next.configure(text="")
        else:
            need_own = math.ceil(cc.chests_for_chance(nxt, odds) / mult)
            left = max(0, need_own - s["total"])
            extra = ""
            if s["avg_per_match"]:
                extra = f"  ·  ~{math.ceil(left / s['avg_per_match']):,} matches"
            self.tok_next.configure(text=f"Next: {nxt * 100:g}% in {left:,} more of your chests{extra}")

        if s["calibrated"]:
            self.pause_btn.configure(state="normal",
                                     text="Resume detection" if not s["auto_enabled"] else "Pause detection")
        else:
            self.pause_btn.configure(state="normal", text="Calibrate first →")


# --------------------------------------------------------------------------- #
# Matches
# --------------------------------------------------------------------------- #
class MatchesPage(Page):
    title = "Matches"
    subtitle = "Chests per match, detected from Fortnite's log (or F12)"

    def __init__(self, master, app):
        super().__init__(master, app)
        b = self.body
        for i in range(4):
            b.grid_columnconfigure(i, weight=1, uniform="m")
        b.grid_rowconfigure(2, weight=1)
        self.c_played = StatCard(b, "Matches tracked", C["text"], 28)
        self.c_avg = StatCard(b, "Average", C["blue"], 28)
        self.c_best = StatCard(b, "Best", C["gold"], 28)
        self.c_last = StatCard(b, "Last match", C["text"], 28)
        for i, c in enumerate((self.c_played, self.c_avg, self.c_best, self.c_last)):
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0), pady=(0, 12))

        chart = Card(b)
        chart.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(0, 12))
        chart.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(chart, text="LAST 30 MATCHES", font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, sticky="w", padx=18, pady=(14, 0))
        self.canvas = ctk.CTkCanvas(chart, height=190, bg=C["card"], highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 12))
        self.canvas.bind("<Configure>", lambda _e: self._draw_chart(force=True))

        table = Card(b)
        table.grid(row=2, column=0, columnspan=4, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(2, weight=1)
        head = ctk.CTkFrame(table, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 4))
        self.cols = (("Match", 80), ("Started", 150), ("Length", 90), ("Chests", 90), ("Squad est.", 110))
        for i, (name, w) in enumerate(self.cols):
            head.grid_columnconfigure(i, minsize=w, weight=1 if i == 1 else 0)
            ctk.CTkLabel(head, text=name.upper(), font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
                row=0, column=i, sticky="w")
        self.rows = ctk.CTkScrollableFrame(table, fg_color="transparent")
        self.rows.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 10))
        for i, (_n, w) in enumerate(self.cols):
            self.rows.grid_columnconfigure(i, minsize=w, weight=1 if i == 1 else 0)
        self._key = None
        self._state = None

    def refresh(self, s: dict):
        self._state = s
        avg = "-" if s["avg_per_match"] is None else f"{s['avg_per_match']:.1f}"
        self.c_played.set(f"{s['matches_played']:,}", "finished matches")
        self.c_avg.set(avg, f"squad ~{s['avg_per_match'] * s['squad_multiplier']:.0f}" if s["avg_per_match"] else "chests per match")
        self.c_best.set(str(s["best_match"]), "chests in one match")
        last = s["last_match_chests"]
        self.c_last.set("-" if last is None else str(last),
                        "in progress: " + str(s["match_chests"]) if s["match_chests"] is not None else "")
        key = (s["matches_played"], s["match_chests"], s["match_number"])
        if key != self._key:
            self._key = key
            self._fill_table()
            self._draw_chart(force=True)

    def _matches(self):
        store = self.app.engine.store
        with store._lock:
            return list(store.matches), dict(store.current) if store.current else None

    def _fill_table(self):
        for w in self.rows.winfo_children():
            w.destroy()
        matches, current = self._matches()
        mult = self.app.engine.store.squad_multiplier
        rows = []
        if current:
            rows.append((f"#{current['number']}", ago(current.get("started")), "live",
                         str(current["chests"]), f"~{current['chests'] * mult:.0f}", True))
        for m in reversed(matches[-100:]):
            rows.append((f"#{m['number']}", ago(m.get("started")), duration(m.get("started"), m.get("ended")),
                         str(m["chests"]), f"~{m['chests'] * mult:.0f}", False))
        if not rows:
            ctk.CTkLabel(self.rows, text="No matches yet. They appear here as you play.",
                         font=F(13), text_color=C["muted"]).grid(row=0, column=0, columnspan=5, sticky="w", padx=10, pady=10)
            return
        for r, row in enumerate(rows):
            live = row[5]
            for c, val in enumerate(row[:5]):
                color = C["blue"] if live else (C["gold"] if c == 3 else C["text"])
                ctk.CTkLabel(self.rows, text=val, font=F(13, "bold" if c in (0, 3) else "normal"),
                             text_color=color, anchor="w").grid(row=r, column=c, sticky="w", padx=10, pady=3)

    def _draw_chart(self, force=False):
        cv = self.canvas
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50:
            return
        matches, current = self._matches()
        data = [(m["number"], m["chests"], False) for m in matches[-30:]]
        if current:
            data = data[-29:] + [(current["number"], current["chests"], True)]
        left, right, top, bottom = 40, 10, 14, 26
        if not data:
            cv.create_text(w / 2, h / 2, text="Your matches will be charted here", fill=C["muted"], font=F(12))
            return
        step = max(1, math.ceil(max(4, max(v for _n, v, _l in data)) / 4))
        top_val = step * 4
        ph = h - top - bottom
        for i in range(0, 5):
            y = top + ph - ph * i / 4
            cv.create_line(left, y, w - right, y, fill=C["border"])
            cv.create_text(left - 8, y, text=str(step * i), fill=C["faint"], font=F(9), anchor="e")
        slot = (w - left - right) / 30
        bw = max(4, slot * 0.62)
        x0 = left + (30 - len(data)) * slot
        for i, (num, val, live) in enumerate(data):
            cx = x0 + slot * (i + 0.5)
            y = top + ph - ph * val / top_val
            color = C["blue"] if live else C["gold"]
            cv.create_rectangle(cx - bw / 2, y, cx + bw / 2, top + ph, fill=color, outline="")
            if len(data) <= 15 or i % 3 == len(data) % 3 or live:
                cv.create_text(cx, top + ph + 12, text=f"#{num}", fill=C["blue"] if live else C["faint"], font=F(8))
        s = self._state
        if s and s["avg_per_match"]:
            y = top + ph - ph * s["avg_per_match"] / top_val
            cv.create_line(left, y, w - right, y, fill=C["green"], dash=(5, 4), width=2)
            cv.create_text(w - right - 4, y - 9, text=f"avg {s['avg_per_match']:.1f}", fill=C["green"],
                           font=F(9, "bold"), anchor="e")


# --------------------------------------------------------------------------- #
# Odds
# --------------------------------------------------------------------------- #
class OddsPage(Page):
    title = "Token odds"
    subtitle = ""

    TARGETS = (0.10, 0.25, 0.5, 1 - 1 / math.e, 0.75, 0.9, 0.95, 0.99)

    def __init__(self, master, app):
        super().__init__(master, app)
        b = self.body
        for i in range(3):
            b.grid_columnconfigure(i, weight=1, uniform="o")
        b.grid_rowconfigure(2, weight=1)
        self.c_team = StatCard(b, "Token chance · team", C["green"])
        self.c_own = StatCard(b, "Own chests only", C["text"])
        self.c_half = StatCard(b, "For a 50% chance", C["gold"])
        for i, c in enumerate((self.c_team, self.c_own, self.c_half)):
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0), pady=(0, 12))

        chart = Card(b)
        chart.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(0, 12))
        chart.grid_columnconfigure(0, weight=1)
        self.formula = ctk.CTkLabel(chart, text="", font=F(14, "bold"), text_color=C["text"], anchor="w")
        self.formula.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 0))
        ctk.CTkLabel(chart, text="Every chest is an independent roll: your odds on the next chest never change. "
                                 "The curve shows how likely it is that the token has dropped by a given number of chests.",
                     font=F(12), text_color=C["muted"], anchor="w", justify="left", wraplength=900).grid(
            row=1, column=0, sticky="w", padx=18)
        self.canvas = ctk.CTkCanvas(chart, height=180, bg=C["card"], highlightthickness=0)
        self.canvas.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 12))
        self.canvas.bind("<Configure>", lambda _e: self._draw_chart())

        table = Card(b)
        table.grid(row=2, column=0, columnspan=3, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        self.table = ctk.CTkScrollableFrame(table, fg_color="transparent")
        self.table.grid(row=0, column=0, sticky="nsew", padx=10, pady=8)
        self.headers = ("Chance", "Squad chests", "Your chests", "Still to open", "≈ Matches")
        for i in range(len(self.headers)):
            self.table.grid_columnconfigure(i, weight=1, uniform="t")
        self._key = None
        self._state = None

    def refresh(self, s: dict):
        self._state = s
        odds = self.app.cfg["token_odds"]
        mult = s["squad_multiplier"] or 1
        self.subtitle_label.configure(text=f"1 in {odds:,} per chest  ·  squad multiplier ×{mult:g}  ·  the token goes to the whole team")
        self.formula.configure(text=f"P  =  1 − (1 − 1/{odds:,})^({mult:g} · n)      n = your chests = {s['total']:,}")
        self.c_team.set(cc.format_chance(s["squad_chance"]), f"~{fmt_int(s['squad_total'])} squad chests")
        self.c_own.set(cc.format_chance(s["token_chance"]), f"{s['total']:,} chests opened by you")
        half_own = math.ceil(cc.chests_for_chance(0.5, odds) / mult)
        self.c_half.set(f"{half_own:,}", f"of your chests ({max(0, half_own - s['total']):,} to go)")
        key = (s["total"], round(s["avg_per_match"] or 0, 2), odds, mult)
        if key != self._key:
            self._key = key
            self._fill_table()
            self._draw_chart()

    def _fill_table(self):
        for w in self.table.winfo_children():
            w.destroy()
        s = self._state
        odds = self.app.cfg["token_odds"]
        mult = s["squad_multiplier"] or 1
        for i, h in enumerate(self.headers):
            ctk.CTkLabel(self.table, text=h.upper(), font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
                row=0, column=i, sticky="w", padx=8, pady=(0, 4))
        for r, t in enumerate(self.TARGETS, start=1):
            squad = cc.chests_for_chance(t, odds)
            own = math.ceil(squad / mult)
            left = max(0, own - s["total"])
            reached = left == 0
            matches = "-" if not s["avg_per_match"] else f"{math.ceil(left / s['avg_per_match']):,}"
            vals = (f"{t * 100:.1f}%", f"{squad:,}", f"{own:,}", "reached ✓" if reached else f"{left:,}",
                    "-" if reached else matches)
            for c, v in enumerate(vals):
                color = C["green"] if reached else (C["gold"] if c == 0 else C["text"])
                ctk.CTkLabel(self.table, text=v, font=F(13, "bold" if c == 0 else "normal"),
                             text_color=color, anchor="w").grid(row=r, column=c, sticky="w", padx=8, pady=1)

    def _draw_chart(self):
        cv = self.canvas
        cv.delete("all")
        s = self._state
        w, h = cv.winfo_width(), cv.winfo_height()
        if not s or w < 80:
            return
        odds = self.app.cfg["token_odds"]
        n_now = s["squad_total"]
        x_max = max(cc.chests_for_chance(0.99, odds), n_now * 1.1)
        left, right, top, bottom = 44, 16, 12, 28
        pw, ph = w - left - right, h - top - bottom
        X = lambda n: left + pw * n / x_max  # noqa: E731
        Y = lambda p: top + ph * (1 - p)  # noqa: E731
        for p in (0, 0.25, 0.5, 0.75, 1.0):
            cv.create_line(left, Y(p), w - right, Y(p), fill=C["border"], dash=() if p in (0, 1) else (2, 4))
            cv.create_text(left - 8, Y(p), text=f"{int(p * 100)}%", fill=C["faint"], font=F(9), anchor="e")
        step = 10 ** max(0, int(math.log10(x_max)) - 0)
        step = step / 2 if x_max / step < 4 else step
        tick = 0
        while tick <= x_max:
            if X(tick) < w - right - 90:
                cv.create_text(X(tick), h - bottom + 14, text=f"{int(tick / 1000)}k" if tick else "0",
                               fill=C["faint"], font=F(9))
            tick += step
        cv.create_text(w - right, h - bottom + 14, text="squad chests →", fill=C["faint"], font=F(9), anchor="e")
        # filled area up to "you are here"
        pts_fill = [X(0), Y(0)]
        steps = 160
        for i in range(steps + 1):
            n = n_now * i / steps
            pts_fill += [X(n), Y(cc.token_chance(n, odds))]
        pts_fill += [X(n_now), Y(0)]
        if n_now > 0:
            cv.create_polygon(*pts_fill, fill=C["green_dim"], outline="")
        pts = []
        for i in range(steps * 2 + 1):
            n = x_max * i / (steps * 2)
            pts += [X(n), Y(cc.token_chance(n, odds))]
        cv.create_line(*pts, fill=C["green"], width=3, smooth=True)
        p_now = s["squad_chance"]
        x, y = X(n_now), Y(p_now)
        cv.create_line(x, y, x, Y(0), fill=C["gold"], dash=(3, 3))
        cv.create_oval(x - 6, y - 6, x + 6, y + 6, fill=C["gold"], outline=C["card"], width=2)
        anchor = "w" if x < w * 0.7 else "e"
        dx = 12 if anchor == "w" else -12
        cv.create_text(x + dx, y - 14, text=f"you: {cc.format_chance(p_now)}", fill=C["gold"],
                       font=F(11, "bold"), anchor=anchor)


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
class CropDialog(ctk.CTkToplevel):
    """Two-step crop: drag roughly around the prompt, then tightly around 'Chest'."""

    def __init__(self, app: "ChestCounterApp", img_bgr, mon: dict):
        super().__init__(app)
        self.app, self.mon = app, mon
        self.full = Image.fromarray(img_bgr[:, :, ::-1].copy())  # BGR -> RGB
        self.img_bgr = img_bgr
        self.title("Select the chest prompt")
        self.configure(fg_color=C["bg"])
        self.attributes("-topmost", True)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.cw, self.ch = int(min(1280, sw * 0.8)), int(min(720, sh * 0.7))
        self.grid_columnconfigure(0, weight=1)

        self.step_label = ctk.CTkLabel(self, text="", font=F(15, "bold"), text_color=C["text"], anchor="w")
        self.step_label.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 0))
        self.hint = ctk.CTkLabel(self, text="", font=F(12), text_color=C["muted"], anchor="w")
        self.hint.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 8))
        self.canvas = ctk.CTkCanvas(self, width=self.cw, height=self.ch, bg="#000", highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=2, column=0, padx=18)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=18, pady=12)
        self.preview = ctk.CTkLabel(bar, text="", height=60)
        self.preview.pack(side="left")
        self.save_btn = button(bar, "Save template", self._save, "primary", width=150, state="disabled")
        self.save_btn.pack(side="right")
        button(bar, "Cancel", self.destroy, "ghost", width=90).pack(side="right", padx=6)
        self.back_btn = button(bar, "← Start over", self._stage1, "secondary", width=120)
        self.back_btn.pack(side="right", padx=6)

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self._rect = None
        self._start = None
        self.selection = None
        self._stage1()
        center_over(self, app)
        self.after(50, self.focus_force)

    def _show(self, x0, y0, x1, y1):
        """Show full-res region (x0,y0)-(x1,y1) scaled to fit the canvas."""
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.full.width, int(x1)), min(self.full.height, int(y1))
        region = self.full.crop((x0, y0, x1, y1))
        scale = min(self.cw / region.width, self.ch / region.height)
        size = (max(1, int(region.width * scale)), max(1, int(region.height * scale)))
        resample = Image.LANCZOS if scale < 1 else Image.NEAREST
        self._photo = ImageTk.PhotoImage(region.resize(size, resample))
        self.canvas.delete("all")
        self.canvas.create_image((self.cw - size[0]) // 2, (self.ch - size[1]) // 2, image=self._photo, anchor="nw")
        self.view = (x0, y0, scale, (self.cw - size[0]) // 2, (self.ch - size[1]) // 2)
        self._rect = None

    def _to_full(self, cx, cy):
        x0, y0, scale, ox, oy = self.view
        return x0 + (cx - ox) / scale, y0 + (cy - oy) / scale

    def _stage1(self):
        self.stage = 1
        self.selection = None
        self.save_btn.configure(state="disabled")
        self.preview.configure(image=None, text="")
        self.step_label.configure(text="Step 1 of 2  ·  Drag a rough box around the chest prompt")
        self.hint.configure(text="Don't aim for precision yet. The next step zooms in.")
        self._show(0, 0, self.full.width, self.full.height)

    def _press(self, e):
        self._start = (e.x, e.y)
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=C["gold"], width=2)

    def _move(self, e):
        if self._start and self._rect:
            self.canvas.coords(self._rect, self._start[0], self._start[1], e.x, e.y)

    def _release(self, e):
        if not self._start:
            return
        (ax, ay), (bx, by) = self._to_full(*self._start), self._to_full(e.x, e.y)
        self._start = None
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return
        if self.stage == 1:
            mx, my = (x1 - x0) * 0.25 + 10, (y1 - y0) * 0.25 + 10
            self.stage = 2
            self.step_label.configure(text="Step 2 of 2  ·  Drag a tight box around the word “Chest”")
            self.hint.configure(text="Only the word Chest, not Search (ammo boxes say Search too). You can redraw as often as you like.")
            self._show(x0 - mx, y0 - my, x1 + mx, y1 + my)
        else:
            self.selection = (int(x0), int(y0), int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0)))
            x, y, w, h = self.selection
            crop = self.full.crop((x, y, x + w, y + h))
            scale = min(4.0, 56 / max(1, h), 360 / max(1, w))
            prev = crop.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.NEAREST)
            self._prev_img = ctk.CTkImage(light_image=prev, dark_image=prev, size=prev.size)
            self.preview.configure(image=self._prev_img, text="")
            self.save_btn.configure(state="normal")

    def _save(self):
        if not self.selection:
            return
        try:
            path, region = cc.save_calibration(self.app.cfg, self.img_bgr, self.mon, *self.selection)
        except Exception as exc:
            self.hint.configure(text=f"Could not save: {exc}", text_color=C["red"])
            return
        self.app.engine.reload_templates()
        self.app.notify(f"Template saved ({path.name}). Search area {region[2]}×{region[3]}.")
        self.app.pages["calibration"].reload_templates()
        self.destroy()


class CalibrationPage(Page):
    title = "Calibration"
    subtitle = "Teach the counter what Fortnite's chest prompt looks like on your screen"

    def __init__(self, master, app):
        super().__init__(master, app)
        b = self.body
        b.grid_columnconfigure((0, 1), weight=1, uniform="c")
        b.grid_rowconfigure(1, weight=1)

        how = Card(b)
        how.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
        how.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(how, text="HOW TO CALIBRATE", font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 6))
        key = app.cfg["calibrate_key"].upper()
        steps = (
            "1.  Start Fortnite in Windowed Fullscreen.",
            "2.  Walk up to a chest so its prompt (“Chest · Search”) shows.",
            f"3.  Press {key}. A screenshot is taken without leaving the game.",
            "4.  Alt-Tab back here and drag a box around the word “Chest”.",
        )
        for i, t in enumerate(steps, start=1):
            ctk.CTkLabel(how, text=t, font=F(13), text_color=C["text"], anchor="w", justify="left").grid(
                row=i, column=0, sticky="w", padx=18, pady=1)
        row = ctk.CTkFrame(how, fg_color="transparent")
        row.grid(row=6, column=0, sticky="ew", padx=18, pady=(14, 16))
        self.arm_btn = button(row, f"Wait for {key}", self.toggle_arm, "primary", width=170)
        self.arm_btn.pack(side="left")
        self.timer_btn = button(row, "Capture in 5 s", self.capture_timer, width=140)
        self.timer_btn.pack(side="left", padx=8)

        live = Card(b)
        live.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))
        live.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(live, text="LIVE DETECTION", font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 0))
        self.score = ctk.CTkLabel(live, text="-", font=F(40, "bold"), text_color=C["text"], anchor="w")
        self.score.grid(row=1, column=0, sticky="w", padx=18)
        self.meter = ctk.CTkCanvas(live, height=26, bg=C["card"], highlightthickness=0)
        self.meter.grid(row=2, column=0, sticky="ew", padx=18)
        self.score_hint = ctk.CTkLabel(live, text="", font=F(12), text_color=C["muted"], anchor="w", justify="left")
        self.score_hint.grid(row=3, column=0, sticky="w", padx=18, pady=(4, 0))
        trow = ctk.CTkFrame(live, fg_color="transparent")
        trow.grid(row=4, column=0, sticky="ew", padx=18, pady=(10, 16))
        ctk.CTkLabel(trow, text="Threshold", font=F(12), text_color=C["muted"]).pack(side="left")
        self.thr = ctk.CTkSlider(trow, from_=0.5, to=0.98, number_of_steps=48, command=self._thr_changed,
                                 progress_color=C["gold"], button_color=C["gold"], button_hover_color=C["gold_dark"])
        self.thr.set(app.cfg["match_threshold"])
        self.thr.pack(side="left", fill="x", expand=True, padx=10)
        self.thr_label = ctk.CTkLabel(trow, text=f"{app.cfg['match_threshold']:.2f}", font=F(12, "bold"), width=40)
        self.thr_label.pack(side="left")

        tpl = Card(b)
        tpl.grid(row=1, column=0, columnspan=2, sticky="new")
        tpl.grid_columnconfigure(0, weight=1)
        tpl.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(tpl, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 4))
        self.tpl_title = ctk.CTkLabel(top, text="TEMPLATES", font=F(11, "bold"), text_color=C["muted"])
        self.tpl_title.pack(side="left")
        button(top, "Remove all", self.clear_all, "danger", width=110, height=30).pack(side="right")
        self.gallery = ctk.CTkScrollableFrame(tpl, fg_color="transparent", orientation="horizontal", height=120)
        self.gallery.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self._countdown = 0
        self.reload_templates()

    # templates
    def reload_templates(self):
        for w in self.gallery.winfo_children():
            w.destroy()
        files = cc.template_files()
        region = self.app.cfg.get("region")
        info = f"TEMPLATES  ·  {len(files)}"
        if region:
            info += f"  ·  search area {region[2]}×{region[3]} at ({region[0]}, {region[1]})"
        self.tpl_title.configure(text=info)
        if not files:
            ctk.CTkLabel(self.gallery, text="No templates yet. Follow the steps above to add your first one.",
                         font=F(13), text_color=C["amber"]).pack(side="left", padx=10, pady=20, anchor="n")
            return
        self._thumbs = []
        for f in files:
            try:
                im = Image.open(f).convert("RGB")
            except OSError:
                continue
            scale = min(3.0, 60 / max(1, im.height), 220 / max(1, im.width))
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.NEAREST)
            img = ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
            self._thumbs.append(img)
            box = ctk.CTkFrame(self.gallery, fg_color=C["card2"], corner_radius=10)
            box.pack(side="left", padx=6, pady=4, anchor="n")
            ctk.CTkLabel(box, image=img, text="").pack(padx=10, pady=(10, 4))
            foot = ctk.CTkFrame(box, fg_color="transparent")
            foot.pack(fill="x", padx=8, pady=(0, 8))
            ctk.CTkLabel(foot, text=f.stem.replace("chest_", ""), font=F(10), text_color=C["faint"]).pack(side="left")
            ctk.CTkButton(foot, text="✕", width=26, height=22, corner_radius=6, fg_color="transparent",
                          hover_color="#3a1e22", text_color=C["red"], font=F(12, "bold"),
                          command=lambda p=f: self.delete(p)).pack(side="right")

    def delete(self, path: Path):
        path.unlink(missing_ok=True)
        if not cc.template_files():
            self.app.cfg["region"] = None
            cc.save_config(self.app.cfg)
        self.app.engine.reload_templates()
        self.reload_templates()
        self.app.notify(f"Removed {path.name}")

    def clear_all(self):
        dlg = ctk.CTkInputDialog(text="Type DELETE to remove all templates and the search area.", title="Remove all templates")
        if (dlg.get_input() or "").strip().upper() == "DELETE":
            cc.clear_calibration(self.app.cfg)
            self.app.engine.reload_templates()
            self.reload_templates()
            self.app.notify("All templates removed")

    # capture
    def toggle_arm(self):
        if self.app.engine.state()["awaiting_screenshot"]:
            self.app.engine.cancel_screenshot()
        else:
            self.app.engine.request_screenshot(self.app.on_screenshot)

    def capture_timer(self):
        if self._countdown:
            return
        self.app.engine.request_screenshot(self.app.on_screenshot)
        self._countdown = 5
        self._tick()

    def _tick(self):
        if self._countdown <= 0:
            self.timer_btn.configure(text="Capture in 5 s")
            threading.Thread(target=self.app.engine.take_screenshot, daemon=True).start()
            return
        self.timer_btn.configure(text=f"Capturing in {self._countdown}…")
        self._countdown -= 1
        self.after(1000, self._tick)

    def _thr_changed(self, v):
        v = round(float(v), 2)
        self.thr_label.configure(text=f"{v:.2f}")
        self.app.cfg["match_threshold"] = v
        cc.save_config(self.app.cfg)

    def refresh(self, s: dict):
        key = self.app.cfg["calibrate_key"].upper()
        armed = s["awaiting_screenshot"] and not self._countdown
        self.arm_btn.configure(text=f"Waiting for {key}…  (cancel)" if armed else f"Wait for {key}")
        thr = self.app.cfg["match_threshold"]
        sc = s["last_score"]
        if not s["calibrated"]:
            self.score.configure(text="-", text_color=C["faint"])
            self.score_hint.configure(text="Add a template to start detecting chests.")
        elif not s["auto_enabled"]:
            self.score.configure(text="paused", text_color=C["faint"])
            self.score_hint.configure(text="Detection is paused (Dashboard → Resume).")
        else:
            hit = sc >= thr
            self.score.configure(text=f"{sc:.3f}", text_color=C["green"] if hit else C["text"])
            self.score_hint.configure(
                text="Chest prompt detected ✓" if hit else
                "Look at a chest in game: the score should jump above the gold line.\n"
                "Looking away, it should stay well below. Move the threshold to fit.")
        cv = self.meter
        cv.delete("all")
        w = cv.winfo_width()
        if w > 20:
            cv.create_rectangle(0, 6, w, 20, fill=C["card2"], outline="")
            cv.create_rectangle(0, 6, w * max(0, min(1, sc)), 20, fill=C["green"] if sc >= thr else C["blue"], outline="")
            x = w * thr
            cv.create_line(x, 0, x, 26, fill=C["gold"], width=3)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class SettingsPage(Page):
    title = "Settings"
    subtitle = "Changes take effect immediately when you press Save"

    # key, label, kind, help, extra
    FIELDS = [
        ("Counting", [
            ("squad_multiplier", "Squad multiplier", "float", "Squad chests = your chests × this. ×3 assumes teammates loot a bit less than you.", (0.0, 10.0)),
            ("token_odds", "Token odds (1 in …)", "int", "Chance of the token per chest.", (2, 10**9)),
            ("interact_key", "Interact key", "key", "The key you open chests with: e, f, or mouse_x1 / mouse_x2.", None),
            ("count_attacks", "Count broken chests", "bool", "Count a chest when its prompt vanishes while you are attacking.", None),
            ("beep_on_count", "Beep on every chest", "bool", "Play a short sound when a chest is counted.", None),
        ]),
        ("Detection", [
            ("match_threshold", "Match threshold", "float", "0–1. Higher is stricter. Tune it on the Calibration page.", (0.3, 0.99)),
            ("fps", "Checks per second", "int", "How often the screen is checked.", (1, 60)),
            ("input_window_s", "Input window (s)", "float", "How recently you must have pressed interact or attack.", (0.1, 5.0)),
            ("cooldown_s", "Cooldown (s)", "float", "Minimum time between two counted chests.", (0.0, 10.0)),
            ("monitor", "Monitor number", "int", "1 = primary monitor.", (1, 16)),
            ("search_scale", "Search scale", "float", "0.5 = faster, slightly less precise. 1.0 = full detail.", (0.25, 1.0)),
        ]),
        ("Matches", [
            ("auto_match_from_log", "Detect matches from log", "bool", "Read Fortnite's log file to see when matches start and end.", None),
            ("fortnite_log", "Log file", "path", "Leave empty for the default location.", None),
            ("match_start_regex", "Match start pattern", "regex", "Advanced. A log line matching this starts a match.", None),
            ("match_end_regex", "Match end pattern", "regex", "Advanced. A log line matching this ends a match.", None),
            ("match_resume_window_s", "Resume window (s)", "int", "Continue the same match if the app is reopened within this time.", (0, 86400)),
        ]),
        ("Hotkeys", [
            ("hotkeys.increment", "+1 chest", "key", "", None),
            ("hotkeys.decrement", "−1 chest", "key", "", None),
            ("hotkeys.new_match", "New match", "key", "", None),
            ("hotkeys.reset_session", "Reset session", "key", "", None),
            ("hotkeys.toggle_auto", "Pause / resume detection", "key", "", None),
            ("calibrate_key", "Calibration screenshot", "key", "", None),
        ]),
    ]

    def __init__(self, master, app):
        super().__init__(master, app)
        button(self.head_right, "Save", self.save, "primary", width=110).pack(side="right")
        button(self.head_right, "Reset to defaults", self.reset, "ghost", width=150).pack(side="right", padx=8)
        self.error = ctk.CTkLabel(self.head_right, text="", font=F(12, "bold"), text_color=C["red"])
        self.error.pack(side="right", padx=8)
        scroll = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        scroll.pack(fill="both", expand=True)
        scroll.grid_columnconfigure(0, weight=1)
        self.widgets = {}
        r = 0
        for section, fields in self.FIELDS:
            card = Card(scroll)
            card.grid(row=r, column=0, sticky="ew", pady=(0, 12))
            card.grid_columnconfigure(1, weight=1)
            r += 1
            ctk.CTkLabel(card, text=section.upper(), font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
                row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 6))
            for i, (key, label, kind, help_text, _rng) in enumerate(fields, start=1):
                ctk.CTkLabel(card, text=label, font=F(13), text_color=C["text"], anchor="w", width=210).grid(
                    row=i * 2 - 1, column=0, sticky="w", padx=18, pady=(6, 0))
                if kind == "bool":
                    w = ctk.CTkSwitch(card, text="", progress_color=C["gold"], button_color=C["text"])
                    w.grid(row=i * 2 - 1, column=1, sticky="w", pady=(6, 0))
                else:
                    width = 520 if kind in ("regex", "path") else 180
                    w = ctk.CTkEntry(card, width=width, height=34, corner_radius=8, fg_color=C["card2"],
                                     border_color=C["border"], text_color=C["text"], font=F(13))
                    w.grid(row=i * 2 - 1, column=1, sticky="w", pady=(6, 0))
                    if kind == "path":
                        button(card, "Browse…", lambda e=w: self._browse(e), "ghost", width=90, height=32).grid(
                            row=i * 2 - 1, column=2, sticky="w", padx=8, pady=(6, 0))
                self.widgets[key] = (w, kind)
                if help_text:
                    ctk.CTkLabel(card, text=help_text, font=F(11), text_color=C["faint"], anchor="w").grid(
                        row=i * 2, column=1, columnspan=2, sticky="w", pady=(0, 2))
            ctk.CTkFrame(card, height=10, fg_color="transparent").grid(row=99, column=0)
        data = Card(scroll)
        data.grid(row=r, column=0, sticky="ew", pady=(0, 12))
        data.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(data, text="DATA", font=F(11, "bold"), text_color=C["muted"], anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 6))
        ctk.CTkLabel(data, text="Data folder", font=F(13), text_color=C["text"], anchor="w", width=210).grid(
            row=1, column=0, sticky="w", padx=18, pady=(0, 16))
        ctk.CTkLabel(data, text=str(cc.HERE), font=F(12), text_color=C["muted"], anchor="w").grid(
            row=1, column=1, sticky="w", pady=(0, 16))
        button(data, "Open", self._open_data, "ghost", width=80, height=32).grid(row=1, column=2, padx=12, pady=(0, 16))
        self.load(app.cfg)

    @staticmethod
    def _open_data():
        import os
        import subprocess

        try:
            if sys.platform == "win32":
                os.startfile(cc.HERE)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(cc.HERE)])
        except Exception:
            pass

    @staticmethod
    def _get(cfg, key):
        if "." in key:
            a, b = key.split(".", 1)
            return cfg[a][b]
        return cfg.get(key)

    def load(self, cfg):
        for key, (w, kind) in self.widgets.items():
            val = self._get(cfg, key)
            if kind == "bool":
                w.select() if val else w.deselect()
            else:
                w.delete(0, "end")
                w.insert(0, "" if val is None else str(val))
        self.error.configure(text="")

    def _browse(self, entry):
        path = filedialog.askopenfilename(title="Fortnite log file", filetypes=[("Log files", "*.log"), ("All files", "*.*")])
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def save(self):
        new = cc.json.loads(cc.json.dumps(self.app.cfg))
        ranges = {key: rng for _s, fields in self.FIELDS for key, _l, _k, _h, rng in fields}
        labels = {key: label for _s, fields in self.FIELDS for key, label, _k, _h, _r in fields}
        try:
            for key, (w, kind) in self.widgets.items():
                if kind == "bool":
                    val = bool(w.get())
                else:
                    raw = w.get().strip()
                    if kind == "int":
                        val = int(raw.replace(",", ""))
                    elif kind == "float":
                        val = float(raw)
                    elif kind == "path":
                        val = raw or None
                    elif kind == "regex":
                        re.compile(raw)
                        val = raw
                    else:  # key
                        if not raw:
                            raise ValueError("can't be empty")
                        val = raw.lower()
                    rng = ranges.get(key)
                    if rng and not (rng[0] <= val <= rng[1]):
                        raise ValueError(f"must be between {rng[0]:g} and {rng[1]:g}")
                if "." in key:
                    a, b = key.split(".", 1)
                    new[a][b] = val
                else:
                    new[key] = val
            keys = [new["hotkeys"][k] for k in new["hotkeys"]] + [new["calibrate_key"]]
            dupes = {k for k in keys if keys.count(k) > 1}
            if dupes:
                key = "hotkeys"
                raise ValueError(f"the same key is used twice ({', '.join(sorted(dupes)).upper()})")
        except (ValueError, re.error) as exc:
            self.error.configure(text=f"{labels.get(key, key)}: {exc}")
            return
        self.error.configure(text="")
        self.app.apply_config(new)
        self.app.notify("Settings saved")

    def reset(self):
        d = cc.json.loads(cc.json.dumps(cc.DEFAULT_CONFIG))
        d["region"] = self.app.cfg.get("region")
        d["overlay_pos"] = self.app.cfg.get("overlay_pos")
        self.load(d)
        self.error.configure(text="Defaults loaded. Press Save to keep them.", text_color=C["amber"])


# --------------------------------------------------------------------------- #
# Add past chests dialog
# --------------------------------------------------------------------------- #
class AddChestsDialog(ctk.CTkToplevel):
    def __init__(self, app: "ChestCounterApp"):
        super().__init__(app)
        self.app = app
        self.title("Add past chests")
        self.configure(fg_color=C["bg"])
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text="Add chests you opened before", font=F(18, "bold"), text_color=C["text"]).grid(
            row=0, column=0, sticky="w", padx=22, pady=(20, 2))
        ctk.CTkLabel(self, text="Use a negative number to take chests off.", font=F(12),
                     text_color=C["muted"]).grid(row=1, column=0, sticky="w", padx=22)
        self.entry = ctk.CTkEntry(self, height=44, font=F(20, "bold"), placeholder_text="e.g. 5000",
                                  fg_color=C["card2"], border_color=C["border"], corner_radius=10)
        self.entry.grid(row=2, column=0, sticky="ew", padx=22, pady=(14, 10))
        self.mode = ctk.CTkSegmentedButton(self, values=["My own chests", "Whole squad"], command=lambda _v: self._preview(),
                                           selected_color="#34507a", selected_hover_color="#3d5f8f",
                                           unselected_color=C["card2"], text_color=C["text"], font=F(13, "bold"))
        self.mode.set("My own chests")
        self.mode.grid(row=3, column=0, sticky="ew", padx=22)
        self.info = ctk.CTkLabel(self, text="", font=F(12), text_color=C["muted"], justify="left", anchor="w")
        self.info.grid(row=4, column=0, sticky="w", padx=22, pady=(12, 0))
        self.result = ctk.CTkLabel(self, text="", font=F(14, "bold"), text_color=C["text"], justify="left", anchor="w")
        self.result.grid(row=5, column=0, sticky="w", padx=22, pady=(4, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=6, column=0, sticky="ew", padx=22, pady=20)
        self.add_btn = button(bar, "Add", lambda: self._apply(False), "primary", width=110)
        self.add_btn.pack(side="right")
        self.set_btn = button(bar, "Set total to this", lambda: self._apply(True), width=150)
        self.set_btn.pack(side="right", padx=8)
        button(bar, "Cancel", self.destroy, "ghost", width=90).pack(side="left")
        self.entry.bind("<KeyRelease>", lambda _e: self._preview())
        self.entry.bind("<Return>", lambda _e: self._apply(False))
        self._preview()
        center_over(self, app)
        self.after(80, lambda: (self.grab_set(), self.entry.focus_set()))

    def _value(self):
        raw = self.entry.get().strip().replace(",", "").replace(" ", "")
        try:
            return int(raw)
        except ValueError:
            return None

    def _own(self, v):
        mult = self.app.engine.store.squad_multiplier
        return round(v / mult) if self.mode.get() == "Whole squad" and mult > 0 else v

    def _preview(self):
        s = self.app.engine.state()
        mult = s["squad_multiplier"]
        v = self._value()
        if self.mode.get() == "Whole squad":
            self.info.configure(text=f"The number includes your teammates, so it's divided by ×{mult:g}.")
        else:
            self.info.configure(text=f"Only the chests you opened yourself. ×{mult:g} is applied on top for the squad.")
        if v is None:
            self.result.configure(text=f"Current total: {s['total']:,}")
            self.add_btn.configure(state="disabled")
            self.set_btn.configure(state="disabled")
            return
        own = self._own(v)
        after = max(0, s["total"] + own)
        odds = self.app.cfg["token_odds"]
        self.result.configure(
            text=f"Add → {after:,} chests · team chance {cc.format_chance(cc.token_chance(after * mult, odds))}\n"
                 f"Set → {max(0, own):,} chests · team chance {cc.format_chance(cc.token_chance(max(0, own) * mult, odds))}")
        self.add_btn.configure(state="normal")
        self.set_btn.configure(state="normal" if own >= 0 else "disabled")

    def _apply(self, set_total: bool):
        v = self._value()
        if v is None:
            return
        self.app.engine.add_past(v, squad=self.mode.get() == "Whole squad", set_total=set_total)
        self.destroy()


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class ChestCounterApp(ctk.CTk):
    NAV = (("dashboard", "Dashboard"), ("matches", "Matches"), ("odds", "Token odds"),
           ("calibration", "Calibration"), ("settings", "Settings"))

    def __init__(self, start_engine: bool = True):
        ctk.set_appearance_mode("dark")
        super().__init__()
        self.cfg = cc.load_config()
        self.events: queue.Queue = queue.Queue()
        self.engine = cc.Engine(self.cfg, on_event=lambda k, t: self.events.put(("event", k, t)))
        self.title(APP_NAME)
        self.geometry("1240x800")
        self.minsize(1060, 700)
        self.configure(fg_color=C["bg"])
        self._icon = ImageTk.PhotoImage(make_icon(64))
        self.iconphoto(True, self._icon)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages: dict[str, Page] = {
            "dashboard": DashboardPage(self.content, self),
            "matches": MatchesPage(self.content, self),
            "odds": OddsPage(self.content, self),
            "calibration": CalibrationPage(self.content, self),
            "settings": SettingsPage(self.content, self),
        }
        self.toast = ctk.CTkLabel(self, text="", font=F(13, "bold"), text_color="#10141a", fg_color=C["green"],
                                  corner_radius=10, height=36)
        self._toast_job = None
        self.current = None
        self.overlay = None
        self.show("dashboard" if self.engine.calibrated else "calibration")

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if start_engine:
            self.engine.start()
        if self.cfg.get("overlay"):
            self.after(400, lambda: self.set_overlay(True))
        self.pages["dashboard"].log("info", f"{APP_NAME} started. Total so far: {self.engine.store.total:,} chests.")
        self.after(100, self._pump)
        self.after(250, self._tick)

    # --- layout ---------------------------------------------------------------- #
    def _build_sidebar(self):
        side = ctk.CTkFrame(self, fg_color=C["side"], corner_radius=0, width=230)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(10, weight=1)
        logo = ctk.CTkFrame(side, fg_color="transparent")
        logo.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 28))
        self._logo = ctk.CTkImage(light_image=make_icon(80), dark_image=make_icon(80), size=(40, 40))
        ctk.CTkLabel(logo, image=self._logo, text="").pack(side="left")
        txt = ctk.CTkFrame(logo, fg_color="transparent")
        txt.pack(side="left", padx=10)
        ctk.CTkLabel(txt, text=APP_NAME, font=F(16, "bold"), text_color=C["text"], anchor="w").pack(anchor="w")
        ctk.CTkLabel(txt, text="Fortnite chest tracker", font=F(11), text_color=C["muted"], anchor="w").pack(anchor="w")

        self.nav_buttons = {}
        for i, (key, label) in enumerate(self.NAV, start=1):
            b = ctk.CTkButton(side, text=label, anchor="w", height=42, corner_radius=10, font=F(14, "bold"),
                              fg_color="transparent", hover_color=C["hover"], text_color=C["muted"],
                              command=lambda k=key: self.show(k))
            b.grid(row=i, column=0, sticky="ew", padx=14, pady=2)
            self.nav_buttons[key] = b

        status = ctk.CTkFrame(side, fg_color=C["card"], corner_radius=12)
        status.grid(row=11, column=0, sticky="ew", padx=14, pady=(0, 10))
        ctk.CTkLabel(status, text="STATUS", font=F(10, "bold"), text_color=C["faint"], anchor="w").pack(
            anchor="w", padx=14, pady=(10, 2))
        self.p_detect = Pill(status, "Detection")
        self.p_log = Pill(status, "Match log")
        self.p_input = Pill(status, "Hotkeys")
        self.p_match = Pill(status, "Now")
        for p in (self.p_detect, self.p_log, self.p_input, self.p_match):
            p.pack(fill="x", padx=14, pady=1)
        ctk.CTkFrame(status, height=8, fg_color="transparent").pack()

        self.overlay_switch = ctk.CTkSwitch(side, text="In-game overlay", font=F(13), text_color=C["text"],
                                            progress_color=C["gold"], command=lambda: self.set_overlay(bool(self.overlay_switch.get())))
        self.overlay_switch.grid(row=12, column=0, sticky="w", padx=22, pady=(4, 20))

    def show(self, key: str):
        if self.current == key:
            return
        for k, b in self.nav_buttons.items():
            active = k == key
            b.configure(fg_color=C["card2"] if active else "transparent",
                        text_color=C["text"] if active else C["muted"])
        if self.current:
            self.pages[self.current].grid_forget()
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        self.current = key
        self.pages[key].refresh(self.engine.state())

    def notify(self, text: str, color: str | None = None):
        self.toast.configure(text=f"   {text}   ", fg_color=color or C["green"])
        self.toast.place(relx=0.5, rely=1.0, y=-24, anchor="s")
        self.toast.lift()
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(3200, self.toast.place_forget)

    # --- actions ----------------------------------------------------------------- #
    def toggle_auto(self):
        if not self.engine.calibrated:
            self.show("calibration")
            return
        self.engine.toggle_auto()

    def open_add_dialog(self):
        AddChestsDialog(self)

    def apply_config(self, new: dict):
        self.cfg = new
        cc.save_config(new)
        self.engine.apply_config(new)
        hk = new["hotkeys"]
        btns = self.pages["dashboard"].ctl_buttons
        btns["plus"].configure(text=f"+1 chest   {hk['increment'].upper()}")
        btns["minus"].configure(text=f"−1 chest   {hk['decrement'].upper()}")
        btns["new"].configure(text=f"New match   {hk['new_match'].upper()}")
        btns["reset"].configure(text=f"Reset session   {hk['reset_session'].upper()}")
        self.pages["calibration"].thr.set(new["match_threshold"])
        self.pages["calibration"].thr_label.configure(text=f"{new['match_threshold']:.2f}")

    def set_overlay(self, on: bool):
        if on and self.overlay is None:
            self.overlay = cc.Overlay(self.engine, master=self, on_close=self._overlay_closed)
        elif not on and self.overlay is not None:
            ov, self.overlay = self.overlay, None
            ov.on_close = None
            ov.close()
        self.overlay_switch.select() if on else self.overlay_switch.deselect()
        if self.cfg.get("overlay") != on:
            self.cfg["overlay"] = on
            cc.save_config(self.cfg)

    def _overlay_closed(self):
        self.overlay = None
        self.overlay_switch.deselect()
        self.cfg["overlay"] = False
        cc.save_config(self.cfg)

    def on_screenshot(self, img, mon):  # background thread
        self.events.put(("shot", img, mon))

    # --- loops ------------------------------------------------------------------- #
    def _pump(self):
        try:
            while True:
                item = self.events.get_nowait()
                if item[0] == "event":
                    _e, kind, text = item
                    self.pages["dashboard"].log(kind, text)
                    if kind == "warn":
                        self.notify(text[:90], C["red"])
                elif item[0] == "shot":
                    _e, img, mon = item
                    self.engine.cancel_screenshot()
                    self.deiconify()
                    self.lift()
                    CropDialog(self, img, mon)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _tick(self):
        s = self.engine.state()
        st = s["status"]
        if not s["calibrated"]:
            self.p_detect.set("not calibrated", C["amber"])
        elif st == "chest in view":
            self.p_detect.set("chest in view", C["green"])
        elif st == "watching":
            self.p_detect.set("watching", C["green"])
        elif st == "paused":
            self.p_detect.set("paused", C["amber"])
        else:
            self.p_detect.set(st[:18], C["red"] if "error" in st else C["muted"])
        log_color = {"watching": C["green"], "off": C["faint"]}.get(s["log_status"], C["amber"])
        self.p_log.set(s["log_status"], log_color)
        self.p_input.set("active" if s["listeners_ok"] else "unavailable", C["green"] if s["listeners_ok"] else C["red"])
        if s["match_number"] is None:
            self.p_match.set("lobby", C["faint"])
        else:
            self.p_match.set(f"match #{s['match_number']}", C["blue"])
        if self.current:
            self.pages[self.current].refresh(s)
        self.after(250, self._tick)

    def on_close(self):
        self.engine.shutdown()
        self.destroy()


def main():
    """Start the app. `--selftest` opens it, checks every page, then exits;
    the build uses this to verify the .exe really starts."""
    selftest = "--selftest" in sys.argv
    log = cc.HERE / ("selftest.log" if selftest else "crash.log")
    try:
        app = ChestCounterApp(start_engine=True)
        if selftest:
            def run_checks():
                for key in app.pages:
                    app.show(key)
                    app.update()
                app.engine.count("manual", +1)
                app.engine.count("manual", -1)
                app.set_overlay(True)
                app.update()
                log.write_text("OK\n", encoding="utf-8")
                app.on_close()
            app.after(3000, run_checks)
        app.mainloop()
    except Exception:
        import traceback

        log.write_text(traceback.format_exc(), encoding="utf-8")
        if not selftest:
            try:
                from tkinter import messagebox

                messagebox.showerror(APP_NAME, f"Chest Counter crashed. Details were saved to:\n{log}")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
