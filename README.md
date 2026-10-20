<div align="center">

# Vanguard

### Failure intelligence for LLM coding agents

**A self-improving knowledge graph that predicts which real-world security fixes an AI coding agent will fail, before you spend anything running it.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?style=flat-square)](#quick-start)
[![Dependencies: none](https://img.shields.io/badge/dependencies-standard%20library%20only-2ea44f?style=flat-square)](#quick-start)
[![Tests: 30 passing](https://img.shields.io/badge/tests-30%20passing-2ea44f?style=flat-square)](#tested-end-to-end)
[![Demo: one command](https://img.shields.io/badge/demo-one%20command-f0a020?style=flat-square)](#try-it-in-one-line)
[![Ranking cost: $0](https://img.shields.io/badge/ranking%20cost-%240-6f42c1?style=flat-square)](#what-it-does)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/loop-animation-dark.svg">
  <img alt="How Vanguard works: consult the knowledge graph, rank advisories, narrow down, build the task, approve, validate, run the models, learn, and repeat" src="docs/assets/loop-animation-light.svg" width="100%">
</picture>

[**Take the animated tour →**](https://abdulhadiadnan1080.github.io/Vanguard/) &nbsp;·&nbsp; [**Explore the interactive knowledge graph →**](https://abdulhadiadnan1080.github.io/Vanguard/graph.html)

<a href="https://abdulhadiadnan1080.github.io/Vanguard/graph.html">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/graph-overview-dark.png">
  <img alt="The knowledge graph: weaknesses, trigger conditions, evidence, tasks and task-design implications, linked by how they relate" src="docs/assets/graph-overview-light.png" width="100%">
</picture>
</a>

</div>

## Try it in one line

```bash
git clone https://github.com/abdulhadiadnan1080/Vanguard.git && cd Vanguard && python3 main.py --demo
```

No install, API keys, Docker or network. In about a second, the demo runs the **real pipeline code** through all four
stages on synthetic data: a 60-task history across 10 fictional repositories, 40 fictional candidate advisories, and
stand-ins for the build agent and the evaluation harness. It ends with an accepted benchmark task and a
[summary JSON](examples/sample_vanguard_benchmark.json).

```text
[1/4] Knowledge graph  remember every task, run and outcome; learn what makes models fail
      history: 60 graded tasks, 246 model trials across 10 repositories
      leave-one-repo-out   Brier   log loss   (lower is better)
      base_rate           0.1461    0.6144
      prior_only          0.0633    0.4192
      fitted              0.0497    0.3663
[2/4] Harvester  score every candidate advisory for each model's chance of failing
[3/4] Synthesizer  an agent triages each candidate and builds a task; a person approves it
      GHSA-demo-0029       -> rejected           REJECT: stub: the check calls the producer directly
[4/4] Judge  validate the task for fairness, run the models behind gates, feed results back
      GHSA-demo-0016       -> accepted           failed by both models on every attempt
```
<sub>Abridged from the real output. The full run also prints the shortlist, every loop transition, the validation checks and the forecast checks.</sub>

## Architecture in 10 seconds

```mermaid
flowchart LR
    KG["🧠 <b>Knowledge Graph</b><br/>knowledge_graph.py · learner.py<br/>weaknesses · evidence · outcomes"]
    H["🔎 <b>Harvester</b><br/>harvester.py · vanguard.py rank<br/>advisories → P(fail) per model"]
    S["🛠️ <b>Synthesizer</b><br/>loop.py<br/>agent builds the task · gate 1"]
    J["⚖️ <b>Judge</b><br/>validate.py · collect<br/>fairness checks · model runs · gate 2"]
    KG --> H --> S --> J
    J -- "results · forecast checks · retrain" --> KG
```

| Stage | What it does | Where |
|---|---|---|
| **Knowledge Graph** | Remembers every candidate, task, trial and outcome; learns which weaknesses and signals defeat which model | `knowledge_graph.py`, `ingest.py`, `learner.py` |
| **Harvester** | Fetches reviewed advisories and their fix patches from GitHub's public advisory database, reads each fix's shape, and scores every advisory for each model's chance of failing, with reasons and evidence | `harvester.py`, `vanguard.py harvest` / `rank` |
| **Synthesizer** | Writes a build package; an AI agent triages and builds a Docker task; a person approves it | `loop.py` |
| **Judge** | Fairness checks in Docker, model rounds behind budgets and gates, results fed back as training data | `validate.py`, `loop.py`, `vanguard.py collect` |

---

## Why this exists

Benchmark tasks that strong coding agents actually **fail** are expensive to find. Most candidate vulnerabilities turn
out to be easy: you build the task, validate it, pay for the model runs, and the agent solves it. Teams burn weeks and
budget on tasks that teach them nothing.

Vanguard turns every run into knowledge. It records **why** agents fail (the weakness, the conditions that trigger it,
the evidence behind it) in a knowledge graph, learns from each outcome, and ranks thousands of real advisories by how
likely each one is to defeat a given model. Then it drives the whole pipeline from that ranking to a validated,
accepted task, with a person approving every step that costs money.

## What it does

| | |
|---|---|
| 🧭 **Maps failure** | A typed knowledge graph of models, weaknesses, trigger conditions, signals, candidates, tasks and trials. Outcome edges carry evidence counts, so the graph can answer *"what defeats Model A, and how often was that confirmed?"* |
| 🎯 **Predicts failure** | Scores every advisory in a pool (1,928 real advisories in about 12 seconds, at no cost) with each model's chance of failing, the reasons behind the score, and how much evidence supports it. |
| 🔁 **Learns from every run** | New results flow back into the graph automatically. The predictor retrains, logs its accuracy, and checks every forecast it made *before* a task ran against what actually happened. |
| 🛡️ **Builds fair tasks** | Scripted validation (the real fix must pass twice, no fix must fail, a partial fix must land in between, nothing may leak) catches unfair tasks before any model runs. |
| 🚦 **Spends only with approval** | Paid steps (an AI build agent, model runs) need an explicit flag and a budget. Two human gates sit in the loop, and a solved round stops all further spending on that task. |

## The knowledge graph

Every finding is a node, and every relationship is typed and backed by evidence. Click a weakness and the graph lights
up its trigger conditions, the reasons the model gave for dismissing the risk, the experiments that isolated the cause,
and the tasks that expose it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/graph-detail-dark.png">
  <img alt="Selecting the primary weakness shows its trigger conditions, dismissal reasons, isolating experiments and full evidence" src="docs/assets/graph-detail-light.png" width="100%">
</picture>

**92 nodes · 125 typed relationships · 10 node types**: models, weaknesses, trigger conditions, evidence, dismissal
reasons, tasks (with a solved or failed ring), code entities, strengths, task-design implications and method.
The same graph ships as [`graph.json`](docs/graph.json) and as [`nodes.csv`](docs/nodes.csv) / [`edges.csv`](docs/edges.csv)
for your own tools.

The pipeline's machine graph uses the same model at scale (thousands of candidate advisories, every trial, every
prediction):

```mermaid
flowchart LR
    Candidate -- "HAS_SIGNAL" --> Signal
    Task -- "BUILT_FROM" --> Candidate
    Task -- "TARGETS" --> Weakness
    Task -- "MITIGATED_BY" --> Signal
    Trial -- "ON" --> Task
    Trial -- "BY" --> Model
    Weakness -- "DEFEATS (fails / runs)" --> Model
    Signal -- "DEFEATS (fails / runs)" --> Model
    Candidate -- "PREDICTED p(fail)" --> Model
```

## What the research found

Studied on real open-source vulnerabilities and on controlled variants that put parts of real fixes back into healthy
code (about 66 graded runs):

> **Primary weakness: a spotted risk dismissed with an untested assumption.** When a fix needs two changes (the
> security check, and the code that saves and reloads the data the check relies on), the model fixes the check every
> time. It *notices* the second path, dismisses it with an assumption it never tests, and stops once the existing tests
> pass.

| Evidence | Result |
|---|---|
| Tasks in the target shape | **8 of 8 failed on every run**; pass@3 = 0 on a real, unmodified codebase |
| Asked directly about the bug, no agent | Explains it correctly **6 / 6**: not a knowledge gap |
| One sentence in the report naming the second path | Solved **3 / 3**: not a skill gap |
| Generic reminders ("check your assumptions", "cover every path") | Solved **0 / 6** |
| One visible failing test on the second path | Solved **3 / 3**: it follows evidence, not reminders |
| Runs that tested the dismissed path before stopping | **0 / 12** |

Three secondary patterns are mapped too: a nearby real bug that captures the model's attention, scope that stops at
what the report describes, and verification that only re-checks the path already fixed. The full write-ups are in
[`findings/`](findings/).

## Sample output

Skim the dataset structure without running anything: [`examples/sample_vanguard_benchmark.json`](examples/sample_vanguard_benchmark.json)
is the summary of one demo run, with the knowledge-graph statistics, predictor accuracy, ranked shortlist, the built
benchmark task (files, validation record, every round's scores), the loop history and the forecast checks. An excerpt:

```json
"benchmark_task": {
  "id": "auth-gateway-16-demo-0016",
  "built_from": "GHSA-demo-0016",
  "files": ["environment/Dockerfile", "instruction.md", "solution/fix.patch", "solution/solve.sh",
            "solution/variants/check_only.patch", "solution/variants/producer_only.patch", "task.toml", "tests/test.sh"],
  "validation": { "status": "pass", "checks": [ { "id": "oracle", "status": "pass", "detail": "rewards [1.0, 1.0] (need 1.0 x2)" }, "..." ] },
  "rounds": [ { "MODEL_A": [0.64], "MODEL_B": [0.5] }, { "MODEL_A": [0.64, 0.64], "MODEL_B": [0.5, 0.5] } ],
  "verdict": "accepted"
}
```

## How the loop works

```mermaid
flowchart TD
    R["Rank advisories<br/><i>free</i>"] --> P["Build package<br/><i>free</i>"]
    P --> B["Agent triage + build<br/><b>paid</b> · budgeted"]
    B -- "REJECT" --> X1(["rejected"])
    B --> G1{{"Gate 1<br/>you approve"}}
    G1 --> V["Validate in Docker<br/>fix ×2 · no fix · partial fixes<br/><i>free</i>"]
    V -- "unfair task" --> X2(["dropped"])
    V --> M1["Round 1<br/>Model A ×1 + Model B ×1<br/><b>paid</b> · budgeted"]
    M1 -- "a model solves it" --> X3(["solved"])
    M1 --> G2{{"Gate 2<br/>you approve"}}
    G2 --> M2["Round 2<br/>2 more attempts each<br/><b>paid</b>"]
    M2 --> A(["accepted:<br/>failed every attempt"])
    M1 --> C["Collect · check forecast · retrain"]
    M2 --> C
    C -.-> R
```

Each `vanguard.py loop` call moves every candidate as far as it can go **without crossing a gate or spending money**.
State lives in the graph, so the loop can be interrupted and resumed at any point.

## Does the learning work?

Measured honestly: each repository's tasks are held out together, and the model is scored on tasks it never saw.

| Predictor | Brier score (lower is better) |
|---|---|
| Overall fail rate | 0.394 |
| Hand-tuned weights | 0.320 |
| **Vanguard, learned from outcomes** | **0.230** |

With an entire repository held out, it still gave the real task in the target shape an 84% chance of failure, and that
task did fail every run. Every `train` appends to an accuracy history, and every forecast made before a run is scored
against the result, so you can watch the predictor improve as tasks are added.

## Quick start

Python 3.9+ and nothing to install. `python3 main.py --demo` needs nothing else. Harvesting and ranking real
advisories need only network access (set `GITHUB_TOKEN` for higher GitHub limits). Docker and the Harbor evaluation
harness are needed only for validation and model runs. Before any task has been graded, `rank` uses the starting
weights; every graded task after that trains the predictor:

```bash
cd vanguard
cp -r private.example private      # then point paths.json at your data and models.json at your models
python3 vanguard.py harvest --since 2025-01-01 --pages 3 --fetch   # real advisories + fix patches from GitHub
python3 vanguard.py rank --top 25  # score every advisory; writes a shortlist with reasons and evidence
python3 vanguard.py doctor         # checks the machine, the data, Docker/Harbor and runs the test suite
python3 vanguard.py loop --select 3   # start the loop (free: writes build packages only)
```

| Command | What it does | Cost |
|---|---|---|
| `harvest` | Fetch real advisories (and fix patches) from GitHub's advisory database | free |
| `ingest` · `train` · `rank` · `report` | Build the graph, learn, rank, report | free |
| `collect` · `watch` | Record new results, check forecasts, retrain | free |
| `validate TASK` · `review TASK` | Fairness checks; reviewer sign-off on manual items | free (local Docker) |
| `loop` · `loop status` · `approve` · `reject` | Drive candidates end to end | gated |
| `loop --build --max-builds N` | Run the build agent | **paid** |
| `loop --run-models --max-model-runs N` | Run model rounds | **paid** |
| `doctor` · `export` | Health check; dump the graph as JSON | free |

Full documentation: [`vanguard/README.md`](vanguard/README.md).

## Built to be trusted

- **Every result is accounted for.** Crashed trials, timeouts and runs on an outdated version of a task are excluded by explicit rules, and those rules reproduce every result in the original study.
- **Validation can't overstate a task.** A task reaches `pass` only when every automated check passes and a reviewer has signed off on every manual item. Reviews can't override automated results.
- **Proven on real infrastructure.** Run against real Docker and Harbor, validation independently reproduced a human finding: a partial fix closed every attack, so the task was dropped.
- **No wasted spend.** A crashed trial re-runs only the crashed attempt and keeps valid results. One loop at a time. Safe concurrent writes to the graph. Atomic saves.
- **Works from a fresh clone.** A clean copy of this repository harvests real advisories from GitHub and ranks them with nothing but Python; the test suite runs in isolation from any private data.
- **Private by design.** The code names no real model or project. Real identities live in a git-ignored `private/` folder (or entirely outside the repository), with templates in `private.example/`.

### Tested end to end

30 automated tests (`python3 -m unittest discover -s vanguard/tests`) cover result parsing, the rules for which trials
count, the graph store, the learner, every validation verdict, and the **complete loop**, from ranking to an accepted
task, including a rejected build, a crashed trial and a model solving the task, using stand-ins for the build agent and
the harness. Further tests run the one-line demo from a clean environment, check the harvester on a real-shaped fix,
and rank advisories with no private pipeline and no history at all.

## Repository layout

```
├── main.py                  entry point: `python3 main.py --demo`, or any vanguard command
├── docs/                    animated tour (index.html) + interactive knowledge graph (graph.html), served by GitHub Pages
├── examples/                sample output of a demo run (summary JSON)
├── findings/                the research: failure analysis, results, weakness specs (anonymised)
└── vanguard/                the pipeline
    ├── vanguard.py          command line
    ├── harvester.py         advisories from GitHub, fix patches, the check / data-producer signature
    ├── knowledge_graph.py   SQLite graph store: nodes, typed edges, metrics, prediction history
    ├── schemas.py           typed record shapes (TypedDict): advisories, trials, validation, loop state
    ├── ingest.py            builds the graph; decides which trials count
    ├── learner.py           logistic regression with priors; held-out evaluation
    ├── validate.py          static and Docker fairness checks
    ├── loop.py              the end-to-end state machine with gates and budgets
    ├── demo.py              the one-line demo: real pipeline, synthetic data, stand-in agent and harness
    ├── catalog.json         curated weaknesses and mitigations
    ├── private.example/     templates for model identities, paths and task tags
    └── tests/               unit and end-to-end tests with stand-in agent and harness
```

## Honest limits

- The predictor is young: about 32 labelled tasks from 13 repositories, and most failures come from a single
  repository. The shortlist says so per candidate ("evidence: N tasks, M repositories"). High scores backed by one
  repository are hypotheses to test, not findings.
- The second model has few labelled tasks, so its predictions mostly reuse the first model's evidence.
- The build agent and paid model rounds are wired in and tested with stand-ins, but have not yet been exercised with
  a real agent.

## Roadmap

- More models through configuration alone: each one gets its own column and its own runs.
- Detection rules for the secondary patterns, so the ranker can target them directly.
- A real-task holdout to move the primary weakness from *supported* to *proven*.

<div align="center">
<br>
<sub>Built with the Python standard library · Knowledge graph rendered with D3 · Diagrams by Mermaid</sub>
</div>
