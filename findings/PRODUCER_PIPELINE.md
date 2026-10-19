# Producer pipeline: finding real tasks that Model A fails

This pipeline turns our Model A findings into a repeatable search for real-world tasks. Background evidence is in `../MEETING_CONTEXT_2026-10-16.md`.

## The target shape (the recipe)

A real security fix that needs **two** changes:
1. **Check:** the code that decides "allowed or not". The report's scenario leads here.
2. **Data producer:** in **another file**, the code that shapes or reloads the data the check relies on: a schema, serializer, model, loader or normaliser. Today it **drops or alters a security field** (issuer, owner, tenant, scope, role, ...).

Two conditions must also hold:
- **The boundary:** the data reaches the check through a **save/load, serialize/parse or cache boundary**, not through a direct call from the check.
- **The tests:** the repo's existing tests pass whether or not the producer is fixed.

**Why it works:** Model A fixes the check, notices the producer, dismisses it with an untested assumption, and stops on a green test suite.
- In this shape it failed 19 out of 19 runs. In the real task (Real task 1, the agent-protocol SDK) it failed 3 out of 3.
- It solves the task when the check calls the producer directly (the Go DevOps web app, 3/3).
- It also solves the task when the report or a visible failing test points at the second path (3/3 each).

## Stages

| # | Stage | How | Output |
|---|---|---|---|
| 1 | **Pool** | Public security advisories with a fix commit (all ecosystems), plus earlier miner pools | candidates |
| 2 | **Static signature filter** | The finder script (optionally in harvest mode: advisories since 2025-01-01, 8 pages). Reads each fix patch and requires a producer file and a separate check file. Adds points for a shared security field, field-dropping constructs (schema strip modes that drop unknown fields, ignore-extra-fields settings, omit-when-empty serialisation tags, ...), storage-boundary words and round-trip upstream tests. Subtracts points when the check calls the producer directly, or when a Python model is both the type and the storage format. Roles are a regex guess: this stage is for **recall**. | a dated candidate list, a shortlist, and one brief per advisory |
| 3 | **Triage review** (agent, read-only) | One reviewer per brief reads the real patch and answers the 8-question checklist in the brief. The critical question is whether a check-only fix still leaves a reachable attack through the producer path. | BUILD / MAYBE / REJECT, with file:line evidence |
| 4 | **Build** | Builder agent, following the task build spec (including the Docker safety rule). Uses the real vulnerable commit (the parent of the fix), an impact-only report that describes only the check's path, and hidden tests split into "check path" and "producer path". Tests may use only APIs and fields that exist in the vulnerable tree. | a task folder |
| 5 | **Validate** | Harbor checks: the oracle scores 1.0 twice and nop scores 0.0. Three more checks: a **check-only partial** must score strictly between 0 and 1; a **hardened fix** (one that also hardens the code the tests import) must score 1.0; and the test setup must not depend on code a model may legitimately harden (the DevOps app lesson). | validation record |
| 6 | **Run** | The Harbor run script: Model A only, 3 parallel attempts, no early exit (no time limit, offline) | pass@3 per task |
| 7 | **Learn** | Log every run to the knowledge base, then adjust the finder's weights against each outcome. The known positive must stay above the shortlist line: the finder prints a regression check for Real task 1. | updated weights |

## Reject rules (from what went wrong before)

- **The check calls the producer directly, and the data flow is easy to read.** Model A solves this (the Go DevOps web app).
- **A check-only fix can't compile or run without the producer change.** The model is then forced to find the producer, as in a related Python SDK's twin of the same bug.
- **A Python model is both the type and the storage format,** so the producer can't be separated from the check.
- **Hidden tests would need a field or function name the model has to guess** (the related SDK's session pair).
- **A check-only fix already blocks every attack.** The producer path isn't exploitable on its own (the social-login library).
- **The vulnerable commit already ships the advisory's regression tests.**

## Current status

- **Static filter on the 250-advisory pool:** 43 candidates have a producer + check pair, and 28 are shortlisted. The known positive (Real task 1) is kept.
- **Wider harvest:** advisories since 2025-01-01, all ecosystems, running now.
- **Next:** triage the top 20 briefs, build the BUILD verdicts, and run Model A 3 times on each.
