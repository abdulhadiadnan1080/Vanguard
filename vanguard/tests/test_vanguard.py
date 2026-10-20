"""Regression tests for vanguard. Standard library only; every test builds its own data in a temporary folder.

    cd "New Pipeline Automated/vanguard" && python3 -m unittest discover -s tests -v
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["VANGUARD_MODELS"] = os.path.join(HERE, "fixtures", "models.json")  # made-up models, before importing sources
_PRIVATE = tempfile.mkdtemp(prefix="vanguard-test-private-")  # never the developer's own settings or data
with open(os.path.join(_PRIVATE, "paths.json"), "w") as _f:
    json.dump({"task_namespace": "tasks"}, _f)
os.environ["VANGUARD_PRIVATE"] = _PRIVATE
os.environ["VANGUARD_DATA"] = os.path.join(_PRIVATE, "data")

import ingest  # noqa: E402
import learner  # noqa: E402
import sources  # noqa: E402
import validate  # noqa: E402
from knowledge_graph import Graph, parse_ts  # noqa: E402

UTC = datetime.timezone.utc


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def write_trial(root, job, trial, *, id, agent="terminus-2", model="vendor-a/model-a", task="t1", reward=0.5,
                started=None, finished=True, exception=None, details=None):
    d = os.path.join(root, job, trial)
    os.makedirs(os.path.join(d, "verifier"), exist_ok=True)
    started = started or datetime.datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    r = {"id": id, "started_at": iso(started), "finished_at": iso(started + datetime.timedelta(minutes=5)) if finished else None,
         "exception_info": {"exception_type": exception} if exception else None,
         "config": {"agent": {"name": agent, "model_name": model if agent == "terminus-2" else None}, "task": {"path": task}},
         "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None}
    with open(os.path.join(d, "result.json"), "w") as f:
        json.dump(r, f)
    if details is not None:
        with open(os.path.join(d, "verifier", "details.json"), "w") as f:
            json.dump(details, f)
    return os.path.join(d, "result.json")


def write_task(root, slug, ghsa="EXP-T", instruction="An attacker can read other users' data.", extra_files=None,
               dockerfile=None):
    d = os.path.join(root, slug)
    for sub in ("environment", "solution", "tests"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    files = {
        "task.toml": '[task]\nname = "tasks/%s"\n\n[metadata]\nghsa = "%s"\nlanguage = "python"\n'
                     'repo = "https://github.com/acme/lib"\nfix_commit = "0123456789abcdef0123456789abcdef01234567"\n\n'
                     '[environment]\nnetwork_mode = "no-network"\n' % (slug, ghsa),
        "instruction.md": instruction,
        "environment/Dockerfile": dockerfile or "FROM python:3.12-slim AS builder\nRUN git clone x /src && rm -rf /src/.git\n"
                                                "FROM python:3.12-slim\nCOPY --from=builder /src /app\n",
        "solution/solve.sh": "#!/bin/bash\ngit apply fix.patch\n",
        "solution/fix.patch": "diff --git a/lib/check.py b/lib/check.py\n--- a/lib/check.py\n+++ b/lib/check.py\n@@ -1 +1 @@\n-x\n+y\n",
        "tests/test.sh": "#!/bin/bash\necho 1.0 > /logs/verifier/reward.txt\n",
    }
    files.update(extra_files or {})
    for p, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
        with open(os.path.join(d, p), "w") as f:
            f.write(text)
    return d


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vanguard-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------- parsing Harbor output
class TestResultParsing(TempDirTest):
    def test_security_counts_all_three_shapes(self):
        self.assertEqual(sources.security_counts({"security": {"passed": 3, "total": 5}}), (3, 5))
        self.assertEqual(sources.security_counts({"security": [{"outcome": "passed"}, {"outcome": "failed"}],
                                                  "summary": {"security_passed": 1, "security_total": 2}}), (1, 2))
        self.assertEqual(sources.security_counts({"security": [{"passed": False}] * 6,
                                                  "security_passed": 0, "security_total": 6}), (0, 6))
        self.assertEqual(sources.security_counts({"security": [{"passed": True}, {"passed": False}]}), (1, 2))
        self.assertEqual(sources.security_counts(None), (None, None))

    def test_functional_ok(self):
        self.assertTrue(sources.functional_ok({"functional": {"passed": 4, "failed": 0, "total": 4}}))
        self.assertFalse(sources.functional_ok({"functional": {"passed": 3, "failed": 1, "total": 4}}))
        self.assertFalse(sources.functional_ok({"functional_passed": 3, "functional_total": 4}))
        self.assertTrue(sources.functional_ok({"summary": {"functional_all_pass": True}}))
        self.assertIsNone(sources.functional_ok({}))

    def test_trial_status(self):
        cases = {"valid": dict(reward=0.6), "crash": dict(reward=None, exception="RuntimeError"),
                 "timeout": dict(reward=0.2, exception="AgentTimeoutError"), "running": dict(reward=None, finished=False)}
        for want, kw in cases.items():
            p = write_trial(self.tmp, "job", want, id=want, **kw)
            self.assertEqual(sources.read_trial(p)["status"], want, want)

    def test_model_ids(self):
        self.assertEqual(sources.model_id("vendor-a/model-a"), "MODEL_A")
        self.assertEqual(sources.model_id("vendor-b/model-b"), "MODEL_B")
        self.assertEqual(sources.model_id("vendor-b/model-b-v2"), "MODEL_B")   # newer version, same family
        self.assertEqual(sources.model_id("OLD_A"), "MODEL_A")                # label used in older data
        self.assertEqual(sources.model_id("other/thing"), "THING")
        self.assertIsNone(sources.model_id(None))
        self.assertEqual(sources.label("model:MODEL_B"), "Model B")

    def test_task_metadata(self):
        d = write_task(self.tmp, "t1", ghsa="GHSA-aaaa-bbbb-cccc")
        meta = sources.task_metadata(d)
        self.assertEqual(meta["ghsa"], "GHSA-aaaa-bbbb-cccc")
        self.assertEqual(meta["name"], "tasks/t1")
        self.assertTrue(meta["fix_commit"].startswith("0123"))

    def test_duplicate_trials_are_read_once(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        write_trial(a, "job", "x", id="same-id")
        write_trial(b, "job-copy", "x", id="same-id")
        old = sources.settings()["JOB_DIRS"]
        sources.settings()["JOB_DIRS"] = [a, b]
        try:
            self.assertEqual(len(list(sources.all_trials())), 1)
        finally:
            sources.settings()["JOB_DIRS"] = old


# ---------------------------------------------------------------- which trials count
class TestTrialRules(TempDirTest):
    def graph_with(self, trials):
        g = Graph(os.path.join(self.tmp, "g.sqlite"))
        for t in trials:
            g.node("trial:" + t["id"], "Trial", t["id"], **{k: v for k, v in t.items() if k != "id"})
            if t["agent"] in ("oracle", "nop"):
                g.edge("trial:" + t["id"], "VALIDATES", "task:t1")
            else:
                g.edge("trial:" + t["id"], "ON", "task:t1")
                g.edge("trial:" + t["id"], "BY", "model:MODEL_A")
        return g

    def test_stale_crash_timeout_excluded(self):
        t = lambda id, agent, start, status="valid", reward=0.5: {
            "id": id, "agent": agent, "status": status, "reward": reward, "started_at": start}
        g = self.graph_with([
            t("old-run", "terminus-2", "2026-10-01T10:00:00Z", reward=0.4),      # before re-validation: stale
            t("oracle", "oracle", "2026-10-01T11:00:00Z", reward=1.0),
            t("good", "terminus-2", "2026-10-01T12:00:00Z", reward=1.0),
            t("crash", "terminus-2", "2026-10-01T12:00:00Z", status="crash", reward=None),
            t("timeout", "terminus-2", "2026-10-01T12:00:00Z", status="timeout", reward=0.1),
        ])
        groups, stats = ingest.valid_trials(g)
        self.assertEqual([x["id"] for x in groups[("task:t1", "model:MODEL_A")]], ["trial:good"])
        self.assertEqual((stats["stale"], stats["crash"], stats["timeout"], stats["valid"]), (1, 1, 1, 1))
        g.close()

    def test_outcomes_and_mitigated_tasks(self):
        g = self.graph_with([{"id": "a", "agent": "terminus-2", "status": "valid", "reward": 0.5, "started_at": "x"},
                             {"id": "b", "agent": "terminus-2", "status": "valid", "reward": 1.0, "started_at": "x"}])
        g.node("weakness:w", "Weakness", "w")
        g.edge("task:t1", "TARGETS", "weakness:w")
        ingest.recompute_outcomes(g)
        e = g.edges(src="task:t1", rel="DEFEATS")[0]
        self.assertEqual((e["fails"], e["runs"], e["failed_all"]), (1, 2, False))
        self.assertEqual(g.edges(src="weakness:w", rel="DEFEATS")[0]["tasks"], 1)
        g.node("mitigation:hint", "Signal", "hint", weight=-1.5)
        g.edge("task:t1", "MITIGATED_BY", "mitigation:hint")
        ingest.recompute_outcomes(g)  # a hinted task must not count toward the weakness's fail rate
        self.assertEqual(g.edges(src="weakness:w", rel="DEFEATS"), [])
        g.close()


# ---------------------------------------------------------------- graph store
class TestGraph(TempDirTest):
    def test_upsert_merges_props_and_replaces_edges(self):
        g = Graph(os.path.join(self.tmp, "g.sqlite"))
        g.node("n", "Task", "n", a=1)
        g.node("n", "Task", None, b=2)
        self.assertEqual((g.get("n")["a"], g.get("n")["b"], g.get("n")["label"]), (1, 2, "n"))
        g.edge("n", "R", "m", x=1)
        g.edge("n", "R", "m", y=2)
        self.assertEqual(g.edges(src="n"), [{"src": "n", "rel": "R", "dst": "m", "y": 2}])
        g.close()

    def test_prediction_before_uses_utc_order(self):
        g = Graph(os.path.join(self.tmp, "g.sqlite"))
        g.record_prediction("c", "model:MODEL_A", 0.2, "v1", at="2026-10-01T10:00:00+00:00")
        g.record_prediction("c", "model:MODEL_A", 0.9, "v2", at="2026-10-01T14:00:00+00:00")
        run = parse_ts("2026-10-01T12:00:00Z")
        self.assertEqual(g.prediction_before("c", "model:MODEL_A", run)[:2], (0.2, "v1"))
        self.assertIsNone(g.prediction_before("c", "model:MODEL_A", parse_ts("2026-10-01T09:00:00Z")))
        g.close()

    def test_two_connections_can_write(self):
        p = os.path.join(self.tmp, "g.sqlite")
        g1, g2 = Graph(p), Graph(p)
        g1.node("a", "T", "a")
        g1.db.commit()
        g2.node("b", "T", "b")
        g2.db.commit()
        self.assertEqual({n["id"] for n in g1.nodes()}, {"a", "b"})
        g1.close()
        g2.close()


# ---------------------------------------------------------------- learner
class TestLearner(unittest.TestCase):
    def test_fit_moves_toward_data_and_keeps_unseen_priors(self):
        rows = [{"task": "t%d" % i, "model": "model:MODEL_A", "x": {"f": 1.0}, "y": 1.0, "w": 3, "group": "r%d" % i}
                for i in range(6)] + \
               [{"task": "u%d" % i, "model": "model:MODEL_A", "x": {}, "y": 0.0, "w": 3, "group": "s%d" % i} for i in range(6)]
        prior = {"f": (0.0, 1.0), "unseen": (2.0, 1.0), "bias": (0.0, 2.0)}
        w = learner.fit(rows, prior)
        self.assertGreater(w["f"], 1.0)
        self.assertAlmostEqual(w["unseen"], 2.0, places=6)  # a feature with no data keeps its prior
        self.assertGreater(learner.predict(w, {"f": 1.0}), 0.8)
        self.assertLess(learner.predict(w, {}), 0.2)

    def test_evaluate_reports_all_baselines(self):
        rows = [{"task": "t%d" % i, "model": "m", "x": {"f": float(i % 2)}, "y": float(i % 2), "w": 1, "group": "g%d" % i}
                for i in range(8)]
        ev = learner.evaluate(rows, {"f": (0.0, 1.0), "bias": (0.0, 2.0)})
        for k in ("base_rate", "prior_only", "fitted"):
            self.assertIsNotNone(ev[k]["brier"])
        self.assertLess(ev["fitted"]["brier"], ev["base_rate"]["brier"])

    def test_solver(self):
        x = learner._solve([[4.0, 1.0], [1.0, 3.0]], [1.0, 2.0])
        self.assertAlmostEqual(4 * x[0] + x[1], 1.0)
        self.assertAlmostEqual(x[0] + 3 * x[1], 2.0)


# ---------------------------------------------------------------- validation
class TestValidation(TempDirTest):
    def test_clean_task_passes_static_checks(self):
        d = write_task(self.tmp, "clean")
        bad = [c for c in validate.static_checks(d) if c["status"] in ("fail", "warn")]
        self.assertEqual(bad, [])

    def test_leaks_are_caught(self):
        d = write_task(self.tmp, "leaky", ghsa="GHSA-aaaa-bbbb-cccc",
                       instruction="Fix GHSA-aaaa-bbbb-cccc in lib/check.py.",
                       dockerfile="FROM python:3.12-slim\nCOPY notes.txt /app/notes.txt\nCOPY tests /app/tests\n",
                       extra_files={"environment/notes.txt": "see GHSA-aaaa-bbbb-cccc"})
        st = {c["id"]: c["status"] for c in validate.static_checks(d)}
        self.assertEqual(st["leak_report"], "fail")
        self.assertEqual(st["impact_only"], "warn")
        self.assertEqual(st["leak_env"], "fail")
        self.assertEqual(st["leak_tests"], "fail")

    def test_builder_stage_files_are_not_leaks(self):
        d = write_task(self.tmp, "revert", ghsa="GHSA-aaaa-bbbb-cccc",
                       dockerfile="FROM python:3.12-slim AS builder\nCOPY revert.patch /revert.patch\n"
                                  "RUN git clone x /src && rm -rf /src/.git\nFROM python:3.12-slim\nCOPY --from=builder /src /app\n",
                       extra_files={"environment/revert.patch": "- fixed in GHSA-aaaa-bbbb-cccc"})
        st = {c["id"]: c["status"] for c in validate.static_checks(d)}
        self.assertEqual(st["leak_env"], "pass")

    def test_missing_files_and_network(self):
        d = write_task(self.tmp, "broken")
        os.remove(os.path.join(d, "instruction.md"))
        with open(os.path.join(d, "task.toml"), "w") as f:
            f.write('[task]\nname = "x"\n')
        st = {c["id"]: c["status"] for c in validate.static_checks(d)}
        self.assertEqual((st["layout"], st["offline"], st["metadata"]), ("fail", "fail", "fail"))

    def test_verdicts(self):
        T = lambda r, f=True: {"reward": r, "functional_ok": f}
        j = validate.judge
        self.assertEqual(j({"kind": "oracle", "attempts": 2}, [T(1.0), T(1.0)])[0], "pass")
        self.assertEqual(j({"kind": "oracle", "attempts": 2}, [T(1.0), T(0.9)])[0], "fail")
        self.assertEqual(j({"kind": "oracle", "attempts": 2}, [T(1.0), T(None)])[0], "infra")
        self.assertEqual(j({"kind": "nop", "attempts": 1}, [T(0.0)])[0], "pass")
        self.assertEqual(j({"kind": "nop", "attempts": 1}, [T(0.0, False)])[0], "fail")
        self.assertEqual(j({"kind": "variant", "expect": "partial", "name": "check_only"}, [T(0.5)])[0], "pass")
        status, why = j({"kind": "variant", "expect": "partial", "name": "check_only"}, [T(1.0)])
        self.assertEqual(status, "fail")
        self.assertIn("drop the task", why)
        self.assertEqual(j({"kind": "variant", "expect": "full", "name": "hardened"}, [T(0.5)])[0], "fail")
        self.assertEqual(j({"kind": "oracle", "attempts": 2}, [])[0], "infra")   # no results = Harbor broke, not the task

    def test_overall_and_expectations(self):
        self.assertEqual(validate.overall([{"status": "pass"}, {"status": "manual"}, {"status": "warn"}]), "needs-review")
        self.assertEqual(validate.overall([{"status": "pass"}]), "pass")
        self.assertEqual(validate.overall([{"status": "pass"}, {"status": "infra"}]), "incomplete")
        self.assertEqual(validate.overall([{"status": "pass"}, {"status": "fail"}]), "fail")
        self.assertEqual(validate.expectation("check_only_v2"), validate.PARTIAL)
        self.assertEqual(validate.expectation("hardened"), validate.FULL)

    def test_review_signs_off_only_open_items(self):
        rec = {"checks": [{"id": "oracle", "status": "pass"}, {"id": "fairness", "status": "manual"},
                          {"id": "variant:check_only", "status": "warn"}]}
        validate.apply_review(rec, {"fairness": "pass"}, "mapped", "me")
        self.assertEqual(rec["status"], "needs-review")
        validate.apply_review(rec, {"variant:check_only": "na"}, "single-site fix", "me")
        self.assertEqual(rec["status"], "pass")
        with self.assertRaises(ValueError):
            validate.apply_review(rec, {"oracle": "pass"}, "", "me")      # automated results cannot be overridden
        with self.assertRaises(ValueError):
            validate.apply_review(rec, {"fairness": "maybe"}, "", "me")
        validate.apply_review(rec, {"fairness": "fail"}, "test 3 unmapped", "me")  # a reviewer can change their mind
        self.assertEqual(rec["status"], "fail")

    def test_split_patch(self):
        text = ("diff --git a/a.py b/a.py\n+1\n" "diff --git a/b.py b/b.py\n+2\n")
        self.assertEqual(sorted(validate.split_patch(text)), ["a.py", "b.py"])


if __name__ == "__main__":
    unittest.main()
