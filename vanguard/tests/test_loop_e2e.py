"""End-to-end test of `vanguard.py loop` with stand-ins for the builder agent and Harbor (tests/fixtures/).

No Docker, no model API, no network: everything lives in a temporary data folder. Covers every stage and gate, the
budget guards, a triage REJECT, a crashed trial (only the crashed attempt is re-run) and a model solving the task.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
VANGUARD = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")


class LoopEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vanguard-loop-")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        rows = [{"ghsa_id": "GHSA-aaaa-0000-0001", "repo": "acme/one", "fix_commit": "1" * 40, "language": "python",
                 "p_fail": {"MODEL_A": 0.9, "MODEL_B": 0.6}, "p_both": 0.54, "why": [["weakness:producer-boundary", 3.5]],
                 "reject": None, "already_built": False, "model_version": "test"},
                {"ghsa_id": "GHSA-aaaa-0000-0002", "repo": "acme/two", "fix_commit": "2" * 40, "language": "python",
                 "p_fail": {"MODEL_A": 0.8, "MODEL_B": 0.5}, "p_both": 0.40, "why": [], "reject": None,
                 "already_built": False, "model_version": "test"}]
        with open(os.path.join(self.data, "rank_20990101-0000.jsonl"), "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump({"builder_cmd": [sys.executable, os.path.join(FIX, "fake_builder.py")],
                       "builder_timeout_min": 2, "max_active": 3}, f)
        self.env = dict(os.environ, VANGUARD_DATA=self.data, VANGUARD_CONFIG=os.path.join(self.tmp, "config.json"),
                        VANGUARD_HARBOR=os.path.join(FIX, "fake_harbor.py"), VANGUARD_TEST_NO_DOCKER="1",
                        VANGUARD_MODELS=os.path.join(FIX, "models.json"),
                        STUB_REJECT="GHSA-aaaa-0000-0002")
        for k in ("FAKE_B_REWARD", "FAKE_CRASH_MODEL", "FAKE_CRASH_FILE"):
            self.env.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def v(self, *args, ok=True, **env):
        p = subprocess.run([sys.executable, os.path.join(VANGUARD, "vanguard.py")] + list(args), cwd=VANGUARD,
                           env=dict(self.env, **env), capture_output=True, text=True, timeout=300)
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout + p.stderr

    def state(self, ghsa):
        sys.path.insert(0, VANGUARD)
        from knowledge_graph import Graph
        g = Graph(os.path.join(self.data, "graph.sqlite"))
        try:
            return g.get("cand:" + ghsa)["loop"]
        finally:
            g.close()

    def build_and_validate(self):
        A, B = "GHSA-aaaa-0000-0001", "GHSA-aaaa-0000-0002"
        self.v("loop", "--select", "2")
        self.assertEqual(self.state(A)["state"], "awaiting-build")
        self.assertIn("give an explicit budget", self.v("loop", "--build", ok=False))
        self.v("loop", "--build", "--max-builds", "2")
        self.assertEqual(self.state(A)["state"], "awaiting-approval")
        self.assertEqual(self.state(B)["state"], "rejected")
        self.assertIn("approves only from", self.v("approve", A, "--more-runs", ok=False))
        self.v("approve", A)
        self.v("loop")
        self.assertEqual(self.state(A)["state"], "awaiting-review")   # fairness and repo tests are for a person
        task = os.path.basename(self.state(A)["task_dir"])
        self.v("review", task, "--set", "repo_tests=pass", "--set", "fairness=pass", "--note", "test")
        self.v("loop")
        self.assertEqual(self.state(A)["state"], "ready-to-run")      # never runs models without the flag
        self.assertIn("give an explicit budget", self.v("loop", "--run-models", ok=False))
        self.assertIn("budget left 1", self.v("loop", "--run-models", "--max-model-runs", "1"))
        self.assertEqual(self.state(A)["state"], "ready-to-run")
        return A

    def test_accepted_path(self):
        A = self.build_and_validate()
        self.v("loop", "--run-models", "--max-model-runs", "2")
        self.assertEqual(self.state(A)["state"], "round1-running")
        out = self.v("loop")
        self.assertIn("forecast check", out)
        self.assertEqual(self.state(A)["state"], "awaiting-more-runs")
        self.v("loop", "--run-models", "--max-model-runs", "4")
        self.assertEqual(self.state(A)["state"], "awaiting-more-runs")  # gate 2 holds even with a budget
        self.v("approve", A, "--more-runs")
        self.v("loop", "--run-models", "--max-model-runs", "4")
        self.v("loop")
        st = self.state(A)
        self.assertEqual(st["state"], "accepted")
        self.assertEqual(sum(len(r) for rnd in st["rounds"] for r in rnd.values()), 6)  # 3 attempts x 2 models

    def test_crash_retry_and_solved(self):
        A = self.build_and_validate()
        crash = dict(FAKE_CRASH_MODEL="vendor-a/model-a", FAKE_CRASH_FILE=os.path.join(self.tmp, "crashed"),
                     FAKE_B_REWARD="1.0")
        self.v("loop", "--run-models", "--max-model-runs", "2", **crash)
        self.v("loop", "--run-models", "--max-model-runs", "2", **crash)
        st = self.state(A)
        self.assertEqual(st["pending"], {"MODEL_A": 1})                 # only the crashed attempt is re-run
        self.assertEqual(st["state"], "round1-running")
        self.v("loop", **crash)
        st = self.state(A)
        self.assertEqual(st["state"], "solved")
        b_jobs = [j for j in os.listdir(os.path.join(self.data, "harbor_jobs")) if j.startswith("loop-") and j.endswith("model-b")]
        self.assertEqual(len(b_jobs), 1)                                 # Model B was not run a second time

    def test_lock(self):
        with open(os.path.join(self.data, "loop.lock"), "w") as f:
            f.write(str(os.getpid()))  # this live process "holds" the lock
        self.assertIn("another loop is running", self.v("loop", ok=False))


if __name__ == "__main__":
    unittest.main()
