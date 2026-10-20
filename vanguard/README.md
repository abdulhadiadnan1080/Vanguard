# Vanguard

Ranks real security advisories by how likely a model is to **fail** a task built from them, and gets better with every
task that is run. The knowledge graph is the memory: every task, run, weakness and signal is a node, and outcome edges
count how often each weakness or signal defeated each model.

```
rank advisories ──► triage + build (agent, you approve) ──► validate ──► run (Model A ×1 + Model B ×1, then ask)
      ▲                                                                              │
      └──────────── train on recorded outcomes ◄──── ingest results into the graph ◄─┘
```

Everything in the diagram is implemented: harvesting, ranking, the knowledge graph and learner, validation, and the
gated loop. Python 3.9+ standard library only. It can also read an existing pipeline's task folders and Harbor runs
in place (configured in `private/paths.json`); it never writes outside its data folder.

## Private settings

The code names no real model, project or repository. Those live in `private/`; templates are in `private.example/`.

`private/` and the generated `data/` folder can sit inside this folder (both are in `.gitignore`) or, safer, in a
folder named `vanguard_local/` next to the repository, so no upload can ever include them. vanguard looks for
`VANGUARD_PRIVATE` / `VANGUARD_DATA` first, then `private/` and `data/` here, then `../../vanguard_local/`.

| File | What it holds |
|---|---|
| `private/models.json` | Which real model is **Model A**, **Model B** (in that order; the first is the reference), how Harbor runs each one, and the substrings that recognise it in old run data |
| `private/paths.json` | Where the existing pipeline and finder folders are, plus extra Harbor job folders and the task-name prefix |
| `private/task_tags.json` | Which of your task folders tests which weakness, and which have a deliberate hint |

Adding a third model is one more entry in `models.json`: it gets its own column in every table and its own runs in the loop.

## Commands

```bash
python3 vanguard.py harvest --since 2025-01-01 --pages 3 --fetch   # real advisories and fix patches from GitHub
python3 vanguard.py ingest           # build/refresh the graph from all past tasks and Harbor trials (idempotent)
python3 vanguard.py train            # fit on recorded outcomes, score it leave-one-repo-out, save data/model.json
python3 vanguard.py rank --top 25    # score the finder's advisory pool, write data/shortlist_<stamp>.md
python3 vanguard.py collect          # record new Harbor results, check earlier forecasts, retrain if outcomes changed
python3 vanguard.py watch            # keep collecting while runs are in progress (checks every 2 min; Ctrl-C stops)
python3 vanguard.py validate TASK    # fairness checks before any model runs (see "Validation" below)
python3 vanguard.py review TASK      # list open items; sign off with --set fairness=pass --note "why"
python3 vanguard.py doctor           # machine, data, Harbor/Docker and the test suite: run before a session
python3 vanguard.py report           # data/report.md: what defeats which model, accuracy over time, forecasts vs results
python3 vanguard.py export [PATH]    # whole graph as JSON
```

**Feedback loop:** leave `watch` running while Harbor jobs run (or run `collect` afterwards). Each finished trial is
recorded in the graph; when the counted outcomes change, the predictor retrains and logs its new accuracy. If the task
was built from a candidate that `rank` scored earlier, the prediction made **before the run** is compared with the
result and logged as a forecast check: the real measure of whether the loop is learning. Run `rank` again afterwards
to re-score the pool with the updated weights.

Extra folders (for example where the loop builds tasks or writes Harbor jobs) can be added with
`VANGUARD_TASK_DIRS` and `VANGUARD_JOB_DIRS` (separate several with `:`). `VANGUARD_DATA` moves the data folder.

## Files

| File | Role |
|---|---|
| `knowledge_graph.py` | SQLite graph store (`data/graph.sqlite`): nodes, typed edges, metrics history |
| `harvester.py` | GitHub advisory harvest, fix-patch download and cache, size filter, the check / data-producer signature and its scored factors, fix spread, weakness detection rules |
| `sources.py` | Settings (loaded lazily; missing ones raise `ConfigError`) and readers for task folders, Harbor trials (all 3 `details.json` shapes) and the advisory pool |
| `schemas.py` | Typed shapes (`TypedDict`) of every record: advisories, analyses, trials, ranked candidates, validation records, loop state, model settings |
| `ingest.py` | Builds the graph and recomputes outcome edges, deciding which trials count |
| `learner.py` | Logistic regression with priors set from the hand weights; leave-one-repo-out evaluation |
| `catalog.json` | Curated knowledge the run data can't supply: the weaknesses and the mitigations (task tags are in `private/`) |
| `vanguard.py` | Command line |

## The loop

`python3 vanguard.py loop` moves every candidate in the loop forward as far as it can **without crossing a gate or
spending money**, then prints where each one stands. Run it again whenever something has changed (a build finished, you
approved something, runs ended). `loop status` only shows the table.

| Step | Command | Cost |
|---|---|---|
| Pick candidates from the latest `rank` and write a build package for each (`data/loop/<GHSA>/`: brief, agent instructions, triage/build prompts, spec, checklist) | `loop --select 3` | free |
| Triage + build: the agent reads `AGENT_TASK.md`, writes `verdict.json`, and on BUILD puts the task in `data/loop_tasks/<slug>/` with check-only/producer-only variants | `loop --build --max-builds 2`, or build by hand from the package | **paid** (agent) |
| Gate 1: look at the built task | `approve GHSA` or `reject GHSA --reason "..."` | free |
| Validate (static + oracle ×2, nop, variants) | automatic on the next `loop` | free (Docker) |
| Sign off open validation items | `review <slug> --set fairness=pass --set repo_tests=pass --note "..."` | free |
| Round 1: Model A ×1 + Model B ×1 | `loop --run-models --max-model-runs 2` | **paid** (models) |
| Collect: record results, check the forecast, retrain | automatic on the next `loop` | free |
| Gate 2: attempts 2-3 (only if both models failed round 1) | `approve GHSA --more-runs`, then `loop --run-models --max-model-runs 4` | **paid** |
| End states | `accepted` (both failed every attempt), `solved`, `rejected`, `dropped` | |

**Guards:**
- Paid steps need their flag **and** an explicit budget, or the command refuses.
- A gate can't be skipped, and gate 2 holds even when a budget is given.
- A model that solves round 1 ends the candidate at once, so no further spending.
- A crashed trial is infrastructure: only the crashed attempts are re-run, at most twice, and valid results are kept.
- Harbor producing no results counts as infrastructure, not as a task failure.
- Only one loop runs at a time (`data/loop.lock`).
- The forecast is frozen when a candidate is selected, so every finished task scores the predictor.

**Builder:** set `builder_cmd` in `config.json`. It's any command that reads the instructions on stdin and works in the
package folder, for example a headless coding agent. It's `null` by default, so `--build` refuses until you set it. Until
then, packages can be built by hand or by an agent you start yourself; the loop picks the result up from
`verdict.json` and `data/loop_tasks/`. *Not yet exercised with a real agent:* the loop was tested end to end with
stand-ins (`tests/test_loop_e2e.py`) and with real Harbor for validation only.

## Validation

`validate <slug or path>` runs the VALIDATION.md checklist and stores the result on the task in the graph
(`data/validation/<slug>.json`, and a table in `report.md`).

- **Instant static checks (no Docker):**
  - task layout, `no-network`, metadata;
  - the report doesn't name the advisory or fix commit, and doesn't name any file the fix touches;
  - no git history in the image, and no tests, fix or advisory text in what the final image copies in;
  - the verifier writes a reward.
- **Harbor checks:** Harbor's own `oracle` and `nop` agents, so no model API and no cost beyond local Docker time.
  - The upstream fix scores 1.0 twice.
  - No fix scores 0.0, with every functional test passing.
  - Fix variants: each patch in `solution/variants/` is judged by its name.
    - `check_only*` and `producer_only*` must score strictly between 0 and 1. A `check_only` scoring 1.0 means drop the task.
    - `hardened*` and `alt*` must score 1.0.
  - `--auto-split` guesses those patches from `fix.patch`. It's opt-in, because it mislabelled the check file on a real two-file task.
- **Left to a reviewer and marked as such:** the repo's own tests on both trees, and the fairness map.
- **Verdict:** `fail` > `incomplete` (a run crashed or was skipped) > `needs-review` (a manual item or warning is open) > `pass`.
  A task only reaches `pass` when every automated check passed and a reviewer has signed off every open item with
  `review --set <check>=pass|fail|na --note "..."`. Automated results can't be overridden by a review.
- **Safety:**
  - It refuses a task that already has counted model runs (a new oracle run would make them stale) unless `--force` is given.
  - It stops if Docker isn't running or the `alpine` image isn't cached. It never pulls or installs anything.
  - It runs at most 3 Harbor jobs at once (`--parallel`) and never stops or removes containers.
- Jobs go to `data/harbor_jobs/val-*`, and scratch variant copies to `data/variant_tasks/`. Both are read by `collect`.

## Tests

```bash
python3 -m unittest discover -s tests -v     # 25 tests, stdlib only, each builds its own data in a temp folder
```

They cover result parsing (all 3 `details.json` shapes, trial statuses, de-duplication), the rules for which trials
count, the graph store (merging, concurrency, UTC ordering of predictions), the learner (it moves toward the data and
keeps priors for unseen signals) and validation (leak detection, builder-stage false positives, every verdict, review
rules). `doctor` runs them too.

## Which trials count

- **Running** (no score yet, not finished) is left alone until it finishes.
- **Crash** (no score) and **timeout** (the old 60-minute limit) are infrastructure problems, not model results.
- **Stale:** a model trial that started before the task's latest oracle run doesn't count. A re-validation means the task changed. This reproduces every result in the original findings, including a test-setup fix and a revised task.

## How the predictor learns

- **Starting point:** each signal's weight starts at the value the project already trusts, `finder.py` `WEIGHTS` for shape signals and the weakness weights in `weaknesses.json` and `catalog.json`.
- **Updating:** a MAP fit moves those weights toward what actually happened. With few outcomes, the weights stay near their starting values.
- **Model-specific weaknesses:** a weakness recorded for one model only (for example stacked-vulnerabilities, recorded for Model B) doesn't affect the other model's prediction.
- **Accuracy:** measured with each repository's tasks held out together, against two baselines: the overall fail rate, and the starting weights with no fitting. It's logged on every `train`, so improvement is visible over time.

**Limits today:**
- 32 labelled tasks from 13 repositories, and most failures come from one repository.
- The top of the shortlist rests on that one repository. The shortlist's Evidence column says so for each candidate.
- Model B has 3 labelled tasks, so its predictions mostly reuse Model A's evidence.
- Each built and run task makes the predictions better.

## Next phases

2. ~~**Automatic record:** collect new Harbor jobs straight into the graph after each run, then retrain.~~ Done: `collect` and `watch`.
3. ~~**Scripted validation:** oracle run twice, nop, check-only partial and leak check, with results stored as graph edges.~~ Done: `validate`.
4. ~~**`vanguard.py loop`:** rank → agent build → your approval → validate → Model A ×1 + Model B ×1 → ask → collect → retrain.~~ Done: see "The loop".
