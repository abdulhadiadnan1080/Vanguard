"""A fresh clone must work on real-shaped data with no private pipeline: settings load lazily, the harvester is in the
repository, and `rank` runs before any task has ever been graded (starting weights)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
VANGUARD = os.path.dirname(HERE)
sys.path.insert(0, VANGUARD)

import harvester  # noqa: E402


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()

# A two-file fix in the shape the ranker looks for: a schema that drops the security field on reload (producer) and an
# authorization check in another file that starts to rely on that field.
PATCH = """From 1111111111111111111111111111111111111111 Mon Sep 17 00:00:00 2001
Subject: [PATCH] Keep the tenant on stored sessions and check it on every request

diff --git a/app/storage/session_schema.py b/app/storage/session_schema.py
--- a/app/storage/session_schema.py
+++ b/app/storage/session_schema.py
@@ -1,6 +1,7 @@ class SessionSchema(BaseModel):
 class SessionSchema(BaseModel):
-    model_config = ConfigDict(extra="ignore")
+    model_config = ConfigDict(extra="ignore")
+    tenant_id: str = Field(...)
     user_id: str
diff --git a/app/web/authz.py b/app/web/authz.py
--- a/app/web/authz.py
+++ b/app/web/authz.py
@@ -10,6 +10,8 @@ def authorize(request, session):
 def authorize(request, session):
+    if session.tenant_id != request.tenant_id:
+        raise PermissionDenied("tenant mismatch")
     return True
diff --git a/tests/test_authz.py b/tests/test_authz.py
--- a/tests/test_authz.py
+++ b/tests/test_authz.py
@@ -1,2 +1,4 @@
+def test_reloaded_session_keeps_tenant():
+    assert reload(save(session)).tenant_id == "t1"
"""


class HarvesterAnalysis(unittest.TestCase):
    def test_signature_finds_producer_and_check(self):
        c = {"language": "python", "summary": "Sessions loaded from storage lose their tenant", "description": ""}
        self.assertIsNone(harvester.size_decision(PATCH, "python"))
        sig, why = harvester.signature(c, PATCH)
        self.assertIsNotNone(sig, why)
        self.assertEqual(sig["features"]["producer_file"], "app/storage/session_schema.py")
        self.assertEqual(sig["features"]["check_file"], "app/web/authz.py")
        factors = {f[0] for f in sig["factors"]}
        self.assertTrue({"field_propagation", "producer_drops_field", "storage_boundary", "check_and_producer_split"} <= factors, factors)
        shape = harvester.analyse_patch(PATCH, "python")
        self.assertEqual((shape["src_files"], shape["test_files"]), (2, 1))

    def test_size_filter(self):
        self.assertIn("no source change", harvester.size_decision("diff --git a/README.md b/README.md\n+x\n", "python"))


class FreshClone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.priv, self.data = os.path.join(t, "private"), os.path.join(t, "data")
        os.makedirs(self.priv)
        os.makedirs(os.path.join(self.data, "patch_cache"))
        write(os.path.join(self.priv, "paths.json"), json.dumps({"task_namespace": "tasks"}))  # no pipeline at all
        sha = "1111111111111111111111111111111111111111"
        write(os.path.join(self.data, "patch_cache", sha + ".patch"), PATCH)
        adv = {"ghsa_id": "GHSA-test-0001", "repo": "example-org/app", "language": "python", "ecosystem": "pip",
               "summary": "Sessions loaded from storage lose their tenant", "description": "", "cwes": ["CWE-639"],
               "published_at": "2026-01-01T00:00:00Z", "fix_commits": [{"repo": "example-org/app", "sha": sha}]}
        write(os.path.join(self.data, "pool.jsonl"), json.dumps(adv) + "\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("VANGUARD_", "FAKE_"))}
        self.env.update(VANGUARD_PRIVATE=self.priv, VANGUARD_DATA=self.data,
                        VANGUARD_MODELS=os.path.join(HERE, "fixtures", "models.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(VANGUARD, "vanguard.py")] + list(args), env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_import_without_settings_does_not_exit(self):
        code = ("import sys; sys.path.insert(0, %r); import sources\n"
                "try:\n    sources.MODELS\nexcept sources.ConfigError as e:\n    print('ConfigError:', e)\n") % VANGUARD
        env = dict(self.env, VANGUARD_PRIVATE=os.path.join(self.tmp.name, "nowhere"))
        env.pop("VANGUARD_MODELS")
        p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ConfigError: missing", p.stdout)
        cli = subprocess.run([sys.executable, os.path.join(VANGUARD, "vanguard.py"), "rank"], env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertNotIn("Traceback", cli.stderr)
        self.assertIn("vanguard: missing", cli.stderr)

    def test_rank_without_pipeline_or_history(self):
        p = self.run_cli("rank", "--top", "5")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("scored 1 of 1", p.stdout)
        rank = [f for f in os.listdir(self.data) if f.startswith("rank_")]
        row = json.loads(read(os.path.join(self.data, rank[0])).splitlines()[0])
        self.assertIn("finder:field_propagation", row["signals"])
        self.assertGreater(row["p_fail"]["MODEL_A"], 0.5)
        short = [f for f in os.listdir(self.data) if f.startswith("shortlist_")]
        self.assertIn("starting weights", read(os.path.join(self.data, short[0])))


if __name__ == "__main__":
    unittest.main()
