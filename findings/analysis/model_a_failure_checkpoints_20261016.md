# Model A failures on the agent-protocol SDK tasks: checkpoint analysis (2026-10-16)

**Scope:** 12 failed trials and 3 solved B2 controls. The Variant S2only job of 2026-10-16 11:03 is excluded because none of its trials has a `reward.txt`.
**Method:** a scripted keyword scan of each `trajectory.json`, then every hit read by hand. C1 counts as "yes" only when the schema definition or body actually shows up in an observation. Many terminal screens are truncated, so C1 is a lower bound. Quotes are verbatim except for identifiers in [brackets], and step numbers are `step_id`.

## Per-run table (failed runs)

| Run (trial) | C1 schema read | C2 strip noticed | C3 round trip | C4 decision | C5 save/reload probe | C6 stop |
|---|---|---|---|---|---|---|
| 1126-t1 (hUJz) | no | yes, wire/DCR [34,42] | yes [44] | assumes stamp survives | none | 900 tests+tc+lint |
| 1553 (BXkV) | **yes** [40-41] | yes, type-only [43,62] | yes [55] | assumes round-trip; casts | none | 900 tests+tc+lint |
| 1553 (i9ys) | no | yes, DCR [54] | yes [48,54] | out of scope | in-memory only | 900 tests+sim |
| B 1239 (2uPP) | no | yes [91] | yes [36,94] | assumes round-trip; "fields preserved" | none | 980 tests+tc+lint |
| B 1358 (4VCV) | no | yes, type-only [35,53] | yes [32] | assumes round-trip | none | 980 tests+tc+lint |
| B 1358 (dWoa) | def line only [36] | yes [31,62] | yes [33,76] | "Not our issue" | in-memory only | 980 tests+repro |
| B3 1526 (3oTW) | adjacent lines [49] | yes, wire [76,89] | yes [77,94] | assumes round-trip | none | 980 tests+tc+lint |
| D 1258 (EGCo) | no | **no** | yes [118,236] | legacy/back-compat | none | 4 suites+lint |
| S2only 1526 (Ndwu) | **yes** [98] | yes, hypothesis [37,55] | yes [106] | other: legacy; fixes metadata | in-memory only | 982 tests+repro |
| Breach (9DB3) | **yes** [42,48] | yes [52] | yes [53] | harmless; check moved earlier | none | 980 tests+tc+lint |
| Breach (BnxX) | **yes** [34-37] | yes [38] | **no** | harmless; check moved earlier | none | 980 tests+tc+lint |
| Breach (hY5y) | no | yes [42] | yes [17,39] | harmless + assumes round-trip | none | 980 tests+tc+lint |

### Key quotes

- **1126-t1 [44]:** "credentials saved earlier there should have issuer stamp from [auth call]. We need ensure [auth call] saved credentials stamped. It does." It stopped one step later: "No further action is needed."
- **B 2uPP [91]:** "[client-information schema] likely `.strip()` and does not include issuer. But [stored client-information type] adds issuer at type level, so runtime object can include. Our cast works. Good." It never opened the schema after writing "Let's inspect schema".
- **B 2uPP [94]:** "is there any chance our patch does not prevent credential leak … because [provider's client-information getter] returns credentials with an `issuer` field that gets stripped? We call directly before any serialization; fields preserved. Good." In the same step it ruled out a probe: "Task says do not modify or add tests; we shouldn't."
- **B dWoa [76]:** "Not our issue."
- **B3 [94]:** "A provider implementation that round-trips verbatim is protected." At [49] it printed the core auth module lines 230–295 (the stored-credential types), about 10 lines below the schema body.
- **S2only [37]:** "maybe SDK silently ignores [expected-issuer option] because [client-information schema] strips unknown `issuer` field on storage or save? Let's inspect [client-credentials provider] constructor … object literal is not schema-validated. Good." At [98] the token schema's `.strip()` body was on screen, and the next step did not mention it. At [105] it rejected fail-closed handling because "existing unit tests explicitly expect back-stamping".
- **Breach 9DB3 [53]:** "[client-information normalizer] strips issuer for [token-request executor], fine."
- **Breach hY5y [42]:** "If parsed, `issuer` is unknown and removed! This is critical." A few sentences later in the same step: "Fine. Gate done."

## Solved B2 controls

| Run | C1 | C2+C3 (joined) | C4 | C5 | C6 |
|---|---|---|---|---|---|
| 1347 (mJTo) | yes [32] | [24], before reading the schema | fixes core schema | **yes** [121], `[token schema].parse({…issuer})` | 4 packages + dist check |
| 1527 (HpixT) | yes [14,16] | [12–14] | fixes core schema [72] | **yes** [91,96,97] | tests+tc+lint+attack sim |
| 1527 (WPZA) | yes [20,23] | [18–19] | fixes core schema | no (token-request function probe only [129]) | targeted tc/tests/lint |

- **1347 [24]:** "If app persists full stored object including issuer then parses via [token schema] (wire schema), issuer stripped, losing binding."

**What the hint changes:** it joins C2 and C3 in a single thought, within the first 12–24 steps and before the schema is opened. After that, C1, the fix, and (in 2 of 3 runs) C5 follow. The hint does not supply knowledge of `.strip()`; the failed runs already show that knowledge.

## Counts (12 failed runs)

| Checkpoint | Count |
|---|---|
| C1 schema body or definition on screen | 4 yes, 2 partial, 6 no |
| C2 strip noticed (the schema library's `.strip()` / unknown keys drop `issuer`) | **11/12** |
| C3 persisted-credential round trip considered | **11/12** |
| C2 and C3 in the same run | 10/12 |
| C2 explicitly linked to the app's save→load path | 2/12 (2uPP [94], S2only [37]); both dismissed after checking the wrong object |
| C4 "assume the stamp survives / provider round-trips" | 10/12 |
| C4 also called out of scope or provider responsibility | 2 |
| C4 also legacy / back-compat | 2 |
| C4 strip judged harmless (wire input or the agent's own normalize) | 7 |
| C4 strip treated as a TypeScript typing or cast issue | 4 |
| C5 save/reload through the SDK schemas | **0/12** |
| C5 any probe at all | 3/12 (all with the in-memory client-credentials provider) |
| C6 stopped on green existing suite (900–982) + typecheck/lint | 12/12 |
| C6 also ran an in-memory exploit repro | 3/12 |

C4 labels overlap, so those rows add up to more than 12.

## Where the chain breaks

**The most common break is at C4: the agent links the strip to the round trip and dismisses it.** In 10 of 12 runs, the agent sees the strip and thinks about saved credentials, but keeps them as two separate facts. It closes the round-trip question with an unverified assumption: "the auth call stamps on save → the provider round-trips → the stamp is present." It closes the strip question by giving it a harmless frame: wire/DCR input, its own normalizer, or a TypeScript cast. It never asks whether the *application* loads saved data *through* the stripping schema.

## Verdicts on the candidate causes

1. **Never considers the round trip: refuted (measured).** 11 of 12 runs reason about saved or persisted credentials and round-tripping, often at length (S2only: dozens of steps). Only Breach BnxX does not. This corrects §5.2 of `MODEL_A_WEAKNESS_2026-10-15.md`, which says it "never asks where the check's data comes from". It does ask, and it answers with an assumption.
2. **Notices, then dismisses: supported; this is the dominant cause (measured).**
   - Dismissal counts are in the C4 rows above: 10 assume round-trip, 2 out of scope, 2 legacy, 7 harmless strip, 4 type/cast.
   - Evidence of mechanism (inferred): the type-level stamp `Stored… = … & { issuer?: string }`, and its doc comment saying the SDK writes the stamp before the save-client-information hook, appear to reassure the agent. Four runs explicitly say "the type includes issuer / cast works".
3. **Verification gap: present but secondary (C5 measured, causal role inferred).**
   - 0 of 12 failed runs probe a schema round trip, and all 12 stop on the green existing suite. So this gap is universal.
   - But probes come *after* the C2–C3 link, and one control (WPZA) solved with no schema probe. "Do not modify or add tests" suppressed at least one probe (2uPP [94]).
   - So the gap explains why the wrong assumption is never caught, not why it was made.
4. **Knowledge gap about the schema library's `.strip()`: refuted (measured).**
   - 11 of 12 runs correctly state that the schema strips unknown keys, including `issuer`. Breach hY5y: "[Schema-library] object probably strips unknown keys by default, unless `.passthrough()`".
   - The B2 runs predicted the strip before reading the schema.

## Recommended experiment

**Trajectory replay with a nudge at step N.** The dominant cause is the moment of dismissal, so that moment should be tested directly. Replay each failed trajectory up to the step where it writes the assumption, then inject one neutral sentence that does not say where the bug is, for example: "Verify that assumption against the code before relying on it." Candidate injection points:
- 1126-t1 @44
- 2uPP @94
- BXkV @62
- dWoa @76
- hY5y @42

Add a control nudge at a random earlier step. If only the targeted nudge leads to a schema read and fix, cause 2 is confirmed. (Inferred caveat: this needs resume-from-step support in Harbor/terminus-2.)

Why not the others: a visible failing test points at the location, like B2, so it cannot separate "doesn't link" from "doesn't verify". The generic line targets cause 1 and the knowledge probe targets cause 4, both already refuted. A secondary option is a variant without "Do not modify or add tests", which would isolate the verification gap.
