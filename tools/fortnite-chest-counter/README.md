# Fortnite chest counter

Counts the chests you open or break in Fortnite and keeps a running total across sessions.

Fortnite has no API or log line for chest events, so the script watches from outside the game:

1. It looks for the **"Chest"** interaction prompt in a small area of the screen (OpenCV template match against a crop you take once).
2. It listens to your **interact key** and **left mouse button**.
3. If the prompt disappears while you're holding the interact key (or just let go of it), that counts as **opened**. If it disappears while you're attacking, that counts as **broken**.

It only captures the screen and listens to input, the same way OBS and Discord do. It never reads game memory or sends input to the game.

## The app

A desktop app with five pages:

| Page | What it shows |
| --- | --- |
| **Dashboard** | Your chests, squad estimate, this match, this session, your team token chance with the next milestone, buttons for every action, and a live activity feed |
| **Matches** | Matches tracked, average, best and last, a chart of your last 30 matches, and the full match history |
| **Token odds** | Team and own-chests chances, the probability curve with "you are here", and a milestone table (chests and matches still to go) |
| **Calibration** | Capture the chest prompt with **F7** or a 5-second timer, a two-step crop (rough box, then zoomed-in exact box), a live detection meter with a threshold slider, and your saved templates |
| **Settings** | Every option, checked before saving and applied without a restart, plus the data folder |

The sidebar shows detection, match log and hotkey status at all times. It also has a switch for the **in-game overlay**, a small always-on-top box with your chests, this match and team chance. Drag the overlay to move it; right-click it to hide it.

### Install and run (Windows)

1. Install [Python 3.10+](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"**.
2. In this folder, run:
   ```bat
   pip install -r requirements.txt
   ```
3. Double-click **`ChestCounter.pyw`**. It opens with no console window. You can also run `python chest_counter.py`.

On first launch the app opens on **Calibration**. Follow the four steps there once.

### Build a standalone .exe (optional)

Double-click **`build_exe.bat`**. It installs PyInstaller and builds `dist\ChestCounter\ChestCounter.exe`. You can pin that to your taskbar, and it runs without Python.

The .exe keeps its data in `%LOCALAPPDATA%\ChestCounter`, not next to the .exe, because every rebuild replaces the `dist` folder. The first build copies your existing counts, settings and templates there. Settings → Data → **Open** shows the folder.

### Hotkeys (work in-game)

| Key | Action |
| --- | --- |
| F8 | +1 (fix a missed chest) |
| F9 | −1 (fix a false count) |
| F10 | reset the session counter |
| F11 | pause or resume auto-detect |
| F12 | start a new match by hand (ends the current one) |
| F7 | take the calibration screenshot (only after clicking *Wait for F7*) |

You can change all of them in Settings.

### Command-line version

Everything still works without the app:

```bat
python chest_counter.py run            console + overlay + auto-detect + hotkeys
python chest_counter.py run --manual   hotkeys only
python chest_counter.py calibrate      calibration without the app
python chest_counter.py test           live match score in the console
python chest_counter.py odds           token chances and milestones
```

## Adding chests you opened before

In the app: Dashboard → **Add past chests…**. Type a number, choose *My own chests* or *Whole squad*, and the dialog shows your new total and token chance before you confirm. From the command line:

```bat
python chest_counter.py add 5000           add 5,000 of your own chests
python chest_counter.py add -20            take 20 off again
python chest_counter.py set 12345          set your own total to exactly 12,345
python chest_counter.py add 9000 --squad   9,000 is a squad-wide number -> adds 9000 / 3 = 3,000
```

Each command prints your total before and after, plus both token chances. Numbers you add go into the all-time total, so the squad estimate and both chances update with them. They show in the overlay as "added", separately from opened, broken and manual. The session counter, current match and per-match average stay as they are, because those chests didn't happen in a tracked match.

You can run these while the counter is open. The running counter notices the change and builds on it instead of overwriting it.

Enter **your own** chests. The ×3 squad multiplier is applied on top, so if your number already includes your teammates, add `--squad`, or you'll count them twice.

## Per-match counter

The overlay shows **This match: N**, along with your average, best and last match. Each finished match is saved to `chest_count.json` with its start time, end time and chest count.

**How matches are detected.** Fortnite writes a log while it runs, at `%LOCALAPPDATA%\FortniteGame\Saved\Logs\FortniteGame.log`. The counter follows that file:

- a map load that isn't the lobby (`LogLoad: LoadMap: …`, without `Frontend`) **starts** a match
- loading back into the lobby (`Frontend`), or `Match State Changed … to WaitingPostMatch`, **ends** it
- a game relaunch ends any match that was still open

**I haven't been able to check these log lines against your game version.** Check them once:

```bat
python chest_counter.py log
```

This prints the map and match lines already in the log, tagged `START` / `END`, then follows new lines live. Play one match and confirm you see one `START` when you drop in and one `END` when you're back in the lobby. If the tags are wrong, find the right lines in that output and change `match_start_regex` / `match_end_regex` in `config.json`.

**Fallbacks:**
- **F12** starts a new match by hand at any time.
- A chest detected while no match is open starts one automatically, so a missed start doesn't lose chests. Manual F8/F9 in the lobby only changes the total.
- If you restart the script within 15 minutes (`match_resume_window_s`), it carries on with the same match. After a longer gap, that match is saved as finished.

## Token chance

The overlay, the console and `python chest_counter.py odds` also show the chance that you'd have got the token by now. The odds are set by `token_odds` in `config.json` (default 1 in 137,500 per chest):

P = 1 − (1 − 1/137500)^n, where n = chests counted

Every chest is an independent roll, so your odds on the *next* chest stay 1 in 137,500 however many you've opened. The number only describes how lucky or unlucky your run has been so far.

## Squad total (estimate)

When you play squads, teammates open chests too. The counter can't see their chests, so it estimates the squad total as your chests × `squad_multiplier` (default **3**, set in `config.json`). It shows that total, the squad's share of the current match, and a second token chance:

P_squad = 1 − (1 − 1/137500)^(3n)

**This is your real token chance.** The token goes to the whole team, so every chest any squadmate opens is a roll for you too. The overlay shows it as **Token chance (team)**. The "own chests only" number is a lower bound: it counts only the chests you open yourself.

**Why ×3 and not ×4.** The squad total includes you, so ×4 would mean three teammates who each open as many chests as you do. ×3 assumes they open about two-thirds as many each, which allows for teammates who are less efficient, die early or go AFK in squad fill. Because it errs low, the team chance is more likely to be under the truth than over it. If you know your teammates usually loot hard, or you often play in fuller squads, raise `squad_multiplier` in `config.json`.

**It applies to every chest you count.** If you also play Solo or Duos, the ×3 overstates those matches. Set the multiplier for the mode you play most, or ask for a per-mode setting.

## Files

- `chest_count.json` holds the running totals (total, opened, broken, manual). It's saved after every change and loaded automatically on the next launch, so the count carries over between sessions. Only the *session* number starts again at 0 each time. A copy is kept in `chest_count.json.bak`. If the main file is ever damaged, the counts are restored from that copy and the damaged file is kept as `chest_count.json.corrupt-<date>`. To keep your count when you move or update the script, copy `chest_count.json` along with it.
- `chest_count.txt` contains `Chests: N`. Point an OBS *Text (GDI+) → Read from file* source at it.
- `match_count.txt` contains `This match: N` for an OBS text source (`-` while in the lobby).
- `squad_count.txt` contains `Squad chests: ~N (x3)  Token chance: X%` for OBS.
- `token_chance.txt` contains `Token chance: X%` for a second OBS text source.
- `config.json` holds all settings: interact key, threshold, fps, search region, hotkeys. It's created on first run.

## Limitations

- **Use Windowed Fullscreen.** The overlay won't show on top of exclusive fullscreen. Detection still works there.
- **Templates only work at the resolution you calibrated at.** If you change resolution, HUD scale or language, calibrate again (Calibration → Remove all, then add a new template).
- **False positives.** Walking away from a chest while holding E, or while swinging, counts it. `cooldown_s` and `input_window_s` cut this down. Fix any you notice with F9.
- **Controllers aren't supported** for auto-detection, because only keyboard and mouse input is read. Use the hotkeys instead.
- If the script is run as a normal user while Fortnite runs as admin, Windows may block the key listener. Run both at the same privilege level.

## Tests

```bat
python -m unittest test_chest_counter.py
```

The app itself (`chest_counter_app.py`) is only UI. All counting and detection is in `chest_counter.py` (`Engine`), which the tests cover.
