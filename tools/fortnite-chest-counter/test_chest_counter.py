"""Unit tests for the parts that don't need Fortnite: detection logic, matcher, storage.

Run:  python -m unittest test_chest_counter.py
"""
import tempfile
import unittest
from pathlib import Path

from chest_counter import ChestEventDetector, CounterStore

FRAME = 0.05  # 20 fps


def feed(det, pattern, start=0.0):
    """pattern: string of '1' (prompt seen) / '0' (not seen). Returns events with times."""
    events = []
    t = start
    for ch in pattern:
        kind = det.update(ch == "1", t)
        if kind:
            events.append((round(t, 3), kind))
        t += FRAME
    return events, t


class DetectorTests(unittest.TestCase):
    def make(self, **kw):
        args = dict(appear_frames=2, disappear_frames=3, input_window_s=0.75, cooldown_s=1.5)
        args.update(kw)
        return ChestEventDetector(**args)

    def test_open_while_holding_interact(self):
        det = self.make()
        det.interact(True, 0.2)
        events, _ = feed(det, "1" * 10 + "0" * 5)
        self.assertEqual([k for _, k in events], ["opened"])

    def test_open_just_after_release(self):
        det = self.make()
        det.interact(True, 0.1)
        det.interact(False, 0.45)  # released, prompt vanishes at t=0.5
        events, _ = feed(det, "1" * 10 + "0" * 5)
        self.assertEqual([k for _, k in events], ["opened"])

    def test_walking_away_does_not_count(self):
        det = self.make()
        events, _ = feed(det, "1" * 10 + "0" * 5)
        self.assertEqual(events, [])

    def test_stale_interact_does_not_count(self):
        det = self.make()
        det.interact(True, -5.0)
        det.interact(False, -4.9)
        events, _ = feed(det, "1" * 10 + "0" * 5)
        self.assertEqual(events, [])

    def test_break_with_attack(self):
        det = self.make()
        det.attack(True, 0.3)
        det.attack(False, 0.35)
        events, _ = feed(det, "1" * 10 + "0" * 5)
        self.assertEqual([k for _, k in events], ["broken"])

    def test_attack_ignored_when_disabled(self):
        det = self.make(count_attacks=False)
        det.attack(True, 0.3)
        events, _ = feed(det, "1" * 10 + "0" * 5)
        self.assertEqual(events, [])

    def test_single_frame_flicker_ignored(self):
        det = self.make()
        det.interact(True, 0.0)
        # prompt drops out for 1-2 frames only -> no count, no re-trigger
        events, _ = feed(det, "11111" + "0" + "1111" + "00" + "1111")
        self.assertEqual(events, [])
        self.assertTrue(det.visible)

    def test_single_frame_appearance_ignored(self):
        det = self.make()
        det.interact(True, 0.0)
        events, _ = feed(det, "0001000000")
        self.assertEqual(events, [])

    def test_cooldown_prevents_double_count(self):
        det = self.make()
        det.interact(True, 0.0)
        # prompt reappears briefly right after opening (e.g. camera wobble)
        events, _ = feed(det, "1" * 6 + "0" * 4 + "1" * 3 + "0" * 4)
        self.assertEqual(len(events), 1)

    def test_two_chests_far_apart_both_count(self):
        det = self.make()
        det.interact(True, 0.0)
        events, t = feed(det, "1" * 6 + "0" * 4)
        det.interact(True, t + 3.0)
        more, _ = feed(det, "0" * 60 + "1" * 6 + "0" * 4, start=t)
        self.assertEqual(len(events) + len(more), 2)

    def test_vanish_time_is_first_miss(self):
        # Release at 0.5 + window 0.2. Prompt vanishes at 0.6 but is only
        # confirmed 3 frames later at 0.7; must still count (measured from 0.6).
        det = self.make(input_window_s=0.15)
        det.interact(True, 0.0)
        det.interact(False, 0.5)
        events, _ = feed(det, "1" * 12 + "0" * 5)
        self.assertEqual([k for _, k in events], ["opened"])


class MatcherTests(unittest.TestCase):
    def test_finds_template_and_rejects_noise(self):
        import cv2
        import numpy as np

        from chest_counter import PromptMatcher

        rng = np.random.default_rng(0)
        scene = rng.integers(0, 60, (300, 500), dtype=np.uint8)
        tpl = np.zeros((30, 90), np.uint8)
        cv2.putText(tpl, "Chest", (2, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 255, 2)
        with_prompt = scene.copy()
        with_prompt[200:230, 150:240] = tpl

        for scale in (1.0, 0.5):
            m = PromptMatcher([tpl], scale=scale)
            self.assertGreater(m.score(with_prompt), 0.8)
            self.assertLess(m.score(scene), 0.5)


class StoreTests(unittest.TestCase):
    def test_persists_and_never_goes_negative(self):
        with tempfile.TemporaryDirectory() as d:
            p, obs = Path(d) / "c.json", Path(d) / "c.txt"
            s = CounterStore(p, obs)
            s.add("opened")
            s.add("broken")
            s.add("manual", -1)
            self.assertEqual(s.snapshot()["total"], 1)
            s.add("manual", -1)
            s.add("manual", -1)  # already 0 -> ignored
            self.assertEqual(s.snapshot()["total"], 0)
            s.add("opened")
            s2 = CounterStore(p, obs)
            self.assertEqual(s2.snapshot()["total"], 1)
            self.assertEqual(s2.snapshot()["session"], 0)
            self.assertEqual(obs.read_text(), "Chests: 1")


class PersistenceTests(unittest.TestCase):
    def test_survives_restart_and_recovers_from_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            s = CounterStore(p, None, chance_path=None)
            for _ in range(42):
                s.add("opened")
            # "Reopen" the program.
            self.assertEqual(CounterStore(p, None, chance_path=None).snapshot()["total"], 42)
            # Main file damaged (e.g. power cut) -> restored from backup, not zeroed.
            p.write_text("{garbage")
            s3 = CounterStore(p, None, chance_path=None)
            self.assertEqual(s3.snapshot()["total"], 42)
            self.assertTrue(any(f.name.startswith("c.json.corrupt-") for f in Path(d).iterdir()))


class MatchCounterTests(unittest.TestCase):
    def store(self, d, clock):
        return CounterStore(Path(d) / "c.json", None, chance_path=None, match_path=Path(d) / "m.txt",
                            resume_window_s=900, clock=lambda: clock[0])

    def test_per_match_counts_and_stats(self):
        with tempfile.TemporaryDirectory() as d:
            clock = [1000.0]
            s = self.store(d, clock)
            self.assertIsNone(s.snapshot()["match_chests"])
            s.start_match()
            for _ in range(5):
                s.add("opened")
            self.assertEqual(s.snapshot()["match_chests"], 5)
            self.assertEqual((Path(d) / "m.txt").read_text(), "This match: 5")
            s.start_match()  # next match closes the previous one
            s.add("broken")
            s.add("opened")
            s.add("opened")
            done = s.end_match()
            self.assertEqual(done["chests"], 3)
            snap = s.snapshot()
            self.assertEqual(snap["matches_played"], 2)
            self.assertEqual(snap["total"], 8)
            self.assertAlmostEqual(snap["avg_per_match"], 4.0)
            self.assertEqual(snap["best_match"], 5)
            self.assertEqual(snap["last_match_chests"], 3)
            self.assertIsNone(s.end_match())  # already ended

    def test_detected_chest_in_lobby_starts_match_but_manual_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.store(d, [0.0])
            s.add("manual")
            self.assertIsNone(s.snapshot()["match_chests"])
            s.add("opened")
            self.assertEqual(s.snapshot()["match_chests"], 1)
            s.add("manual", -1)
            s.add("manual", -1)  # never below 0 in a match
            self.assertEqual(s.snapshot()["match_chests"], 0)

    def test_match_resumes_after_quick_restart_but_not_after_long_one(self):
        with tempfile.TemporaryDirectory() as d:
            clock = [0.0]
            s = self.store(d, clock)
            s.start_match()
            s.add("opened")
            s.add("opened")
            clock[0] = 300.0  # restarted 5 min later -> same match continues
            s2 = self.store(d, clock)
            self.assertEqual(s2.snapshot()["match_chests"], 2)
            s2.add("opened")
            clock[0] = 300.0 + 3600  # restarted an hour later -> match filed as finished
            s3 = self.store(d, clock)
            snap = s3.snapshot()
            self.assertIsNone(snap["match_chests"])
            self.assertEqual(snap["matches_played"], 1)
            self.assertEqual(snap["last_match_chests"], 3)
            self.assertEqual(snap["total"], 3)


START = "[2026.11.08-17.40.12:123][  0]LogLoad: LoadMap: /Game/Athena/Maps/Athena_Terrain?game=/Script/X"
LOBBY = "[2026.11.08-18.01.02:456][  0]LogLoad: LoadMap: /Game/Maps/Frontend?Name=Player"
WIN = "[2026.11.08-18.00.00:000][  0]LogGameState: Match State Changed from InProgress to WaitingPostMatch"
NOISE = "[2026.11.08-17.40.13:000][  0]LogTemp: something unrelated"


def make_parser():
    from chest_counter import DEFAULT_CONFIG, MatchLogParser
    return MatchLogParser(DEFAULT_CONFIG["match_start_regex"], DEFAULT_CONFIG["match_end_regex"], 30)


class LogParserTests(unittest.TestCase):
    def test_start_end_and_lobby(self):
        p = make_parser()
        self.assertIsNone(p.feed(LOBBY, 0))  # lobby load while not in match
        self.assertEqual(p.feed(START, 1), "start")
        self.assertIsNone(p.feed(NOISE, 2))
        self.assertIsNone(p.feed(START, 5))  # duplicate within min gap
        self.assertEqual(p.feed(WIN, 900), "end")
        self.assertIsNone(p.feed(LOBBY, 920))  # already ended
        self.assertEqual(p.feed(START, 1000), "start")


class LogTailerTests(unittest.TestCase):
    def run_tail(self, encoding, bom):
        from chest_counter import LogTailer

        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "FortniteGame.log"

            def write(lines, mode="ab"):
                with open(log, mode) as f:
                    f.write("".join(l + "\r\n" for l in lines).encode(encoding))

            log.write_bytes(bom)
            write([NOISE, LOBBY, START])  # game already in a match when we start
            events = []
            tail = LogTailer(log, make_parser(), lambda ev, line: events.append(ev))
            self.assertTrue(tail.replay_existing())
            self.assertEqual(events, [])  # replay never fires events
            write([NOISE, LOBBY])
            tail.poll_once()
            self.assertEqual(events, ["end"])
            # half a line arrives, then the rest
            raw = (START + "\r\n").encode(encoding)
            with open(log, "ab") as f:
                f.write(raw[:20])
            tail.poll_once()
            self.assertEqual(events, ["end"])
            with open(log, "ab") as f:
                f.write(raw[20:])
            tail.poll_once()
            self.assertEqual(events, ["end", "start"])
            # game relaunch recreates a shorter log -> current match ends
            log.write_bytes(bom + (NOISE + "\r\n").encode(encoding))
            tail.poll_once()
            self.assertEqual(events, ["end", "start", "end"])

    def test_utf8(self):
        self.run_tail("utf-8", b"")

    def test_utf16(self):
        import codecs
        self.run_tail("utf-16-le", codecs.BOM_UTF16_LE)


class SquadTests(unittest.TestCase):
    def test_squad_estimate_and_chance(self):
        from chest_counter import token_chance

        with tempfile.TemporaryDirectory() as d:
            sq = Path(d) / "sq.txt"
            s = CounterStore(Path(d) / "c.json", None, chance_path=None, match_path=None,
                             squad_multiplier=3, squad_path=sq)
            s.start_match()
            for _ in range(1000):
                s.add("opened")
            snap = s.snapshot()
            self.assertEqual(snap["squad_total"], 3000)
            self.assertEqual(snap["squad_match_chests"], 3000)
            self.assertAlmostEqual(snap["squad_chance"], token_chance(3000, 137500))
            self.assertGreater(snap["squad_chance"], snap["token_chance"])
            self.assertEqual(sq.read_text(), "Squad chests: ~3,000 (x3)  Token chance: 2.158%")
            s.end_match()
            self.assertIsNone(s.snapshot()["squad_match_chests"])


class TokenChanceTests(unittest.TestCase):
    def test_values(self):
        import math
        from chest_counter import chests_for_chance, format_chance, token_chance

        odds = 137500
        self.assertEqual(token_chance(0, odds), 0.0)
        self.assertAlmostEqual(token_chance(1, odds), 1 / odds, places=12)
        self.assertAlmostEqual(token_chance(odds, odds), 1 - 1 / math.e, places=5)
        self.assertAlmostEqual(token_chance(10_000, odds), 0.070146, places=5)
        self.assertEqual(chests_for_chance(0.5, odds), 95_308)
        self.assertEqual(chests_for_chance(0.9, odds), 316_605)
        self.assertEqual(format_chance(token_chance(10_000, odds)), "7.015%")


if __name__ == "__main__":
    unittest.main()
