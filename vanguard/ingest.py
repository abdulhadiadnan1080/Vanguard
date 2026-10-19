"""Build the graph from everything the project has produced so far, then recompute the outcome (DEFEATS) edges.

Safe to re-run: every write is an upsert keyed by a stable id (GHSA, task slug, Harbor trial id).

Which trials count (applied in recompute_outcomes):
  * crash (no score) and timeout (old 60-min limit) trials are infrastructure, not model results;
  * a model trial that started before the task's most recent oracle run is stale: re-validating means the task changed
    (e.g. a test-setup fix, or a revised task). This reproduces every result in the original findings.
"""
import collections

import sources

SOLVED = 1.0  # a trial solves the task only with every hidden security test passing


def ingest(g, log=print):
    catalog = sources.load_json(sources.CATALOG, {})
    kb_weaknesses = sources.load_json(sources.WEAKNESS_FILE, []) or []
    detect_weaknesses = [w for w in kb_weaknesses if w.get("status") != "rejected" and w.get("weight")]
    adv = sources.advisory_text()
    pool = sources.load_jsonl(sources.POOL)
    repo_adv = collections.Counter(r.get("repo") for r in pool)

    # models, weaknesses, signals
    for m in sources.MODELS:
        g.node("model:" + m["id"], "Model", m["label"])
    for w in kb_weaknesses:
        g.node("weakness:" + w["id"], "Weakness", w["id"], description=w.get("description"),
               models=[sources.model_id(x) for x in w.get("models") or []],
               status=w.get("status"), weight=w.get("weight"), detect=w.get("detect"), trigger=w.get("trigger"),
               evidence=w.get("evidence"), source="weaknesses.json")
    for w in catalog.get("weaknesses", []):
        g.node("weakness:" + w["id"], "Weakness", w["id"], description=w["label"],
               models=[sources.model_id(x) for x in w.get("models") or []],
               status=w.get("status"), weight=w.get("weight"), detect=w.get("detect"),
               behavioural=w.get("behavioural"), source=w.get("source"))
    for name, (weight, reason) in sources.finder().WEIGHTS.items():
        g.node("finder:" + name, "Signal", name, weight=weight, description=reason, source="finder.py WEIGHTS")
    for m in catalog.get("mitigations", []):
        g.node("mitigation:" + m["id"], "Signal", m["id"], weight=m["weight"], description=m["label"], source=m["source"])

    # candidates: everything the old knowledge base and the finder pool know about
    for ghsa, a in adv.items():
        g.node("cand:" + ghsa, "Candidate", ghsa, repo=a.get("repo"), language=a.get("language"), cwes=a.get("cwes"),
               summary=a.get("summary"), fix_commit=a.get("fix_commit"), published_at=a.get("published_at"))
    for k in sources.load_json(sources.KNOWN_CASES, []) or []:
        g.node("cand:" + k["ghsa_id"], "Candidate", k["ghsa_id"], repo=k.get("repo"), fix_commit=k.get("fix_commit"),
               language=k.get("language"), known_label=k.get("expected"), known_note=k.get("why"))

    # tasks
    tags = (sources.load_json(sources.TASK_TAGS, {}) or {}).get("task_tags", {})
    n_sig = 0
    for slug, path in sources.task_dirs():
        meta = sources.task_metadata(path) or {}
        tag = tags.get(slug, {})
        ghsa = tag.get("candidate") or (meta.get("ghsa") if (meta.get("ghsa") or "").startswith("GHSA-") else None)
        kind = "real" if (meta.get("ghsa") or "").startswith("GHSA-") else "experiment"
        full_fix = kind == "real" or bool(tag.get("full_fix"))
        g.node("task:" + slug, "Task", slug, kind=kind, full_fix=full_fix, ghsa=meta.get("ghsa"),
               language=meta.get("language"), repo=_repo(meta.get("repo")), fix_commit=meta.get("fix_commit"),
               vulnerable_commit=meta.get("vulnerable_commit"), cwe=meta.get("cwe"), path=path, note=tag.get("note"))
        for w in tag.get("weaknesses", []):
            g.edge("task:" + slug, "TARGETS", "weakness:" + w, source="task_tags")
        for m in tag.get("mitigations", []):
            g.edge("task:" + slug, "MITIGATED_BY", "mitigation:" + m, source="task_tags")
        if not full_fix:
            continue
        cand_id = "cand:" + (ghsa or "commit:" + (meta.get("fix_commit") or slug))
        a = adv.get(ghsa or "", {})
        repo = _repo(meta.get("repo")) or a.get("repo")
        g.node(cand_id, "Candidate", ghsa or slug, repo=repo, fix_commit=meta.get("fix_commit"),
               language=meta.get("language"))
        g.edge("task:" + slug, "BUILT_FROM", cand_id)
        n_sig += add_signals(g, cand_id, {"language": meta.get("language"), "summary": a.get("summary", ""),
                                          "description": a.get("description", ""), "cwes": a.get("cwes", [])},
                             sources.patch_for(repo, meta.get("fix_commit")), detect_weaknesses, repo_adv.get(repo, 1))
    for k in sources.load_json(sources.KNOWN_CASES, []) or []:
        a = adv.get(k["ghsa_id"], {})
        n_sig += add_signals(g, "cand:" + k["ghsa_id"], {"language": k.get("language"), "summary": a.get("summary", "")},
                             sources.patch_for(k.get("repo"), k.get("fix_commit")), detect_weaknesses, repo_adv.get(k.get("repo"), 1))

    # trials
    n = collections.Counter()
    for t in sources.all_trials():
        tid = "trial:" + t["id"]
        g.node(tid, "Trial", t["trial"], **{k: v for k, v in t.items() if k not in ("id", "trial")})
        if not g.get("task:" + t["task"]):
            parent = next((s for s, _ in sorted(sources.task_dirs(), key=lambda d: -len(d[0])) if t["task"].startswith(s + "-")), None)
            g.node("task:" + t["task"], "Task", t["task"], kind="probe" if parent else "unknown",
                   note="validation probe of %s (partial or alternative fix)" % parent if parent else "trial found but no task folder")
            if parent:
                g.edge("task:" + t["task"], "PROBES", "task:" + parent)
        if t["agent"] in ("oracle", "nop"):
            g.edge(tid, "VALIDATES", "task:" + t["task"], agent=t["agent"], reward=t["reward"])
        else:
            g.edge(tid, "ON", "task:" + t["task"])
            if t["model"]:
                g.edge(tid, "BY", "model:" + t["model"])
        n[t["agent"] or "?"] += 1
    stats = recompute_outcomes(g)
    log("ingest: %d candidates, %d tasks, %d signal links, trials %s" % (
        len(g.nodes("Candidate")), len(g.nodes("Task")), n_sig, dict(n)))
    log("        model trials counted: %(valid)d valid; excluded %(crash)d crash, %(timeout)d timeout, %(stale)d stale" % stats)
    return stats


def add_signals(g, cand_id, cand, patch, detect_weaknesses, repo_advisories):
    if not patch:
        g.node(cand_id, "Candidate", None, analysis="no cached patch")
        return 0
    res = sources.analyse(cand, patch, detect_weaknesses, repo_advisories)
    g.node(cand_id, "Candidate", None, analysis="ok", reject=res["reject"], shape=res["shape"],
           finder_points=(res["finder"] or {}).get("points"), finder_features=(res["finder"] or {}).get("features"))
    for sid, w, reason in res["signals"]:
        g.edge(cand_id, "HAS_SIGNAL", sid, weight=w, reason=reason)
    return len(res["signals"])


def _repo(url):
    if not url or "github.com/" not in url:
        return url if url and "/" in url and " " not in url else None
    return url.split("github.com/")[1].strip("/").removesuffix(".git")


def valid_trials(g):
    """{(task_id, model_id): [trial, ...]} for model trials that count, plus exclusion stats."""
    trials = {t["id"]: t for t in g.nodes("Trial")}
    latest_oracle = {}
    for e in g.edges(rel="VALIDATES"):
        t = trials.get(e["src"])
        if t and t.get("agent") == "oracle" and t.get("started_at"):
            latest_oracle[e["dst"]] = max(latest_oracle.get(e["dst"], ""), t["started_at"])
    by_model = {e["src"]: e["dst"] for e in g.edges(rel="BY")}
    out, stats = collections.defaultdict(list), collections.Counter(valid=0, crash=0, timeout=0, stale=0)
    for e in g.edges(rel="ON"):
        t, model = trials.get(e["src"]), by_model.get(e["src"])
        if not t or not model:
            continue
        if t["status"] != "valid":
            stats[t["status"]] += 1
            continue
        if (t.get("started_at") or "") < latest_oracle.get(e["dst"], ""):
            stats["stale"] += 1
            continue
        stats["valid"] += 1
        out[(e["dst"], model)].append(t)
    return out, stats


def recompute_outcomes(g):
    g.drop_edges("DEFEATS")
    groups, stats = valid_trials(g)
    mitigated = {e["src"] for e in g.edges(rel="MITIGATED_BY") if g.get(e["dst"]) and (g.get(e["dst"]).get("weight") or 0) < 0}
    agg = collections.defaultdict(lambda: collections.Counter())
    for (task, model), ts in groups.items():
        fails = sum(1 for t in ts if t["reward"] < SOLVED)
        g.edge(task, "DEFEATS", model, fails=fails, runs=len(ts), failed_all=fails == len(ts),
               mean_reward=round(sum(t["reward"] for t in ts) / len(ts), 4))
        if task in mitigated:  # a hint or visible test changes the outcome on purpose; keep it out of weakness rates
            continue
        owners = {e["dst"] for e in g.edges(src=task, rel="TARGETS")}
        for b in g.edges(src=task, rel="BUILT_FROM"):
            owners |= {e["dst"] for e in g.edges(src=b["dst"], rel="HAS_SIGNAL")}
        for o in owners:
            c = agg[(o, model)]
            c["fails"] += fails
            c["runs"] += len(ts)
            c["tasks"] += 1
            c["tasks_failed"] += fails == len(ts)
    for (owner, model), c in agg.items():
        g.edge(owner, "DEFEATS", model, **c)
    return stats
