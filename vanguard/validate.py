"""Stage 5, scripted: check that a built task is fair before any model runs on it (share/.../VALIDATION.md).

Static checks (instant, no Docker): task layout, offline network, report and image free of the advisory, tests not
shipped in the image, a reward written by the verifier.

Harbor checks (Docker, built-in agents only, no model API, no cost beyond local time):
  1  oracle x2          the upstream fix scores 1.0 both times
  2  nop                no fix scores 0.0, with every functional test passing
  3+ fix variants       solution/variants/<name>.patch, judged by the name:
                          check_only*, producer_only*  -> strictly between 0 and 1 (check_only = 1.0 means drop the task)
                          hardened*, alt*              -> 1.0
                        --auto-split derives check_only/producer_only from fix.patch with the finder's file roles.
                        Opt-in only: on a real two-file task it mislabelled the check file, so builder-written patches
                        are the default and a task without them is reported as not fully validated.

Left to a reviewer, and recorded as such: 7 repo tests on both trees, 9 fairness map.

Variant tasks are scratch copies in data/variant_tasks/<slug>-<variant>/; all jobs go to data/harbor_jobs/ with names
val-<stamp>-<slug>-<check>. Containers are Harbor's own; this module never stops or removes any container.
"""
import datetime
import glob
import os
import re
import shutil
import subprocess

import sources

HARBOR = os.environ.get("VANGUARD_HARBOR") or shutil.which("harbor") or os.path.expanduser("~/.local/bin/harbor")
VARIANT_DIR = os.path.join(sources.DATA, "variant_tasks")
PARTIAL, FULL = "partial", "full"


def expectation(name):
    return PARTIAL if name.startswith(("check_only", "producer_only")) else FULL


# ---------------------------------------------------------------- static checks
def static_checks(task_dir):
    meta = sources.task_metadata(task_dir) or {}
    out = []

    def add(cid, name, ok, detail="", warn=False):
        out.append({"id": cid, "name": name, "status": "pass" if ok else ("warn" if warn else "fail"), "detail": detail})

    need = ["task.toml", "instruction.md", "environment/Dockerfile", "solution/solve.sh", "solution/fix.patch", "tests/test.sh"]
    missing = [p for p in need if not os.path.exists(os.path.join(task_dir, p))]
    add("layout", "Task layout", not missing, "missing: " + ", ".join(missing) if missing else "all required files present")
    toml = _read(os.path.join(task_dir, "task.toml"))
    add("offline", "No network in the task container", 'network_mode = "no-network"' in toml,
        'network_mode = "no-network"' if 'network_mode = "no-network"' in toml else 'task.toml lacks network_mode = "no-network"')
    add("metadata", "Advisory metadata", bool(meta.get("fix_commit")),
        "fix_commit %s" % (meta.get("fix_commit") or "missing")[:12])

    ids = _advisory_ids(meta, toml)
    instr = _read(os.path.join(task_dir, "instruction.md"))
    hits = [i for i in ids if i and i.lower() in instr.lower()]
    add("leak_report", "Report does not name the advisory or fix", not hits, "names " + ", ".join(hits) if hits else "clean")
    fix = _read(os.path.join(task_dir, "solution", "fix.patch"))
    fix_files = re.findall(r"^diff --git a/(\S+) b/", fix, re.M)
    named = sorted({f for f in fix_files if f in instr or (len(os.path.basename(f)) > 6 and os.path.basename(f) in instr)})
    add("impact_only", "Report is impact-only (names no fixed file)", not named,
        "names " + ", ".join(named) if named else "clean", warn=True)

    docker = _read(os.path.join(task_dir, "environment", "Dockerfile"))
    final = docker.split("FROM ")[-1]  # last build stage = the image the model sees
    git_kept = ("git clone" in docker and "rm -rf" not in docker and ".git" not in docker) or \
        re.search(r"COPY\s+(\.\s|\S*\.git\b)", final)
    add("leak_git", "No upstream git history in the image", not git_kept,
        "the Dockerfile may keep .git" if git_kept else "history removed or never copied")
    env_tests = [p for p in glob.glob(os.path.join(task_dir, "environment", "**", "*"), recursive=True)
                 if re.search(r"(^|/)(tests?|__tests__)/|fix\.patch$|solve\.sh$", os.path.relpath(p, task_dir))]
    copies_tests = re.search(r"COPY\s+.*\btests?\b", final)
    add("leak_tests", "Hidden tests and the fix are not in the image", not env_tests and not copies_tests,
        "; ".join(filter(None, ["environment/ holds " + ", ".join(os.path.relpath(p, task_dir) for p in env_tests[:3]) if env_tests else "",
                                "Dockerfile copies tests" if copies_tests else ""])) or "clean")
    # only files the final stage copies in reach the model; builder-stage inputs (e.g. revert.patch) do not
    shipped = []
    for m in re.finditer(r"^\s*(?:COPY|ADD)\s+(?!--from)(?:--\S+\s+)*(.+?)\s+\S+\s*$", final, re.M):
        for src in m.group(1).split():
            p = os.path.join(task_dir, "environment", src)
            shipped += [q for q in glob.glob(os.path.join(p, "**", "*"), recursive=True) if os.path.isfile(q)] \
                if os.path.isdir(p) else [p] if os.path.isfile(p) else []
    leaked = sorted({i for i in ids for p in shipped if i.lower() in _read(p).lower()})
    add("leak_env", "Files copied into the image do not name the advisory", not leaked,
        "names " + ", ".join(leaked) if leaked else "clean (%d shipped file(s) checked)" % len(shipped))
    test_sh = _read(os.path.join(task_dir, "tests", "test.sh"))
    add("reward", "Verifier writes a reward", "reward" in test_sh,
        "tests/test.sh writes the reward" if "reward" in test_sh else "tests/test.sh never mentions reward.txt")
    out.append({"id": "repo_tests", "name": "Repo's own tests pass on the vulnerable and fixed tree", "status": "manual",
                "detail": "reviewer: run the repo suite on both trees (VALIDATION.md check 7)"})
    out.append({"id": "fairness", "name": "Every hidden test maps to a sentence of the report", "status": "manual",
                "detail": "reviewer: fairness map (VALIDATION.md check 9)"})
    return out


def _advisory_ids(meta, toml):
    ids = {meta.get("ghsa") or ""}
    ids |= set(re.findall(r"CVE-\d{4}-\d+", toml))
    if meta.get("fix_commit"):
        ids.add(meta["fix_commit"][:12])
    return sorted(i for i in ids if i and not i.startswith("EXP-"))


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ---------------------------------------------------------------- fix variants
def split_patch(text):
    """{file: diff block} for a unified git diff."""
    blocks, cur, out = re.split(r"(?m)^(?=diff --git )", text), None, {}
    for b in blocks:
        m = re.match(r"diff --git a/(\S+) b/", b)
        if m:
            out[m.group(1)] = b
    return out


def auto_split(task_dir):
    """check_only / producer_only patches from fix.patch, using the finder's guess of producer vs check files."""
    meta = sources.task_metadata(task_dir) or {}
    fix = _read(os.path.join(task_dir, "solution", "fix.patch"))
    res = sources.analyse({"language": meta.get("language"), "summary": ""}, fix, [])
    feats = (res.get("finder") or {}).get("features")
    if not feats:
        return [], "the finder found no producer/check pair in fix.patch (%s)" % (res.get("reject") or "no signature")
    blocks = split_patch(fix)
    producers = set(feats["producer_candidates"]) - {feats["check_file"]}
    check_only = "".join(b for f, b in blocks.items() if f not in producers)
    producer_only = "".join(b for f, b in blocks.items() if f in producers)
    out = [(n, t) for n, t in (("check_only_auto", check_only), ("producer_only_auto", producer_only)) if t.strip() and t != fix]
    return out, "producer files guessed: %s; check file: %s" % (", ".join(sorted(producers)), feats["check_file"])


def variants(task_dir, use_auto):
    found = [(os.path.splitext(os.path.basename(p))[0], _read(p))
             for p in sorted(glob.glob(os.path.join(task_dir, "solution", "variants", "*.patch")))]
    note = ""
    if use_auto and not any(n.startswith("check_only") for n, _ in found):
        auto, note = auto_split(task_dir)
        found += auto
    return found, note


def make_variant_task(task_dir, name, patch):
    slug = os.path.basename(os.path.normpath(task_dir))
    dst = os.path.join(VARIANT_DIR, "%s-%s" % (slug, name.replace("_", "-")))
    if os.path.isdir(dst):
        shutil.rmtree(dst)  # our own scratch copy from an earlier validation
    shutil.copytree(task_dir, dst, ignore=shutil.ignore_patterns("variants"))
    with open(os.path.join(dst, "solution", "fix.patch"), "w", encoding="utf-8") as f:
        f.write(patch)
    return dst


# ---------------------------------------------------------------- Harbor
def preflight():
    """Problems that would make every Harbor check fail; empty when ready. Never installs or pulls anything."""
    problems = []
    if os.environ.get("VANGUARD_TEST_NO_DOCKER") == "1":  # the test suite's stand-in Harbor needs no Docker
        return problems
    if not os.path.exists(HARBOR):
        problems.append("Harbor not found (expected ~/.local/bin/harbor)")
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        problems.append("Docker is not running: start Docker Desktop")
        return problems
    # the explicit tag matters: a bare "alpine" stops resolving once Docker also holds an untagged alpine@sha256 copy
    if subprocess.run(["docker", "image", "inspect", "alpine:latest"], capture_output=True).returncode != 0:
        problems.append("the alpine image is not cached (Harbor's offline check needs it): run `docker pull alpine` once")
    return problems


def start(agent, task_path, name, attempts):
    os.makedirs(sources.VANGUARD_JOBS, exist_ok=True)
    cmd = [HARBOR, "run", "-a", agent, "-p", task_path, "-o", sources.VANGUARD_JOBS, "--job-name", name,
           "-k", str(attempts), "-n", str(attempts), "-y", "-q"]
    log = open(os.path.join(sources.VANGUARD_JOBS, name + ".log"), "w")
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)


def job_trials(name):
    return [sources.read_trial(p) for p in glob.glob(os.path.join(sources.VANGUARD_JOBS, name, "*", "result.json"))]


def judge(check, trials):
    if not trials:  # Harbor itself failed (Docker hiccup, image build error): not evidence about the task
        return "infra", "Harbor produced no trial results: see data/harbor_jobs/%s.log, then re-run" % check.get("job", "<job>")
    scored = [t for t in trials if t["reward"] is not None]
    crashed = len(trials) - len(scored)
    rewards = [round(t["reward"], 4) for t in scored]
    if check["kind"] == "oracle":
        ok = len(scored) == check["attempts"] and all(r >= 1.0 for r in rewards)
        why = "rewards %s (need 1.0 x%d)" % (rewards, check["attempts"])
    elif check["kind"] == "nop":
        func = [t.get("functional_ok") for t in scored]
        ok = bool(scored) and all(r == 0.0 for r in rewards) and all(f is not False for f in func)
        why = "reward %s, functional tests %s" % (rewards, "all pass" if all(f for f in func) else
                                                   "FAIL" if False in func else "not reported")
    elif check["expect"] == PARTIAL:
        ok = bool(scored) and all(0.0 < r < 1.0 for r in rewards)
        why = "rewards %s (need strictly between 0 and 1)" % rewards
        if check["name"].startswith("check_only") and any(r >= 1.0 for r in rewards):
            why += ": a check-only fix closes every tested attack, so the producer path is not separately exploitable (drop the task)"
    else:
        ok = bool(scored) and all(r >= 1.0 for r in rewards)
        why = "rewards %s (need 1.0)" % rewards
    if crashed and not ok:
        return "infra", "%d trial(s) crashed without a score: re-run (%s)" % (crashed, why)
    return ("pass" if ok else "fail"), why


def run_checks(task_dir, use_auto=False, parallel=3, log=print):
    slug = os.path.basename(os.path.normpath(task_dir))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    checks = [{"id": "oracle", "kind": "oracle", "name": "Correct fix scores 1.0 twice", "path": task_dir, "attempts": 2},
              {"id": "nop", "kind": "nop", "name": "No fix scores 0.0, functional tests pass", "path": task_dir, "attempts": 1}]
    vs, note = variants(task_dir, use_auto)
    if note:
        log("  variants: " + note)
    extra = []
    if not any(n.startswith("check_only") for n, _ in vs):
        extra.append({"id": "variant:check_only", "name": "Check-only fix scores strictly between 0 and 1",
                      "status": "warn", "detail": "not run: add solution/variants/check_only.patch (VALIDATION.md check 3), "
                      "or use --auto-split; skip only if the fix has no separate producer site"})
    for vname, patch in vs:
        checks.append({"id": "variant:" + vname, "kind": "variant", "name": "%s fix (%s)" % (vname, expectation(vname)),
                       "expect": expectation(vname), "path": make_variant_task(task_dir, vname, patch), "attempts": 1})
    pending, running = list(checks), []
    while pending or running:
        while pending and len(running) < parallel:
            c = pending.pop(0)
            c["job"] = "val-%s-%s-%s" % (stamp, slug, c["id"].replace("variant:", "").replace("_", "-"))
            agent = "nop" if c["kind"] == "nop" else "oracle"
            log("  start %-26s %s" % (c["id"], c["job"]))
            running.append((c, start(agent, c["path"], c["job"], c["attempts"])))
        c, proc = running.pop(0)
        proc.wait()
        c["status"], c["detail"] = judge(c, job_trials(c["job"]))
        log("  done  %-26s %-5s %s" % (c["id"], c["status"], c["detail"]))
    return [{k: c.get(k) for k in ("id", "name", "status", "detail", "job")} for c in checks] + extra


def overall(checks):
    """fail > incomplete (a run crashed or was skipped) > needs-review (a manual item or warning is open) > pass.
    A task is only "pass" when every check passed or a reviewer marked it pass / not applicable."""
    st = [c["status"] for c in checks]
    if "fail" in st:
        return "fail"
    if "infra" in st or "skipped" in st:
        return "incomplete"
    if "manual" in st or "warn" in st:
        return "needs-review"
    return "pass"


REVIEW_STATES = ("pass", "fail", "na")


def apply_review(record, decisions, note, who):
    """decisions: {check_id: pass|fail|na}. Only manual/warn items can be reviewed; automated results stand."""
    by_id = {c["id"]: c for c in record["checks"]}
    for cid, state in decisions.items():
        if state not in REVIEW_STATES:
            raise ValueError("%s: state must be one of %s" % (cid, ", ".join(REVIEW_STATES)))
        c = by_id.get(cid)
        if not c:
            raise ValueError("%s: no such check (have: %s)" % (cid, ", ".join(by_id)))
        if c["status"] not in ("manual", "warn") and not c.get("reviewed"):
            raise ValueError("%s: an automated result (%s) cannot be overridden by review" % (cid, c["status"]))
        c.update(status="pass" if state == "na" else state, reviewed={"state": state, "note": note, "by": who})
    record["status"] = overall(record["checks"])
    return record
