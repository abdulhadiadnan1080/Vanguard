#!/usr/bin/env python3
# Stand-in for `harbor run` in tests: Harbor-shaped results, no Docker, no model API.
# FAKE_B_REWARD sets Model B's score; FAKE_CRASH_MODEL + FAKE_CRASH_FILE crash that model's first job only.
import json, os, sys, uuid, datetime
a = sys.argv; get = lambda f, d=None: a[a.index(f) + 1] if f in a else d
agent, task, out, name, k, model = get("-a"), get("-p"), get("-o"), get("--job-name"), int(get("-k", "1")), get("-m")
slug = os.path.basename(task.rstrip("/"))
if agent == "nop": reward = 0.0
elif agent == "oracle": reward = 0.6 if ("check-only" in slug or "producer-only" in slug) else 1.0
else: reward = {"vendor-a/model-a": 0.64, "vendor-b/model-b": float(os.environ.get("FAKE_B_REWARD", "0.5"))}[model]
crash = bool(model) and model == os.environ.get("FAKE_CRASH_MODEL") and not os.path.exists(os.environ.get("FAKE_CRASH_FILE", "/nonexistent"))
if crash: open(os.environ["FAKE_CRASH_FILE"], "w").close()   # crash the first model job only
for i in range(k):
    d = os.path.join(out, name, "%s__t%d" % (slug[:32], i)); os.makedirs(d + "/verifier", exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    json.dump({"id": str(uuid.uuid4()), "started_at": now, "finished_at": now, "exception_info": None,
               "config": {"agent": {"name": agent, "model_name": model}, "task": {"path": task}},
               "verifier_result": None if crash else {"rewards": {"reward": reward}},
               **({"exception_info": {"exception_type": "RuntimeError"}} if crash else {})}, open(d + "/result.json", "w"))
    json.dump({"reward": reward, "security": {"passed": int(reward * 10), "total": 10},
               "functional": {"passed": 4, "failed": 0, "total": 4}}, open(d + "/verifier/details.json", "w"))
