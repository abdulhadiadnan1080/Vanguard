#!/usr/bin/env python3
"""Run the whole Vanguard pipeline end to end on synthetic data: no setup, no API keys, no Docker, about 20 seconds.

    python3 vanguard/demo.py                     # narrated run in a temporary folder
    python3 vanguard/demo.py --keep DIR          # keep the run's data (graph, rankings, packages, task) in DIR
    python3 vanguard/demo.py --write-sample PATH # also write the run's summary JSON to PATH

The real pipeline code runs every stage. Only the inputs are synthetic and two outside parties are stand-ins:
  * a history of 60 graded tasks across 10 fictional repositories, generated from a hidden "true" failure model,
    so the learner has something real to discover;
  * 40 fictional candidate advisories to rank;
  * the build agent and the evaluation harness are the test suite's stand-ins (tests/fixtures/), so "building" and
    "running models" take milliseconds and cost nothing.
"""
import argparse
import datetime
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "tests", "fixtures")
HISTORY = 60  # graded tasks in the synthetic history (6 per repository)
REPOS = ["billing-service", "doc-store", "auth-gateway", "media-pipeline", "chat-server", "job-queue", "config-hub",
         "report-engine", "sso-bridge", "asset-cdn"]
SIGNALS = {  # shape signals, with the hand-set weights the learner starts from
    "finder:field_propagation": (3.0, "the same security field is set by a data producer and read by a check in another file"),
    "finder:producer_drops_field": (1.5, "the producer drops or whitelists fields"),
    "finder:storage_boundary": (1.5, "the data crosses a save/load or serialize/parse boundary"),
    "finder:cross_package": (0.5, "check and producer live in different packages"),
    "finder:upstream_tests": (0.3, "the upstream fix ships tests"),
    "finder:check_calls_producer": (-3.0, "the check calls the producer directly (an easy data flow)"),
}
TRUTH = {  # the hidden "true" model the synthetic history is drawn from (logits). It deliberately differs from the
    # hand-set starting weights (dropped fields and boundaries matter more, upstream tests help the model), so the
    # demo shows the learner discovering what the hand weights got wrong.
    "bias": {"MODEL_A": -1.7, "MODEL_B": -1.1},
    "finder:field_propagation": 0.5, "finder:producer_drops_field": 2.3, "finder:storage_boundary": 1.9,
    "finder:cross_package": 0.0, "finder:upstream_tests": -1.1, "finder:check_calls_producer": -3.2,
}


def banner(n, title, what):
    print("\n\033[1m[%d/4] %s\033[0m  %s" % (n, title, what))


def say(msg):
    print("      " + msg)


def setup(work):
    """A private-settings folder and data folder for the demo, pointing at the test suite's stand-ins."""
    priv, data = os.path.join(work, "private"), os.path.join(work, "data")
    for d in (priv, data, os.path.join(work, "pipeline"), os.path.join(work, "finder")):
        os.makedirs(d, exist_ok=True)
    json.dump({"pipeline": "../pipeline", "finder": "../finder", "extra_job_dirs": [], "task_namespace": "demo"},
              open(os.path.join(priv, "paths.json"), "w"), indent=1)
    models = json.load(open(os.path.join(FIX, "models.json")))
    for m in models["models"]:
        m["env_file"] = os.path.join(FIX, m["env_file"])
    json.dump(models, open(os.path.join(priv, "models.json"), "w"), indent=1)
    json.dump({"task_tags": {}}, open(os.path.join(priv, "task_tags.json"), "w"))
    json.dump({"builder_cmd": [sys.executable, os.path.join(FIX, "fake_builder.py")], "builder_timeout_min": 2,
               "max_active": 3}, open(os.path.join(work, "config.json"), "w"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VANGUARD_", "FAKE_"))}
    env.update(VANGUARD_PRIVATE=priv, VANGUARD_DATA=data, VANGUARD_CONFIG=os.path.join(work, "config.json"),
               VANGUARD_HARBOR=os.path.join(FIX, "fake_harbor.py"), VANGUARD_TEST_NO_DOCKER="1",
               USER="demo-reviewer")  # approvals and reviews are signed with this name
    return env


def sigmoid(z):
    return 1 / (1 + math.exp(-z))


def draw_signals(rng):
    x = {}
    if rng.random() < 0.45:  # about half the fixes have a separate data producer
        x["finder:field_propagation"] = 1.0
        for s in ("finder:producer_drops_field", "finder:storage_boundary", "finder:cross_package"):
            if rng.random() < 0.55:
                x[s] = 1.0
        if rng.random() < 0.25:
            x["finder:check_calls_producer"] = 1.0
    if rng.random() < 0.7:
        x["finder:upstream_tests"] = 1.0
    return x


def p_true(x, model):
    return sigmoid(TRUTH["bias"][model] + sum(TRUTH[f] for f in x))


def fake_sha(rng):
    return "".join(rng.choice("0123456789abcdef") for _ in range(40))


def seed_history(g, sources, ingest, rng):
    """HISTORY graded tasks across 10 fictional repositories: oracle-validated, then 1-3 attempts per model."""
    for m in sources.MODELS:
        g.node("model:" + m["id"], "Model", m["label"])
    for sid, (w, why) in SIGNALS.items():
        g.node(sid, "Signal", sid.split(":")[1], weight=w, description=why, source="demo")
    for w in json.load(open(os.path.join(HERE, "catalog.json")))["weaknesses"]:
        g.node("weakness:" + w["id"], "Weakness", w["id"], description=w["label"], models=w["models"], weight=w["weight"])
    start = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)
    n = 0
    for r, repo in enumerate(REPOS):
        for k in range(HISTORY // len(REPOS)):
            n += 1
            cid, tid = "cand:GHSA-demo-h%03d" % n, "task:demo-history-%02d" % n
            x = draw_signals(rng)
            g.node(cid, "Candidate", cid[5:], repo="example-org/" + repo, language="python", synthetic=True)
            for s in x:
                g.edge(cid, "HAS_SIGNAL", s, reason=SIGNALS[s][1])
            g.node(tid, "Task", tid[5:], kind="real", repo="example-org/" + repo, synthetic=True)
            g.edge(tid, "BUILT_FROM", cid)
            t0 = start + datetime.timedelta(days=n)
            g.node("trial:%s-oracle" % tid, "Trial", "oracle", agent="oracle", reward=1.0, status="valid",
                   started_at=t0.isoformat().replace("+00:00", "Z"), task=tid[5:])
            g.edge("trial:%s-oracle" % tid, "VALIDATES", tid, agent="oracle", reward=1.0)
            for m in sources.MODEL_IDS:
                for a in range(rng.choice((1, 2, 3))):
                    fail = rng.random() < p_true(x, m)
                    trid = "trial:%s-%s-%d" % (tid, m.lower(), a)
                    g.node(trid, "Trial", trid[6:], agent="terminus-2", model=m, status="valid", task=tid[5:],
                           reward=round(rng.uniform(0.3, 0.9), 2) if fail else 1.0,
                           started_at=(t0 + datetime.timedelta(hours=1 + a)).isoformat().replace("+00:00", "Z"))
                    g.edge(trid, "ON", tid)
                    g.edge(trid, "BY", "model:" + m)
    ingest.recompute_outcomes(g)
    g.db.commit()
    return n


def rank_pool(g, sources, learner, model, rng, data):
    """40 fictional candidate advisories, scored by the trained predictor; written in the format `rank` writes."""
    w = model["weights"]
    rows = []
    for i in range(1, 41):
        x = draw_signals(rng)
        views = {m: learner.for_model(g, x, m) for m in sources.MODEL_IDS}
        p = {m: round(learner.predict(w, views[m]), 3) for m in sources.MODEL_IDS}
        both = 1.0
        for v in p.values():
            both *= v
        ghsa = "GHSA-demo-%04d" % i
        rows.append({"ghsa_id": ghsa, "repo": "example-org/%s-%02d" % (rng.choice(REPOS), i), "language": "python",
                     "fix_commit": fake_sha(rng), "summary": "Synthetic advisory %d" % i, "p_fail": p,
                     "p_both": round(both, 3), "why": learner.explain(w, views[sources.BASE_MODEL]), "signals": sorted(x),
                     "reject": None, "already_built": False, "model_version": model["version"], "synthetic": True})
        cid = "cand:" + ghsa
        g.node(cid, "Candidate", ghsa, repo=rows[-1]["repo"], language="python", synthetic=True)
        for s in x:
            g.edge(cid, "HAS_SIGNAL", s, reason=SIGNALS[s][1])
        for m in sources.MODEL_IDS:
            g.record_prediction(cid, "model:" + m, p[m], model["version"])
    rows.sort(key=lambda r: -r["p_both"])
    path = os.path.join(data, "rank_%s.jsonl" % datetime.datetime.now().strftime("%Y%m%d-%H%M"))
    with open(path, "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    g.db.commit()
    return rows


def run(env, *args, show=("->",)):
    p = subprocess.run([sys.executable, os.path.join(HERE, "vanguard.py")] + list(args), env=env, cwd=HERE,
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        sys.exit("demo: `vanguard.py %s` failed:\n%s%s" % (" ".join(args), p.stdout, p.stderr))
    for line in p.stdout.splitlines():
        if any(s in line for s in show):
            say(line.strip())
    return p.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", metavar="DIR", help="keep the run's data in DIR instead of a temporary folder")
    ap.add_argument("--write-sample", metavar="PATH", help="write the run's summary JSON to PATH")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    work = os.path.abspath(args.keep) if args.keep else tempfile.mkdtemp(prefix="vanguard-demo-")
    if args.keep and os.path.exists(os.path.join(work, "data")):
        sys.exit("demo: %s already holds a run; pick an empty folder" % work)
    env = setup(work)
    os.environ.update(env)  # before importing the pipeline: it reads its settings at import time
    sys.path.insert(0, HERE)
    import ingest
    import learner
    import loop
    import sources
    from knowledge_graph import Graph
    rng = random.Random(args.seed)
    print("\033[1mVanguard demo\033[0m: the real pipeline on synthetic data (no keys, no Docker, no cost)")
    print("      working folder: %s" % work)

    banner(1, "Knowledge graph", "remember every task, run and outcome; learn what makes models fail")
    g = Graph(os.path.join(sources.DATA, "graph.sqlite"))
    n = seed_history(g, sources, ingest, rng)
    groups, _ = ingest.valid_trials(g)
    say("history: %d graded tasks, %d model trials across %d repositories" % (
        n, sum(len(v) for v in groups.values()), len(REPOS)))
    g.close()
    out = run(env, "train", show=("leave-one-repo-out", "base_rate", "prior_only", "fitted", "largest moves"))
    model = json.load(open(os.path.join(sources.DATA, "model.json")))

    banner(2, "Harvester", "score every candidate advisory for each model's chance of failing")
    g = Graph(os.path.join(sources.DATA, "graph.sqlite"))
    rows = rank_pool(g, sources, learner, model, rng, sources.DATA)
    g.close()
    say("ranked %d candidates; top 3:" % len(rows))
    for r in rows[:3]:
        say("  %-15s %-28s %s  why: %s" % (r["ghsa_id"], r["repo"], "  ".join(
            "%s %.2f" % (sources.label(m), r["p_fail"][m]) for m in sources.MODEL_IDS),
            ", ".join(f.split(":")[1] for f, _ in r["why"][:2]) or "-"))
    accept, reject = rows[0]["ghsa_id"], rows[1]["ghsa_id"]
    env["STUB_REJECT"] = reject  # the stand-in agent's triage turns the second candidate down

    banner(3, "Synthesizer", "an agent triages each candidate and builds a task; a person approves it")
    run(env, "loop", "--select", "2")
    run(env, "loop", "--build", "--max-builds", "2")
    run(env, "approve", accept)

    banner(4, "Judge", "validate the task for fairness, run the models behind gates, feed results back")
    run(env, "loop", show=("->", "done ", "result:"))
    g = Graph(os.path.join(sources.DATA, "graph.sqlite"))
    slug = os.path.basename(loop.state_of(g, accept)["task_dir"])
    g.close()
    run(env, "review", slug, "--set", "repo_tests=pass", "--set", "fairness=pass", "--note", "demo reviewer", show=("review",))
    run(env, "loop")
    run(env, "loop", "--run-models", "--max-model-runs", "2")
    run(env, "loop", show=("->", "forecast"))
    run(env, "approve", accept, "--more-runs")
    run(env, "loop", "--run-models", "--max-model-runs", "4")
    run(env, "loop", show=("->", "forecast", "train:"))
    run(env, "report", show=())

    sample = summarize(work, sources, accept, reject, slug)
    path = os.path.join(work, "sample_vanguard_benchmark.json")
    json.dump(sample, open(path, "w"), indent=2)
    if args.write_sample:
        shutil.copy(path, args.write_sample)
    print("\n\033[1mDone.\033[0m  %s accepted (failed by both models on every attempt), %s rejected at triage." % (accept, reject))
    say("summary JSON: %s" % path)
    say("full report:  %s" % os.path.join(sources.DATA, "report.md"))
    if not args.keep:
        say("(temporary folder; use --keep DIR to keep everything)")


def summarize(work, sources, accept, reject, slug):
    """The run's artifacts in one JSON document: what a dataset reviewer would want to see."""
    from knowledge_graph import Graph
    g = Graph(os.path.join(sources.DATA, "graph.sqlite"))
    model = json.load(open(os.path.join(sources.DATA, "model.json")))
    rank_file = sorted(f for f in os.listdir(sources.DATA) if f.startswith("rank_"))[-1]
    ranked = [json.loads(l) for l in open(os.path.join(sources.DATA, rank_file))]
    st = g.get("cand:" + accept)["loop"]
    task_dir = st["task_dir"]
    nodes, edges = g.counts()
    out = {
        "_about": "Sample output of `python3 vanguard/demo.py`: the real pipeline run on SYNTHETIC data (fictional "
                  "repositories and advisories, stand-in build agent and harness). Field names and structure match a "
                  "real run; the values are illustrative.",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "models": [{"id": m["id"], "label": m["label"]} for m in sources.MODELS],
        "knowledge_graph": {
            "nodes_by_type": nodes, "edges_by_relation": edges,
            "what_defeats_which_model": [
                {"signal_or_weakness": e["src"], "model": sources.label(e["dst"]), "tasks_failed": e["tasks_failed"],
                 "tasks": e["tasks"], "runs_failed": e["fails"], "runs": e["runs"]}
                for e in sorted(g.edges(rel="DEFEATS"), key=lambda e: -e.get("runs", 0))
                if e["src"].startswith(("finder:", "weakness:"))][:8],
        },
        "predictor": {"version": model["version"], "labelled_tasks": model["labelled_tasks"],
                      "held_out_brier": {k: model["evaluation"][k]["brier"] for k in ("base_rate", "prior_only", "fitted")},
                      "weights": {k: round(v, 3) for k, v in sorted(model["weights"].items(), key=lambda kv: -abs(kv[1]))[:8]}},
        "shortlist": [{k: r[k] for k in ("ghsa_id", "repo", "p_fail", "p_both", "why", "signals")} for r in ranked[:5]],
        "benchmark_task": {
            "id": slug, "built_from": accept,
            "files": sorted(os.path.relpath(os.path.join(dp, f), task_dir) for dp, _, fs in os.walk(task_dir) for f in fs),
            "validation": (g.get("task:" + slug) or {}).get("validation"),
            "rounds": st.get("rounds"),
            "verdict": st["state"],
        },
        "loop_history": {c: [{"state": h["state"], "note": h.get("note")} for h in g.get("cand:" + c)["loop"]["history"]]
                         for c in (accept, reject)},
        "forecast_checks": [{k: f[k] for k in ("task", "model", "p_fail", "fails", "runs", "brier")}
                            for f in g.metrics("forecast")],
    }
    g.close()
    return json.loads(json.dumps(out).replace(work, "<demo-folder>"))  # no local paths in the shared sample


if __name__ == "__main__":
    main()
