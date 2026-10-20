"""Typed shapes of the records that flow through the pipeline.

These are TypedDicts (standard library): the records stay plain dicts at run time, because they are stored as JSON in
SQLite and in .jsonl files and are merged and re-saved constantly, but every field is named and typed for readers,
editors and type checkers.
"""
from typing import Dict, List, Literal, Optional, Tuple, TypedDict

TrialStatus = Literal["valid", "crash", "timeout", "running"]
CheckStatus = Literal["pass", "fail", "warn", "manual", "infra", "skipped"]
Verdict = Literal["pass", "needs-review", "incomplete", "fail"]
LoopStage = Literal["selected", "awaiting-build", "building", "rejected", "awaiting-approval", "approved",
                    "awaiting-review", "dropped", "ready-to-run", "round1-running", "solved", "awaiting-more-runs",
                    "round2-approved", "round2-running", "accepted"]


class ModelConfig(TypedDict, total=False):
    """One entry of private/models.json."""
    id: str                     # neutral id, e.g. "MODEL_A"
    label: str                  # shown in every table, e.g. "Model A"
    match: List[str]            # substrings that recognise this model in old run data
    aliases: List[str]
    harbor_model: str           # the model name Harbor is given
    env_file: str               # credentials file Harbor reads (never read by vanguard itself)
    kwargs: List[str]           # extra `--ak key=value` agent arguments


class FixCommit(TypedDict):
    repo: str                   # "owner/name"
    sha: str


class Advisory(TypedDict, total=False):
    """One reviewed advisory with a fix commit (harvester.harvest / data/pool.jsonl)."""
    ghsa_id: str
    cve_id: Optional[str]
    ecosystem: str
    language: str
    repo: str
    cwes: List[str]
    severity: Optional[str]
    published_at: str
    summary: str
    description: str
    fix_commits: List[FixCommit]


Signal = Tuple[str, Optional[float], str]  # (signal id, hint weight, reason)


class Analysis(TypedDict):
    """sources.analyse(): what the harvester read from one fix patch."""
    signals: List[Signal]
    finder: Optional[Dict[str, object]]     # {"points": float, "features": {producer_file, check_file, ...}}
    reject: Optional[str]                   # why the fix cannot become a task, if it cannot
    shape: Optional[Dict[str, object]]      # files, hunks, functions, repeated fix sites


class Trial(TypedDict, total=False):
    """One Harbor trial (sources.read_trial)."""
    id: str
    job: str
    trial: str
    task: str                   # task folder slug
    agent: str                  # "oracle", "nop" or the model agent
    model: Optional[str]        # neutral model id for model trials
    reward: Optional[float]     # share of hidden security tests passed; None if the trial crashed
    exception: Optional[str]
    status: TrialStatus
    security_passed: Optional[int]
    security_total: Optional[int]
    functional_ok: Optional[bool]
    started_at: str             # ISO 8601, UTC
    finished_at: Optional[str]
    cost_usd: Optional[float]
    path: str


class RankedCandidate(TypedDict, total=False):
    """One row of data/rank_<stamp>.jsonl (vanguard.py rank)."""
    ghsa_id: str
    repo: str
    language: str
    summary: str
    fix_commit: str
    p_fail: Dict[str, float]    # model id -> chance of failing one attempt
    p_both: float               # every model fails one attempt (product)
    finder_points: Optional[float]
    reject: Optional[str]
    already_built: bool
    signals: List[str]
    why: List[Tuple[str, float]]  # strongest contributions to the reference model's score
    evidence_signal: Optional[str]
    evidence_tasks: int
    evidence_repos: int
    model_version: str


class ValidationCheck(TypedDict, total=False):
    id: str
    name: str
    status: CheckStatus
    detail: str
    job: str                    # Harbor job folder, for checks that ran one
    reviewed: Dict[str, str]    # {"state", "note", "by"} once a reviewer signed it off


class ValidationRecord(TypedDict, total=False):
    at: str
    status: Verdict
    checks: List[ValidationCheck]
    reviewed_at: str


class HistoryEntry(TypedDict, total=False):
    at: str
    state: LoopStage
    note: Optional[str]


class LoopState(TypedDict, total=False):
    """A candidate's place in the loop (stored on its Candidate node as `loop`)."""
    ghsa: str
    state: LoopStage
    history: List[HistoryEntry]
    slug: str
    rank_file: str
    p_fail: Dict[str, float]
    repo: str
    fix_commit: str
    package: str                # build package folder
    task_dir: str               # where the built task is (or will be)
    jobs: Dict[str, Dict[str, object]]          # model id -> {"job", "pid", "attempts"}
    rounds: List[Dict[str, List[Optional[float]]]]
    round_acc: Optional[Dict[str, List[float]]]  # valid rewards collected so far in the current round
    pending: Optional[Dict[str, int]]           # model id -> attempts still to re-run after a crash
    round_attempts: int
    round_retries: int
    validation_tries: int
    build_error: str
