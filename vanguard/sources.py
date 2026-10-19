"""Readers for the data the earlier pipeline already produced. Read-only: nothing here writes outside vanguard/.

Reuses the existing analysis code instead of copying it:
  <finder>/finder.py             -> size_decision, signature, WEIGHTS (the producer/check shape)
  <pipeline>/01_mine/miner_v2.py -> analyse_patch, weakness_factors (detect rules in weaknesses.json)

Everything that names a real model or a local folder comes from private/ (templates in private.example/):
  private/paths.json      where the pipeline and finder folders are
  private/models.json     which real model is Model A, Model B, ... and how Harbor runs it
  private/task_tags.json  which local task tests which weakness
Both private/ and the generated data/ may sit in a vanguard_local/ folder next to the repository instead (see
_pick below). VANGUARD_MODELS points at another models file (the tests use one).
"""
import glob
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Private settings and generated data can live outside the repository, so an upload can never include them:
# first an explicit VANGUARD_PRIVATE / VANGUARD_DATA, then private/ and data/ inside the repo, then a folder named
# vanguard_local/ next to the repository (VANGUARD_LOCAL moves it).
LOCAL = os.environ.get("VANGUARD_LOCAL") or os.path.normpath(os.path.join(HERE, "..", "..", "vanguard_local"))


def _pick(env, name):
    if os.environ.get(env):
        return os.environ[env]
    inside, outside = os.path.join(HERE, name), os.path.join(LOCAL, name)
    return inside if os.path.isdir(inside) or not os.path.isdir(outside) else outside


PRIVATE = _pick("VANGUARD_PRIVATE", "private")


def _private(name, env=None):
    path = os.environ.get(env) if env and os.environ.get(env) else os.path.join(PRIVATE, name)
    if not os.path.exists(path):
        sys.exit("vanguard: missing %s. Copy private.example/%s to private/%s and fill it in." % (path, name, name))
    with open(path, encoding="utf-8") as f:
        return json.load(f), os.path.dirname(os.path.abspath(path))


_paths, _pbase = _private("paths.json")
_resolve = lambda p: os.path.normpath(os.path.join(_pbase, p))
PIPELINE = _resolve(_paths["pipeline"])
FINDER_DIR = _resolve(_paths["finder"])
ROOT = os.path.commonpath([PIPELINE, FINDER_DIR, HERE])
TASK_NAMESPACE = _paths.get("task_namespace", "tasks")  # Harbor task names are "<namespace>/<slug>"

_models, _mbase = _private("models.json", "VANGUARD_MODELS")
MODELS = _models["models"]                       # ordered; the first is the reference model
MODEL_IDS = [m["id"] for m in MODELS]
BASE_MODEL = MODEL_IDS[0]
AGENT_TIMEOUT_MULTIPLIER = float(_models.get("agent_timeout_multiplier", 1000.0))
for _m in MODELS:
    if _m.get("env_file"):
        _m["env_file"] = os.path.normpath(os.path.join(_mbase, _m["env_file"]))


def label(model_id):
    """'MODEL_A' or 'model:MODEL_A' -> 'Model A'."""
    mid = (model_id or "").split(":")[-1]
    return next((m["label"] for m in MODELS if m["id"] == mid), mid)


def _extra(var):  # extra folders, os.pathsep-separated, e.g. where the loop builds tasks or writes Harbor jobs
    return [p for p in os.environ.get(var, "").split(os.pathsep) if p]


DATA = _pick("VANGUARD_DATA", "data")
VANGUARD_JOBS = os.path.join(DATA, "harbor_jobs")  # where vanguard's own Harbor runs (validation, the loop) go
LOOP_TASKS = os.path.join(DATA, "loop_tasks")  # tasks built by the loop's agents
TASK_DIRS = [os.path.join(PIPELINE, "03_build", "tasks"), LOOP_TASKS] + _extra("VANGUARD_TASK_DIRS")
JOB_DIRS = [os.path.join(PIPELINE, "data", "harbor_jobs")] + [_resolve(p) for p in _paths.get("extra_job_dirs", [])] + \
    [VANGUARD_JOBS] + _extra("VANGUARD_JOB_DIRS")
PATCH_CACHES = [os.path.join(FINDER_DIR, "output", "patch_cache"), os.path.join(PIPELINE, "data", "mined", "patch_cache")]
POOL = os.path.join(FINDER_DIR, "output", "pool.jsonl")
WEAKNESS_FILE = os.path.join(PIPELINE, "knowledge_base", "weaknesses", "weaknesses.json")
KNOWN_CASES = os.path.join(FINDER_DIR, "examples", "known_cases.json")
KB_DB = os.path.join(PIPELINE, "knowledge_base", "kb.sqlite")
CATALOG = os.path.join(HERE, "catalog.json")
TASK_TAGS = os.path.join(PRIVATE, "task_tags.json")

ANALYSIS_LANG = {"typescript": "javascript"}  # the finder and miner treat .ts files under "javascript"


def model_id(name):
    """A Harbor model name or an old label from earlier data -> the neutral id (MODEL_A, ...)."""
    if not name:
        return None
    low = name.lower()
    for m in MODELS:
        if low == m.get("harbor_model", "").lower() or name in m.get("aliases", []) or name == m["id"] or \
                any(k in low for k in m.get("match", [])):
            return m["id"]
    return name.split("/")[-1].upper()


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# ---------------------------------------------------------------- reused analysis modules
_modules = {}


def _load(name, path):
    if name not in _modules:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _modules[name] = mod
    return _modules[name]


def finder():
    return _load("vg_finder", os.path.join(FINDER_DIR, "finder.py"))


def miner():
    return _load("vg_miner_v2", os.path.join(PIPELINE, "01_mine", "miner_v2.py"))


def patch_for(repo, sha, fetch=False):
    """Fix patch from the existing caches; with fetch=True download it through finder.fetch_patch (adds to its cache)."""
    if not sha:
        return None
    for cache in PATCH_CACHES:
        hits = glob.glob(os.path.join(cache, sha[:40] + "*.patch")) if len(sha) >= 7 else []
        if hits:
            with open(hits[0], encoding="utf-8", errors="replace") as f:
                return f.read()
    if fetch and repo:
        return finder().fetch_patch(repo, sha)
    return None


def analyse(cand, text, weaknesses, repo_advisories=1):
    """Signals for one advisory's fix patch: [(signal_id, hint_weight, reason)], plus the finder signature and any reject.

    cand needs: language, summary (and optionally description, cwes)."""
    lang = ANALYSIS_LANG.get(cand.get("language"), cand.get("language"))
    c = dict(cand, language=lang)
    out = {"signals": [], "finder": None, "reject": None, "shape": None}
    out["reject"] = finder().size_decision(text, lang)
    if not out["reject"]:
        sig, why = finder().signature(c, text)
        if sig:
            out["finder"] = {"points": sig["points"], "features": sig["features"]}
            names = {f[0] for f in sig["factors"]}
            out["signals"] += [("finder:" + n, w, r) for n, w, r in sig["factors"]]
            if "check_calls_producer" not in names and names & {"storage_boundary", "producer_drops_field"} \
                    and "field_propagation" in names:
                out["signals"].append(("weakness:producer-boundary", None, "finder: producer drops a shared security field across a boundary, check does not call it"))
        else:
            out["reject"] = why
    shape = miner().analyse_patch(text, lang)
    out["shape"] = {k: v for k, v in shape.items() if k != "src_paths"}
    for name, w, reason in miner().weakness_factors(c, shape, repo_advisories, weaknesses):
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
    for base in TASK_DIRS:
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


def read_trial(result_path):
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


def all_trials():
    seen = set()
    for base in JOB_DIRS:
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
    if os.path.exists(KB_DB):
        import sqlite3
        db = sqlite3.connect("file:%s?mode=ro" % KB_DB, uri=True)
        for g, repo, lang, cwe, summary, sha in db.execute(
                "SELECT ghsa_id, repo, language, cwe, summary, fix_commit FROM candidates"):
            out[g] = {"repo": repo, "language": lang, "cwes": [c for c in (cwe or "").split(",") if c],
                      "summary": summary or "", "fix_commit": sha}
        db.close()
    for row in load_jsonl(POOL):
        fix = (row.get("fix_commits") or [{}])[0]
        out[row["ghsa_id"]] = dict(out.get(row["ghsa_id"], {}), repo=row.get("repo"), language=row.get("language"),
                                   cwes=row.get("cwes") or [], summary=row.get("summary") or "",
                                   description=row.get("description") or "", fix_commit=fix.get("sha"),
                                   published_at=row.get("published_at"))
    return out
