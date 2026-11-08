# Fortnite chest counter

Counts the chests you open or break in Fortnite and keeps a running total across sessions.

Fortnite has no API or log line for chest events, so the script watches from outside the game:

1. It looks for the **"Chest"** interaction prompt in a small area of the screen (OpenCV template match against a crop you take once).
2. It listens to your **interact key** and **left mouse button**.
3. If the prompt disappears while you're holding the interact key (or just let go of it), that counts as **opened**. If it disappears while you're attacking, that counts as **broken**.

It only captures the screen and listens to input, the same way OBS and Discord do. It never reads game memory or sends input to the game.

## Setup (Windows)

```bat
pip install -r requirements.txt
python chest_counter.py calibrate
```

Calibration: stand at a chest so its prompt shows, press **F7**, Alt-Tab to the window that opens, and drag a tight box around the word **Chest**. Don't box *Search*, because ammo boxes say that too. Press Enter. Do this again to add extra templates, for example for a different prompt style.

Check the detection:

```bat
python chest_counter.py test
```

Look at a chest, then away from it. You want the score clearly above `match_threshold` (default 0.80) on a chest and well below it otherwise. Change the threshold in `config.json` if you need to.

Run it:

```bat
python chest_counter.py            # overlay + auto-detect + hotkeys
python chest_counter.py --manual   # hotkeys only
```

| Key | Action |
| --- | --- |
| F8 | +1 (fix a missed chest) |
| F9 | −1 (fix a false count) |
| F10 | reset the session counter |
| F11 | pause or resume auto-detect |
| Right-click overlay | quit |

## Files

- `chest_count.json` holds the running totals (total, opened, broken, manual). It's saved after every change.
- `chest_count.txt` contains `Chests: N`. Point an OBS *Text (GDI+) → Read from file* source at it.
- `config.json` holds all settings: interact key, threshold, fps, search region, hotkeys. It's created on first run.

## Limitations

- **Use Windowed Fullscreen.** The overlay won't show on top of exclusive fullscreen. Detection still works there.
- **Templates only work at the resolution you calibrated at.** If you change resolution, HUD scale or language, calibrate again (delete `templates/` and `region` in `config.json`).
- **False positives.** Walking away from a chest while holding E, or while swinging, counts it. `cooldown_s` and `input_window_s` cut this down. Fix any you notice with F9.
- **Controllers aren't supported** for auto-detection, because only keyboard and mouse input is read. Use the hotkeys instead.
- If the script is run as a normal user while Fortnite runs as admin, Windows may block the key listener. Run both at the same privilege level.

## Tests

```bat
python -m unittest test_chest_counter.py
```
