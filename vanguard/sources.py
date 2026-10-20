"""Readers for the project's data, and the settings that say where it lives. Nothing here writes outside the data
folder, and importing this module never reads settings or exits: settings load on first use.

Everything that names a real model or a local folder comes from private/ (templates in private.example/):
  private/paths.json      optional folders of an existing pipeline (task folders, Harbor jobs, caches)
  private/models.json     which real model is Model A, Model B, ... and how Harbor runs it
  private/task_tags.json  which local task tests which weakness (optional)
Both private/ and the generated data/ may sit in a vanguard_local/ folder next to the repository instead (see
_pick below). VANGUARD_MODELS points at another models file (the tests use one).

Patch analysis is harvester.py, in this repository.
"""
import glob
import json
import os
import re
from typing import Any, Dict, Iterator, List, Optional

from schemas import Analysis, Trial

import harvester

HERE = os.path.dirname(os.path.abspath(__file__))
# Private settings and generated data can live outside the repository, so an upload can never include them:
# first an explicit VANGUARD_PRIVATE / VANGUARD_DATA, then private/ and data/ inside the repo, then a folder named
# vanguard_local/ next to the repository (VANGUARD_LOCAL moves it).
LOCAL = os.environ.get("VANGUARD_LOCAL") or os.path.normpath(os.path.join(HERE, "..", "..", "vanguard_local"))


class ConfigError(Exception):
    """Settings are missing or unusable. The command line prints the message; library callers can catch it."""


def _pick(env, name):
    if os.environ.get(env):
        return os.environ[env]
    inside, outside = os.path.join(HERE, name), os.path.join(LOCAL, name)
    return inside if os.path.isdir(inside) or not os.path.isdir(outside) else outside


PRIVATE = _pick("VANGUARD_PRIVATE", "private")
DATA = _pick("VANGUARD_DATA", "data")
VANGUARD_JOBS = os.path.join(DATA, "harbor_jobs")  # where vanguard's own Harbor runs (validation, the loop) go
LOOP_TASKS = os.path.join(DATA, "loop_tasks")  # tasks built by the loop's agents
CATALOG = os.path.join(HERE, "catalog.json")
TASK_TAGS = os.path.join(PRIVATE, "task_tags.json")
REPO_WEAKNESSES = os.path.normpath(os.path.join(HERE, "..", "findings", "weaknesses", "weaknesses.json"))
ANALYSIS_LANG = {"typescript": "javascript"}  # the harvester treats .ts files under "javascript"


def _private(name, env=None):
    path = os.environ.get(env) if env and os.environ.get(env) else os.path.join(PRIVATE, name)
    if not os.path.exists(path):
        raise ConfigError("missing %s. Copy private.example/%s to private/%s and fill it in (or run "
                          "`python3 main.py --demo`, which needs no settings)." % (path, name, name))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), os.path.dirname(os.path.abspath(path))
    except ValueError as e:
        raise ConfigError("%s is not valid JSON: %s" % (path, e)) from e


def _extra(var):  # extra folders, os.pathsep-separated, e.g. where the loop builds tasks or writes Harbor jobs
    return [p for p in os.environ.get(var, "").split(os.pathsep) if p]


_SETTINGS = None


def settings() -> Dict[str, Any]:
    """All settings-dependent values, loaded once on first use."""
    global _SETTINGS
    if _SETTINGS is not None:
        return _SETTINGS
    paths, pbase = _private("paths.json")
    resolve = lambda p: os.path.normpath(os.path.join(pbase, p)) if p else None
    pipeline, finder_dir = resolve(paths.get("pipeline")), resolve(paths.get("finder"))
    models_doc, mbase = _private("models.json", "VANGUARD_MODELS")
    models = models_doc.get("models") or []
    if not models:
        raise ConfigError("models.json lists no models")
    for m in models:
        if m.get("env_file"):
            m["env_file"] = os.path.normpath(os.path.join(mbase, m["env_file"]))
    under = lambda base, *parts: os.path.join(base, *parts) if base else None
    own_pool, finder_pool = os.path.join(DATA, "pool.jsonl"), under(finder_dir, "output", "pool.jsonl")
    pipeline_weaknesses = under(pipeline, "knowledge_base", "weaknesses", "weaknesses.json")
    _SETTINGS = {
        "PIPELINE": pipeline, "FINDER_DIR": finder_dir,
        "ROOT": os.path.commonpath([p for p in (pipeline, finder_dir, HERE) if p]),
        "TASK_NAMESPACE": paths.get("task_namespace", "tasks"),  # Harbor task names are "<namespace>/<slug>"
        "MODELS": models, "MODEL_IDS": [m["id"] for m in models], "BASE_MODEL": models[0]["id"],
        "AGENT_TIMEOUT_MULTIPLIER": float(models_doc.get("agent_timeout_multiplier", 1000.0)),
        "TASK_DIRS": [p for p in [under(pipeline, "03_build", "tasks"), LOOP_TASKS] if p] + _extra("VANGUARD_TASK_DIRS"),
        "JOB_DIRS": [p for p in [under(pipeline, "data", "harbor_jobs")] if p] +
                    [resolve(p) for p in paths.get("extra_job_dirs", [])] + [VANGUARD_JOBS] + _extra("VANGUARD_JOB_DIRS"),
        "PATCH_CACHES": [p for p in [os.path.join(DATA, "patch_cache"), under(finder_dir, "output", "patch_cache"),
                                     under(pipeline, "data", "mined", "patch_cache")] if p],
        # the pool `vanguard.py harvest` writes, else an existing finder's pool
        "POOL": own_pool if os.path.exists(own_pool) or not (finder_pool and os.path.exists(finder_pool)) else finder_pool,
        "WEAKNESS_FILE": pipeline_weaknesses if pipeline_weaknesses and os.path.exists(pipeline_weaknesses) else REPO_WEAKNESSES,
        "KNOWN_CASES": under(finder_dir, "examples", "known_cases.json") or "",
        "KB_DB": under(pipeline, "knowledge_base", "kb.sqlite") or "",
    }
    return _SETTINGS


def __getattr__(name):  # PEP 562: sources.MODELS, sources.POOL, ... resolve through settings() on first use
    if name.isupper():
        s = settings()
        if name in s:
            return s[name]
    raise AttributeError("module 'sources' has no attribute %r" % name)


def label(model_id):
    """'MODEL_A' or 'model:MODEL_A' -> 'Model A'."""
    mid = (model_id or "").split(":")[-1]
    return next((m["label"] for m in settings()["MODELS"] if m["id"] == mid), mid)


def model_id(name: Optional[str]) -> Optional[str]:
    """A Harbor model name or an old label from earlier data -> the neutral id (MODEL_A, ...)."""
    if not name:
        return None
    low = name.lower()
    for m in settings()["MODELS"]:
        if low == m.get("harbor_model", "").lower() or name in m.get("aliases", []) or name == m["id"] or \
                any(k in low for k in m.get("match", [])):
            return m["id"]
    return name.split("/")[-1].upper()


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# ---------------------------------------------------------------- fix patches and their analysis
def patch_for(repo, sha, fetch=False):
    """Fix patch from the caches; with fetch=True download it from GitHub into data/patch_cache."""
    if not sha:
        return None
    for cache in settings()["PATCH_CACHES"]:
        hits = glob.glob(os.path.join(cache, sha[:40] + "*.patch")) if len(sha) >= 7 else []
        if hits:
            with open(hits[0], encoding="utf-8", errors="replace") as f:
                return f.read()
    if fetch and repo:
        return harvester.fetch_patch(repo, sha, settings()["PATCH_CACHES"][0])
    return None


def analyse(cand: dict, text: str, weaknesses: List[dict], repo_advisories: int = 1) -> Analysis:
    """Signals for one advisory's fix patch: [(signal_id, hint_weight, reason)], plus the signature and any reject.

    cand needs: language, summary (and optionally description, cwes)."""
    lang = ANALYSIS_LANG.get(cand.get("language"), cand.get("language"))
    c = dict(cand, language=lang)
    out = {"signals": [], "finder": None, "reject": None, "shape": None}
    out["reject"] = harvester.size_decision(text, lang)
    if not out["reject"]:
        sig, why = harvester.signature(c, text)
        if sig:
            out["finder"] = {"points": sig["points"], "features": sig["features"]}
            names = {f[0] for f in sig["factors"]}
            out["signals"] += [("finder:" + n, w, r) for n, w, r in sig["factors"]]
            if "check_calls_producer" not in names and names & {"storage_boundary", "producer_drops_field"} \
                    and "field_propagation" in names:
                out["signals"].append(("weakness:producer-boundary", None, "finder: producer drops a shared security field across a boundary, check does not call it"))
        else:
            out["reject"] = why
    shape = harvester.analyse_patch(text, lang)
    out["shape"] = {k: v for k, v in shape.items() if k != "src_paths"}
    for name, w, reason in harvester.weakness_factors(c, shape, repo_advisories, weaknesses):
        out["signals"].append((name, w, reason))
    return out


# ---------------------------------------------------------------- tasks and trials
META_RX = re.compile(r'^\s*([A-Za-z_]+)\s*=\s*"(.*)"\s*$')


def task_metadata(task_dir):
    """The [task] name and [metadata] block of task.toml (Python 3.9 has no tomllib; values there are plain strings)."""
    path = os.path.join(task_dir, "task.toml")
    if not os.path.exists(path):
        return None
    meta, section = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("["):
                section = s.strip("[]")
                continue
            m = META_RX.match(line)
            if m and (section == "metadata" or (section == "task" and m.group(1) == "name")):
                meta[m.group(1)] = m.group(2)
    return meta


def task_dirs():
    for base in settings()["TASK_DIRS"]:
        for d in sorted(glob.glob(os.path.join(base, "*", ""))):
            if os.path.exists(os.path.join(d, "task.toml")):
                yield os.path.basename(d.rstrip("/")), d


def security_counts(details):
    """details.json comes in three shapes; return (passed, total) or (None, None)."""
    if not isinstance(details, dict):
        return None, None
    if "security_total" in details:
        return details.get("security_passed"), details.get("security_total")
    summary = details.get("summary")
    if isinstance(summary, dict) and "security_total" in summary:
        return summary.get("security_passed"), summary.get("security_total")
    sec = details.get("security")
    if isinstance(sec, dict) and "total" in sec:
        return sec.get("passed"), sec.get("total")
    if isinstance(sec, list):
        ok = sum(1 for t in sec if isinstance(t, dict) and (t.get("passed") is True or
                 str(t.get("outcome", t.get("status", ""))).lower() in ("passed", "pass", "ok")))
        return ok, len(sec)
    return None, None


def functional_ok(details):
    """True if every functional (legitimate-use) test passed, False if any failed, None if the file does not say."""
    if not isinstance(details, dict):
        return None
    summary = details.get("summary")
    if isinstance(summary, dict) and "functional_all_pass" in summary:
        return bool(summary["functional_all_pass"])
    if "functional_total" in details:
        return details.get("functional_passed") == details.get("functional_total")
    fn = details.get("functional")
    if isinstance(fn, dict):
        if "failed" in fn:
            return not fn["failed"]
        if "total" in fn:
            return fn.get("passed") == fn.get("total")
    if isinstance(fn, list):
        return all(t.get("passed") is True or str(t.get("outcome", "")).lower() in ("passed", "pass", "ok")
                   for t in fn if isinstance(t, dict))
    return None


def read_trial(result_path: str) -> Trial:
    with open(result_path, encoding="utf-8") as f:
        r = json.load(f)
    trial_dir = os.path.dirname(result_path)
    cfg = r.get("config") or {}
    agent = cfg.get("agent") or {}
    rewards = (r.get("verifier_result") or {}).get("rewards") or {}
    exc = r.get("exception_info") or {}
    details = load_json(os.path.join(trial_dir, "verifier", "details.json"))
    passed, total = security_counts(details)
    task_path = (cfg.get("task") or {}).get("path") or (r.get("task_id") or {}).get("path") or ""
    reward = rewards.get("reward")
    etype = exc.get("exception_type")
    if reward is None and not etype and not r.get("finished_at"):
        status = "running"  # still in progress: neither a result nor a crash yet
    elif reward is None:
        status = "crash"  # no score: infrastructure, re-run (HANDOVER gotcha 5)
    elif etype and "Timeout" in etype:
        status = "timeout"  # earlier runs had a 60-min limit; a timeout is not a model failure
    else:
        status = "valid"
    return {
        "id": r.get("id") or os.path.basename(trial_dir), "job": os.path.basename(os.path.dirname(trial_dir)),
        "trial": os.path.basename(trial_dir), "task": os.path.basename(task_path.rstrip("/")),
        "agent": agent.get("name"), "model": model_id(agent.get("model_name")),
        "reward": reward, "exception": etype, "status": status,
        "security_passed": passed, "security_total": total, "functional_ok": functional_ok(details),
        "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
        "cost_usd": (r.get("agent_result") or {}).get("cost_usd"), "path": trial_dir,
    }


def all_trials() -> Iterator[Trial]:
    seen = set()
    for base in settings()["JOB_DIRS"]:
        for path in sorted(glob.glob(os.path.join(base, "*", "*", "result.json"))):
            try:
                t = read_trial(path)
            except (ValueError, OSError):
                continue
            if t["id"] in seen:  # the same trial copied into several folders
                continue
            seen.add(t["id"])
            yield t


def advisory_text():
    """GHSA -> {summary, description, cwes, repo} from the finder pool and the old knowledge base."""
    out = {}
    kb_db = settings()["KB_DB"]
    if kb_db and os.path.exists(kb_db):
        import sqlite3
        db = sqlite3.connect("file:%s?mode=ro" % kb_db, uri=True)
        for g, repo, lang, cwe, summary, sha in db.execute(
                "SELECT ghsa_id, repo, language, cwe, summary, fix_commit FROM candidates"):
            out[g] = {"repo": repo, "language": lang, "cwes": [c for c in (cwe or "").split(",") if c],
                      "summary": summary or "", "fix_commit": sha}
        db.close()
    for row in load_jsonl(settings()["POOL"]):
        fix = (row.get("fix_commits") or [{}])[0]
        out[row["ghsa_id"]] = dict(out.get(row["ghsa_id"], {}), repo=row.get("repo"), language=row.get("language"),
                                   cwes=row.get("cwes") or [], summary=row.get("summary") or "",
                                   description=row.get("description") or "", fix_commit=fix.get("sha"),
                                   published_at=row.get("published_at"))
    return out
