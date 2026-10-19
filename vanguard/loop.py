"""The end-to-end loop: rank -> package -> build (agent) -> approve -> validate -> run -> collect -> retrain.

Each call to `tick` moves every candidate in the loop as far as it can go without crossing a gate:

  selected            picked from the latest rank file
  awaiting-build      package written to data/loop/<GHSA>/; waiting for a builder (agent or person)
  building            the builder agent is running                         [paid: needs --build and --max-builds]
  rejected            triage said REJECT (verdict.json)                    [end]
  awaiting-approval   task folder built; waiting for `approve`             [gate 1: a person approves the build]
  approved            next tick validates it (Docker, no model, free)
  awaiting-review     validation needs a reviewer: `review TASK --set ...`
  dropped             validation failed (e.g. a check-only fix closes every attack)  [end]
  ready-to-run        valid task; round 1 needs --run-models                [paid: needs --run-models and --max-model-runs]
  round1-running      one attempt per model, every model always (the project's run rule)
  solved              a model solved it, so pass@3 = 0 is impossible        [end]
  awaiting-more-runs  both failed round 1; waiting for `approve --more-runs`   [gate 2]
  round2-approved     next tick (with --run-models) runs 2 more attempts per model
  round2-running
  accepted            both models failed every attempt: a finished task     [end]

Nothing paid ever runs without its flag and an explicit budget. Crashed trials are infrastructure: a round is retried
(at most twice) instead of being counted. State lives on the Candidate node (`loop` prop), so a tick can be interrupted
and the next one resumes.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

import sources
import validate as validate_mod

LOOP_DIR = os.path.join(sources.DATA, "loop")            # one package folder per candidate
LOOP_TASKS = sources.LOOP_TASKS                         # where builders put finished task folders
CONFIG = os.environ.get("VANGUARD_CONFIG") or os.path.join(sources.HERE, "config.json")
LOCK = os.path.join(sources.DATA, "loop.lock")
PROMPTS = os.path.join(sources.FINDER_DIR, "prompts")
MODELS = tuple(sources.MODEL_IDS)
END = {"rejected", "dropped", "solved", "accepted"}
MAX_RETRIES = 2


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_config():
    cfg = {"builder_cmd": None, "builder_timeout_min": 120, "max_active": 3}
    cfg.update(sources.load_json(CONFIG, {}) or {})
    return cfg


# ---------------------------------------------------------------- state
def state_of(g, ghsa):
    return (g.get("cand:" + ghsa) or {}).get("loop")


def set_state(g, ghsa, state, log, **extra):
    st = state_of(g, ghsa) or {"ghsa": ghsa, "history": []}
    st.update(extra)
    if st.get("state") != state:
        st["history"] = st.get("history", []) + [{"at": now(), "state": state, "note": extra.get("note")}]
        log("  %-20s -> %-18s %s" % (ghsa, state, extra.get("note") or ""))
    st["state"] = state
    g.node("cand:" + ghsa, "Candidate", None, loop=st)
    g.db.commit()
    return st


def active(g):
    return [c["loop"] for c in g.nodes("Candidate") if c.get("loop") and c["loop"]["state"] not in END]


def all_loop(g):
    return sorted((c["loop"] for c in g.nodes("Candidate") if c.get("loop")), key=lambda s: s["history"][0]["at"])


# ---------------------------------------------------------------- lock
class Lock:
    def __enter__(self):
        os.makedirs(sources.DATA, exist_ok=True)
        if os.path.exists(LOCK):
            try:
                pid = int(open(LOCK).read().strip() or 0)
                os.kill(pid, 0)
                sys.exit("loop: another loop is running (pid %d); remove %s only if it is not" % (pid, LOCK))
            except (ValueError, ProcessLookupError, PermissionError):
                pass  # stale lock from a crashed run
        with open(LOCK, "w") as f:
            f.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            os.remove(LOCK)
        except OSError:
            pass


# ---------------------------------------------------------------- selection and packages
def latest_rank():
    files = sorted(f for f in os.listdir(sources.DATA) if f.startswith("rank_") and f.endswith(".jsonl")) \
        if os.path.isdir(sources.DATA) else []
    return os.path.join(sources.DATA, files[-1]) if files else None


def select(g, n, log):
    path = latest_rank()
    if not path:
        log("  no rank file: run `vanguard.py rank` first")
        return []
    taken = {c["label"] for c in g.nodes("Candidate") if c.get("loop")}
    picked, seen_fix = [], set()
    for row in sources.load_jsonl(path):  # already sorted best first
        if len(picked) >= n:
            break
        if row["ghsa_id"] in taken or row.get("already_built") or row.get("fix_commit") in seen_fix:
            continue
        if row.get("reject") and ("no source change" in row["reject"] or "oversized" in row["reject"]):
            continue
        seen_fix.add(row.get("fix_commit"))
        picked.append(row)
        for m in MODELS:  # freeze the forecast at selection, so every loop task can be scored against it later
            g.record_prediction("cand:" + row["ghsa_id"], "model:" + m, row["p_fail"][m],
                                (row.get("model_version") or "unknown") + "@select")
        set_state(g, row["ghsa_id"], "selected", log, slug=slug_for(row), rank_file=os.path.basename(path),
                  p_fail=row["p_fail"], repo=row["repo"], fix_commit=row["fix_commit"],
                  note="P(fail) " + ", ".join("%s %.2f" % (sources.label(m), row["p_fail"][m]) for m in MODELS))
    return picked


def slug_for(row):
    name = (row.get("repo") or "repo").split("/")[-1].lower()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:28]
    return "%s-%s" % (name, row["ghsa_id"].lower().replace("ghsa-", "")[:9].strip("-"))


def write_package(g, st, row):
    d = os.path.join(LOOP_DIR, st["ghsa"])
    os.makedirs(d, exist_ok=True)
    for name in ("triage_prompt.md", "build_prompt.md", "TASK_BUILD_SPEC.md"):
        src = os.path.join(PROMPTS, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(d, name))
    shutil.copy(os.path.join(sources.FINDER_DIR, "VALIDATION.md"), os.path.join(d, "VALIDATION.md"))
    task_dir = os.path.join(LOOP_TASKS, st["slug"])
    cand = g.get("cand:" + st["ghsa"]) or {}
    feats = cand.get("finder_features") or {}
    if not feats:  # ranked before features were stored: analyse the cached patch now
        patch = sources.patch_for(st["repo"], st["fix_commit"])
        if patch:
            res = sources.analyse({"language": cand.get("language") or row.get("language"),
                                   "summary": cand.get("summary") or ""}, patch, [])
            feats = (res.get("finder") or {}).get("features") or {}
    brief = [
        "# Candidate %s" % st["ghsa"], "",
        "- Repository: https://github.com/%s" % st["repo"],
        "- Fix commit: https://github.com/%s/commit/%s" % (st["repo"], st["fix_commit"]),
        "- Advisory: https://github.com/advisories/%s" % st["ghsa"],
        "- Summary: %s" % (cand.get("summary") or row.get("summary") or ""),
        "- Predicted chance of failing one attempt: %s (vanguard %s)" % (
            ", ".join("%s %.2f" % (sources.label(m), st["p_fail"][m]) for m in MODELS), row.get("model_version")),
        "- Why: " + "; ".join("%s (%+.1f)" % (f, c) for f, c in row.get("why", [])),
        ("- Finder's guess: producer `%s`, check `%s`, shared field(s) %s; other producer candidates: %s" % (
            feats.get("producer_file"), feats.get("check_file"), ", ".join(feats.get("shared_fields") or []) or "none",
            ", ".join(feats.get("producer_candidates") or []) or "none")) if feats else
        "- Finder's guess: no producer/check pair found in the patch",
        "", "These are static guesses from the patch text. Verify every one against the real code.", ""]
    with open(os.path.join(d, "brief.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(brief))
    agent = """# Your job (one candidate, start to finish)

Working folder: this directory. Candidate brief: `brief.md`.

1. **Triage (read-only).** Follow `triage_prompt.md` for this candidate. Save your full answer as `triage.md`.
2. **Write `verdict.json`** in this directory: `{"verdict": "BUILD" | "MAYBE" | "REJECT", "reason": "<one paragraph>"}`.
   Stop here unless the verdict is BUILD.
3. **Build** following `build_prompt.md` and `TASK_BUILD_SPEC.md`, with these two changes:
   - put the task folder at `%(task_dir)s` (not in the pipeline's own tasks folder), task name `%(ns)s/%(slug)s`;
   - also write `solution/variants/check_only.patch` (only the upstream CHECK hunks) and
     `solution/variants/producer_only.patch` (only the PRODUCER hunks). The validator runs both; a check-only fix
     that scores 1.0 means the task is dropped.
4. **Do not run validation and do not run any model.** The loop validates the task itself after a person approves it.
5. **Rules:** never call an LLM API or read API keys; install nothing on the host; Docker safety: never stop, remove or
   prune containers or images you did not create, name scratch containers `%(slug)s-scratch-*` and use `--rm`.
""" % {"task_dir": task_dir, "slug": st["slug"], "ns": sources.TASK_NAMESPACE}
    with open(os.path.join(d, "AGENT_TASK.md"), "w", encoding="utf-8") as f:
        f.write(agent)
    return d, task_dir


# ---------------------------------------------------------------- builder
def run_builder(cfg, pkg, log):
    """Run the configured builder command in the package folder with AGENT_TASK.md on stdin."""
    cmd = cfg["builder_cmd"]
    with open(os.path.join(pkg, "AGENT_TASK.md"), encoding="utf-8") as f, \
            open(os.path.join(pkg, "builder.log"), "w") as out:
        try:
            p = subprocess.run(cmd, stdin=f, stdout=out, stderr=subprocess.STDOUT, cwd=pkg,
                               timeout=cfg["builder_timeout_min"] * 60)
            return p.returncode, None
        except subprocess.TimeoutExpired:
            return None, "builder timed out after %d min" % cfg["builder_timeout_min"]
        except OSError as e:
            return None, "builder could not start: %s" % e


def build_outcome(pkg, task_dir):
    """What the builder left behind: ('rejected', why) | ('built', None) | ('none', why)."""
    verdict = sources.load_json(os.path.join(pkg, "verdict.json"), None)
    if isinstance(verdict, dict) and str(verdict.get("verdict", "")).upper() in ("REJECT", "MAYBE"):
        return "rejected", "%s: %s" % (verdict["verdict"].upper(), (verdict.get("reason") or "")[:200])
    if os.path.exists(os.path.join(task_dir, "task.toml")):
        return "built", None
    return "none", "no verdict.json and no task folder yet"


# ---------------------------------------------------------------- model runs
def model_settings():
    """{model id: {model, env_file, kwargs}} for Harbor, from private/models.json."""
    return {m["id"]: {"model": m["harbor_model"], "env_file": m.get("env_file"), "kwargs": m.get("kwargs", [])}
            for m in sources.MODELS}, sources.AGENT_TIMEOUT_MULTIPLIER


def start_round(st, plan, log):
    """plan: {model: attempts}. Starts one Harbor job per model; returns {model: job info}."""
    models, mult = model_settings()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    jobs = {}
    os.makedirs(sources.VANGUARD_JOBS, exist_ok=True)
    for key, attempts in plan.items():
        m = models[key]
        name = "loop-%s-%s-%s" % (stamp, st["slug"], key.lower().replace("_", "-"))
        cmd = [validate_mod.HARBOR, "run", "-a", "terminus-2", "-m", m["model"], "--env-file", m["env_file"],
               "-k", str(attempts), "-n", str(attempts), "-o", sources.VANGUARD_JOBS, "--job-name", name, "-y", "-q",
               "-p", st["task_dir"], "--agent-timeout-multiplier", str(mult)]
        for kw in m["kwargs"]:
            cmd += ["--ak", kw]
        log_f = open(os.path.join(sources.VANGUARD_JOBS, name + ".log"), "w")
        p = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True)
        jobs[key] = {"job": name, "pid": p.pid, "attempts": attempts}
    return jobs


def jobs_finished(jobs):
    for j in jobs.values():
        try:
            os.kill(j["pid"], 0)
            pid_alive = True
        except (ProcessLookupError, PermissionError):
            pid_alive = False
        if pid_alive:
            try:  # reap our own child if this process started it
                if os.waitpid(j["pid"], os.WNOHANG)[0] == 0:
                    return False
            except ChildProcessError:
                return False
    return True


def round_results(jobs):
    """{model: [reward or None per trial]} from the job folders."""
    out = {}
    for key, j in jobs.items():
        trials = validate_mod.job_trials(j["job"])
        out[key] = [t["reward"] if t["status"] == "valid" else None for t in trials]
        out[key] += [None] * max(0, j["attempts"] - len(trials))
    return out


def env_problems():
    models, _ = model_settings()
    return ["%s env file missing: %s" % (sources.label(k), models[k]["env_file"]) for k in MODELS
            if not models[k]["env_file"] or not os.path.exists(models[k]["env_file"])]


# ---------------------------------------------------------------- one tick
def tick(g, opts, hooks, log=print):
    """opts: select, build, max_builds, run_models, max_model_runs. hooks: collect(g), validate(task_dir) -> record."""
    cfg = load_config()
    budget = {"builds": opts.get("max_builds", 0) if opts.get("build") else 0,
              "model_runs": opts.get("max_model_runs", 0) if opts.get("run_models") else 0}
    room = max(0, cfg["max_active"] - len(active(g)))
    n = min(opts.get("select", 0), room)
    rows = {r["ghsa_id"]: r for r in select(g, n, log)} if n else {}
    rank = {r["ghsa_id"]: r for r in sources.load_jsonl(latest_rank())} if latest_rank() else {}
    for st in active(g):
        st = advance(g, st, rows.get(st["ghsa"]) or rank.get(st["ghsa"], {}), cfg, budget, opts, hooks, log)
    return budget


def advance(g, st, row, cfg, budget, opts, hooks, log):
    ghsa = st["ghsa"]
    for _ in range(12):  # each pass moves at most one stage; stop when nothing changes
        before = st["state"]
        s = st["state"]
        if s == "selected":
            pkg, task_dir = write_package(g, st, row)
            st = set_state(g, ghsa, "awaiting-build", log, package=pkg, task_dir=task_dir,
                           note="package ready: %s" % pkg)
        elif s in ("awaiting-build", "building"):
            outcome, why = build_outcome(st["package"], st["task_dir"])
            if outcome == "rejected":
                st = set_state(g, ghsa, "rejected", log, note=why)
            elif outcome == "built":
                st = set_state(g, ghsa, "awaiting-approval", log,
                               note="built: approve with `vanguard.py approve %s`" % ghsa)
            elif s == "awaiting-build" and budget["builds"] > 0 and cfg["builder_cmd"]:
                budget["builds"] -= 1
                st = set_state(g, ghsa, "building", log, note="builder started")
                code, err = run_builder(cfg, st["package"], log)
                outcome, why = build_outcome(st["package"], st["task_dir"])
                if outcome == "none":
                    st = set_state(g, ghsa, "awaiting-build", log, build_error=err or "builder exited %s without a task "
                                   "or verdict (see builder.log)" % code, note=err or "builder left no result")
                    break
            elif s == "awaiting-build" and opts.get("build") and not cfg["builder_cmd"]:
                log("  %-20s    --build given but config.json has no builder_cmd" % ghsa)
                break
            else:
                break
        elif s == "approved":
            rec = hooks["validate"](st["task_dir"])
            if rec is None:
                log("  %-20s    validation could not run (see above); will retry next tick" % ghsa)
                break
            nxt = {"pass": "ready-to-run", "needs-review": "awaiting-review", "fail": "dropped"}.get(rec["status"])
            if not nxt:  # incomplete: a crash; retry a limited number of times
                tries = st.get("validation_tries", 0) + 1
                st = set_state(g, ghsa, "approved" if tries <= MAX_RETRIES else "dropped", log, validation_tries=tries,
                               note="validation incomplete (try %d)" % tries)
                break
            fails = [c["id"] for c in rec["checks"] if c["status"] == "fail"]
            st = set_state(g, ghsa, nxt, log, note="validation %s%s" % (rec["status"], ": " + ", ".join(fails) if fails else ""))
        elif s == "awaiting-review":
            rec = (g.get("task:" + os.path.basename(st["task_dir"])) or {}).get("validation") or {}
            if rec.get("status") == "pass":
                st = set_state(g, ghsa, "ready-to-run", log, note="review complete")
            elif rec.get("status") == "fail":
                st = set_state(g, ghsa, "dropped", log, note="reviewer failed it")
            else:
                break
        elif s in ("ready-to-run", "round2-approved"):
            attempts = 1 if s == "ready-to-run" else 2
            # after a crash only the missing attempts are re-run; valid results already in hand are kept
            plan = st.get("pending") or {k: attempts for k in MODELS}
            need = sum(plan.values())
            if budget["model_runs"] < need:
                if opts.get("run_models"):
                    log("  %-20s    needs %d model run(s); budget left %d" % (ghsa, need, budget["model_runs"]))
                break
            problems = env_problems() + validate_mod.preflight()
            if problems:
                log("  %-20s    cannot run models: %s" % (ghsa, "; ".join(problems)))
                break
            budget["model_runs"] -= need
            nxt = "round1-running" if s == "ready-to-run" else "round2-running"
            st = set_state(g, ghsa, nxt, log, jobs=start_round(st, plan, log), round_attempts=attempts,
                           note=" + ".join("%s x%d" % (sources.label(k), n) for k, n in plan.items()))
            break  # runs take a while: the next tick collects them
        elif s in ("round1-running", "round2-running"):
            if not jobs_finished(st["jobs"]):
                break
            hooks["collect"](g)
            got = round_results(st["jobs"])
            acc = {k: list(v) for k, v in (st.get("round_acc") or {}).items()}  # valid rewards so far this round
            pending = {}
            for k, rs in got.items():
                acc.setdefault(k, []).extend(r for r in rs if r is not None)
                if any(r is None for r in rs):
                    pending[k] = sum(1 for r in rs if r is None)
            rounds = st.get("rounds", []) + [got]
            if pending:
                retries = st.get("round_retries", 0) + 1
                back = "ready-to-run" if s == "round1-running" else "round2-approved"
                st = set_state(g, ghsa, back if retries <= MAX_RETRIES else "dropped", log, round_retries=retries,
                               rounds=rounds, round_acc=acc, pending=pending, note="crashed trial(s), infrastructure: %s" % (
                                   "re-run only %s (retry %d)" % (", ".join("%s x%d" % (sources.label(k), n) for k, n in pending.items()), retries)
                                   if retries <= MAX_RETRIES else "giving up"))
                continue
            res = acc
            st.update(round_acc=None, pending=None, round_retries=0)
            solved_by = [k for k, rs in res.items() if any(r >= 1.0 for r in rs)]
            summary = ", ".join("%s %s" % (sources.label(k), rs) for k, rs in res.items())
            if solved_by:
                st = set_state(g, ghsa, "solved", log, rounds=rounds, note="solved by %s (%s)" % (
                    " + ".join(sources.label(k) for k in solved_by), summary))
            elif s == "round1-running":
                st = set_state(g, ghsa, "awaiting-more-runs", log, rounds=rounds,
                               note="both failed round 1 (%s): approve 2 more each with `vanguard.py approve %s --more-runs`"
                               % (summary, ghsa))
            else:
                st = set_state(g, ghsa, "accepted", log, rounds=rounds, note="failed by both models on every attempt")
        else:
            break
        if st["state"] == before:
            break
    return st


def approve(g, ghsa, more_runs, log=print):
    st = state_of(g, ghsa)
    if not st:
        sys.exit("approve: %s is not in the loop" % ghsa)
    want = "awaiting-more-runs" if more_runs else "awaiting-approval"
    if st["state"] != want:
        sys.exit("approve: %s is %s; %s approves only from %s" % (
            ghsa, st["state"], "--more-runs" if more_runs else "approve", want))
    set_state(g, ghsa, "round2-approved" if more_runs else "approved", log,
              note="approved by %s" % os.environ.get("USER", "user"))


def reject(g, ghsa, reason, log=print):
    st = state_of(g, ghsa)
    if not st:
        sys.exit("reject: %s is not in the loop" % ghsa)
    if st["state"] in END:
        sys.exit("reject: %s already ended (%s)" % (ghsa, st["state"]))
    set_state(g, ghsa, "rejected", log, note="rejected by %s: %s" % (os.environ.get("USER", "user"), reason))


NEXT_STEP = {
    "selected": "next tick writes the package",
    "awaiting-build": "build it: an agent with `loop --build --max-builds N`, or by hand from the package",
    "building": "builder running",
    "awaiting-approval": "look at the task, then `approve GHSA` (or `reject GHSA --reason`)",
    "approved": "next tick validates it",
    "awaiting-review": "`review TASK` to sign off open items",
    "ready-to-run": "round 1 needs `loop --run-models --max-model-runs N` (paid)",
    "round1-running": "running; next tick collects",
    "awaiting-more-runs": "`approve GHSA --more-runs` for 2 more attempts each",
    "round2-approved": "needs `loop --run-models --max-model-runs N` (paid)",
    "round2-running": "running; next tick collects",
}
