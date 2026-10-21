#!/usr/bin/env python3
"""Tests for bench/eatflow/crescent/holds.py: the hold interval record and the ownership
helpers hold.sh uses. No cluster, no docker, no root."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import holds as H  # noqa: E402


class Check(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.log = os.path.join(self.d, "holds.log")

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, *recs):
        with open(self.log, "w") as f:
            for r in recs:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    @staticmethod
    def ev(ev, tag, t, host="crescent"):
        return {"ev": ev, "tag": tag, "t": t, "host": host}

    def test_disjoint_no_void(self):
        self.write(self.ev("start", "a", 1), self.ev("end", "a", 2), self.ev("start", "b", 3), self.ev("end", "b", 4))
        rep, rc = H.check(self.log)
        self.assertEqual((rep["void"], rep["unterminated"], rc), ([], [], 0))

    def test_overlap_voids_both(self):
        self.write(self.ev("start", "a", 1), self.ev("start", "b", 2), self.ev("end", "a", 3), self.ev("end", "b", 4))
        rep, rc = H.check(self.log)
        self.assertEqual((rep["void"], rc), (["a", "b"], 1))

    def test_other_host_no_overlap(self):
        self.write(self.ev("start", "a", 1), self.ev("start", "b", 2, host="x"),
                   self.ev("end", "a", 3), self.ev("end", "b", 4, host="x"))
        rep, rc = H.check(self.log)
        self.assertEqual((rep["void"], rc), ([], 0))

    def test_unterminated_voids_later_hold(self):
        self.write(self.ev("start", "a", 1), self.ev("start", "b", 5), self.ev("end", "b", 6),
                   self.ev("start", "c", 7), self.ev("end", "c", 8))
        rep, rc = H.check(self.log)
        self.assertEqual((rep["unterminated"], rep["void"], rc), (["a"], ["a", "b"], 1))

    def test_malformed_line_exit_2(self):
        self.write(self.ev("start", "a", 1), "not json", self.ev("end", "a", 2))
        self.assertEqual(H.check(self.log)[1], 2)
        self.write(self.ev("end", "a", 2))
        self.assertEqual(H.check(self.log)[1], 2)
        self.write({"ev": "start", "tag": "a", "host": "h"})
        self.assertEqual(H.check(self.log)[1], 2)

    def test_append_one_write_per_record(self):
        real = os.write
        with mock.patch.object(H.os, "write", side_effect=real) as w:
            H.append(self.log, ["ev=start", "tag=a:1:2"])
            H.append(self.log, ["ev=end", "tag=a:1:2", "rc=0"])
        self.assertEqual(w.call_count, 2)
        recs = [json.loads(ln) for ln in open(self.log)]
        self.assertEqual([r["ev"] for r in recs], ["start", "end"])
        self.assertEqual(H.check(self.log)[1], 0)

    def test_cli_check_exit_codes(self):
        self.write(self.ev("start", "a", 1))
        p = subprocess.run([sys.executable, os.path.join(HERE, "holds.py"), "check", self.log],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["unterminated"], ["a"])


class Kill(unittest.TestCase):
    def spawn(self, **env):
        p = subprocess.Popen(["sleep", "60"], env={**os.environ, **env})
        self.addCleanup(lambda: (p.kill(), p.wait()))
        time.sleep(0.2)
        return p

    def test_reap_exact_tag_only(self):
        me = self.spawn(K3DGPU_HOLD="c3u:1:2")
        near = self.spawn(K3DGPU_HOLD="c3u:1:22")
        n, clear = H.kill_until_clear(lambda env: env.get("K3DGPU_HOLD") == "c3u:1:2")
        self.assertEqual((n, clear), (1, True))
        self.assertEqual(me.wait(5), -9)
        self.assertIsNone(near.poll())

    def test_stale_scoped_to_campaign(self):
        me = os.getpid()
        live = f"sib:{me}:{H.starttime(me)}"
        m = H.stale("own:1:1", "/c/runs")
        self.assertTrue(m({"K3DGPU_HOLD": "old:999999:1", "K3DGPU_HOLD_RUNS": "/c/runs"}))
        self.assertFalse(m({"K3DGPU_HOLD": "old:999999:1", "K3DGPU_HOLD_RUNS": "/other/runs"}))
        self.assertFalse(m({"K3DGPU_HOLD": "old:999999:1"}))
        self.assertFalse(m({"K3DGPU_HOLD": live, "K3DGPU_HOLD_RUNS": "/c/runs"}))
        self.assertFalse(m({"K3DGPU_HOLD": f"sib:{me}:1", "K3DGPU_HOLD_RUNS": "/x"}))
        self.assertTrue(m({"K3DGPU_HOLD": f"sib:{me}:1", "K3DGPU_HOLD_RUNS": "/c/runs"}))  # reused pid
        self.assertFalse(m({"K3DGPU_HOLD": "own:1:1", "K3DGPU_HOLD_RUNS": "/c/runs"}))
        self.assertFalse(m({"K3DGPU_HOLD": "notatag", "K3DGPU_HOLD_RUNS": "/c/runs"}))

    def test_alive_rejects_non_numeric(self):
        self.assertFalse(H.alive("abc", "1"))
        self.assertTrue(H.alive(os.getpid(), H.starttime(os.getpid())))


if __name__ == "__main__":
    unittest.main()
