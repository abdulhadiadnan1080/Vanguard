#!/usr/bin/env python3
"""Vanguard: rank real advisories by how likely models are to fail a task built from them, and learn from every run.

Commands (none of them runs a model, starts Docker or needs API keys; only --fetch touches the network, for patches):

    python3 vanguard.py harvest           fetch reviewed advisories from GitHub's advisory database into data/pool.jsonl
    python3 vanguard.py ingest            build/refresh the knowledge graph from all past tasks and Harbor trials
    python3 vanguard.py train             fit the predictor on recorded outcomes and score it (leave-one-repo-out)
    python3 vanguard.py rank [--top 25]   score every advisory in the finder pool, write a shortlist
    python3 vanguard.py collect           record new Harbor results, check earlier predictions, retrain if anything changed
    python3 vanguard.py watch             keep collecting while runs are in progress (Ctrl-C to stop)
    python3 vanguard.py validate TASK     fairness checks for a built task (static, then oracle/nop/variant runs in Docker)
    python3 vanguard.py review TASK --set fairness=pass ...   sign off the manual items of a validation
    python3 vanguard.py loop [--select N] advance the end-to-end loop one step per candidate (see loop.py; paid steps
                                          need --build --max-builds N / --run-models --max-model-runs N)
    python3 vanguard.py loop status       where every candidate is and what it waits for
    python3 vanguard.py approve GHSA [--more-runs]    gate 1 (built task) / gate 2 (attempts 2-3)
    python3 vanguard.py reject GHSA --reason "..."
    python3 vanguard.py doctor            check the machine, the data and the code before a run (exit 1 on a problem)
    python3 vanguard.py report            what the graph knows, how accurate the predictor is, and how that changed
    python3 vanguard.py export [PATH]     dump the whole graph as JSON

Data lives in vanguard/data/ (graph.sqlite, model.json, rank_*.jsonl, shortlist_*.md, report.md).
"""
import argparse
import collections
import datetime
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ingest as ingest_mod  # noqa: E402
import learner  # noqa: E402
import harvester  # noqa: E402
import sources  # noqa: E402
import validate as validate_mod  # noqa: E402
import loop as loop_mod  # noqa: E402
from knowledge_graph import Graph, parse_ts  # noqa: E402

DATA = sources.DATA
GRAPH = os.path.join(DATA, "graph.sqlite")
MODEL = os.path.join(DATA, "model.json")


def models():
    return tuple(sources.MODEL_IDS)


def base():
    return sources.BASE_MODEL


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M")


def cmd_harvest(g, args):
    """Reviewed advisories with a fix commit, from GitHub's public advisory database, merged into data/pool.jsonl."""
    rows = harvester.harvest(args.ecosystems, args.since, args.pages)
    path = os.path.join(DATA, "pool.jsonl")
    pool = {r["ghsa_id"]: r for r in sources.load_jsonl(path)}
    new = sum(1 for r in rows if r["ghsa_id"] not in pool)
    pool.update((r["ghsa_id"], r) for r in rows)
    os.makedirs(DATA, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in sorted(pool.values(), key=lambda r: r.get("published_at") or "", reverse=True):
            f.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(tmp, path)
    print("harvest: %d advisories with a fix commit (%d new); pool now %d -> %s" % (len(rows), new, len(pool), path))
    if args.fetch:
        got = sum(1 for r in rows if sources.patch_for((r["fix_commits"][0] or {}).get("repo"), r["fix_commits"][0]["sha"], fetch=True))
        print("  fix patches cached: %d of %d" % (got, len(rows)))


def cmd_ingest(g, args):
    ingest_mod.ingest(g)


def cmd_train(g, args):
    if not g.nodes("Signal"):
        ingest_mod.ingest(g, log=lambda *a: None)
    rows = learner.task_rows(g)
    prior = learner.priors(g, rows)
    if not rows:  # nothing run yet: the starting (hand-set) weights are the model until outcomes arrive
        model = {"version": stamp() + "-prior", "trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "rows": 0, "labelled_tasks": 0, "weights": learner.prior_weights(prior),
                 "prior": {k: v[0] for k, v in prior.items()}, "evaluation": None}
        os.makedirs(DATA, exist_ok=True)
        with open(MODEL + ".tmp", "w", encoding="utf-8") as f:
            json.dump(model, f, indent=1, sort_keys=True)
        os.replace(MODEL + ".tmp", MODEL)
        print("train: no labelled outcomes yet, so the model is the starting weights (%d signals). Run tasks, then "
              "`collect`, to start learning." % len(prior))
        return
    weights = learner.fit(rows, prior)
    ev = learner.evaluate(rows, prior)
    labelled_tasks = len({r["task"] for r in rows})
    model = {"version": stamp(), "trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
             "rows": len(rows), "labelled_tasks": labelled_tasks, "weights": weights,
             "prior": {k: v[0] for k, v in prior.items()}, "evaluation": ev}
    os.makedirs(DATA, exist_ok=True)
    tmp = MODEL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=1, sort_keys=True)
    os.replace(tmp, MODEL)  # atomic: a crash mid-write never leaves a half-written model
    g.metric("train", version=model["version"], rows=len(rows), labelled_tasks=labelled_tasks, **{
        "%s_%s" % (m, k): ev[m][k] for m in ("base_rate", "prior_only", "fitted") for k in ("brier", "logloss")})
    print("train: %d rows from %d labelled tasks (%d repos)" % (len(rows), labelled_tasks, ev["n_groups"]))
    if ev["fitted"]["brier"] is None:  # every repository would have to be held out against nothing
        print("  held-out accuracy: not measurable yet (needs labelled tasks from at least 2 repositories)")
    else:
        print("  leave-one-repo-out   Brier   log loss   (lower is better)")
        for m in ("base_rate", "prior_only", "fitted"):
            print("  %-18s %7.4f   %7.4f" % (m, ev[m]["brier"], ev[m]["logloss"]))
    moved = sorted(((weights[f] - prior.get(f, (0, 0))[0], f) for f in weights), key=lambda t: -abs(t[0]))[:6]
    print("  largest moves from the prior: " + ", ".join("%s %+.2f" % (f, d) for d, f in moved if abs(d) >= 0.05))
    print("  saved %s" % os.path.relpath(MODEL, HERE))


def load_model(g):
    if not os.path.exists(MODEL):
        cmd_train(g, None)
    with open(MODEL, encoding="utf-8") as f:
        return json.load(f)


def cmd_rank(g, args):
    if not g.nodes("Signal"):
        ingest_mod.ingest(g, log=lambda *a: None)
    model = load_model(g)
    w = model["weights"]
    catalog_kb = [x for x in (sources.load_json(sources.WEAKNESS_FILE, []) or [])
                  if x.get("status") != "rejected" and x.get("weight")]
    pool_path = args.pool or sources.POOL
    pool = sources.load_jsonl(pool_path)
    if not pool:
        sys.exit("rank: no advisories in %s. Fetch some first: `vanguard.py harvest --since 2025-01-01 --pages 3 --fetch`" % pool_path)
    built = {(g.get(e["dst"]) or {}).get("label") for e in g.edges(rel="BUILT_FROM")}
    repo_adv = collections.Counter(r.get("repo") for r in pool)
    out, skipped = [], collections.Counter()
    for row in pool:
        fix = (row.get("fix_commits") or [{}])[0]
        text = sources.patch_for(fix.get("repo") or row.get("repo"), fix.get("sha"), fetch=args.fetch)
        if not text:
            skipped["no patch (use --fetch)"] += 1
            continue
        res = sources.analyse(row, text, catalog_kb, repo_adv.get(row.get("repo"), 1))
        x = {sid: 1.0 for sid, _, _ in res["signals"]}
        views = {m: learner.for_model(g, x, m) for m in models()}
        p = {m: learner.predict(w, views[m]) for m in models()}
        item = {"ghsa_id": row["ghsa_id"], "repo": row.get("repo"), "language": row.get("language"),
                "summary": (row.get("summary") or "")[:140], "fix_commit": fix.get("sha"),
                "p_fail": {m: round(v, 3) for m, v in p.items()}, "p_both": round(_product(p.values()), 3),
                "finder_points": (res["finder"] or {}).get("points"), "reject": res["reject"],
                "already_built": row["ghsa_id"] in built, "signals": sorted(x),
                "why": learner.explain(w, views[base()]), "model_version": model["version"]}
        out.append(item)
        cid = "cand:" + row["ghsa_id"]
        g.node(cid, "Candidate", row["ghsa_id"], repo=row.get("repo"), language=row.get("language"),
               summary=row.get("summary"), fix_commit=fix.get("sha"), reject=res["reject"],
               finder_points=item["finder_points"], finder_features=(res["finder"] or {}).get("features"),
               shape=res["shape"])
        for sid, wt, reason in res["signals"]:
            g.edge(cid, "HAS_SIGNAL", sid, weight=wt, reason=reason)
        for m in models():
            g.edge(cid, "PREDICTED", "model:" + m, p_fail=item["p_fail"][m], version=model["version"],
                   at=model["trained_at"])
            g.record_prediction(cid, "model:" + m, item["p_fail"][m], model["version"])
    # evidence behind each score: labelled tasks that share the candidate's strongest positive signal
    rows = learner.task_rows(g)
    labelled, repos = collections.defaultdict(set), collections.defaultdict(set)
    for r in rows:
        for f in r["x"]:
            labelled[f].add(r["task"])
            repos[f].add(r["group"])
    for i in out:
        pos = [f for f, c in i["why"] if c > 0]
        i["evidence_signal"] = pos[0] if pos else None
        i["evidence_tasks"] = len(labelled.get(i["evidence_signal"], ()))
        i["evidence_repos"] = len(repos.get(i["evidence_signal"], ()))
    eligible, seen_fix = [], set()
    for i in sorted(out, key=lambda i: (-i["p_both"], -i["p_fail"][base()])):
        if i["already_built"] or _unbuildable(i["reject"]) or i["fix_commit"] in seen_fix:
            continue  # one advisory per fix commit: several GHSAs can share one fix
        seen_fix.add(i["fix_commit"])
        eligible.append(i)
    other_tasks = min((len({r["task"] for r in rows if r["model"] == "model:" + m}) for m in models()[1:]), default=0)
    s = stamp()
    jsonl = os.path.join(DATA, "rank_%s.jsonl" % s)
    with open(jsonl, "w", encoding="utf-8") as f:
        for i in sorted(out, key=lambda i: -i["p_both"]):
            f.write(json.dumps(i, sort_keys=True) + "\n")
    md = os.path.join(DATA, "shortlist_%s.md" % s)
    with open(md, "w", encoding="utf-8") as f:
        f.write(shortlist_md(eligible[:args.top], model, len(pool), len(out), len(eligible), skipped, other_tasks))
    print("rank: scored %d of %d advisories (%s); %d eligible" % (len(out), len(pool), dict(skipped) or "all patches cached", len(eligible)))
    for n, i in enumerate(eligible[:10], 1):
        print("  %2d. %-20s %-34s %s  %s" % (n, i["ghsa_id"], (i["repo"] or "")[:34],
              "  ".join("%s %.2f" % (sources.label(m)[-1], i["p_fail"][m]) for m in models()), ", ".join(f for f, _ in i["why"][:2])))
    print("  wrote %s and %s" % (os.path.relpath(md, HERE), os.path.relpath(jsonl, HERE)))


def _unbuildable(reject):
    return bool(reject) and ("no source change" in reject or "oversized" in reject)


def _product(xs):
    out = 1.0
    for x in xs:
        out *= x
    return out


def shortlist_md(items, model, n_pool, n_scored, n_eligible, skipped, other_tasks):
    ev = model["evaluation"]
    accuracy = ("Model trained on %d labelled tasks; leave-one-repo-out Brier %.3f (base rate %.3f, hand weights %.3f). "
                "Treat these as a ranking, not a guarantee." % (model["labelled_tasks"], ev["fitted"]["brier"],
                                                                 ev["base_rate"]["brier"], ev["prior_only"]["brier"])
                ) if ev and ev["fitted"]["brier"] is not None else (
                    "Model trained on %d labelled task(s); held-out accuracy needs tasks from at least 2 repositories."
                    % model["labelled_tasks"] if ev else
                    "No labelled outcomes yet: these scores are the starting weights, before any learning.")
    lines = [
        "# Vanguard shortlist (%s)" % model["version"], "",
        "Predicted chance that each model **fails one attempt** at a task built from the advisory. " + accuracy, "",
        "**Evidence** is how many labelled tasks, and from how many different repositories, share the candidate's "
        "strongest signal. A high probability backed by one repository is a hypothesis to test, not a finding. The other models have as few as %d labelled task(s), so "
        "their columns mostly reuse %s evidence." % (other_tasks, sources.label(base())), "",
        "Pool %d · scored %d · eligible %d (not built yet, buildable size, one per fix commit) · skipped %s" % (
            n_pool, n_scored, n_eligible, dict(skipped) or "none"), "",
        "| # | Advisory | Repo | Lang | %s | Evidence | Finder pts | Main reasons |" % " | ".join(
            "P(%s fails)" % sources.label(m) for m in models()),
        "|---|---|---|---|%s---|---|---|" % ("---|" * len(models()))]
    for n, i in enumerate(items, 1):
        why = "; ".join("%s (%+.1f)" % (f.split(":", 1)[1], c) for f, c in i["why"])
        lines.append("| %d | %s | %s | %s | %s | %d task(s), %d repo(s) | %s | %s |" % (
            n, i["ghsa_id"], i["repo"], i["language"], " | ".join("%.2f" % i["p_fail"][m] for m in models()),
            i["evidence_tasks"], i["evidence_repos"], "" if i["finder_points"] is None else i["finder_points"], why or "no strong signal"))
    return "\n".join(lines) + "\n"


def cmd_collect(g, args, quiet=False):
    """Record new Harbor results: re-read every job folder (idempotent), report what is new, score any prediction that
    was made before the task ran, and retrain when the set of counted outcomes changed."""
    before = {t["id"]: t.get("status") for t in g.nodes("Trial")}
    groups_before, _ = ingest_mod.valid_trials(g)
    ingest_mod.ingest(g, log=lambda *a: None)
    trials = {t["id"]: t for t in g.nodes("Trial")}
    new = [t for i, t in trials.items() if before.get(i) in (None, "running") and t.get("status") != "running"]
    groups, stats = ingest_mod.valid_trials(g)
    changed = sorted(k for k in groups if len(groups[k]) != len(groups_before.get(k, ())))
    running = sum(1 for t in trials.values() if t.get("status") == "running")
    if new or not quiet:
        print("collect: %d new finished trial(s), %d still running; counted outcomes changed for %d task/model pair(s)"
              % (len(new), running, len(changed)))
        for t in sorted(new, key=lambda t: t.get("started_at") or ""):
            print("  %-40s %-8s %-7s reward %s" % (t["task"][:40], t.get("model") or t.get("agent"), t["status"],
                                                   "-" if t.get("reward") is None else "%.3f" % t["reward"]))
    for f in check_forecasts(g, groups):
        print("  forecast check: %s on %s predicted P(fail) %.2f before it ran; actual %d / %d failed" % (
            f["model"], f["task"], f["p_fail"], f["fails"], f["runs"]))
    if changed and not args.no_train:
        cmd_train(g, None)
    g.db.commit()
    return len(new)


def check_forecasts(g, groups):
    """For tasks built from a ranked candidate: compare the last prediction made before the first run with the result.
    Logged once per (task, model, number of runs) as a 'forecast' metric: the truest test of the predictor."""
    done = {m["key"] for m in g.metrics("forecast")}
    out = []
    for (task, model), ts in sorted(groups.items()):
        start = min((parse_ts(t.get("started_at")) for t in ts if t.get("started_at")), default=None)
        if not start:
            continue
        for b in g.edges(src=task, rel="BUILT_FROM"):
            pred = g.prediction_before(b["dst"], model, start)
            if not pred:
                continue
            fails = sum(1 for t in ts if t["reward"] < 1.0)
            key = "%s|%s|%d" % (task, model, len(ts))
            if key in done:
                continue
            f = {"key": key, "task": task.split(":", 1)[1], "model": sources.label(model), "candidate": b["dst"],
                 "p_fail": pred[0], "version": pred[1], "fails": fails, "runs": len(ts),
                 "brier": round((pred[0] - fails / len(ts)) ** 2, 4)}
            g.metric("forecast", **f)
            out.append(f)
    return out


def job_fingerprint():
    fp = set()
    for base in sources.JOB_DIRS:
        for p in glob.glob(os.path.join(base, "*", "*", "result.json")):
            try:
                fp.add((p, os.path.getmtime(p)))
            except OSError:
                pass
    return fp


def cmd_watch(g, args):
    print("watch: checking %s every %ds; Ctrl-C to stop" % (", ".join(os.path.relpath(d, sources.ROOT) for d in sources.JOB_DIRS), args.interval))
    last = job_fingerprint()
    cmd_collect(g, args)
    try:
        while True:
            time.sleep(args.interval)
            fp = job_fingerprint()
            if fp != last:
                last = fp
                cmd_collect(g, args, quiet=True)
    except KeyboardInterrupt:
        print("watch: stopped")


def resolve_task(arg):
    if os.path.isfile(os.path.join(arg, "task.toml")):
        return os.path.abspath(arg)
    for slug, path in sources.task_dirs():
        if slug == arg:
            return path
    sys.exit("validate: no task folder %r (give a path, or a slug under %s)" % (arg, ", ".join(sources.TASK_DIRS)))


def validate_task(g, task_dir, auto_split=False, parallel=3, force=False, static_only=False, log=print):
    """Validate one task and store the record on its graph node. Returns the record, or None if it could not run
    (the reason is logged): a task that already has model runs (without force), or Docker/Harbor not ready."""
    slug = os.path.basename(os.path.normpath(task_dir))
    log("validate %s" % slug)
    checks = validate_mod.static_checks(task_dir)
    for c in checks:
        log("  %-12s %-6s %s: %s" % (c["id"], c["status"], c["name"], c["detail"]))
    if static_only:
        checks.append({"id": "harbor", "name": "Oracle, nop and variant runs", "status": "skipped", "detail": "--static-only"})
    elif any(c["status"] == "fail" for c in checks):
        checks.append({"id": "harbor", "name": "Oracle, nop and variant runs", "status": "skipped",
                       "detail": "static checks failed: fix them first"})
    else:
        ingest_mod.ingest(g, log=lambda *a: None)
        groups, _ = ingest_mod.valid_trials(g)
        ran = sum(len(ts) for (t, _), ts in groups.items() if t == "task:" + slug)
        if ran and not force:
            log("  stop: %s already has %d counted model run(s). A new oracle run marks a task as changed, so those "
                "runs would stop counting. Re-validate only after editing the task, with --force." % (slug, ran))
            return None
        problems = validate_mod.preflight()
        if problems:
            log("  stop: " + "; ".join(problems))
            return None
        checks += validate_mod.run_checks(task_dir, use_auto=auto_split, parallel=parallel, log=log)
        ingest_mod.ingest(g, log=lambda *a: None)
    status = validate_mod.overall(checks)
    review = [c["name"] for c in checks if c["status"] in ("manual", "warn")]
    record = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "status": status, "checks": checks}
    g.node("task:" + slug, "Task", slug, validation=record)
    g.metric("validation", task=slug, status=status, fails=[c["id"] for c in checks if c["status"] == "fail"])
    os.makedirs(os.path.join(DATA, "validation"), exist_ok=True)
    with open(os.path.join(DATA, "validation", slug + ".json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1)
    log("  result: %s%s" % (status.upper(), "; open for a reviewer: " + "; ".join(review) if review else ""))
    g.db.commit()
    return record


def cmd_validate(g, args):
    failed = False
    for arg in args.tasks:
        rec = validate_task(g, resolve_task(arg), args.auto_split, args.parallel, args.force, args.static_only)
        failed |= rec is None
    if failed:
        sys.exit(1)


def cmd_loop(g, args):
    if args.action == "status":
        rows = loop_mod.all_loop(g)
        if not rows:
            print("loop: empty. Start with `vanguard.py loop --select 3` (free: writes build packages only)")
            return
        print("%-22s %-20s %-20s %s" % ("candidate", "state", "since", "next step"))
        for st in rows:
            print("%-22s %-20s %-20s %s" % (st["ghsa"], st["state"], st["history"][-1]["at"],
                                            loop_mod.NEXT_STEP.get(st["state"], "-").replace("GHSA", st["ghsa"])))
        return
    if args.run_models and args.max_model_runs is None:
        sys.exit("loop: --run-models spends money; give an explicit budget with --max-model-runs N")
    if args.build and args.max_builds is None:
        sys.exit("loop: --build starts paid agents; give an explicit budget with --max-builds N")
    with loop_mod.Lock():
        print("loop tick: select %d, builds %s, model runs %s" % (
            args.select, args.max_builds if args.build else "off", args.max_model_runs if args.run_models else "off"))
        hooks = {"collect": lambda g: cmd_collect(g, argparse.Namespace(no_train=False), quiet=True),
                 "validate": lambda task_dir: validate_task(g, task_dir, parallel=3)}
        left = loop_mod.tick(g, {"select": args.select, "build": args.build, "max_builds": args.max_builds or 0,
                                 "run_models": args.run_models, "max_model_runs": args.max_model_runs or 0}, hooks)
        if args.build or args.run_models:
            print("budget left: builds %d, model runs %d" % (left["builds"], left["model_runs"]))
    cmd_loop(g, argparse.Namespace(action="status"))


def cmd_approve(g, args):
    loop_mod.approve(g, args.ghsa, args.more_runs)


def cmd_reject(g, args):
    loop_mod.reject(g, args.ghsa, args.reason)


def cmd_review(g, args):
    task_dir = resolve_task(args.task)
    slug = os.path.basename(os.path.normpath(task_dir))
    node = g.get("task:" + slug) or {}
    record = node.get("validation")
    if not record:
        sys.exit("review: %s has not been validated yet (run `vanguard.py validate %s`)" % (slug, slug))
    if not args.set:
        open_items = [c for c in record["checks"] if c["status"] in ("manual", "warn")]
        print("review %s: %s" % (slug, record["status"]))
        for c in open_items:
            print("  open  %-22s %s: %s" % (c["id"], c["name"], c["detail"]))
        print("  sign off with: vanguard.py review %s --set <id>=pass|fail|na --note \"why\"" % slug if open_items else "  nothing open")
        return
    decisions = dict(x.split("=", 1) for x in args.set)
    try:
        validate_mod.apply_review(record, decisions, args.note, os.environ.get("USER", "reviewer"))
    except ValueError as e:
        sys.exit("review: %s" % e)
    record["reviewed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    g.node("task:" + slug, "Task", slug, validation=record)
    g.metric("review", task=slug, status=record["status"], decisions=decisions, note=args.note)
    with open(os.path.join(DATA, "validation", slug + ".json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1)
    print("review %s: now %s" % (slug, record["status"].upper()))


def cmd_doctor(g, args):
    """Everything that would make a later stage fail, checked up front. Exit status 1 if anything is wrong."""
    import subprocess
    results = []

    def check(name, ok, detail):
        results.append(ok)
        print("  %-4s %-30s %s" % ("ok" if ok else "FAIL", name, detail))

    check("python", sys.version_info >= (3, 9), sys.version.split()[0])
    check("settings", True, sources.PRIVATE)
    for label, path in (("weaknesses.json", sources.WEAKNESS_FILE), ("catalog.json", sources.CATALOG)):
        check(label, os.path.exists(path), path)
    pool = sources.load_jsonl(sources.POOL)
    check("advisory pool", bool(pool), "%d advisories in %s" % (len(pool), sources.POOL) if pool else
          "empty: run `vanguard.py harvest --since 2025-01-01 --pages 3`")
    check("harvester", bool(harvester.WEIGHTS), "%d scoring factors" % len(harvester.WEIGHTS))
    ok = g.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    nodes, _ = g.counts()
    check("graph integrity", ok, "%s, %d nodes" % (os.path.relpath(GRAPH, HERE), sum(nodes.values())))
    check("trained model", os.path.exists(MODEL), "data/model.json" if os.path.exists(MODEL) else "run `vanguard.py train`")
    for p in validate_mod.preflight() or []:
        check("harbor/docker", False, p)
    if not validate_mod.preflight():
        out = subprocess.run(["docker", "info", "--format", "{{.ContainersRunning}}"], capture_output=True, text=True).stdout.strip()
        check("harbor/docker", True, "Harbor found, Docker up, alpine cached, %s container(s) running" % out)
    # a separate process: the tests use made-up models, and this process has already loaded the private ones
    env = {k: v for k, v in os.environ.items() if not k.startswith("VANGUARD_")}
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", os.path.join(HERE, "tests")],
                       cwd=HERE, env=env, capture_output=True, text=True, timeout=600)
    summary = (p.stderr.strip().splitlines() or ["no output"])
    ran = next((l for l in summary if l.startswith("Ran ")), "?")
    check("test suite", p.returncode == 0, "%s: %s" % (ran, summary[-1]))
    print("doctor: %s" % ("all good" if all(results) else "%d problem(s)" % results.count(False)))
    if not all(results):
        sys.exit(1)


def cmd_report(g, args):
    nodes, edges = g.counts()
    groups, stats = ingest_mod.valid_trials(g)
    lines = ["# Vanguard report", "", "Generated %s." % datetime.datetime.now().isoformat(timespec="minutes"), "",
             "## Graph", "", "Nodes: " + ", ".join("%s %d" % kv for kv in sorted(nodes.items())),
             "", "Edges: " + ", ".join("%s %d" % kv for kv in sorted(edges.items())), "",
             "Model trials counted: %(valid)d valid. Excluded: %(crash)d crashes, %(timeout)d timeouts, "
             "%(stale)d run on an older version of their task." % stats, "",
             "## What defeats which model", "",
             "Weaknesses and signals with recorded outcomes. Tasks whose report or tests were given a deliberate hint "
             "are left out of these rates.", "",
             "| Weakness or signal | Model | Tasks failed / tasks | Runs failed / runs |", "|---|---|---|---|"]
    for e in sorted(g.edges(rel="DEFEATS"), key=lambda e: (e["src"].split(":")[0], -e.get("runs", 0))):
        if e["src"].startswith(("weakness:", "finder:", "mitigation:")):
            lines.append("| %s | %s | %d / %d | %d / %d |" % (e["src"], sources.label(e["dst"]), e["tasks_failed"],
                                                           e["tasks"], e["fails"], e["runs"]))
    lines += ["", "## Task outcomes", "", "| Task | Model | Failed / valid runs | Mean score |", "|---|---|---|---|"]
    for (task, model), ts in sorted(groups.items()):
        fails = sum(1 for t in ts if t["reward"] < 1.0)
        lines.append("| %s | %s | %d / %d | %.2f |" % (task.split(":", 1)[1], sources.label(model), fails, len(ts),
                                                      sum(t["reward"] for t in ts) / len(ts)))
    hist = g.metrics("train")
    lines += ["", "## Predictor accuracy over time", "",
              "Leave-one-repo-out. Brier score: 0 is perfect, 0.25 is a coin flip. It should fall as tasks are added.", "",
              "| Trained | Labelled tasks | Base rate | Hand weights | Fitted |", "|---|---|---|---|---|"]
    fmt = lambda v: "n/a" if v is None else "%.4f" % v
    for h in hist:
        lines.append("| %s | %d | %s | %s | %s |" % (h["version"], h["labelled_tasks"], fmt(h["base_rate_brier"]),
                                                   fmt(h["prior_only_brier"]), fmt(h["fitted_brier"])))
    vals = [(t["label"], t["validation"]) for t in g.nodes("Task") if t.get("validation")]
    lines += ["", "## Validation", ""]
    if vals:
        lines += ["| Task | Result | Failed checks | Validated |", "|---|---|---|---|"]
        lines += ["| %s | %s | %s | %s |" % (s, v["status"], ", ".join(c["id"] for c in v["checks"] if c["status"] == "fail") or "-",
                                            v["at"]) for s, v in sorted(vals)]
    else:
        lines.append("No task validated with `vanguard.py validate` yet.")
    fc = g.metrics("forecast")
    lines += ["", "## Forecasts checked against real runs", "",
              "Predictions made **before** a task was built and run, compared with what happened. This is the "
              "real test of the predictor; leave-one-repo-out above is an estimate of it.", ""]
    if fc:
        latest = {}
        for f in fc:  # keep the most recent check per task/model (more runs = more reliable)
            latest[(f["task"], f["model"])] = f
        lines += ["Mean Brier over %d task/model forecasts: %.4f" % (
            len(latest), sum(f["brier"] for f in latest.values()) / len(latest)), "",
            "| Task | Model | Predicted P(fail) | Actual failed / runs | Model version |", "|---|---|---|---|---|"]
        lines += ["| %s | %s | %.2f | %d / %d | %s |" % (f["task"], f["model"], f["p_fail"], f["fails"], f["runs"],
                                                       f["version"]) for f in latest.values()]
    else:
        lines.append("None yet: no task built from a ranked candidate has been run.")
    lines += ["", "## Predictions that now have results", ""]
    resolved = []
    for e in g.edges(rel="PREDICTED"):
        for b in g.edges(dst=e["src"], rel="BUILT_FROM"):
            for d in g.edges(src=b["src"], rel="DEFEATS", dst=e["dst"]):
                resolved.append("| %s | %s | %.2f | %d / %d failed | %s |" % (
                    e["src"].split(":", 1)[1], sources.label(e["dst"]), e["p_fail"], d["fails"], d["runs"], e["version"]))
    lines += (["| Candidate | Model | Predicted P(fail) | Actual | Model version |", "|---|---|---|---|---|"] + resolved) \
        if resolved else ["None yet: ranked candidates have not been built and run."]
    path = os.path.join(DATA, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:14]))
    print("... full report: %s" % os.path.relpath(path, HERE))


def cmd_export(g, args):
    path = args.path or os.path.join(DATA, "graph_export.json")
    n, e = g.export_json(path)
    print("export: %d nodes, %d edges -> %s" % (n, e, path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    hv = sub.add_parser("harvest")
    hv.add_argument("--ecosystems", nargs="+", default=["pip", "npm", "go", "maven", "rubygems", "composer"])
    hv.add_argument("--since", default="2025-01-01", help="published on or after (YYYY-MM-DD)")
    hv.add_argument("--pages", type=int, default=3, help="pages of 100 per ecosystem (set GITHUB_TOKEN for more)")
    hv.add_argument("--fetch", action="store_true", help="also download each fix patch")
    sub.add_parser("ingest")
    sub.add_parser("train")
    r = sub.add_parser("rank")
    r.add_argument("--pool", help="advisory pool JSONL (default: data/pool.jsonl from `harvest`, else an existing finder's pool)")
    r.add_argument("--top", type=int, default=25)
    r.add_argument("--fetch", action="store_true", help="download fix patches that are not cached yet")
    c = sub.add_parser("collect")
    c.add_argument("--no-train", action="store_true", help="record results but do not retrain")
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=int, default=120, help="seconds between checks (default 120)")
    w.add_argument("--no-train", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("tasks", nargs="+", help="task folder path or slug")
    v.add_argument("--static-only", action="store_true", help="only the instant checks, no Docker")
    v.add_argument("--auto-split", action="store_true",
                   help="derive check-only/producer-only patches from fix.patch with the finder's guess of file roles")
    v.add_argument("--parallel", type=int, default=3, help="Harbor jobs at once (default 3; the Docker VM is shared)")
    v.add_argument("--force", action="store_true", help="re-validate a task that already has model runs")
    sub.add_parser("doctor")
    lp = sub.add_parser("loop")
    lp.add_argument("action", nargs="?", default="tick", choices=["tick", "status"])
    lp.add_argument("--select", type=int, default=0, help="add up to N new candidates from the latest rank (free)")
    lp.add_argument("--build", action="store_true", help="run the builder agent from config.json (PAID)")
    lp.add_argument("--max-builds", type=int, help="most builder runs this tick (required with --build)")
    lp.add_argument("--run-models", action="store_true", help="start model rounds on ready tasks (PAID)")
    lp.add_argument("--max-model-runs", type=int, help="most model attempts this tick (required with --run-models)")
    ap_ = sub.add_parser("approve")
    ap_.add_argument("ghsa")
    ap_.add_argument("--more-runs", action="store_true", help="gate 2: allow attempts 2-3 after a failed round 1")
    rj = sub.add_parser("reject")
    rj.add_argument("ghsa")
    rj.add_argument("--reason", required=True)
    rv = sub.add_parser("review")
    rv.add_argument("task")
    rv.add_argument("--set", action="append", metavar="CHECK=STATE", help="pass, fail or na; repeatable")
    rv.add_argument("--note", default="", help="why: kept with the decision")
    sub.add_parser("report")
    x = sub.add_parser("export")
    x.add_argument("path", nargs="?")
    args = ap.parse_args()
    g = Graph(GRAPH)
    try:
        {"harvest": cmd_harvest, "ingest": cmd_ingest, "train": cmd_train, "rank": cmd_rank, "collect": cmd_collect, "watch": cmd_watch, "validate": cmd_validate, "review": cmd_review, "doctor": cmd_doctor,
         "loop": cmd_loop, "approve": cmd_approve, "reject": cmd_reject,
         "report": cmd_report, "export": cmd_export}[args.cmd](g, args)
    except sources.ConfigError as e:  # missing or broken settings: a clear message, not a traceback
        sys.exit("vanguard: %s" % e)
    finally:
        g.close()


if __name__ == "__main__":
    main()
