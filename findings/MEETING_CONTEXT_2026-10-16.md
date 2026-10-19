# Model A: tasks run, weaknesses and failure patterns

**Status as of:** 2026-10-16, 13:00
**Model studied:** Model A (reasoning enabled, maximum effort)

---

## 1. Summary

We ran Model A on **27 security-fix tasks**:
- 10 built from real, unmodified open-source vulnerabilities;
- 17 controlled variants, built by putting back parts of real upstream fixes into healthy code.

Altogether that is **about 66 graded runs**. We identified **one primary weakness**, reproduced it on a real repository (pass@3 = 0), isolated its exact cause, and found **three secondary patterns**.

**Primary weakness: dismissing a spotted risk with an untested assumption.**
- **The shape:** some fixes need two changes, one to the security check and one to the code that produces or transforms the data that check relies on.
- **What Model A does:** it fixes the check every time. It also notices that the data producer drops the security-relevant field, but it settles the question with an assumption it never verifies, and it never runs the one test that would disprove it.
- **Strength of the evidence:**
  - 19 out of 19 runs failed in this shape, always on the same 5 tests, including 3 out of 3 on the real, unmodified agent-protocol SDK.
  - Generic instructions do not fix it (0/6 solved); a concrete failing test that exercises the second path does (3/3 solved).
  - It is not a knowledge gap: asked directly, it explains the bug correctly 6 out of 6 times.
  - It is not a skill gap: with one sentence in the report pointing at the path, it solves the task 3 out of 3 times.

**Secondary patterns:**
1. When a nearby real bug also explains the symptom, it fixes that one and argues itself out of the correct fix.
2. Its scope stops at what the report describes; it misses unmentioned flaws of a different kind.
3. Its verification only goes through the path it already fixed, and it stops once the existing tests pass. A visible failing test on the second path is the one intervention that flips the result (3/3).

---

## 2. How tasks are run and scored

| Item | Setting |
|---|---|
| **Harness** | Harbor 0.24.0, `terminus-2` agent. The model works in a Docker container through a terminal. |
| **Network** | None inside the task container. |
| **Time limit** | None. Timeouts would never count as failures; no run timed out. |
| **Task image** | The repository at the vulnerable state, one clean commit with no history and no advisory text, so the fix cannot be looked up. |
| **Report given to the model** | Impact-only: what an attacker can do, with no fix location, file names or exploit input. Constraints: fix the attack and close variants, keep legitimate use working, do not modify or add tests, do not look up the upstream fix. |
| **Hidden tests** | Added only after the model stops. Security tests fail on vulnerable code and pass on a correct fix, and they check outcomes, never error text. Functional tests make sure legitimate behaviour still works. |
| **Score** | Security tests passed ÷ total, or 0 if any functional test fails. **Any score below 1.0 is a failure.** |
| **Acceptance bar** | All 3 attempts score below 1.0 (pass@3 = 0). |
| **Task validation** | The real upstream fix scores 1.0 twice, no change scores 0.0, and the partial score of a check-only fix is measured where relevant. |

---

## 3. Every task we ran

### 3.1 Real, unmodified vulnerabilities

| # | Task | Language | What the fix needs | Model A result |
|---|---|---|---|---|
| 1 | the Python auth library: JWT signature bypass | Python | one missing signature check | solved 3/3 |
| 2 | the Python streaming tool: redirect to `file://` | Python | scheme check on redirects | solved 3/3 |
| 3 | the Python environment tool: integrity of bundled packages | Python | integrity verification of bundled packages | solved |
| 4 | the Python web server: chunked-trailer smuggling | Python | trailer parsing limits | solved |
| 5 | the Python access-control library: format-string disclosure | Python + C | guard in both the C and the Python implementation | solved |
| 6 | the Java JSON parser: async number length | Java | length limit in the async parser | solved |
| 7 | the JavaScript serialiser: typed-array length DoS | JavaScript | length checks at adjacent deserialisers | solved |
| 8 | the Python HTTP client: decompression chunk DoS | Python | chunk limits in 4 codecs, sync and async | solved |
| 9 | **Real task 1: the agent-protocol SDK, OAuth issuer confusion** | TypeScript | (a) issuer check on token requests; (b) core schemas that strip the issuer field | **failed 3/3** (0.64 ×3), pass@3 = 0 |
| 10 | the Go DevOps web app: custom-role escalation | Go | (a) permission middleware; (b) role loader not scoped to the project | solved 3/3 |

### 3.2 Controlled variants (single change at a time)

All agent-protocol SDK variants use the same healthy SDK snapshot, with the real upstream fix (one upstream fix commit for F1, another for F2) put back in parts.

| # | Task | What is broken | What the report says | Model A result |
|---|---|---|---|---|
| 11 | **Variant A** | F1 check only | credentials sent to the attacker's server via the token-request function | **solved 3/3** |
| 12 | **Variant B** | F1 check + schemas that strip the issuer field | same as A | **failed 3/3** (0.64 each) |
| 13 | **Variant B2** | same as B | A's report + one sentence describing save-and-reload through the SDK schemas | **solved 3/3** |
| 14 | **Variant B3** | same as B, with every "intentional / backward compatible" code comment removed | same as A | **failed 1/1** (0.64) |
| 15 | **Variant S2only** | schemas only (check already fixed) | same as A | **failed 3/3** (0.0 each) |
| 16 | **Variant Breach** | same as B, plus the stripping schema placed directly inside the token-request function | same as A | **failed 3/3** (0.64 each) |
| 17 | **Variant Bverify** | same as B | + "do not rely on assumptions about how other code handles this data; check each against the code" | **failed 3/3** (0.64 each) |
| 18 | **Variant Baudit** | same as B | + "make sure the protection holds for every way credential data can reach the token request" | **failed 3/3** (0.64 each) |
| 19 | **Variant Bvistest** | same as B, plus one visible failing save-and-reload test in the repo | same as A | **solved 3/3** |
| 20 | Variant C | F1 check + F2 (one user's tool output delivered to another user) | both described | failed 2/3 (0.73, 0.73; one run solved 1.0) |
| 21 | Variant D | B + F2 | both described | failed 1/1 (0.55) |
| 22 | Variant E1 | F2 at the one reported site | F2 report | solved 1/1 |
| 23 | Variant E3 | F2 at all 3 sites | same F2 report | failed 1/1 (0.33) |
| 24 | the Git wrapper library S1 / SN | S1: tag creation only; SN: 6 functions (option-injection gaps) | tag-creation scenario only | S1 solved; SN failed (0.29) |
| 25 | the Java JSON parser S1 | limit in one input-source parser only (sibling parsers already safe) | that parser's scenario | solved |
| 26 | the AI agent framework S1 / SN | S1: concurrency-slot leak; SN: leak + shared-limiter deadlock | streamed-disconnect scenario | S1 solved; SN failed (0.60) |
| 27 | DevOps app A (control) | middleware only | same as #10 | solved 1/1 |

---

## 4. Primary weakness: a spotted risk dismissed with an untested assumption

### 4.1 Description

Some vulnerabilities need two coordinated changes:
1. **the check:** the code that decides "allowed or not". The report's scenario leads here.
2. **the data producer:** the code that creates, converts or reloads the data the check relies on, such as a schema, serializer, parser or loader. If it drops or alters the security-relevant field, the check is bypassed on that path.

Model A reliably fixes the check. During its investigation it **does notice** that the producer drops the field, and it **does think about** the path where data is saved and later reloaded. It then **closes the question with an assumption about how other code behaves** ("the application keeps that field when it saves"), **never verifies the assumption**, and stops once the existing test suite passes. The attack stays open through the producer.

### 4.2 Where we observed it

**Real repository:** the agent-protocol SDK (Real task 1, task 9).
- The OAuth client is supposed to send credentials only to the authorization server they belong to, and each credential carries an issuer stamp for this.
- The SDK's own schemas (the client-information schema and the token schema) use a strip mode that drops unknown fields, so the stamp is removed when an application loads saved credentials back through them.
- The check then sees "no issuer" and, by design, allows the credential, so a malicious server can collect the secret.
- **Model A fixed the check in all 3 attempts and left the schemas unchanged in all 3.**

**Every run in this shape failed on exactly the 5 "credentials persisted by the application and validated on load with the SDK schemas" tests, and passed all functional tests:**

| Task | Runs failed / run | Score |
|---|---|---|
| Real task 1 (#9) | 3/3 | 0.64 each |
| Variant B (#12) | 3/3 | 0.64 each |
| Variant B3 (#14) | 1/1 | 0.64 |
| Variant S2only (#15) | 3/3 | 0.0 each (the schemas are the only bug) |
| Variant Breach (#16) | 3/3 | 0.64 each |
| Variant D (#21) | 1/1 | 0.55 (plus the F2 miss) |
| Variant Bverify (#17) | 3/3 | 0.64 each |
| Variant Baudit (#18) | 3/3 | 0.64 each |
| **Total** | **19/19** | |

### 4.3 How we isolated the cause

Each step changes one thing and reads the effect:

| Question | Experiment | Result | Conclusion |
|---|---|---|---|
| Is the task fair and solvable? | Variant A: check only | solved 3/3 | yes |
| Is the producer what causes the failure? | Variant B vs A: same report, producer also broken | 3/3 fail vs 3/3 solve | yes |
| Can Model A fix the producer at all? | Variant B2: one sentence pointing at save-and-reload | solved 3/3 | yes, once pointed at it |
| Do misleading "intentional" comments cause it? | Variant B3: comments removed | still failed | no |
| Can it find the producer when that is the only bug? | Variant S2only | failed 3/3 (0.0) | no |
| Is it about where the code sits (off its path)? | Variant Breach: schema placed inside the reported function | still failed 3/3; it read the helper and schema dozens of times | no |
| Does it know how the schema behaves? | Direct questions, no agent, 3 tries each | "issuer is removed" 3/3; "the secret leaks" 3/3, with the correct explanation | yes: not a knowledge gap |
| Does it notice during the task? | Step-by-step reading of 12 failed runs | noticed the strip in 11/12, thought about saved credentials in 11/12, both in the same run in 10/12 | yes |
| What stops it from fixing? | Same analysis | dismissed with an assumption in 10/12; tested a save-and-reload 0/12; stopped on the existing tests + typecheck + lint in 12/12 | untested assumption |
| Does a reminder to check assumptions help? | Variant Bverify | failed 3/3 | no |
| Does a reminder to cover every data path help? | Variant Baudit | failed 3/3 | no |
| Does a concrete failing test help? | Variant Bvistest | solved 3/3 | yes; caveat: the test also points at the schema |
| Does the weakness apply when the check calls the producer directly? | the Go DevOps web app (#10) | solved 3/3; the loader is called by the check, and the data flow is direct | no: contrast case |

### 4.4 What Model A actually says (quotes from its runs)

- "If parsed, [the issuer field] is unknown and removed! This is critical." A few sentences later: "Fine. Gate done."
- "credentials saved earlier there should have issuer stamp from [the auth call]… It does."

**Why it dismissed the risk** (out of 12 failed runs; one run can give several reasons):

| Reason | Runs |
|---|---|
| "The application/provider keeps the issuer when saving" | 10 |
| "The strip only affects network input" | 7 |
| "The TypeScript type already includes [the issuer field]" | 4 |
| "Out of scope" | 2 |
| "Backward compatibility" | 2 |

**How it decided it was done:** in all 12 runs it ran the existing ~980 repository tests, typecheck and lint. None of these save and reload a credential, so everything passed.

**What the one-sentence hint changes:** in the 3 solved runs (B2), Model A joins "the schema strips issuer" and "saved credentials pass through the schema" into one thought early, at steps 12–24. It then fixes the schema, and in 2 of 3 runs it writes its own save-and-reload check.

### 4.5 Precise trigger conditions

Model A fails when **all** of these hold:
1. The fix needs a change in a data producer (schema, serializer, parser, loader, normaliser) **as well as** in the check.
2. The producer drops or alters the security-relevant field **across a time or storage boundary**, i.e. data that is saved now and loaded later, rather than a direct call from the check.
3. The report describes the impact through the check's path and does not describe that second path concretely.
4. The repository's existing tests pass whether or not the producer is fixed.

It does **not** fail when the producer is called directly by the check with a straightforward data flow (the Go DevOps web app), when one sentence describes the second path (B2, 3/3), or when a visible failing test exercises the second path (Bvistest, 3/3). Generic reminders to check assumptions (Bverify) or to cover every data path (Baudit) do **not** help (0/6).

---

## 5. Secondary pattern A: a nearby real bug captures it, and it argues itself out of the correct fix

### 5.1 Description

The report describes one cause. The code also contains a second, real bug that explains the same symptom. Model A identifies the reported cause early, then talks itself out of fixing it, typically because the correct fix refuses a setup that the report describes as normal use. It fixes the alternative bug instead and stops.

### 5.2 Where we observed it

**Variant C (#20): failed 2 of 3 runs (0.73, 0.73); one run solved it (1.0).** In the failed runs:
- **Scenario:** one user's tool output is delivered to a different user when one server instance is shared.
- **What Model A did:**
  - It named the real cause at step 31: a second connection overwrites the shared server's single transport.
  - It then dismissed this more than 10 times ("If the same [shared server instance] is connected to one transport only, okay"), and ruled out the correct fix, refusing a second connection, as something that "would break legitimate use".
  - It built a large fix for a request-ID collision in the shared transport instead. That passed the 2 shared-transport tests and failed all 4 shared-server tests.

**Variant E3 (#23), 0.33.**
- **Setup:** the same report, with the bug at all 3 upstream sites.
- **What Model A did:** exactly the same. It fixed the shared-transport issue and left the reported shared-server case open.

**Controls and combinations:**
- **Variant E1 (#22):** the same bug at only the reported site, with no alternative bug available. Solved.
- **Variant D (#21):** the same miss as C, plus the primary weakness.

### 5.3 Conditions

1. A second real defect elsewhere also explains the reported symptom.
2. The correct fix for the reported cause restricts something the report presents as a normal deployment.
3. Reproducing the bug needs a concurrent, multi-client setup. Model A wrote no concurrent reproduction in 194 steps.

---

## 6. Secondary pattern B: scope stops at what the report describes

### 6.1 Description

When an upstream fix closes several distinct flaws but the report describes only one scenario, Model A fixes that scenario cleanly and does not look for flaws of a **different kind** elsewhere.

### 6.2 Where we observed it

**The Git wrapper library SN (#24), 0.29.**
- **Report:** tag creation with an attacker-influenced name makes git read an arbitrary file.
- **What it did:** fixed tag creation.
- **What it missed:** 5 other functions with option-injection gaps (diff, index diff, blame with two different options, and remote listing).
- **Control:** the Git wrapper library S1 (tag creation only) was solved.

**The AI agent framework SN (#26), 0.60.**
- **Report:** a concurrency-limited model keeps its slot when a streamed request ends early.
- **What it did:** fixed the slot leak.
- **What it missed:** a deadlock when one limiter is shared by an agent and its model.
- **Control:** the AI agent framework S1 (the leak only) was solved.

### 6.3 Contrast: what it does find

It reliably finds **repeated copies of the same flaw**, even across files and languages:
- the Python access-control library: C and Python implementations;
- the Python database driver: 3 parsing paths;
- the Python HTTP client: 4 codecs, sync and async;
- the JavaScript serialiser: adjacent deserialisers.

The miss is specific to **different kinds** of flaws that the report does not suggest.

---

## 7. Secondary pattern C: self-confirming verification

### 7.1 Description

Model A decides it is done once:
- the repository's existing test suite, typecheck and lint pass, and
- its own reproduction **of the reported scenario** passes.

Its probes go through the same path it already fixed. It does not write a test for the second path it considered and dismissed, even when it has noticed that path.

### 7.2 Where we observed it

- **All 12 analysed failures of the primary weakness:** none tested a save-and-reload; all stopped on the existing tests.
- **Variant C:** no concurrent multi-client reproduction.
- **The flip side:** where it did test the second path (2 of the 3 B2 runs), it fixed it. With a visible failing test for that path in the repository (Bvistest), it solved 3/3, while generic written reminders solved 0/6.

---

## 8. Strengths observed (useful for task design)

- **Never breaks legitimate functionality.** 0 functional-test failures across all graded runs; its fixes are conservative.
- **Strong on single-location and repeated-pattern fixes,** including across files and C/Python.
- **Strong when the report names the path,** even abstractly with one sentence (B2).
- **Traces call graphs well.** It finds every function that calls into the check, and every function the check calls (the Go DevOps web app).
- **Persistent:** runs of 100–200 steps with no timeouts.

---

## 9. Implications for task design

Tasks most likely to defeat Model A combine:
1. **A data-producer site plus a check site.** The producer transforms or drops the security field across a save/load or serialize/parse boundary, not through a direct call from the check (primary weakness).
2. **An impact-only report** that leads to the check and does not describe the second path concretely.
3. **Repository tests that pass either way,** so its usual "done" signal is reached early (pattern C).
4. Optionally, **a nearby real defect that explains the same symptom,** with a correct fix that restricts something presented as normal (pattern A).

These are rare in real advisories: roughly 5 of 45 multi-file fixes we reviewed had the producer shape.

**Task-quality safeguards we apply:**
- tests check outcomes, never error text or internal names;
- test setup never depends on code the model may legitimately harden;
- every task is validated with the real fix (1.0, twice) and with no change (0.0).

---

## 10. In progress

- **Settled:** generic instructions (Bverify, Baudit) failed 6/6, while a concrete failing test (Bvistest) solved 3/3. Model A follows evidence, not reminders: it needs an executable signal that contradicts its assumption.
- **A second real repository with the primary-weakness shape,** to reach pass@3 = 0 on another codebase.

---

## 11. Reproduction

Real task 1, its hidden tests, the official fix, all 3 Model A runs (step logs, terminal recordings, per-test results) and a setup checker are packaged in the reproduction bundle, with step-by-step instructions to re-run it.
