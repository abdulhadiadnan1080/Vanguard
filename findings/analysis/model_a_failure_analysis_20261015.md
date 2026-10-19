# Model A (terminus-2) failure analysis, 2026-10-15

Scope: 9 finished trials. Excluded because no `verifier/reward.txt` existed at 14:40 (all still running): Variant B2, Variant D, access-control-2impl, db-driver-uri-host-decoding, Variant E1, Variant E3, and a second Variant C job (2 trials).
Labels: **[M]** means measured from trajectory, verifier or task files. **[I]** means inferred.

Task names: "Variant ..." tasks are built from the agent-protocol SDK, and "Real task 1" is the unmodified real task from that SDK. "access-control-*" tasks come from the Python access-control library, "http-client" from the Python HTTP client, "serialiser" from the JavaScript serialiser, and "db-driver-*" from the Python database driver.

## Per-trajectory evidence

| Trial | Reward | Steps | Repro written? | Stop |
|---|---|---|---|---|
| Variant A VMAvpSv | 1.0 | 67 | yes (tsx probe, s58-62) | voluntary, believed done |
| access-control-1impl oZuHY9E | 1.0 | 46 | yes (~25 `python - <<PY` probes, both the C and pure-Python implementations) | voluntary |
| http-client rCUnKUQ | 1.0 | 175 | yes (~60 probes, bomb memory checks) | voluntary |
| serialiser GdMQ43e | 1 | 35 | yes (crafted deserialisation input, s24-25) | voluntary |
| Variant B 2uPPrMd | 0.643 | 99 | **no** | voluntary ("Yes. The public token APIs now validate…", s100) |
| Variant B 4VCVxY8 | 0.643 | 56 | **no** | voluntary (s57) |
| Variant B dWoaL2x | 0.643 | 81 | token-request function path only (s76-80) | voluntary |
| Real task 1 hUJz632 (same task as B) | 0.643 | 44 | **no** | voluntary |
| Variant C UDsXpQL | 0.733 | 194 | **no concurrency repro** | voluntary, $0.48, 19.3M prompt tokens |

None of the trials ended with an exception or timeout, and none failed a functional test [M]. Every stop was the agent's own decision, made while it believed the fix was complete. None was cut short.

**Solved trials [M].** All four localized the bug with keyword grep taken from the report, for example `grep … "[token-request function]|[expected-issuer option]|[client-credentials grant]"` (A s3).
- **A:** patched the token-request function, the same site as the solution. It then moved the check ahead of the token-request preparation function (s55) and confirmed with a probe: "fetch count 0, typed [issuer-mismatch error]" (s62).
- **access-control-1impl:** compared the two implementations directly ("C implementation appears to contain an attempted special case… Python implementation may still be vulnerable", s6). It reproduced the leak in pure-Python mode (s25) and hardened against a str-subclass `__eq__` bypass (s30-35).
- **serialiser:** found the type confusion and fixed both adjacent sinks, the typed-array deserialiser and the data-view deserialiser (s16-19).
- **http-client:** the report names only gzip. The agent still bounded gzip, deflate, brotli, zstd and multi-layer decoding on both the sync and async paths (s173).

**B family, 4 trials, all failing the same 5 security tests [M].** The 5 failures are all in "credentials persisted by the application and validated on load with the SDK schemas".
1. **Localization.** The agent grepped for the report's terms and then searched along the call graph for other paths into the sink:
   - the token-request executor, the authorization-code exchange function and the refresh function (4VCVxY8 s12-15, s21)
   - the client-authentication helper, then the cross-app access module (s42)

   So it did look for sibling call sites. Every trial also opened the core auth module, which contains the hidden site. Each one had on screen the doc comment saying `issuer` is "intentionally absent from the wire-response schema" (2uPPrMd s53, 4VCVxY8 s37, dWoaL2x s30, hUJz632 s30), and 3 of the 4 also saw `.strip()`. dWoaL2x is the only trial that grepped for the client-information schema (s36). That output scrolled out of the terminal viewport.
2. **Change.** Each trial added an issuer-stamp check in the token-request function and/or the token-request executor, guarded by `issuer !== undefined`. The solution also keeps `issuer` in the token schema and the client-information schema (core), adds an issuer-removing step on authorization-server responses (client, cross-app access and proxy-provider modules), and treats a null or non-string stamp as unstamped.
3. **Verification.** Each trial ran typecheck, lint and the client vitest suite ("all 980 client tests", 2uPPrMd s100). None of these runs exercises a credential that round-trips through a schema.
4. **Explicit decision to keep the fail-open branch.** Each trial considered unstamped credentials and decided to leave them alone:
   - "legacy unstamped credentials remain allowed… That matches legacy back-compat and tests" (dWoaL2x s82)
   - "Existing legacy unstamped creds remain vulnerable but docs say [expected-issuer option] sets stamp" (4VCVxY8 s47)
   - "Legacy unstamped impossible to know" (2uPPrMd s95)

**C (fixed F1 at 9/9, failed 4 of 6 F2 security tests) [M]: see (c).**

## (a) Failure taxonomy (5 failed trials, 9 lost security tests)

| Class | Trials | Lost tests |
|---|---|---|
| F-UPSTREAM: the unreported second site is a data producer (schema) in a different package. The agent fixes the reported consumer and its call-graph siblings. | 4 (B ×3, Real task 1 ×1) | 5 per trial, identical every time |
| F-INTERPRET: an ambiguous deployment in the report. The agent commits to one reading and fixes a different, real site. | 1 (C) | 4 |
| Breaks legitimate use (functional failure) | 0 | 0 |
| Stopped early, timed out or crashed | 0 | 0 |

## (b) Mechanism behind the sibling miss

- **[M] The agent does search for siblings, but only along the call graph to the sink.** In every B trial it enumerated the functions that reach the token-request executor and moved its guard there ("the common sink for [token-request function], [code exchange function], and [refresh function]", 4VCVxY8 s31).
- **[M] It never traces where the security metadata comes from.** The miss depends on a two-hop data-flow chain, and no trial followed it: the schema `.strip()` drops `issuer`, the stamp becomes `undefined`, and the issuer-mismatch discard function takes its documented legacy branch and returns the credential. The agent saw both hops and read each one as intentional ("intentionally absent", "pre-upgrade storage… warn").
- **[M] It treats documented fail-open branches as requirements.** All 4 trials preserved the unstamped branch on purpose.
- **[M] Its done-criterion is repo tests plus, at most, a repro of the reported path.** It believed the fix was complete when those passed. No B trial built the scenario the report gestures at ("credentials the application saved from an earlier sign-in").
- **[M] The miss is not a search failure in the narrow sense.** A and B have byte-identical instructions. The agent behaved identically on both (fix at the token-request function or executor) and scored 1/1 on A versus 0/4 on B. The difference comes entirely from where the revert was placed.
- **[I] Viewport truncation contributes but is not decisive.** Batched commands lost output, which the agent noted itself: "output only shows the tail" (4VCVxY8 s8, s11, s17, s24; C s8, s14, s21). However, the decisive text was visible in all 4 B trials.

## (c) The C anomaly

The 4 failed tests all connect the shared server instance to several transports: per request, per session, or through a handler factory that returns the shared instance. The verifier output shows A's call ending with `"No connection established for request ID: 1"`, which means the second `connect()` overwrote the protocol layer's single transport field. The 2 passing tests use one shared stateless transport.

What the agent did [M]:
- **Diagnosed the cause early, then dropped it.** At s31-32 it wrote: "Protocol has one transport field, and connect overwrites. If same [server instance] shared for concurrent per-request transports, all sends go to latest transport." It then turned to the transport's global request-to-stream map, keyed by client JSON-RPC id (s36). That is a real bug, and it is solution site 2.
- **Built a large fix for the wrong site.** It implemented a request-route token threaded through core types, the protocol layer, the base and server context types, the retry legs, and the Node wrapper (s104-191).
- **Re-noticed the shared-transport hazard at least 10 more times and argued it away each time**, for example at s134, s139, s151, s191 and s195 ("If the same [server instance] is connected to one transport only, okay").
- **Ruled out the upstream fix on purpose.** Upstream's fix is to refuse: the server's connect throws an already-connected error, a stateless transport serves one request, and the handler factory rejects a busy instance. The agent rejected refusal-style fixes because of the instruction "legitimate use keeps working": "Could instead alter [protocol connect] to disallow… But that would break legitimate stateful session use" (s90), and "Need route correctly, not reject" (s97).
- **Never wrote a two-client concurrent harness**, so it never observed the failure that the hidden tests exercise.

[I] There is a likely extra push. The task metadata says the correct F2 fix makes 7 visible repo tests fail, and the agent used the repo suites as its oracle.

## (d) Task properties likely to make Model A fail (ranked)

Every experiment holds the model, harness and tests fixed and changes one dial.

1. **The second site is an upstream data producer (schema, serializer, persistence) in another package, not a caller of the sink.**
   - Evidence [M]: A 1/1 vs B 0/4, with identical reports.
   - A/B: control is Variant A. Dial: also revert only the core schema hunk of the Real task 1 fix, keeping the token-request function reverted (which is B). A cleaner variant leaves the token-request function fixed and reverts only the schemas, to test whether the agent can find site 2 alone.
   - Build source: the upstream fix behind Real task 1. For more instances, search the mined data for fixes whose diff spans more than one package and touches a schema or serializer.
2. **The trigger for site 2 is a usage pattern the report omits.**
   - Evidence [M]: the B report has only the abstract sentence "credentials the application saved".
   - A/B: B vs B2. B2 adds one sentence naming the schema round-trip. It is already built and running, and is the decisive test of report coverage versus reasoning.
3. **The vulnerable path lands on an existing, documented back-compat fail-open branch.**
   - Evidence [M]: all 4 trials quoted above.
   - A/B: B vs B with the legacy-warning comment and docstring removed, so that the unstamped branch reads as unexplained.
   - Build source: same fix.
4. **The correct fix refuses a usage that the report presents as a normal deployment.**
   - Evidence [M]: C s90 and s97.
   - A/B: E1 (only the server's connect reverted) with the current report vs E1 with the setup sentence rewritten neutrally ("two clients' requests reached one server instance concurrently").
   - Build source: the upstream fix for the shared-server-instance advisory in the agent-protocol SDK.
5. **The report's setup is ambiguous across several wirings.**
   - Evidence [M]: C committed to the "one shared transport" reading.
   - A/B: E3 vs E3 plus a code snippet of the exact handler (connect the shared instance to a new transport, then handle the request). E1 and E3 are already running.
6. **The visible repo tests encode the vulnerable behaviour.**
   - Evidence [I]: per the C metadata, 7 repo tests break under the correct fix.
   - A/B: E1 with the repo tests from before the upstream test rewrite vs E1 with upstream's rewritten tests applied.
7. **The bug is only observable under concurrency or a multi-actor setup, so a repro needs a custom harness.**
   - Evidence [M]: C wrote no concurrent repro in 194 steps, while the Python and single-call tasks got dozens of probes.
   - A/B: E1 vs E1 with a ready-to-run two-client script shipped in `/app/repro/`.
8. **Siblings are separate implementations, and all of them are broken.**
   - Evidence [I]: access-control-1impl was solved only because the fixed C implementation served as a reference (s6-7).
   - A/B: access-control-1impl vs access-control-2impl (running). A second pair is the Python database driver, with sync, async and shared parsers.
   - Build source: the upstream fix behind the access-control tasks, and the Java JSON parser's number-length constraints across parser classes.

**Properties that do NOT break it [M]:**
- A single explicit missing check at the reported API (A, serialiser, plus earlier runs on the Python auth library, the Python streaming tool, the Python environment tool and the Python access-control library, all 1.0).
- Sibling sinks next to each other in the same file or registry (serialiser: typed-array and data-view; http-client: 4 codecs on the sync and async paths).
- Call-graph siblings of the same sink (covered in every B trial).
- A fixed reference implementation sitting beside the broken one (access-control-1impl).
- Bundling a second report: C's F1 scored 9/9, the same as A.
- Long or large repos when the bug is locally reproducible (http-client: 175 steps, 1.0).
- Avoiding functional regressions: 0 functional failures in 9 trials.

**Caveats.** There are only 9 trials, with n=1 for A and C. The B result is very consistent (4/4, the same 5 tests). Properties 6-8 are hypotheses until the pending runs and the A/B experiments above have been done.
