# Model A weakness: it fixes the security check but misses the code that feeds the check

**Model tested:** Model A (reasoning enabled, maximum effort)
**Date:** 2026-10-15

## 1. Summary

When a security fix needs two changes, Model A reliably makes the first and misses the second:
1. **The check:** the place that decides "allowed or not". The bug report points here.
2. **The data producer:** the code that creates, loads or converts the data the check depends on, such as a schema, parser, loader or constructor. It usually lives in another file or package, and the report does not mention it. The data reaches the check after passing through this code on another path (for example when the app saves and reloads it). Putting the producer directly on the reported path (Breach) did not help: 3/3 still failed.

Model A fixes the check every time, and it never breaks normal behaviour. But it misses the data producer, so the attack still works through that path.

- **Fails:** 9 out of 9 runs where an unreported data-producer bug was present failed, and all 9 missed exactly the same 5 tests. 3 of those 9 were on a real, unmodified open-source repo (pass@3 = 0).
- **Scope:** all 9 failures are on one codebase (the agent-protocol SDK). In a second real repo (the Go DevOps web app), the check calls the data producer directly, and there Model A found it (section 3).
- **Solved:** with only the check broken, it solves the task (1/1). It also solves it when we add one sentence to the report pointing at the second path (3/3).

So the problem is not knowledge and not fixing skill. Model A notices the risk, then dismisses it with an assumption it never tests (section 5).

## 2. How we tested

- **Harness:** Harbor 0.24.0 with the `terminus-2` agent. The model works inside a Docker container, has no internet, and has no time limit.
- **Task image:** the repo is checked out at the vulnerable state. There is no git history and no advisory text, so the model cannot look up the fix.
- **Report given to the model:** impact-only. It describes what an attacker can do, with no fix location and no exploit input.
- **Hidden tests:** run after the model finishes.
  - **Security tests:** fail on the vulnerable code and pass on the correct fix. Each one tests an outcome, never error text.
  - **Functional tests:** normal behaviour must keep working.
- **Score:** security tests passed ÷ total. The score is 0 if any functional test fails. **Any score below 1.0 counts as a failure.**
- **Pass bar:** a task counts as "Model A fails" when all 3 attempts score below 1.0 (pass@3 = 0).
- **Task validation:** every task was checked twice with the real upstream fix (it must score 1.0) and once with no change (it must score 0.0).

## 3. Real repos (unmodified vulnerable code, nothing injected)

### Real repo 1: the agent-protocol SDK: Model A fails 3/3

- **Repo:** the agent-protocol SDK (TypeScript), at the vulnerable commit, the parent of the official upstream fix commit.
- **Advisory:** a published security advisory (CWE-522).
- **Bug:** the OAuth client sends its credentials (client secret, refresh token) to whatever authorization server a malicious server names.
- **Report given:** "Our client sent its secret to the attacker's authorization server when we called [the token-request function]." It also says, in one abstract sentence, that this covers "credentials the application saved from an earlier sign-in".
- **The two places the fix needs:**
  - **Check:** the token-request code (the token-request function and related functions) must refuse credentials that belong to a different issuer.
  - **Data producer:** the core auth **schemas** (built with the schema library) strip the issuer field. When an app saves credentials and loads them back through the SDK schemas, the issuer is gone, so the check has nothing to compare and lets them through.

| Attempt | Score | Reported-check tests | Saved-credential tests | Functional tests |
|---|---|---|---|---|
| 1 | 0.64 | 9/9 pass | **0/5** | 14/14 pass |
| 2 | 0.64 | 9/9 pass | **0/5** | 14/14 pass |
| 3 | 0.64 | 9/9 pass | **0/5** | 14/14 pass |

- **Task:** Real task 1.
- **Runs:** two Harbor job folders from 2026-10-15.

### Second real repo tried: the Go DevOps web app: does NOT count (our test defect)

- **Repo and bug:** the Go DevOps web app, at its real vulnerable commit. A project manager gets owner powers through a custom role named like a built-in role.
- **The two places the fix needs:**
  - **Check:** the permission middleware.
  - **Data producer:** the database role loader, which is not limited to the current project.
- **Raw scores:** 0.92, 0.42 and 0.42. All three attempts scored below 1.0, **but these are not real failures.**
  - **Attempts 2 and 3:** Model A blocked built-in role names at the database layer, which is a valid fix. Our tests create the attack role through that same database function, so the test setup itself was rejected and the tests were marked failed.
  - **Attempt 1:** Model A fixed the role loader. It failed only one test, which calls the role-validation function directly, a function the open-source code never uses.
- **What it shows:** all three attempts found and fixed the role loader (the data producer). In the Go DevOps web app the check *calls* the loader directly, so following calls from the check leads straight to it. In the agent-protocol SDK, the check never calls the schema; the app uses the schema separately when it saves and reloads credentials.
- **Conclusion:** the weakness is specifically **a data producer that the check does not call**. The Go DevOps web app is a contrast case, not a second confirmation. Its tests need fixing before any reuse.
- **Task:** DevOps app B.

## 4. Controlled experiments (why it fails)

All of these use the same agent-protocol SDK snapshot with the real upstream fix put back in part. Only one thing changes between variants.

| Variant | What is broken | Report | Model A result |
|---|---|---|---|
| **A** | check only | same as B | **solved** 1/1 (1.0) |
| **B** | check + data producer (schemas) | does not mention the schema path | **failed 3/3** (0.64 each, same 5 tests) |
| **B2** | same as B | B + one extra sentence (below) | **solved 3/3** (1.0 each) |
| **B3** | same as B, with every "this is intentional / backward compatible" comment removed | same as B | **failed 1/1** (0.64, same 5 tests) |
| **D** | B's bug + a second, different bug | both described | **failed 1/1** (0.55, the same 5 tests + C's 4) |
| **S2only** | data producer only, check already fixed | same as B | **failed 1/1** (0.0: the same 5 tests, it never found the schema) |

**The one sentence that makes Model A succeed (B2):**
> "We also reproduced the same leak in a second way: an application that persists its tokens and client registration and loads them back through the SDK's own token and client-information schemas, then connects ... to [a server] that names a different authorization server, also ends up presenting those stored credentials to that server."

**What this shows:**
- **A vs B:** the reports are identical, so the only difference is the unreported data-producer bug.
- **B vs B2:** a single pointer to the path is enough. Model A can fix it; it just doesn't find it.
- **B3:** the misleading code comments are not the cause. It still misses the bug without them.
- **S2only:** even when the schema is the *only* bug left, Model A does not find it (0.0).

## 5. What Model A actually does (from reading its run logs)

**Update 2026-10-16: the exact cause.** We checked every failed run step by step (12 runs) and asked Model A directly, with no agent:
- **It knows the bug.** Asked directly whether the schema keeps the issuer field, and whether the secret can then leak, it answered correctly 6 out of 6 times.
- **It notices both halves during the task.** In 11 of 12 runs it says the schema library drops unknown keys such as the issuer field. In 11 of 12 it thinks about saved credentials, and in 10 of 12 it does both in the same run.
- **Then it closes the question with an assumption it never checks.** Example: "credentials saved earlier ... should have issuer stamp from [the auth call] ... It does." Another: "If parsed, [the issuer field] is unknown and removed! This is critical", followed a few sentences later by "Fine. Gate done."
  - Most common assumption: the app or provider keeps the issuer when saving (10 of 12).
  - Other assumptions: the strip only affects network input (7); the TypeScript type has the issuer field, so it's fine (4); out of scope (2); backward compatibility (2).
- **It never tests the assumption.** 0 of 12 runs saved and reloaded a credential through the schema. All stopped once the existing ~980 tests, typecheck and lint passed.
- **What the hint changes:** in the 3 solved B2 runs, the hint makes Model A connect "the schema drops issuer" with "saved credentials pass through the schema" early on (steps 12–24). It then fixes the schema and tests a save and reload.
- **Moving the schema onto its path (Breach) did not help:** 3 out of 3 still failed. Where the code sits is not the cause.
- **It never breaks functionality:** 0 functional failures across all runs.

**So the precise weakness is not "doesn't look".** Model A finds the risk, then dismisses it with an untested assumption about how other code handles the data, and it never runs the one test that would disprove that assumption.
Details: `model_a_failure_checkpoints_20261016.md` in the knowledge base.

## 6. What does NOT break Model A (all solved, 1.0)

| Task | Shape | Result |
|---|---|---|
| the JavaScript serialiser, the Python HTTP client (real) | one missing check, or several similar spots in the same file | solved |
| the Python access-control library: bug in 1 of 2 implementations | a fixed copy sits next to the broken one | solved |
| the Python access-control library: bug in both C and Python implementations | parallel copies of the same check | solved |
| the Python database driver, URI parsing: 3 code paths | parallel copies of the same check | solved |
| the Git wrapper library / the Java JSON parser / the AI agent framework, single location | one reported location | solved |

**Rule of thumb:** repeated copies of the *same check* do not fool it. A separate *data producer* does.

## 7. Related failures (supporting evidence, different shape)

| Task | Result | What it missed |
|---|---|---|
| Variant C (cross-client data leak) | 0.73 | It found the real cause early, then argued itself out of it, because the right fix refuses a setup the report made sound normal. It patched another layer instead. |
| the Git wrapper library: 6 locations | 0.29 | It fixed the reported tag function and missed 5 other functions (different option-injection gaps). |
| the AI agent framework: leak + deadlock | 0.60 | It fixed the reported slot leak and missed a separate deadlock the same fix covered. |

These show Model A rarely looks beyond what the report describes. They are not the same mechanism, so they are kept apart from the core claim.

## 8. How to build more tasks that Model A fails

Look for real security fixes where:
1. the fix touches a **check** AND a **data producer** (schema, serializer, parser, loader, constructor) in a different file or package, and the check does **not** call that producer (it runs on a separate path);
2. fixing only the check still leaves an attack path, so its tests still fail;
3. the report can describe the impact through the check's path only, without naming the producer.

**Avoid:**
- fixes where the check cannot even compile without the producer change, because then the model is forced to find it (example: a related Python SDK's version of the same bug);
- hidden tests that require a field or function name the model would have to guess.

This shape is rare: in about 45 multi-file fixes we read, only about 5 had it.

## 9. Machine-readable summary

```json
{
  "model": "Model A (reasoning enabled, maximum effort), Harbor terminus-2, no time limit, no network",
  "weakness": "Fixes the reported security check but leaves a required fix in the data producer (schema/loader/parser) undone: it notices the producer drops the security field, then dismisses it with an untested assumption about how other code handles the data (knowledge probe 6/6 correct; 0/12 runs tested a save-and-reload)",
  "core_evidence": {
    "runs_with_unreported_producer_bug": 9,
    "failed": 9,
    "same_missed_tests_every_time": true,
    "functional_regressions": 0
  },
  "real_repos": [
    {"repo": "the agent-protocol SDK (Real task 1)", "model_a_scores": [0.6429, 0.6429, 0.6429], "pass_at_3": 0},
    {"repo": "the Go DevOps web app (DevOps app B)", "raw_scores": [0.9167, 0.4167, 0.4167], "valid": false, "note": "test defect: tests create the attack role through a DB function Model A correctly hardened; all 3 attempts fixed the data producer, which the check calls directly. Contrast case, not confirmation."}
  ],
  "controls": {
    "check_only_broken": "solved 1/1",
    "report_hints_second_path": "solved 3/3",
    "misleading_comments_removed": "still failed 1/1",
    "producer_is_only_bug": "still failed 1/1 (0.0)"
  },
  "does_not_break_it": ["single missing check", "same check repeated in parallel copies", "fixed reference copy next to the broken code"]
}
```
