"""Harvester: find real security fixes and read their shape. Standard library only (Python 3.9+).

  harvest()          reviewed advisories from the public GitHub advisory database (set GITHUB_TOKEN for higher limits)
  fetch_patch()      the upstream fix commit as a unified diff, cached on disk
  size_decision()    rejects fixes with no source change, or too large to build a task from
  signature()        the check / data-producer shape: which file decides "allowed or not", which file shapes or
                     reloads the data it relies on, the security field they share, and scored factors (WEIGHTS)
  analyse_patch()    size and spread of the fix: files, hunks, functions, repeated fix sites
  weakness_factors() applies the detection rules of a weaknesses.json file

The roles are regex guesses over the diff: this stage is for recall. Every shortlisted candidate goes through an
agent's triage before anything is built.
"""
import collections
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

from schemas import Advisory

API = "https://api.github.com/advisories"
LANG = {"pip": "python", "maven": "java", "npm": "javascript", "go": "go", "rubygems": "ruby", "composer": "php",
        "rust": "rust", "nuget": "csharp"}
SRC_EXT = {"python": (".py", ".pyx", ".c"), "java": (".java", ".kt", ".scala"),
           "javascript": (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"), "go": (".go",), "ruby": (".rb",),
           "php": (".php",), "rust": (".rs",), "csharp": (".cs",)}
TEST_RX = re.compile(r"(^|/)(tests?|__tests__|spec|testdata|testing)(/|$)|(^|/)test_[^/]*$|_test\.(py|go|rb)$|"
                     r"\.(test|spec)\.[jt]sx?$|Tests?\.(java|kt|scala|cs)$|/src/test/", re.I)
NONSRC_RX = re.compile(r"(^|/)(docs?|\.github|changelog[^/]*|changes[^/]*|news[^/]*|history[^/]*|release[^/]*)(/|$|\.)|"
                       r"\.(md|rst|txt|adoc|lock|toml|cfg|ini|ya?ml|json|xml|gradle|properties)$|"
                       r"(^|/)(go\.(mod|sum)|requirements[^/]*|setup\.py|package-lock\.json)$", re.I)
EXPLICIT_RX = re.compile(r"(does not|doesn't|never|not) (verify|validate|check|sanitize|escape)|"
                         r"without (verifying|validation|checking)|missing (signature|auth|validation|check)", re.I)
COMMIT_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/commit/([0-9a-fA-F]{7,40})")
REPO_RE = re.compile(r"https?://github\.com/([^/]+)/([^/#?]+)")
NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
# Boilerplate lines that repeat in any diff and say nothing about a repeated security fix.
GENERIC_RX = re.compile(r"^(return(\s+(this|nil|err|null|None|true|false|self|0|-1|undefined))?;?|if err != nil \{|\} else \{|else \{|else:|try:|try \{|"
                        r"except( \w+)?( as \w+)?:|catch \(.*\) \{|finally:|finally \{|break;?|continue;?|pass|\}\)?;?|\);?|"
                        r"[\w.]+: (true|false|null|None|S),?|@Override|@\w+|super\(.*\);?|this\.\w+ = \w+;|self\.\w+ = \w+|"
                        r"\w+ \w+ = null;|var \w+;|let \w+;|\.\.\.\w+,?)$")


# ---------------------------------------------------------------- advisories (GitHub advisory database)
def http_get(url):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vanguard-harvester"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        nxt = NEXT_RE.search(resp.headers.get("Link", ""))
        return body, (nxt.group(1) if nxt else None), resp.headers.get("X-RateLimit-Remaining")


def to_candidate(adv, eco):
    if adv.get("withdrawn_at"):
        return None
    m = REPO_RE.match(adv.get("source_code_location") or "")
    repo = (m.group(1) + "/" + m.group(2)).lower().removesuffix(".git") if m else None
    commits, seen = [], set()
    for ref in adv.get("references") or []:
        cm = COMMIT_RE.match(ref)
        if cm:
            r = (cm.group(1) + "/" + cm.group(2)).lower()
            key = r + "@" + cm.group(3).lower()
            if key not in seen:
                seen.add(key)
                commits.append({"repo": r, "sha": cm.group(3).lower()})
                repo = repo or r
    if not repo or not commits:
        return None
    return {"ghsa_id": adv.get("ghsa_id"), "cve_id": adv.get("cve_id"), "ecosystem": eco, "language": LANG.get(eco, eco),
            "repo": repo, "cwes": [c.get("cwe_id") for c in adv.get("cwes") or [] if c.get("cwe_id")],
            "severity": adv.get("severity"), "published_at": adv.get("published_at"), "summary": adv.get("summary"),
            "description": (adv.get("description") or "")[:4000], "fix_commits": commits}


def harvest(ecosystems: List[str], since: str, pages: int) -> List[Advisory]:
    out = []
    for eco in ecosystems:
        url = API + "?" + urllib.parse.urlencode({"ecosystem": eco, "type": "reviewed", "published": ">=" + since,
                                                   "per_page": "100", "sort": "published", "direction": "desc"})
        for page in range(pages):
            if not url:
                break
            body = None
            for _ in range(3):
                try:
                    body, url, left = http_get(url)
                    break
                except urllib.error.HTTPError as err:
                    print("  %s: HTTP %s (rate limit? set GITHUB_TOKEN)" % (eco, err.code))
                    url = None
                    break
                except Exception as err:  # noqa: BLE001  (GitHub sometimes cuts long responses)
                    print("  %s page %d: %s, retrying" % (eco, page + 1, type(err).__name__))
                    time.sleep(3)
            if body is None:
                break
            out.extend(c for c in (to_candidate(a, eco) for a in body) if c)
            print("  %-9s page %d: %3d advisories (API calls left: %s)" % (eco, page + 1, len(body), left), flush=True)
    return out


# ---------------------------------------------------------------- fix patch and size filter
def fetch_patch(repo: str, sha: str, cache_dir: str) -> Optional[str]:
    path = os.path.join(cache_dir, sha + ".patch")
    if os.path.exists(path):
        return open(path, encoding="utf-8", errors="replace").read()
    try:
        req = urllib.request.Request("https://github.com/%s/commit/%s.patch" % (repo, sha), headers={"User-Agent": "vanguard-harvester"})
        text = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    os.makedirs(cache_dir, exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)
    return text


def size_decision(text, lang):
    exts, files, lines, cur = SRC_EXT.get(lang, ()), set(), 0, None
    for line in text.split("\n"):
        m = re.match(r"diff --git a/(.+?) b/", line)
        if m:
            cur = m.group(1) if m.group(1).endswith(exts) and not TEST_RX.search(m.group(1)) and not NONSRC_RX.search(m.group(1)) else None
            if cur:
                files.add(cur)
        elif cur and line[:1] in "+-" and not line.startswith(("+++", "---")):
            lines += 1
    if not files:
        return "no source change in the project language (dependency/config/docs-only)"
    if len(files) > 20 or lines > 1200:
        return "oversized fix (> 20 source files or > 1200 lines)"
    return None


# ---------------------------------------------------------------- file roles and the check / producer signature
# Security-relevant field names whose loss lets data slip past a check.
SEC_FIELD_RX = re.compile(
    r"\b(issuer|iss|audience|aud|tenant(_?id)?|owner(_?id)?|org(anization)?(_?id)?|project(_?id)?|workspace(_?id)?|"
    r"scope[sd]?|roles?|permissions?|privileges?|realm|origin|domain|host(name)?|subject|sub|user(_?id|name)?|"
    r"account(_?id)?|session(_?id)?|signature|nonce|expir\w*|not_?before|kid|alg(orithm)?|client_?id|redirect_?uri|"
    r"resource|namespace|visibility|is_?admin|admin|verified|trusted|allow\w*|deny\w*|acl|policy)\b", re.I)
# Constructs that mark a file/hunk as a DATA PRODUCER (shapes, converts or reloads data).
PRODUCER_LINE_RX = re.compile(
    r"z\.object|\.strip\(\)|\.passthrough\(\)|\.strict\(\)|\.extend\(|\.merge\(|\.pick\(|\.omit\(|Schema\b|schema\b|"
    r"BaseModel|model_config|ConfigDict|extra\s*=|Field\(|@dataclass|dataclasses\.|attrs\.|fields\.\w+\(|unknown\s*=|"
    r"marshmallow|serializ|deserializ|to_dict|from_dict|to_json|from_json|toJSON|fromJSON|asdict|__getstate__|"
    r"__setstate__|__reduce__|json\.(dumps|loads)|JSON\.(parse|stringify)|pickle|yaml\.(safe_)?load|"
    r"`json:\"|json:\"|omitempty|@Json\w+|ObjectMapper|readObject|writeObject|Marshal|Unmarshal|protobuf|"
    r"\.save\(|\.load\(|persist|hydrat|normaliz|normalis|canonical|decode|encode|parse\w*\(|from_row|to_row|"
    r"Column\(|db\.\w+\(|\bINSERT\b|\bSELECT\b|serde|#\[derive", re.I)
PRODUCER_PATH_RX = re.compile(
    r"schema|serial|model|dto|codec|marshal|encod|decod|pars|loader|load|store|storage|persist|cache|repositor|"
    r"dao|entity|record|types?\.|mapper|convert|normali|session|token_store|state", re.I)
COMMENT_RX = re.compile(r"^\s*(//|#|\*|/\*|\"\"\"|''')")
# A security field DECLARED in a data shape (schema/struct/model/serializer field): the strongest producer signal.
FIELD_DECL_RX = re.compile(r"^\s*[\"']?\w+[\"']?\??\s*:\s*(z\.|Optional|str\b|string\b|int\b|bool|list|List|Field\(|fields\.)|"
                           r"^\s*[A-Z]\w*\s+\*?[\w.\[\]]+\s+`json:|^\s*\w+\s*=\s*(Column|models\.|fields\.|Field\()|"
                           r"@Json(Property|Alias)|^\s*(private|public|protected)\s+[\w<>]+\s+\w+\s*;")
# Data-access calls (load/read/parse): if the check file calls one the producer file defines, the check reaches it directly.
DATA_CALL_RX = re.compile(r"\b((?:Get|get|Load|load|Find|find|Read|read|Fetch|fetch|Parse|parse|Decode|decode|Deserialize|"
                          r"deserialize|Lookup|lookup|Query|query|Resolve|resolve)[A-Z_]\w{3,})\s*\(")
# Constructs that mark a CHECK (a decision that refuses).
CHECK_LINE_RX = re.compile(
    r"\bthrow\b|\braise\b|\breturn\s+(false|None|null|nil|err|401|403|404)\b|Forbidden|Unauthori[sz]ed|PermissionDenied|"
    r"AccessDenied|Mismatch|mismatch|!==|!=|\bnot\s+in\b|\bdeny|\breject|\brefuse|\bassert\b|verify|validat|"
    r"\bcheck\w*\(|allowed|permit|authori[sz]|abort\(|http\.Error|StatusForbidden|StatusUnauthorized", re.I)
# A storage/serialization boundary: data saved now and read back later, or crossing a parse step.
BOUNDARY_RX = re.compile(
    r"persist\w*|\bstored?\b|\bsav(e|ed|es|ing)\b|reload\w*|re-?load|round[- ]?trip|serializ\w*|deserializ\w*|"
    r"\bcache[ds]?\b|restart|on disk|database|\bdb\b|session store|token store|storage|rehydrat\w*|"
    r"from (a )?(file|disk|storage|cache)|import(ed)? (from|data)|export(ed)?", re.I)
# Producer constructs that DROP or WHITELIST fields (the field can be lost).
DROPS_RX = re.compile(r"\.strip\(\)|extra\s*=\s*['\"]?(ignore|forbid)|unknown\s*=\s*EXCLUDE|omitempty|@JsonIgnore|"
                      r"\bomit\b|\bpick\(|exclude\s*=|only\s*=|fields\s*=|__slots__|allowlist|whitelist|"
                      r"FAIL_ON_UNKNOWN|ignoreUnknown|json:\"-\"", re.I)
FUNC_DEF_RX = re.compile(r"(?:def|function|func|fn)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w{3,})|"
                         r"(?:const|let|var|export const)\s+([A-Za-z_]\w{3,})\s*=\s*(?:async\s*)?(?:\(|function|z\.)|"
                         r"(?:public|private|protected|static)\s+[\w<>\[\], ]+\s+([A-Za-z_]\w{3,})\s*\(")

WEIGHTS = {  # (points, reason). Evidence: the controlled study's variant runs.
    "field_propagation": (3.0, "the same security field is added at a producer site and used at a check site in another file"),
    "producer_drops_field": (1.5, "the producer has a construct that drops/whitelists fields (strip, ignore extra, omitempty, ...)"),
    "producer_schema_change": (1.0, "the fix changes a schema/serializer/model/loader construct"),
    "storage_boundary": (1.5, "advisory, commit message or diff mentions persist/save/reload/serialize/cache"),
    "check_and_producer_split": (1.0, "check and producer changes are in different files"),
    "cross_package": (0.5, "check and producer are in different top-level directories/packages"),
    "roundtrip_tests": (0.8, "upstream tests exercise save/reload or serialize/parse: hidden tests come almost free"),
    "upstream_tests": (0.3, "upstream fix ships tests"),
    "check_calls_producer": (-3.0, "the check's own changed code calls the producer directly (an easy data flow: models solve it)"),
    "python_model_is_type": (-3.0, "Python model is both the type and the storage format: a check-only fix cannot be separated (seen in a related SDK)"),
    "explicit_summary": (-0.4, "advisory title names the missing mechanism; the report would give the fix away"),
}


def file_roles(text, lang):
    """Per source file: producer/check scores, security fields touched, function names defined in the hunks."""
    files, cur = collections.OrderedDict(), None
    for line in text.split("\n"):
        m = re.match(r"diff --git a/(.+?) b/", line)
        if m:
            cur = m.group(1)
            files[cur] = {"plus": [], "minus": [], "ctx": []}
        elif cur and line.startswith("@@"):
            files[cur]["ctx"].append(line.split("@@")[-1].strip())
        elif cur and line.startswith("+") and not line.startswith("+++"):
            files[cur]["plus"].append(line[1:].strip())
        elif cur and line.startswith("-") and not line.startswith("---"):
            files[cur]["minus"].append(line[1:].strip())
    exts = SRC_EXT.get(lang, ())
    roles, tests = {}, {}
    for f, v in files.items():
        v["plus"] = [l for l in v["plus"] if not COMMENT_RX.match(l)]
        v["minus"] = [l for l in v["minus"] if not COMMENT_RX.match(l)]
        changed = v["plus"] + v["minus"]
        if TEST_RX.search(f):
            tests[f] = changed
            continue
        if not f.endswith(exts) or NONSRC_RX.search(f):
            continue
        prod_lines = [l for l in changed if PRODUCER_LINE_RX.search(l)]
        check_lines = [l for l in v["plus"] if CHECK_LINE_RX.search(l)]
        funcs = set()
        for src in v["ctx"] + v["plus"]:
            for g in FUNC_DEF_RX.findall(src):
                funcs.update(x for x in g if x)
        drops = [l for l in changed if DROPS_RX.search(l)]
        decls = [l for l in v["plus"] if FIELD_DECL_RX.search(l) and SEC_FIELD_RX.search(l)]
        prod_lines = decls + [l for l in prod_lines if l not in decls]
        path_bonus = 2 if PRODUCER_PATH_RX.search(f) else 0
        roles[f] = {
            # a producer site shapes/reloads data; files dominated by refusals are checks, not producers
            "producer": len(prod_lines) + 3 * len(drops) + 4 * len(decls) + path_bonus - 0.5 * len(check_lines),
            "decls": decls[:4],
            "data_calls": set(DATA_CALL_RX.findall("\n".join(v["ctx"] + changed))),
            "check_strength": len(check_lines) - 0.5 * len(prod_lines),
            "all_text": "\n".join(v["ctx"] + changed),
            "check": len(check_lines),
            "prod_lines": prod_lines[:6], "check_lines": check_lines[:6],
            "drops": drops[:4],
            "fields_plus": {m.group(0).lower() for l in v["plus"] for m in SEC_FIELD_RX.finditer(l)},
            "plus": v["plus"], "funcs": funcs,
        }
    return roles, tests


def signature(c, text):
    roles, tests = file_roles(text, c["language"])
    if len(roles) < 2:
        return None, "fewer than 2 source files: no separate producer site"
    producers = sorted((f for f, r in roles.items() if r["producer"] >= 2), key=lambda f: -roles[f]["producer"])
    checks = sorted((f for f, r in roles.items() if r["check"] >= 1), key=lambda f: -roles[f]["check"])
    pairs = [(p, k) for p in producers for k in checks if p != k]
    if not pairs:
        return None, "no (producer file, check file) pair in the fix"
    best, best_s, best_shared = None, float("-inf"), set()
    for p, k in pairs:  # pick the pair with the strongest shared security field
        shared = roles[p]["fields_plus"] & ({m.group(0).lower() for l in roles[k]["plus"] for m in SEC_FIELD_RX.finditer(l)})
        s = len(shared) * 3 + roles[p]["producer"] + roles[k]["check_strength"]
        if s > best_s:
            best, best_s, best_shared = (p, k), s, shared
    p, k = best
    commit_msg = "\n".join(l for l in text.split("\n")[:60] if not l.startswith(("diff ", "+++", "---", "index ")))
    narrative = " ".join([c.get("summary") or "", c.get("description") or "", commit_msg])
    boundary_hits = sorted({m.group(0).lower() for m in BOUNDARY_RX.finditer(narrative + "\n" + "\n".join(roles[p]["plus"]))})[:8]
    k_text = roles[k]["all_text"]  # changed lines + hunk headers of the check file
    calls = sorted(fn for fn in roles[p]["funcs"] | roles[p]["data_calls"] if re.search(r"\b%s\s*\(" % re.escape(fn), k_text))
    test_text = "\n".join(l for ls in tests.values() for l in ls)
    feats = {
        "producer_file": p, "check_file": k, "shared_fields": sorted(best_shared),
        # the static role guess is coarse: list every plausible producer/check file for the triage stage
        "producer_candidates": producers[:4], "check_candidates": checks[:4],
        "producer_evidence": roles[p]["prod_lines"], "check_evidence": roles[k]["check_lines"],
        "drop_evidence": roles[p]["drops"], "boundary_words": boundary_hits, "check_calls": calls,
        "n_src_files": len(roles), "test_files": len(tests),
    }
    factors = []
    add = lambda name: factors.append((name,) + WEIGHTS[name])
    if best_shared:
        add("field_propagation")
    if roles[p]["drops"]:
        add("producer_drops_field")
    if roles[p]["prod_lines"]:
        add("producer_schema_change")
    if boundary_hits:
        add("storage_boundary")
    add("check_and_producer_split")
    if p.split("/")[0:2] != k.split("/")[0:2]:
        add("cross_package")
    if tests:
        add("upstream_tests")
        if BOUNDARY_RX.search(test_text):
            add("roundtrip_tests")
    if calls:
        add("check_calls_producer")
    # Python: the security field is added to a model/dataclass that is both the in-memory type and the stored form,
    # so a check-only fix cannot even read the field (seen in a related SDK). Shown by a declared field in the producer.
    if c["language"] == "python" and best_shared and (roles[p]["decls"] or re.search(r"BaseModel|@dataclass|model_config", roles[p]["all_text"])):
        add("python_model_is_type")
    if EXPLICIT_RX.search(c.get("summary") or ""):
        add("explicit_summary")
    return {"features": feats, "factors": factors, "points": round(sum(f[1] for f in factors), 2)}, None


# ---------------------------------------------------------------- fix size and spread, weakness detection rules
def analyse_patch(text, lang):
    files, cur, hunk = collections.OrderedDict(), None, None
    for line in text.split("\n"):
        m = re.match(r"diff --git a/(.+?) b/", line)
        if m:
            cur = m.group(1)
            files[cur] = {"lines": 0, "hunks": 0, "contexts": set(), "added": []}
        elif cur and line.startswith("@@"):
            files[cur]["hunks"] += 1
            ctx = line.split("@@")[-1].strip()
            hunk = (cur, files[cur]["hunks"])
            if ctx:
                files[cur]["contexts"].add(ctx[:80])
        elif cur and line and line[0] in "+-" and not line.startswith(("+++", "---")):
            files[cur]["lines"] += 1
            files[cur]["added"].append((hunk, line[0] + line[1:].strip()))  # keep the sign: same line added OR removed at many sites
    exts = SRC_EXT.get(lang, ())
    src = {f: v for f, v in files.items() if f.endswith(exts) and not TEST_RX.search(f) and not NONSRC_RX.search(f)}
    tests = [f for f in files if TEST_RX.search(f)]
    other = [f for f in files if f not in src and f not in tests]
    # Multi-site signal: the same (normalised) added line appearing in >= 2 different hunks = one check added at
    # several sites; and how many distinct enclosing functions the source changes touch.
    norm = lambda l: re.sub(r"\s+", " ", re.sub(r"[\"'].*?[\"']", "S", l)).strip()
    sites = collections.defaultdict(set)
    for v in src.values():
        for h, l in v["added"]:
            sign, body = l[:1], l[1:]
            n = norm(body)
            if (len(n) >= 12 and not n.startswith(("#", "//", "*", "/*", "import ", "from ", "}", "{", ")", "package "))
                    and not GENERIC_RX.match(n) and re.search(r"[A-Za-z_]\w*\s*[(.=\[]", n)):
                n = sign + n
                sites[n].add(h)
    repeated = sorted(((len(hs), l) for l, hs in sites.items() if len(hs) >= 2), reverse=True)
    funcs = set()
    for f, v in src.items():
        funcs.update((f, c) for c in v["contexts"])
    return {
        "repeated_fix_sites": repeated[0][0] if repeated else 0, "repeated_fix_line": repeated[0][1][:100] if repeated else "",
        "functions_touched": len(funcs),
        "files_total": len(files), "src_files": len(src), "src_lines": sum(v["lines"] for v in src.values()),
        "src_hunks": sum(v["hunks"] for v in src.values()), "src_dirs": len({os.path.dirname(f) for f in src}),
        "test_files": len(tests), "other_files": len(other), "src_paths": list(src)[:8],
    }


def weakness_factors(c, f, repo_advisories, weaknesses):
    """Apply every weakness spec whose detect conditions all hold. Returns [(name, weight, reason)]."""
    out = []
    for w in weaknesses:
        d = w.get("detect") or {}
        checks = [
            f.get("repeated_fix_sites", 0) >= d.get("min_repeated_fix_sites", 0),
            f["src_files"] >= d.get("min_src_files", 0),
            f["src_files"] <= d.get("max_src_files", 10 ** 6),
            f["src_dirs"] >= d.get("min_src_dirs", 0),
            f.get("functions_touched", 0) >= d.get("min_functions", 0),
            f["src_hunks"] >= d.get("min_hunks", 0),
            f["src_hunks"] <= d.get("max_hunks", 10 ** 6),
            repo_advisories >= d.get("min_advisories_same_repo", 0),
            (not d.get("summary_regex")) or bool(re.search(d["summary_regex"], c.get("summary") or "", re.I)),
            (not d.get("cwes")) or bool(set(d["cwes"]) & set(c.get("cwes") or [])),
            (not d.get("languages")) or c.get("language") in d["languages"],
        ]
        if all(checks):
            out.append(("weakness:" + w["id"], float(w["weight"]), "%s [%s; %s]" % (w["description"], w["status"], "+".join(w["models"]))))
    return out
