#!/usr/bin/env python3
r"""
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
  python chest_counter.py odds        chance you have had the token so far, and milestones
  python chest_counter.py log         show which Fortnite log lines start/end a match

Per-match counting: match start/end is read from Fortnite's own log file
(%LOCALAPPDATA%\FortniteGame\Saved\Logs\FortniteGame.log). F12 starts a new
match by hand if the log isn't available or misses one.

Default hotkeys while running:
  F8  +1      F9  -1      F10  reset session      F11  pause/resume auto-detect
  F12 start a new match
"""
from __future__ import annotations

import argparse
import codecs
import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"
CONFIG_FILE = HERE / "config.json"
COUNT_FILE = HERE / "chest_count.json"
OBS_TEXT_FILE = HERE / "chest_count.txt"
OBS_CHANCE_FILE = HERE / "token_chance.txt"
OBS_MATCH_FILE = HERE / "match_count.txt"
OBS_SQUAD_FILE = HERE / "squad_count.txt"

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
        "new_match": "f12",
    },
    "calibrate_key": "f7",
    "overlay": True,
    "beep_on_count": False,
    # Token drop odds: 1 in this many chests. Used for the "chance you have
    # already got it" readout: P = 1 - (1 - 1/odds)^chests.
    "token_odds": 137500,
    # Estimated squad-wide chests = your chests x this. Your own chests are
    # counted exactly; teammates' can't be seen, so this is an estimate.
    "squad_multiplier": 3.0,
    # --- per-match counter ---
    # Detect match start/end from Fortnite's log file.
    "auto_match_from_log": True,
    # null = %LOCALAPPDATA%\FortniteGame\Saved\Logs\FortniteGame.log
    "fortnite_log": None,
    # A line matching this starts a match (any map load that isn't the lobby).
    "match_start_regex": r"LogLoad: LoadMap: (?!.*Frontend)",
    # A line matching this ends it (back to the lobby, or the match finished).
    "match_end_regex": r"LogLoad: LoadMap: .*Frontend|Match State Changed from \w+ to WaitingPostMatch",
    # Ignore a second "start" line within this many seconds of the first.
    "match_min_gap_s": 30,
    # If the script is restarted, continue the saved match when its last
    # activity was within this many seconds; otherwise file it as finished.
    "match_resume_window_s": 900,
    # How many finished matches to keep in chest_count.json.
    "match_history_size": 500,
}


# --------------------------------------------------------------------------- #
# Token probability
# --------------------------------------------------------------------------- #
def token_chance(chests: float, odds: float) -> float:
    """P(at least one token in `chests` independent tries at 1/odds each)."""
    if chests <= 0 or odds <= 0:
        return 0.0
    if odds <= 1:
        return 1.0
    # log1p/expm1 keep full precision when 1/odds is tiny.
    return -math.expm1(chests * math.log1p(-1.0 / odds))


def chests_for_chance(target: float, odds: float) -> int:
    """Chests needed for P(at least one token) >= target."""
    if odds <= 1:
        return 1
    return math.ceil(math.log1p(-target) / math.log1p(-1.0 / odds))


def format_chance(p: float) -> str:
    pct = p * 100
    if pct == 0:
        return "0%"
    if pct < 0.01:
        return f"{pct:.4f}%"
    if pct < 10:
        return f"{pct:.3f}%"
    return f"{pct:.2f}%"


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
    """Thread-safe chest + match counter, persisted to disk after every change."""

    def __init__(
        self,
        path: Path = COUNT_FILE,
        obs_path: Path | None = OBS_TEXT_FILE,
        token_odds: float = DEFAULT_CONFIG["token_odds"],
        chance_path: Path | None = OBS_CHANCE_FILE,
        match_path: Path | None = OBS_MATCH_FILE,
        squad_multiplier: float = DEFAULT_CONFIG["squad_multiplier"],
        squad_path: Path | None = OBS_SQUAD_FILE,
        history_size: int = DEFAULT_CONFIG["match_history_size"],
        resume_window_s: float = DEFAULT_CONFIG["match_resume_window_s"],
        clock=time.time,
    ):
        self.path = path
        self.obs_path = obs_path
        self.token_odds = token_odds
        self.chance_path = chance_path
        self.match_path = match_path
        self.squad_multiplier = max(0.0, float(squad_multiplier))
        self.squad_path = squad_path
        self.history_size = max(1, history_size)
        self.clock = clock
        self._lock = threading.Lock()
        self.backup_path = path.with_suffix(path.suffix + ".bak")
        data = self._load()
        self.total = int(data.get("total", 0))
        self.opened = int(data.get("opened", 0))
        self.broken = int(data.get("broken", 0))
        self.manual = int(data.get("manual", 0))
        self.session = 0

        history = [m for m in data.get("matches", []) if isinstance(m, dict)]
        self.matches: list[dict] = history[-self.history_size :]
        self.matches_played = int(data.get("matches_played", len(history)))
        self.match_chests_sum = int(
            data.get("match_chests_sum", sum(int(m.get("chests", 0)) for m in history))
        )
        self.best_match = int(
            data.get("best_match", max((int(m.get("chests", 0)) for m in history), default=0))
        )
        self.current: dict | None = None
        saved = data.get("current_match")
        if isinstance(saved, dict):
            last = float(saved.get("last_activity", saved.get("started", 0)))
            if self.clock() - last <= resume_window_s:
                self.current = saved
            else:
                self._finalize(saved, ended=last)
        self._save()

    def _load(self) -> dict:
        """Load saved counts. Falls back to the backup if the main file is
        damaged, and never silently replaces a damaged file with zeros."""
        for candidate in (self.path, self.backup_path):
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                int(data.get("total", 0))
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                broken = candidate.with_name(
                    f"{candidate.name}.corrupt-{time.strftime('%Y%m%d_%H%M%S')}"
                )
                os.replace(candidate, broken)
                print(f"Warning: {candidate.name} was damaged; kept a copy as {broken.name}")
                continue
            if candidate is self.backup_path:
                print(f"Restored counts from backup {candidate.name}")
            return data
        return {}

    # --- chests ----------------------------------------------------------- #
    def add(self, kind: str, amount: int = 1) -> int:
        with self._lock:
            if amount < 0 and self.total <= 0:
                return self.total
            now = self.clock()
            self.total += amount
            self.session = max(0, self.session + amount)
            if kind == "opened":
                self.opened += amount
            elif kind == "broken":
                self.broken += amount
            else:
                self.manual += amount
            # A detected chest means you are in a match, even if the start of
            # it was missed. Manual +/- between matches only touches the total.
            if self.current is None and amount > 0 and kind in ("opened", "broken"):
                self._start_locked(now)
            if self.current is not None:
                self.current["chests"] = max(0, int(self.current["chests"]) + amount)
                self.current["last_activity"] = now
            self._save()
            return self.total

    def reset_session(self) -> None:
        with self._lock:
            self.session = 0
            self._save()

    # --- matches ---------------------------------------------------------- #
    def start_match(self) -> int:
        """End the current match (if any) and start a new one. Returns its number."""
        with self._lock:
            now = self.clock()
            if self.current is not None:
                self._finalize(self.current, ended=now)
            number = self._start_locked(now)
            self._save()
            return number

    def end_match(self) -> dict | None:
        """End the current match. Returns the finished match, or None."""
        with self._lock:
            if self.current is None:
                return None
            finished = self._finalize(self.current, ended=self.clock())
            self.current = None
            self._save()
            return finished

    @property
    def in_match(self) -> bool:
        with self._lock:
            return self.current is not None

    def _start_locked(self, now: float) -> int:
        number = self.matches_played + 1
        self.current = {"number": number, "started": now, "last_activity": now, "chests": 0}
        return number

    def _finalize(self, match: dict, ended: float) -> dict:
        done = {
            "number": int(match.get("number", self.matches_played + 1)),
            "started": match.get("started"),
            "ended": ended,
            "chests": int(match.get("chests", 0)),
        }
        self.matches.append(done)
        del self.matches[: -self.history_size]
        self.matches_played += 1
        self.match_chests_sum += done["chests"]
        self.best_match = max(self.best_match, done["chests"])
        return done

    # --- read ------------------------------------------------------------- #
    def _squad(self, chests: float) -> float:
        return chests * self.squad_multiplier

    def snapshot(self) -> dict:
        with self._lock:
            played = self.matches_played
            squad_total = self._squad(self.total)
            return {
                "squad_multiplier": self.squad_multiplier,
                "squad_total": squad_total,
                "squad_chance": token_chance(squad_total, self.token_odds),
                "squad_match_chests": self._squad(self.current["chests"]) if self.current else None,
                "total": self.total,
                "session": self.session,
                "opened": self.opened,
                "broken": self.broken,
                "manual": self.manual,
                "token_chance": token_chance(self.total, self.token_odds),
                "match_number": self.current["number"] if self.current else None,
                "match_chests": self.current["chests"] if self.current else None,
                "matches_played": played,
                "avg_per_match": (self.match_chests_sum / played) if played else None,
                "best_match": self.best_match,
                "last_match_chests": self.matches[-1]["chests"] if self.matches else None,
            }

    def _save(self) -> None:
        data = {
            "total": self.total,
            "opened": self.opened,
            "broken": self.broken,
            "manual": self.manual,
            "last_session": self.session,
            "token_odds": self.token_odds,
            "token_chance": token_chance(self.total, self.token_odds),
            "squad_multiplier": self.squad_multiplier,
            "squad_total_estimate": round(self._squad(self.total)),
            "squad_token_chance": token_chance(self._squad(self.total), self.token_odds),
            "matches_played": self.matches_played,
            "match_chests_sum": self.match_chests_sum,
            "best_match": self.best_match,
            "current_match": self.current,
            "matches": self.matches,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        text = json.dumps(data, indent=2)
        _atomic_write(self.path, text)
        _atomic_write(self.backup_path, text)
        if self.obs_path is not None:
            _atomic_write(self.obs_path, f"Chests: {self.total}")
        if self.chance_path is not None:
            chance = format_chance(token_chance(self.total, self.token_odds))
            _atomic_write(self.chance_path, f"Token chance: {chance}")
        if self.match_path is not None:
            n = self.current["chests"] if self.current else "-"
            _atomic_write(self.match_path, f"This match: {n}")
        if self.squad_path is not None:
            squad = self._squad(self.total)
            chance = format_chance(token_chance(squad, self.token_odds))
            _atomic_write(
                self.squad_path,
                f"Squad chests: ~{round(squad):,} (x{self.squad_multiplier:g})  Token chance: {chance}",
            )


# --------------------------------------------------------------------------- #
# Match detection from Fortnite's log file
# --------------------------------------------------------------------------- #
def default_log_path() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "FortniteGame" / "Saved" / "Logs" / "FortniteGame.log"


def resolve_log_path(cfg: dict) -> Path | None:
    return Path(cfg["fortnite_log"]).expanduser() if cfg.get("fortnite_log") else default_log_path()


class MatchLogParser:
    """Turns Fortnite log lines into "start" / "end" match events."""

    def __init__(self, start_regex: str, end_regex: str, min_gap_s: float = 30):
        self.start_re = re.compile(start_regex)
        self.end_re = re.compile(end_regex)
        self.min_gap_s = min_gap_s
        self.in_match = False
        self._last_start = -math.inf

    def feed(self, line: str, now: float) -> str | None:
        if self.end_re.search(line):
            if self.in_match:
                self.in_match = False
                return "end"
            return None
        if self.start_re.search(line):
            if self.in_match and now - self._last_start < self.min_gap_s:
                return None  # duplicate load line for the same match
            self.in_match = True
            self._last_start = now
            return "start"
        return None


class LogTailer:
    """Follows FortniteGame.log (like `tail -f`), handling the file being
    recreated each time the game launches, UTF-8 or UTF-16."""

    def __init__(self, path: Path, parser: MatchLogParser, on_event, poll_s: float = 1.0):
        self.path = path
        self.parser = parser
        self.on_event = on_event
        self.poll_s = poll_s
        self.pos: int | None = None
        self._buf = ""
        self._decoder = None

    def _read_new(self, size: int) -> list[str]:
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            chunk = f.read(size - self.pos)
        start_pos = self.pos
        self.pos += len(chunk)
        if start_pos == 0 or self._decoder is None:
            if chunk.startswith(codecs.BOM_UTF16_LE):
                self._decoder = codecs.getincrementaldecoder("utf-16-le")("replace")
                chunk = chunk[2:]
            else:
                if chunk.startswith(codecs.BOM_UTF8):
                    chunk = chunk[3:]
                self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buf += self._decoder.decode(chunk)
        lines = self._buf.split("\n")
        self._buf = lines.pop()
        return lines

    def replay_existing(self) -> bool:
        """Read what is already in the log (without firing events) to learn
        whether a match is in progress right now."""
        self.pos, self._buf, self._decoder = 0, "", None
        try:
            size = self.path.stat().st_size
        except OSError:
            self.pos = None
            return False
        for line in self._read_new(size):
            self.parser.feed(line, 0.0)
        self.parser._last_start = -math.inf
        return self.parser.in_match

    def poll_once(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if self.pos is None:
            self.pos = size  # start following from the end
            return
        if size < self.pos:
            # Game relaunched: the log was recreated, so any match is over.
            self.pos, self._buf, self._decoder = 0, "", None
            if self.parser.in_match:
                self.parser.in_match = False
                self.on_event("end", "(Fortnite restarted)")
        if size > self.pos:
            for line in self._read_new(size):
                ev = self.parser.feed(line, time.monotonic())
                if ev:
                    self.on_event(ev, line.strip())

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.poll_once()
            except OSError:
                pass
            stop.wait(self.poll_s)


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


def make_store(cfg: dict) -> CounterStore:
    return CounterStore(
        token_odds=cfg["token_odds"],
        history_size=cfg["match_history_size"],
        resume_window_s=cfg["match_resume_window_s"],
        squad_multiplier=cfg["squad_multiplier"],
    )


def cmd_odds(cfg: dict) -> None:
    odds = cfg["token_odds"]
    snap = make_store(cfg).snapshot()
    total, avg = snap["total"], snap["avg_per_match"]
    print(f"Token odds: 1 in {odds:,} per chest")
    print(f"Chests counted: {total:,}")
    print(f"Chance from your own chests only: {format_chance(token_chance(total, odds))}")
    if avg:
        print(
            f"Matches: {snap['matches_played']:,} finished, average {avg:.1f} chests/match, "
            f"best {snap['best_match']}"
        )
    print()
    for target in (0.25, 0.5, 1 - 1 / math.e, 0.9, 0.95, 0.99):
        need = chests_for_chance(target, odds)
        left = max(0, need - total)
        line = f"  {target * 100:5.1f}% at {need:>9,} chests   ({left:,} to go"
        if avg:
            line += f", ~{math.ceil(left / avg):,} matches at your average"
        print(line + ")")

    mult = snap["squad_multiplier"]
    squad = snap["squad_total"]
    print(f"\nWITH YOUR SQUAD (the token goes to the whole team; estimate = your chests x {mult:g})")
    print(f"Squad chests: ~{round(squad):,}")
    print(
        f"YOUR REAL TOKEN CHANCE (team-wide drops): "
        f"{format_chance(snap['squad_chance'])}\n"
    )
    if mult > 0:
        for target in (0.25, 0.5, 1 - 1 / math.e, 0.9, 0.95, 0.99):
            need = chests_for_chance(target, odds)
            yours = math.ceil(need / mult)
            left = max(0, yours - total)
            line = f"  {target * 100:5.1f}% at {need:>9,} squad chests = {yours:>9,} of yours   ({left:,} to go"
            if avg:
                line += f", ~{math.ceil(left / avg):,} matches"
            print(line + ")")


def cmd_log(cfg: dict) -> None:
    path = resolve_log_path(cfg)
    if path is None or not path.exists():
        sys.exit(f"Fortnite log not found at {path}. Set \"fortnite_log\" in config.json.")
    parser = MatchLogParser(cfg["match_start_regex"], cfg["match_end_regex"], cfg["match_min_gap_s"])
    hint = re.compile(r"LoadMap|Match ?State|Matchmaking|Frontend", re.I)
    print(f"Reading {path}\nLines tagged START/END are what the counter reacts to.\n")

    def show(tag, line):
        print(f"{tag:<6} {line.strip()[:200]}")

    tailer = LogTailer(path, parser, on_event=lambda ev, line: None)
    tailer.pos, tailer._decoder = 0, None
    for line in tailer._read_new(path.stat().st_size):
        ev = parser.feed(line, 0.0)
        if ev:
            show(ev.upper(), line)
        elif hint.search(line):
            show("", line)
    parser._last_start = -math.inf
    print(f"\n-- now {'IN A MATCH' if parser.in_match else 'not in a match'}; following live (Ctrl+C to stop) --\n")
    tailer.on_event = lambda ev, line: show(ev.upper(), line)
    try:
        while True:
            tailer.poll_once()
            time.sleep(1)
    except KeyboardInterrupt:
        print()


class App:
    def __init__(self, cfg: dict, manual_only: bool):
        self.cfg = cfg
        self.store = make_store(cfg)
        self.log_status = "log: off"
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
        match = "" if snap["match_chests"] is None else f"  match #{snap['match_number']}: {snap['match_chests']}"
        print(
            f"[{time.strftime('%H:%M:%S')}] {kind:<7} {sign}{amount}  ->  total {total}{match}"
            f"  (session {snap['session']})  token chance {format_chance(snap['token_chance'])}"
            f"  | squad ~{round(snap['squad_total']):,} -> real chance {format_chance(snap['squad_chance'])}"
        )
        if amount > 0 and self.cfg["beep_on_count"]:
            beep()

    # --- matches ---------------------------------------------------------- #
    def new_match(self, why: str) -> None:
        prev = self.store.snapshot()
        number = self.store.start_match()
        if prev["match_number"] is not None:
            print(f"[{time.strftime('%H:%M:%S')}] match #{prev['match_number']} over: {prev['match_chests']} chests")
        print(f"[{time.strftime('%H:%M:%S')}] match #{number} started ({why})")

    def finish_match(self, why: str) -> None:
        done = self.store.end_match()
        if done:
            snap = self.store.snapshot()
            avg = snap["avg_per_match"] or 0
            print(
                f"[{time.strftime('%H:%M:%S')}] match #{done['number']} over ({why}): "
                f"{done['chests']} chests   (average {avg:.1f}, best {snap['best_match']})"
            )

    def on_log_event(self, event: str, line: str) -> None:
        if event == "start":
            self.new_match("log")
        elif event == "end":
            self.finish_match("log")

    def start_log_watcher(self) -> None:
        if not self.cfg["auto_match_from_log"]:
            return
        path = resolve_log_path(self.cfg)
        if path is None or not path.exists():
            self.log_status = f"log: not found ({self.cfg['hotkeys']['new_match'].upper()} = new match)"
            print(f"Fortnite log not found at {path}; use {self.cfg['hotkeys']['new_match'].upper()} to start matches.")
            return
        try:
            parser = MatchLogParser(
                self.cfg["match_start_regex"], self.cfg["match_end_regex"], self.cfg["match_min_gap_s"]
            )
        except re.error as exc:
            print(f"Bad match regex in config.json ({exc}); log watching disabled.")
            return
        tailer = LogTailer(path, parser, self.on_log_event)
        # Sync with what the game is doing right now.
        in_match = tailer.replay_existing()
        if in_match and not self.store.in_match:
            self.new_match("already in progress")
        elif not in_match and self.store.in_match:
            self.finish_match("not in a match any more")
        threading.Thread(target=tailer.run, args=(self.stop,), daemon=True).start()
        self.log_status = "log: watching"

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
            elif action == "new_match":
                self.new_match("hotkey")
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
        self.start_log_watcher()
        if not self.manual_only:
            threading.Thread(target=self.detection_loop, daemon=True).start()

        hk = self.cfg["hotkeys"]
        snap = self.store.snapshot()
        odds = self.cfg["token_odds"]
        print(
            f"Chest counter running. Total so far: {snap['total']}\n"
            f"  Token odds 1 in {odds:,}: chance you have had it by now "
            f"{format_chance(snap['token_chance'])}  "
            f"(50% at {chests_for_chance(0.5, odds):,} chests, "
            f"90% at {chests_for_chance(0.9, odds):,})\n"
            f"  Squad estimate (x{snap['squad_multiplier']:g}): ~{round(snap['squad_total']):,} chests -> "
            f"your real token chance {format_chance(snap['squad_chance'])} (token is team-wide)\n"
            f"  {hk['increment'].upper()} +1   {hk['decrement'].upper()} -1   "
            f"{hk['reset_session'].upper()} reset session   "
            f"{hk['toggle_auto'].upper()} pause auto-detect   "
            f"{hk['new_match'].upper()} new match\n"
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
        mid = tk.Label(root, text="", font=("Segoe UI", 14, "bold"), fg="#7fd4ff", bg="#111", padx=12)
        squad = tk.Label(root, text="", font=("Segoe UI", 12, "bold"), fg="#9be89b", bg="#111", padx=12, justify="left")
        small = tk.Label(root, text="", font=("Segoe UI", 9), fg="#ccc", bg="#111", padx=12, justify="left")
        big.pack(anchor="w", pady=(6, 0))
        mid.pack(anchor="w")
        squad.pack(anchor="w")
        small.pack(anchor="w", pady=(0, 6))

        drag = {}

        def start_drag(e):
            drag["x"], drag["y"] = e.x, e.y

        def do_drag(e):
            root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

        def quit_app(_e=None):
            self.stop.set()
            root.destroy()

        for widget in (root, big, mid, squad, small):
            widget.bind("<Button-1>", start_drag)
            widget.bind("<B1-Motion>", do_drag)
            widget.bind("<Button-3>", quit_app)

        def refresh():
            if self.stop.is_set():
                root.destroy()
                return
            s = self.store.snapshot()
            big.config(text=f"Chests: {s['total']}")
            if s["match_chests"] is None:
                match_line = "This match: -  (in lobby)"
            else:
                match_line = f"This match: {s['match_chests']}  (match #{s['match_number']})"
            mid.config(text=match_line)
            sm = s["squad_match_chests"]
            squad.config(
                text=f"Squad total (x{s['squad_multiplier']:g}): ~{round(s['squad_total']):,}"
                + ("" if sm is None else f"   this match ~{round(sm)}")
                + f"\nToken chance (team): {format_chance(s['squad_chance'])}"
            )
            avg = "-" if s["avg_per_match"] is None else f"{s['avg_per_match']:.1f}"
            small.config(
                text=f"avg {avg}/match  best {s['best_match']}  last {s['last_match_chests'] if s['last_match_chests'] is not None else '-'}\n"
                f"Own chests only: {format_chance(s['token_chance'])}"
                f"  (1 in {self.cfg['token_odds']:,})\n"
                f"session {s['session']}  |  opened {s['opened']}  broken {s['broken']}"
                f"  manual {s['manual']}\n{self.status}  |  {self.log_status}"
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
    parser.add_argument("command", nargs="?", default="run", choices=["run", "calibrate", "test", "odds", "log"])
    parser.add_argument("--manual", action="store_true", help="hotkeys only, no screen detection")
    parser.add_argument("--no-overlay", action="store_true", help="console output only")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.no_overlay:
        cfg["overlay"] = False

    if args.command == "odds":
        cmd_odds(cfg)
    elif args.command == "log":
        cmd_log(cfg)
    elif args.command == "calibrate":
        cmd_calibrate(cfg)
    elif args.command == "test":
        cmd_test(cfg)
    else:
        App(cfg, manual_only=args.manual).run()


if __name__ == "__main__":
    main()
