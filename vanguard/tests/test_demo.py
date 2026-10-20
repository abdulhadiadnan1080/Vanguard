"""The one-line demo must keep working from a fresh clone: no private settings, keys, Docker or network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


class DemoRuns(unittest.TestCase):
    def test_demo_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = os.path.join(tmp, "sample.json")
            env = {k: v for k, v in os.environ.items() if not k.startswith(("VANGUARD_", "FAKE_"))}
            p = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "--demo", "--keep", os.path.join(tmp, "run"),
                                "--write-sample", sample], env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            for stage in ("[1/4] Knowledge graph", "[2/4] Harvester", "[3/4] Synthesizer", "[4/4] Judge", "-> accepted"):
                self.assertIn(stage, p.stdout)
            out = json.load(open(sample))
            self.assertEqual(out["benchmark_task"]["verdict"], "accepted")
            self.assertEqual(out["benchmark_task"]["validation"]["status"], "pass")
            self.assertTrue(out["forecast_checks"])
            ev = out["predictor"]["held_out_brier"]
            self.assertLess(ev["fitted"], ev["base_rate"])
            self.assertNotIn(tmp, json.dumps(out))  # no local paths leak into the shared sample


if __name__ == "__main__":
    unittest.main()
