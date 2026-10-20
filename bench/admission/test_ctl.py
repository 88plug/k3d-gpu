#!/usr/bin/env python3
"""Tests for the H55 A_slo controller (ctl.py), against a fake cgroup directory."""

import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctl  # noqa: E402


class Fake:
    """A cgroup directory with a cpu.max file, a load JSONL and a ctl log."""

    def __init__(self, tmp, exists=True):
        self.dir = os.path.join(tmp, "docker-x.scope")
        self.cpu_max = os.path.join(self.dir, "cpu.max")
        if exists:
            os.makedirs(self.dir)
            with open(self.cpu_max, "w") as f:
                f.write("375000 100000\n")
        self.jsonl = os.path.join(tmp, "load.jsonl")
        open(self.jsonl, "w").close()
        self.log = []

    def resolve(self):
        if not os.path.isdir(self.dir):
            raise FileNotFoundError(self.cpu_max)
        return self.cpu_max

    def read(self):
        with open(self.cpu_max) as f:
            return f.read()

    def foreign_write(self, text):
        with open(self.cpu_max, "w") as f:
            f.write(text)

    def add(self, ttfts, newline=True):
        with open(self.jsonl, "a") as f:
            for i, v in enumerate(ttfts):
                f.write(json.dumps({"ok": True, "ttft_s": v}))
                if newline or i < len(ttfts) - 1:
                    f.write("\n")

    def finish_line(self):
        with open(self.jsonl, "a") as f:
            f.write("\n")

    def controller(self, slo=10.0, level=25.0):
        return ctl.Controller(slo=slo, tail=ctl.Tail(self.jsonl), resolve=self.resolve,
                              log=self.log.append, start_level=level)


class Ctl(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_absolute_store_repairs_stomp(self):
        f = Fake(self.tmp)
        c = f.controller(level=50.0)
        c.tick(1.0)
        self.assertEqual(f.read(), "750000 100000\n")
        self.assertEqual(c.stomps, 0)
        f.foreign_write("100000 100000\n")
        c.tick(2.0)
        self.assertEqual(f.read(), "750000 100000\n")
        self.assertEqual(c.stomps, 1)
        self.assertTrue(f.log[-1]["stomp"])
        c.tick(3.0)
        self.assertEqual(c.stomps, 1)

    def test_enoent_no_write(self):
        f = Fake(self.tmp, exists=False)
        c = f.controller()
        c.tick(1.0)
        self.assertFalse(os.path.exists(f.cpu_max))
        self.assertEqual(f.log[-1]["event"], "no_neighbour")
        self.assertIsNone(c.last_written)

    def test_window_needs_20(self):
        f = Fake(self.tmp)
        c = f.controller(slo=10.0, level=50.0)
        f.add([1.0] * 19)
        c.tick(1.0)
        self.assertEqual(c.windows, 0)
        self.assertEqual(c.level, 50.0)
        f.add([1.0])
        c.tick(2.0)
        self.assertEqual(c.windows, 1)
        self.assertEqual(c.level, 62.5)  # p95 1.0 <= 0.9 * 10: one level up
        self.assertEqual(f.read(), "937500 100000\n")

    def test_hysteresis_band(self):
        i50 = ctl.LEVELS.index(50.0)
        self.assertEqual(ctl.step(i50, 9.5, 10.0), i50)       # in (0.9 SLO, SLO]: hold
        self.assertEqual(ctl.step(i50, 10.0, 10.0), i50)      # = SLO: hold
        self.assertEqual(ctl.step(i50, 10.01, 10.0), i50 - 1)  # > SLO: down
        self.assertEqual(ctl.step(i50, 9.0, 10.0), i50 + 1)   # = 0.9 SLO: up
        self.assertEqual(ctl.step(0, 99.0, 10.0), 0)          # clamp at 25%
        top = len(ctl.LEVELS) - 1
        self.assertEqual(ctl.step(top, 1.0, 10.0), top)       # clamp at 100%
        self.assertEqual(ctl.LEVELS[0], 25.0)
        self.assertEqual(ctl.LEVELS[-1], 100.0)

    def test_partial_line_ignored(self):
        f = Fake(self.tmp)
        c = f.controller(level=50.0)
        f.add([1.0] * 20, newline=False)
        c.tick(1.0)
        self.assertEqual(c.windows, 0)
        f.finish_line()
        c.tick(2.0)
        self.assertEqual(c.windows, 1)

    def test_p95_nearest_rank(self):
        xs = list(range(1, 21))
        random.Random(5).shuffle(xs)
        self.assertEqual(ctl.p95(xs), 19)

    def test_absolute_not_delta(self):
        f = Fake(self.tmp)
        c = f.controller(level=37.5)
        for junk in ("max 100000\n", "1 100000\n", "1500000 100000\n", ""):
            f.foreign_write(junk)
            c.tick(1.0)
            self.assertEqual(f.read(), ctl.quota_line(37.5))
        self.assertEqual(ctl.quota_line(37.5), "562500 100000\n")
        self.assertEqual(ctl.quota_line(100.0), "1500000 100000\n")

    def test_failed_request_not_in_window(self):
        f = Fake(self.tmp)
        c = f.controller(level=50.0)
        f.add([1.0] * 19)
        with open(f.jsonl, "a") as fh:
            fh.write(json.dumps({"ok": False, "ttft_s": None}) + "\n")
        c.tick(1.0)
        self.assertEqual(c.windows, 0)


class LoadToCtl(unittest.TestCase):
    """load.py against tests/openai_stub.py; ctl.py's Tail reads what it appended."""

    def test_load_records_feed_the_controller(self):
        repo = os.path.dirname(os.path.dirname(HERE))
        stub = subprocess.Popen([sys.executable, os.path.join(repo, "tests", "openai_stub.py"), "--port", "0",
                                 "--tokens", "4", "--ttft", "0.01", "--itl", "0.001"],
                                stdout=subprocess.PIPE, text=True)
        try:
            port = stub.stdout.readline().split()[1]
            with tempfile.TemporaryDirectory() as tmp:
                prompt = os.path.join(tmp, "p.txt")
                with open(prompt, "w") as f:
                    f.write("hello\n")
                fake = Fake(tmp)
                subprocess.run([sys.executable, os.path.join(HERE, "load.py"), "--base-url",
                                f"http://127.0.0.1:{port}/v3", "--model", "m", "--prompt-file", prompt,
                                "--until", str(time.time() + 1.0), "--out", fake.jsonl], check=True, timeout=60)
                with open(fake.jsonl) as f:
                    recs = [json.loads(line) for line in f]
                self.assertGreaterEqual(len(recs), WINDOW_MIN)
                self.assertTrue(all(r["ok"] and r["ttft_s"] > 0 and r["t_end"] >= r["t_start"] for r in recs))
                c = fake.controller(slo=10.0, level=50.0)
                c.tick(1.0)
                self.assertEqual(c.windows, len(recs) // ctl.WINDOW)
        finally:
            stub.terminate()
            stub.wait()


WINDOW_MIN = 20

if __name__ == "__main__":
    unittest.main()
