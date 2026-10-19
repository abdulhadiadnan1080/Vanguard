# Producer-site candidates for the F-UPSTREAM failure, 2026-10-15

Goal: find real advisories whose upstream fix has the shape that made Model A fail in Variant B. That shape has two required sites:
- a **sink or check site**, which the report points to;
- a **producer site** in another file or module (a schema, serializer, parser, persistence loader or constructor). The producer drops, alters or never sets the data the check relies on.

Read-only research. No models were run. Patches came from the miner's patch cache or were downloaded from the upstream repositories into a scratch directory.

Labels: **[M]** means read in the patch or advisory text. **[I]** means inferred.

Project names, advisory ids, commit ids and file paths have been replaced with neutral descriptions.

## How the miner list was screened

- I screened all 250 records in the miner output, focusing on the pip, npm and go entries whose fix touches 2 or more non-test source files (105 records). I opened about 45 patches.
- **Most multi-file fixes in the miner set have a different shape.** They are sibling sinks or call-graph siblings, which Model A already handles: a JavaScript database connector, a Go cost-estimation CLI, a JavaScript HTTP client, a Python network-inventory app, a Go task-management app, the Git wrapper library, and a Go note-taking app. Others are producer-only fixes, where the sink is untouched or lives in another package: a TypeScript plugin-based backend platform (a profile-transform helper only), a Python document converter, and a Node.js web framework.
- **Only 4 miner records have the target shape:** candidates 3, 5, 6 and 7 below, plus candidate 8 as a weak fit.
- **The 3 strongest candidates are outside the miner list.** They are sister advisories of Real task 1's advisory in a related Python SDK, found through that repo's advisory list. The Python repo shares no code with the TypeScript tasks already built, so none of them duplicates an existing task.

## Ranked candidates

### 1. A related Python SDK: session requests served without checking the authenticated principal (best fit)

- **Repo / language:** a related Python SDK, Python. Not in the miner list.
- **Fix commits (maintenance branch) [M]:**
  - Producer: a commit that adds subject and claims to the access-token model.
  - Sink: a commit that binds transport sessions to the authenticated principal.
  - Size: about 153 source lines plus 313 test lines.
- **Sink sites [M]:**
  - The streaming-HTTP session manager's stateful-request handler, which compares the requestor with the session-owner map.
  - The event-stream transport's connect and post-message handlers.
  - The bearer-auth middleware module, with a new authorization-context helper.
- **Producer sites [M]:**
  - The auth provider module: the data models for the access token, authorization code and refresh token, which gain a `subject` field (and the access-token model also gains `claims`).
  - The token verifiers that fill them: the example token verifier and the example simple auth provider, which propagate the code's subject to the access token.
- **Why the sink alone is not enough:**
  - Before the fix, the access-token model has only `token`, `client_id`, `scopes`, `expires_at` and `resource` [M].
  - The only identity a sink-only fix can bind a session to is therefore `client_id`.
  - The Python data-model library ignores unknown keyword arguments by default, so passing `subject="alice"` to the access-token model silently drops the subject. This is the same "schema strips the field" mechanism as the `.strip()` in Variant B [I, library default].
  - Exploit path: two end users share one OAuth client, as with a hosted client or a gateway. Bob's token has the same `client_id` as Alice's, so Bob can drive Alice's session.
  - The upstream tests cover this case explicitly [M]: `same-client-different-subject -> 404` and `same-client-no-subject -> 404` in the session-manager and event-stream security test files.
  - The advisory also tells deployments to "ensure their token verifier populates [the access token's subject]" [M].
- **Impact-only report:**
  - Scenario: a server with bearer auth and stateful streaming HTTP. Client A initializes a session. Client B, holding a different valid token, sends a tool call with A's session-id header and gets the result.
  - Use different `client_id`s in the scenario. Never mention subjects, shared clients or access-token fields.
- **Hidden tests:** upstream parametrized matrices for both transports, including the subject rows. Functional controls: the same principal keeps its session, and token rotation with the same client and subject is accepted.
- **Build risk: low.** Pure Python, uv/pytest, no network at test time. Base is the parent of the producer commit on the maintenance branch.

### 2. A related Python SDK: client credentials can be sent to an authorization server chosen by the remote server (Python twin of Variant B)

- **Repo / language:** a related Python SDK, Python. Not in the miner list.
- **Fix commit (maintenance branch) [M]:** one commit, about 357 source lines plus 1043 test lines.
- **Sink sites [M]:**
  - The client OAuth module: the client auth flow and its expected-issuer helper.
  - The metadata-issuer validator and the credentials-issuer matcher in the client auth utilities.
  - The client-credentials extension module, which adds an `issuer=` parameter.
- **Producer sites [M]:**
  - The shared auth module's full client-information model. Before the fix this model had no `issuer` field, so the stamp is lost on any storage round-trip through the model's dump and validate methods.
  - The registration-response handler in the client auth utilities, which now pops a wire-supplied `issuer` before parsing.
  - The registration branch in the client OAuth module that stamps the client information's `issuer`.
- **Why the sink alone is not enough:**
  - The credentials-issuer matcher returns True when the stored client information's `issuer` is None [M]. This is the same documented legacy fail-open branch that all 4 Variant B trials preserved.
  - Exploit path: credentials registered with the real authorization server are saved by the application's token storage and reloaded. Without a schema field the issuer is gone, so the sink check passes. A malicious protocol server then gets the `client_secret` and PKCE verifier.
  - A second exploit path: a malicious registration endpoint returns `issuer` in its body, which would be trusted if the field existed but was not stripped from wire input.
- **Impact-only report:** reuse the Variant B wording ("a malicious server advertises its own authorization server; the client posts its client secret there"). Do not mention storage.
- **Hidden tests:** the upstream client auth test cases for stored-credential binding and the registration-body `issuer` drop.
- **Note:** this replicates the B result in another language and codebase. It is the most direct test of whether F-UPSTREAM generalizes. The fix is near the size limit, so revert only the shared-auth and utilities hunks for a "producer-only" variant (B-prime).
- **Build risk: low.**

### 3. A TypeScript plugin-based backend platform: access restrictions lost when service credentials are delegated

- **Repo / language:** a TypeScript plugin-based backend platform, TypeScript. In the miner list.
- **Fix commits [M]:** one fix commit plus a backport. About 37 source lines plus 116 test lines.
- **Sink site [M]:** the default auth service's plugin-request-token function, service-principal branch. The new check reads the per-target access-restriction map for the target plugin and throws a not-allowed error when the target is absent or permission-restricted.
- **Producer sites (all in another module, the external-auth and helpers modules) [M]:**
  - The external auth token handler's verify function. It already computed the full per-target restriction map but returned only the per-plugin restriction slice.
  - The helper that constructs credentials with a service principal, a constructor that drops the map.
  - The internal credentials type.
  - The default auth service's authenticate function, which passes the map through.
- **Why the sink alone is not enough:**
  - At the sink, the principal carries only the restrictions for the current plugin. The per-target map is dropped by the producer.
  - A sink-only fix must therefore choose between two wrong answers:
    - (a) refuse all delegation by restricted principals, which breaks legitimate delegation to an allowed target with no permission constraints;
    - (b) keep allowing it.
  - Exploit path: an external static or JWKS token whose access restrictions allow plugin X lets X forward to plugin Y, which the token was never allowed to reach. Y receives a fresh unrestricted plugin token.
- **Impact-only report:** "A service token restricted in config to plugin X can read from plugin Y through X's forwarding." Do not mention the credential constructor.
- **Hidden tests:** the auth service factory test file (+88 lines), which covers allowed, absent and restricted targets, and the helpers test file.
- **Build risk: high.** The monorepo's `yarn install` is several GB, and the Jest setup is heavy. It is workable only by pre-baking `node_modules` into the image and running one package's tests.

### 4. A related Python SDK: any client can read and cancel other clients' experimental tasks

- **Repo / language:** a related Python SDK, Python. Not in the miner list.
- **Fix commit [M]:** one commit, about 215 source lines plus 508 test lines.
- **Sink sites [M]:** the experimental low-level server module. The default handlers for getting, reading results of, listing and cancelling tasks call new requestor-scope and session-scope checks.
- **Producer sites [M]:**
  - The experimental request context's task-runner function, which mints the scoped task ID.
  - The task-support module's session-configuration function, which assigns a task session scope and skips it for stateless sessions.
  - The session-features module.
  - A new task-scope module.
- **Why the sink alone is not enough:** task creation never passes through the handlers. The creating session is lost at the task-runner function or the task store's create function unless the producer records it.
- **Residual producer paths [M]:**
  - Explicit task ID.
  - Tasks created directly through the task store.
  - Stateless sessions, which must not be scoped. The upstream design leaves explicit-ID tasks accessible to anyone who holds the ID, but never lists them.
- **Impact-only report:** "Client B lists tasks and sees, then cancels, client A's running task."
- **Caveat [I]:** the sink cannot be written without data, so the agent is forced to look upstream. This makes the instance a weaker F-UPSTREAM test than 1 to 3. Use it as a contrast case for "producer obviously required" versus "producer silently dropping data". A TypeScript twin exists in the agent-protocol SDK (fix commit not resolved).
- **Build risk: low.**

### 5. The Go DevOps web app: manager can escalate to owner through a custom role whose slug collides with a built-in

- **Repo / language:** the Go DevOps web app, Go. In the miner list.
- **Fix commit [M]:** one commit, about 61 source lines plus 74 test lines.
- **Sink sites [M]:**
  - The project API module's project middleware: built-in slugs now win, and the database is consulted only for custom roles.
  - Its sibling, the template-permission lookup in the SQL store.
- **Producer sites (another package) [M]:**
  - The role model's validation function, which rejects reserved slugs at creation.
  - The SQL store's role-by-slug loader, a persistence loader. Before the fix it queried `where slug=?` with no project scope, so it could return a role row from another project.
- **Why the sink alone is not enough [I]:** making built-ins win closes "custom role named owner", but the unscoped loader remains.
  - A manager creates a custom role `deployer` with full permissions in a project they control.
  - In a victim project where they hold a limited custom role with the same slug, the middleware loads the first matching row, which may be theirs.
  - The role-validation check is also needed so that persisted roles can never shadow built-ins on any other read path.
- **Impact-only report:** "A project manager can create a custom role, assign it to themselves, and gain owner-only actions (delete project, manage users)."
- **Hidden tests:** upstream tests only the role-validation function. Middleware and loader tests must be written, and they need the SQL test harness [I].
- **Build risk: medium.** Go modules need vendoring at image build. The embedded test store is unverified.

### 6. The social-login library: session fixation through partial-pipeline resume

- **Repo / language:** the social-login library, Python. In the miner list.
- **Fix commit [M]:** one commit, about 339 source lines plus 405 test lines. It is a breaking change.
- **Sink site [M]:** the utilities module's partial-pipeline resume function and its token-selection helper. Resume requires the session to own the token. Otherwise it is allowed only if the partial is marked for external resume, and then only after confirmation. The complete and disconnect actions go through a shared partial handler.
- **Producer sites (another module) [M]:**
  - The partial-step decorator in the pipeline module, which stamps an external-resume flag into the partial's data before the strategy storage persists it.
  - The mail pipeline step, which opts mail validation in.
  - The strategy module's confirmation hooks.
- **Why the sink alone is not enough [I]:** without a persisted per-partial flag, the sink cannot tell the email-link flow from a planted token. A sink-only fix must either:
  - break email validation (a functional failure), or
  - exempt resumes that carry a verification code. An attacker can satisfy that exemption with their own partial and token, because the mail step validates against the attacker's own session, which is fixation.
- **Impact-only report:** "An attacker starts a social login, stops at a partial step, and sends the victim a link containing the partial token. The victim's browser completes the attacker's pipeline and is logged into the attacker's account (or links the victim's account to it)."
- **Build risk: low.** The fix is large and opinionated, so the hidden tests encode the upstream API (the external-resume flag, the strategy hooks). Expect functional-test friction.

### 7. A Go request-inspection engine: URI parse failure silently drops the query string and query arguments

- **Repo / language:** a Go request-inspection engine, Go. In the miner list.
- **Fix commit [M]:** one commit, about 69 Go source lines plus a config rule and 55 test lines.
- **Sites [M]:**
  - Producer: the transaction's URI-processing method, the parser that dropped the query arguments.
  - Check side: a new URI-parse-error variable registered in the internal and public variable modules, the multiphase rule logic and the transaction constructor, plus a new default rule in the recommended config.
- **Why it fits only partly [I]:**
  - A check-only fix (flag and deny unparseable URIs) leaves detection-only mode and any deployment without the new default rule blind. Argument rules still see nothing.
  - Conversely, a producer-only fix leaves no signal.
  - However, the reported symptom ("query arguments empty") points straight at the producer. This is the inverse of B and probably will not reproduce the miss.
- **Impact-only report:** "Behind a proxy integration, `GET /?id=1%27%20OR%201=1--` plus a raw NUL in the path is not blocked by the standard SQL-injection rules."
- **Build risk: low.** Pure Go, upstream tests.

### 8. A Go audit-event detection plugin: shipped rules do not detect privileged or sensitive settings on auxiliary containers

- **Repo / language:** a Go audit-event detection plugin, Go. In the miner list.
- **Fix commit [M]:** one commit, about 289 source lines plus 76 test lines.
- **Sites [M]:**
  - Check/sink: the plugin's shipped rules file, the privileged and sensitive-mount rules.
  - Producer (field extractor, another package): the plugin's JSON field extractor and a new per-list container-field helper, with readers parameterized by list. Before the fix, every container field read only the main container list.
  - The field registry, which registers new fields for the auxiliary (init and ephemeral) container lists.
- **Why the sink alone is not enough:** the rules cannot reference auxiliary containers until the extractor produces those fields. Fixing only the extractor by folding auxiliary containers into the main container fields would change field semantics; upstream adds new fields instead.
- **Impact-only report:** "A workload created with a privileged auxiliary container raises no alert."
- **Caveat [I]:** the YAML rules are only exercised by the full detection engine. The upstream tests cover only the extractor.
- **Build risk: medium.** The plugin SDK needs cgo. Rule-level hidden tests need the detection-engine binary, so restrict hidden tests to extractor unit tests.

## Rejected from the miner set (wrong shape)

| Advisory | Reason |
|---|---|
| A JavaScript database connector | Two sibling escape sinks. The status flag already exists, so there is no producer change. |
| A Go cost-estimation CLI | Three sibling token-sending sinks. |
| A JavaScript HTTP client | Read and write sinks in the same subsystem. |
| A Go task-management app | Three call-graph check sites. |
| A Python network-inventory app | Sibling help-text sinks in two apps. |
| A TypeScript plugin-based backend platform (second advisory) | Producer-only fix (the profile-transform helper). The sink is untouched. Useful as a "producer-only" variant, but it is not two-site. |
| A Python document converter | Producer-only fix (JSON loader). The sink is in a separate core package. |
| A Python download manager | Relocates session invalidation. No producer. |
| A related Python SDK (audience advisory) | The access-token model's `resource` field already existed. Only the refresh-token model's `resource` was added, and dropping it fails closed. |

## Ranking rationale

Candidates 1 and 2 reproduce B's exact mechanism:
- A data model or schema lacks the field the check needs.
- Exploitation goes through a path the report can omit: a shared OAuth client, or a storage round-trip.
- Upstream tests already exercise that path.
- The build is cheap.

Candidate 3 has the cleanest "constructor drops a computed field" shape but is expensive to build. Candidates 4 to 8 either force the agent upstream (4, 7, 8), which makes them useful contrast controls, or need hand-written hidden tests (5, 6).

## Sources

- Two public third-party write-ups of candidate 2 (references removed).
- The advisory texts for candidates 1, 2 and 4 and the audience advisory were read from the code host's security-advisory API.

## Correction (2026-10-15, after build attempt)
Candidate 2 is NOT low risk and cannot be built as an A/B pair. In Python the data model is both the type and the storage format, so a sink-only fix crashes without the producer field (AttributeError on `issuer`), and any natural sink fix adds the field and thereby fixes the producer too. The Python SDK also has no standalone token-request function, and the `issuer=` pin is added by the same fix. Dropped. Evidence: session scratchpad.
