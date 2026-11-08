#!/usr/bin/env python3
"""
Fortnite chest counter.

Counts every chest you open (hold the interact key on it) or break (hit it
while its prompt is on screen). Fortnite has no public API or log line for
chest events, so this script works from the outside, the same way a capture
program like OBS does:

  1. It watches a small area of your screen for the "Chest" interaction prompt
     (template matching with OpenCV against a crop you take once).
  2. It listens to your interact key and left mouse button (pynput).
  3. When the prompt was visible and then disappears right while you were
     holding/just released the interact key  -> counted as "opened".
     ... or right while you were attacking   -> counted as "broken".

It never reads or writes game memory and never sends input to the game.

Commands:
  python chest_counter.py calibrate   take a template of the chest prompt (do this first)
  python chest_counter.py test        print live match scores to tune the threshold
  python chest_counter.py             run the counter (overlay + hotkeys)
  python chest_counter.py --manual    run with hotkeys only, no screen detection

Default hotkeys while running:
  F8  +1      F9  -1      F10  reset session      F11  pause/resume auto-detect
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"
CONFIG_FILE = HERE / "config.json"
COUNT_FILE = HERE / "chest_count.json"
OBS_TEXT_FILE = HERE / "chest_count.txt"

DEFAULT_CONFIG: dict = {
    # Key you use to interact in Fortnite. A letter ("e", "f"), a pynput key
    # name ("space", "f1"), or a mouse button: "mouse_x1", "mouse_x2",
    # "mouse_middle", "mouse_right".
    "interact_key": "e",
    # Count a chest as "broken" when it vanishes while you are attacking (LMB).
    "count_attacks": True,
    # 0..1. Higher = stricter. Use `test` to see what scores you actually get.
    "match_threshold": 0.80,
    # Screen-checks per second.
    "fps": 20,
    # Consecutive frames needed to decide the prompt appeared / disappeared.
    # Filters out single-frame flicker (muzzle flash, storm effects, ...).
    "appear_frames": 2,
    "disappear_frames": 3,
    # How recent (seconds) your interact/attack input must be when the prompt
    # vanishes for it to count.
    "input_window_s": 0.75,
    # Minimum seconds between two counts. Prevents double counting.
    "cooldown_s": 1.5,
    # Region searched for the prompt: [left, top, width, height] in physical
    # screen pixels. Filled in by `calibrate`; null = whole monitor (slower).
    "region": None,
    # mss monitor index: 1 = primary monitor.
    "monitor": 1,
    # Downscale factor applied before matching (0.5 = half size, ~4x faster).
    "search_scale": 1.0,
    "hotkeys": {
        "increment": "f8",
        "decrement": "f9",
        "reset_session": "f10",
        "toggle_auto": "f11",
    },
    "calibrate_key": "f7",
    "overlay": True,
    "beep_on_count": False,
}


# --------------------------------------------------------------------------- #
# Config / persistence
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            sys.exit(f"config.json is not valid JSON: {exc}")
        hotkeys = {**cfg["hotkeys"], **user.get("hotkeys", {})}
        cfg.update(user)
        cfg["hotkeys"] = hotkeys
    else:
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    _atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class CounterStore:
    """Thread-safe counter persisted to disk after every change."""

    def __init__(self, path: Path = COUNT_FILE, obs_path: Path | None = OBS_TEXT_FILE):
        self.path = path
        self.obs_path = obs_path
        self._lock = threading.Lock()
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        self.total = int(data.get("total", 0))
        self.opened = int(data.get("opened", 0))
        self.broken = int(data.get("broken", 0))
        self.manual = int(data.get("manual", 0))
        self.session = 0
        self._save()

    def add(self, kind: str, amount: int = 1) -> int:
        with self._lock:
            if amount < 0 and self.total <= 0:
                return self.total
            self.total += amount
            self.session = max(0, self.session + amount)
            if kind == "opened":
                self.opened += amount
            elif kind == "broken":
                self.broken += amount
            else:
                self.manual += amount
            self._save()
            return self.total

    def reset_session(self) -> None:
        with self._lock:
            self.session = 0
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total": self.total,
                "session": self.session,
                "opened": self.opened,
                "broken": self.broken,
                "manual": self.manual,
            }

    def _save(self) -> None:
        data = {
            "total": self.total,
            "opened": self.opened,
            "broken": self.broken,
            "manual": self.manual,
            "last_session": self.session,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _atomic_write(self.path, json.dumps(data, indent=2))
        if self.obs_path is not None:
            _atomic_write(self.obs_path, f"Chests: {self.total}")


# --------------------------------------------------------------------------- #
# Detection logic (pure, no I/O -> unit-testable)
# --------------------------------------------------------------------------- #
class ChestEventDetector:
    """
    Turns a stream of "prompt seen this frame?" booleans plus input events into
    chest events. Call the input methods from any thread; call update() from
    the capture loop.
    """

    def __init__(
        self,
        appear_frames: int = 2,
        disappear_frames: int = 3,
        input_window_s: float = 0.75,
        cooldown_s: float = 1.5,
        count_attacks: bool = True,
    ):
        self.appear_frames = max(1, appear_frames)
        self.disappear_frames = max(1, disappear_frames)
        self.input_window_s = input_window_s
        self.cooldown_s = cooldown_s
        self.count_attacks = count_attacks

        self.visible = False
        self._hits = 0
        self._misses = 0
        self._first_miss_t = 0.0
        self._last_count_t = -math.inf

        self._lock = threading.Lock()
        self._interact_held = False
        self._interact_last = -math.inf  # last press or release
        self._attack_held = False
        self._attack_last = -math.inf

    # --- inputs ----------------------------------------------------------- #
    def interact(self, down: bool, t: float) -> None:
        with self._lock:
            self._interact_held = down
            self._interact_last = t

    def attack(self, down: bool, t: float) -> None:
        with self._lock:
            self._attack_held = down
            self._attack_last = t

    # --- frames ----------------------------------------------------------- #
    def update(self, prompt_seen: bool, now: float) -> str | None:
        """Returns "opened", "broken" or None."""
        if prompt_seen:
            self._hits += 1
            self._misses = 0
            if not self.visible and self._hits >= self.appear_frames:
                self.visible = True
            return None

        if self._misses == 0:
            self._first_miss_t = now
        self._misses += 1
        self._hits = 0
        if not (self.visible and self._misses >= self.disappear_frames):
            return None

        # Prompt has really gone. Was the player acting on it at that moment?
        self.visible = False
        vanish_t = self._first_miss_t
        if vanish_t - self._last_count_t < self.cooldown_s:
            return None
        kind = self._cause(vanish_t)
        if kind:
            self._last_count_t = vanish_t
        return kind

    def _cause(self, t: float) -> str | None:
        with self._lock:
            if self._interact_held or abs(t - self._interact_last) <= self.input_window_s:
                return "opened"
            if self.count_attacks and (
                self._attack_held or abs(t - self._attack_last) <= self.input_window_s
            ):
                return "broken"
        return None


class PromptMatcher:
    """Template matcher: max normalized correlation over all templates."""

    def __init__(self, templates, scale: float = 1.0):
        import cv2

        self.cv2 = cv2
        self.scale = scale
        self.templates = []
        for tpl in templates:
            if scale != 1.0:
                tpl = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            self.templates.append(tpl)

    def score(self, gray) -> float:
        cv2 = self.cv2
        if self.scale != 1.0:
            gray = cv2.resize(gray, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        best = 0.0
        h, w = gray.shape[:2]
        for tpl in self.templates:
            th, tw = tpl.shape[:2]
            if th > h or tw > w:
                continue
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            best = max(best, float(max_val))
        return best


def load_templates() -> list:
    import cv2

    TEMPLATES_DIR.mkdir(exist_ok=True)
    out = []
    for p in sorted(TEMPLATES_DIR.glob("*.png")):
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            out.append(img)
    return out


# --------------------------------------------------------------------------- #
# Screen capture / input helpers
# --------------------------------------------------------------------------- #
def enable_dpi_awareness() -> None:
    """Make screen coordinates physical pixels on Windows with display scaling."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def monitor_rect(sct, index: int) -> dict:
    mons = sct.monitors
    if index < 1 or index >= len(mons):
        index = 1
    return dict(mons[index])


def grab_gray(sct, rect: dict):
    import cv2
    import numpy as np

    img = np.asarray(sct.grab(rect))  # BGRA
    return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)


def search_rect(cfg: dict, sct) -> dict:
    if cfg.get("region"):
        left, top, width, height = cfg["region"]
        return {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
    return monitor_rect(sct, cfg["monitor"])


def key_name(key) -> str:
    """Normalise a pynput key to a lowercase string like 'e', 'f8', 'space'."""
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    name = getattr(key, "name", None)
    if name:
        return name.lower()
    vk = getattr(key, "vk", None)
    # Letters pressed together with Ctrl arrive without .char on Windows.
    if vk is not None and 65 <= vk <= 90:
        return chr(vk).lower()
    return str(key).lower()


MOUSE_INTERACT = {"mouse_x1": "x1", "mouse_x2": "x2", "mouse_middle": "middle", "mouse_right": "right"}


def beep() -> None:
    if sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(880, 80)
            return
        except Exception:
            pass
    print("\a", end="", flush=True)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_calibrate(cfg: dict) -> None:
    import cv2
    import mss
    from pynput import keyboard

    enable_dpi_awareness()
    hotkey = cfg["calibrate_key"].lower()
    print(
        "\nCALIBRATION\n"
        "  1. Open Fortnite (Windowed Fullscreen recommended) and walk up to a chest\n"
        "     so its interaction prompt ('Chest' + 'Search') is on screen.\n"
        f"  2. Press {hotkey.upper()} to take a screenshot.   (Esc cancels)\n"
    )

    shot = {}
    done = threading.Event()

    def on_press(key):
        name = key_name(key)
        if name == hotkey:
            with mss.mss() as sct:
                rect = monitor_rect(sct, cfg["monitor"])
                shot["img"] = grab_gray(sct, rect)
                shot["rect"] = rect
            done.set()
            return False
        if name == "esc":
            done.set()
            return False

    with keyboard.Listener(on_press=on_press):
        done.wait()

    if "img" not in shot:
        print("Cancelled.")
        return

    img = shot["img"]
    mon = shot["rect"]
    h, w = img.shape[:2]
    view_scale = min(1.0, 1600 / w, 900 / h)
    view = cv2.resize(img, None, fx=view_scale, fy=view_scale) if view_scale < 1 else img.copy()

    print(
        "Screenshot taken. Alt-Tab to the window titled 'Select chest prompt'.\n"
        "  Drag a TIGHT box around the word 'Chest' in the prompt (not 'Search' --\n"
        "  ammo boxes say 'Search' too), then press ENTER or SPACE.  C = cancel."
    )
    win = "Select chest prompt"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    try:
        cv2.setWindowProperty(win, cv2.WND_PROP_TOPMOST, 1)
    except Exception:
        pass
    x, y, bw, bh = cv2.selectROI(win, view, showCrosshair=False, fromCenter=False)
    cv2.destroyAllWindows()
    if bw == 0 or bh == 0:
        print("No area selected. Nothing saved.")
        return

    # Back to full-resolution coordinates.
    x, y = int(x / view_scale), int(y / view_scale)
    bw, bh = int(round(bw / view_scale)), int(round(bh / view_scale))
    crop = img[y : y + bh, x : x + bw]

    TEMPLATES_DIR.mkdir(exist_ok=True)
    tpl_path = TEMPLATES_DIR / f"chest_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imwrite(str(tpl_path), crop)

    # The prompt moves a bit depending on where the chest is, so search a
    # generous area around where it was seen, clipped to the monitor.
    pad_x, pad_y = max(150, bw * 2), max(120, bh * 4)
    rx0, ry0 = max(0, x - pad_x), max(0, y - pad_y)
    rx1, ry1 = min(w, x + bw + pad_x), min(h, y + bh + pad_y)
    new_region = [mon["left"] + rx0, mon["top"] + ry0, rx1 - rx0, ry1 - ry0]

    old = cfg.get("region")
    if old:  # union with the existing region so every template stays covered
        l = min(old[0], new_region[0])
        t = min(old[1], new_region[1])
        r = max(old[0] + old[2], new_region[0] + new_region[2])
        b = max(old[1] + old[3], new_region[1] + new_region[3])
        new_region = [l, t, r - l, b - t]
    cfg["region"] = new_region
    save_config(cfg)

    print(f"\nSaved template: {tpl_path}")
    print(f"Search region set to {new_region} (left, top, width, height) in config.json")
    print("Next: run `python chest_counter.py test`, look at a chest, and check the score.")


def cmd_test(cfg: dict) -> None:
    import mss

    enable_dpi_awareness()
    templates = load_templates()
    if not templates:
        sys.exit("No templates found. Run `python chest_counter.py calibrate` first.")
    matcher = PromptMatcher(templates, cfg["search_scale"])
    thr = cfg["match_threshold"]
    print(f"Live match score (threshold {thr}). Look at chests / non-chests. Ctrl+C to stop.\n")
    with mss.mss() as sct:
        rect = search_rect(cfg, sct)
        try:
            while True:
                t0 = time.perf_counter()
                s = matcher.score(grab_gray(sct, rect))
                ms = (time.perf_counter() - t0) * 1000
                bar = "#" * int(s * 40)
                tag = "CHEST" if s >= thr else "     "
                print(f"\r{s:0.3f} {tag} {bar:<40} ({ms:4.1f} ms/frame)", end="", flush=True)
                time.sleep(0.2)
        except KeyboardInterrupt:
            print()


class App:
    def __init__(self, cfg: dict, manual_only: bool):
        self.cfg = cfg
        self.store = CounterStore()
        self.stop = threading.Event()
        self.auto_enabled = not manual_only
        self.manual_only = manual_only
        self.status = "manual only" if manual_only else "starting"
        self.detector = ChestEventDetector(
            cfg["appear_frames"],
            cfg["disappear_frames"],
            cfg["input_window_s"],
            cfg["cooldown_s"],
            cfg["count_attacks"],
        )
        self.matcher = None
        if not manual_only:
            templates = load_templates()
            if not templates:
                sys.exit(
                    "No templates found. Run `python chest_counter.py calibrate` first,\n"
                    "or start with --manual to use hotkeys only."
                )
            self.matcher = PromptMatcher(templates, cfg["search_scale"])

    # --- counting --------------------------------------------------------- #
    def count(self, kind: str, amount: int = 1) -> None:
        total = self.store.add(kind, amount)
        snap = self.store.snapshot()
        sign = "+" if amount > 0 else ""
        print(
            f"[{time.strftime('%H:%M:%S')}] {kind:<7} {sign}{amount}  ->  total {total}"
            f"  (session {snap['session']})"
        )
        if amount > 0 and self.cfg["beep_on_count"]:
            beep()

    # --- inputs ----------------------------------------------------------- #
    def start_input_listeners(self) -> None:
        from pynput import keyboard, mouse

        hk = {v.lower(): k for k, v in self.cfg["hotkeys"].items()}
        interact = self.cfg["interact_key"].lower()
        mouse_interact = MOUSE_INTERACT.get(interact)

        def on_press(key):
            name = key_name(key)
            if name == interact:
                self.detector.interact(True, time.monotonic())
            action = hk.get(name)
            if action == "increment":
                self.count("manual", +1)
            elif action == "decrement":
                self.count("manual", -1)
            elif action == "reset_session":
                self.store.reset_session()
                print(f"[{time.strftime('%H:%M:%S')}] session reset")
            elif action == "toggle_auto" and not self.manual_only:
                self.auto_enabled = not self.auto_enabled
                print(f"[{time.strftime('%H:%M:%S')}] auto-detect {'ON' if self.auto_enabled else 'PAUSED'}")

        def on_release(key):
            if key_name(key) == interact:
                self.detector.interact(False, time.monotonic())

        def on_click(_x, _y, button, pressed):
            bname = getattr(button, "name", "")
            if bname == "left":
                self.detector.attack(pressed, time.monotonic())
            if mouse_interact and bname == mouse_interact:
                self.detector.interact(pressed, time.monotonic())

        self._kb = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._ms = mouse.Listener(on_click=on_click)
        self._kb.daemon = self._ms.daemon = True
        self._kb.start()
        self._ms.start()

    # --- capture loop ----------------------------------------------------- #
    def detection_loop(self) -> None:
        import mss

        period = 1.0 / max(1, self.cfg["fps"])
        thr = self.cfg["match_threshold"]
        with mss.mss() as sct:
            rect = search_rect(self.cfg, sct)
            while not self.stop.is_set():
                t0 = time.monotonic()
                if self.auto_enabled:
                    try:
                        seen = self.matcher.score(grab_gray(sct, rect)) >= thr
                    except Exception as exc:  # e.g. capture failure on display change
                        self.status = f"capture error: {exc}"
                        time.sleep(1)
                        continue
                    kind = self.detector.update(seen, t0)
                    self.status = "chest in view" if self.detector.visible else "watching"
                    if kind:
                        self.count(kind)
                else:
                    self.status = "paused"
                self.stop.wait(max(0.0, period - (time.monotonic() - t0)))

    # --- UI ---------------------------------------------------------------- #
    def run(self) -> None:
        enable_dpi_awareness()
        self.start_input_listeners()
        if not self.manual_only:
            threading.Thread(target=self.detection_loop, daemon=True).start()

        hk = self.cfg["hotkeys"]
        snap = self.store.snapshot()
        print(
            f"Chest counter running. Total so far: {snap['total']}\n"
            f"  {hk['increment'].upper()} +1   {hk['decrement'].upper()} -1   "
            f"{hk['reset_session'].upper()} reset session   "
            f"{hk['toggle_auto'].upper()} pause auto-detect\n"
            "  Ctrl+C here (or right-click the overlay) to quit.\n"
        )

        if self.cfg["overlay"]:
            try:
                self.run_overlay()
                return
            except Exception as exc:  # no display / tkinter missing
                print(f"Overlay unavailable ({exc}); running in console only.")
        try:
            while not self.stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        self.stop.set()

    def run_overlay(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.title("Chest Counter")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        root.configure(bg="#111")
        root.geometry("+20+20")

        big = tk.Label(root, text="", font=("Segoe UI", 22, "bold"), fg="#ffd34d", bg="#111", padx=12)
        small = tk.Label(root, text="", font=("Segoe UI", 9), fg="#ccc", bg="#111", padx=12)
        big.pack(anchor="w", pady=(6, 0))
        small.pack(anchor="w", pady=(0, 6))

        drag = {}

        def start_drag(e):
            drag["x"], drag["y"] = e.x, e.y

        def do_drag(e):
            root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

        def quit_app(_e=None):
            self.stop.set()
            root.destroy()

        for widget in (root, big, small):
            widget.bind("<Button-1>", start_drag)
            widget.bind("<B1-Motion>", do_drag)
            widget.bind("<Button-3>", quit_app)

        def refresh():
            if self.stop.is_set():
                root.destroy()
                return
            s = self.store.snapshot()
            big.config(text=f"Chests: {s['total']}")
            small.config(
                text=f"session {s['session']}  |  opened {s['opened']}  broken {s['broken']}"
                f"  manual {s['manual']}\n{self.status}"
            )
            root.after(150, refresh)

        refresh()
        try:
            root.mainloop()
        except KeyboardInterrupt:
            pass
        self.stop.set()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Count chests opened/broken in Fortnite.")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "calibrate", "test"])
    parser.add_argument("--manual", action="store_true", help="hotkeys only, no screen detection")
    parser.add_argument("--no-overlay", action="store_true", help="console output only")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.no_overlay:
        cfg["overlay"] = False

    if args.command == "calibrate":
        cmd_calibrate(cfg)
    elif args.command == "test":
        cmd_test(cfg)
    else:
        App(cfg, manual_only=args.manual).run()


if __name__ == "__main__":
    main()
