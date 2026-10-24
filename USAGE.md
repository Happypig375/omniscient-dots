# Observational usage tracking

`account-usage.py` is a Python 3.11+ standard-library tool. It supports the
reviewed **Codex app-server 0.164.0 subset** and synthetic structured replay.
It reports observations; it has no scheduler, account switching, login, retry,
quota allocation, priority ranking or automatic recovery. See
[the prior-art recommendation](PRIOR-ART-USAGE.md).

## Local Codex commands

These commands explicitly launch the chosen local setup's Codex. They do not run
full setup. **The setup launcher itself refreshes persistent config defaults and
directory modes even for help**; authorization for that activation is required
in addition to runtime observation. Nothing in this document grants access.

```bash
python3 account-usage.py probe --launcher /actual/checkout/setup.sh
python3 account-usage.py snapshot --launcher /actual/checkout/setup.sh --evidence-only
python3 account-usage.py selftest --launcher /actual/checkout/setup.sh --timeout 90 --evidence-only
```

`probe` checks version/help and generates schemas into a temporary directory.
Exact reviewed schema hashes gate live collection. Other versions/builds stop
with a capability failure. Eleven generated schema hashes cover the reviewed
request/response and token, turn, item and quota-push structures. `snapshot`
performs `initialize`, `account/read` with
`refreshToken:false`, and one `account/rateLimits/read`. It never forces refresh.
Before that first account read succeeds, initialization `account/updated`
notifications are counted without establishing identity, and quota pushes are
discarded. The successful account response establishes only the connection's
initial scope. Every later `account/updated` stops collection and invalidates
prior samples with `revalidation_required`; no automatic retry follows.
`selftest` additionally creates **one new ephemeral thread and one synthetic
turn**, using `gpt-6.1-sol`, explicit `xhigh`, an empty temporary working directory
and instructions to reply `OK` without tools. The app-server process and thread
start in the temporary directory. It requests a read-only sandbox and a user
approval reviewer, checks both returned settings before submitting the turn,
refuses all server-initiated requests (including token refresh and approvals),
and terminates its stdio child on completion, failure or deadline. Existing
configuration and managed restrictions still apply. Read-only sandboxing does
not disable tools; the observer stops on any tool item. This is a diagnostic,
not an enforcement boundary preventing all tool execution. The observer enables
no network listener or telemetry exporter and does not change telemetry settings.
The stdio deadline is 10–120 seconds; the three protocol checks each have a
separate 20-second deadline, followed by bounded child teardown. Codex itself uses its existing authentication
internally; the observer never reads auth files or supplies tokens. Neither a
read RPC nor `refreshToken:false` is a guarantee about all internal Codex auth
maintenance; the observer requests no refresh and stops if asked for one.

Without `--evidence-only`, snapshot/selftest stdout includes **private numeric
observations**. Keep it private; do not add it to Git or share it as a fixture.
Only identity-presence booleans and process-local aliases survive normalization.
Email, account/thread/turn IDs, prompts, model output, raw error text, balances and
all unknown payload fields are dropped. Stderr is discarded. CLI diagnostics use
fixed codes, an optional bounded numeric RPC code, a standard RPC category and
a fixed cause when the method, code and message exactly match a reviewed 0.164.0
source literal. No substring matching or HTTP-status extraction is used;
unrecognized causes stay unknown. Free-form error messages/data are discarded.
Failed account reads leave identity
presence null, rather than proving no account exists. Reports do not establish
stable cross-process account identity. The observer writes no report by itself;
stdout redirection is the caller's private storage and retention choice. No raw
protocol capture or selftest measurements belong in Git.

Creation evidence distinguishes `not_requested`, `outcome_unknown` and confirmed
`created`/`started`. A missing creation response cannot prove that a thread/turn
was never created. Do not retry a one-turn allowance from the success booleans
alone; an unknown outcome requires resolving that uncertainty first.

## Report meaning

- `account_quota`: one historical sample per meter/window/query. The multi-meter
  response takes precedence over the legacy mirror. Samples are never summed.
  Explicit synthetic pool aliases may group sources; a live connection alias
  proves only where the observation came from, not a globally shared pool.
- `session_tokens`: provider cumulative `total` and `last` remain distinct.
  Cache/reasoning counts are subcategories, not additional totals. Context
  capacity is a separate gauge. Absent optional counters stay null (even where
  the wire schema offers a deserialization default). Parent/child inclusion and
  payer are unknown.
- `account_credit_availability`: availability flags only. Numeric balances,
  financial reports and API-cost adapters are deferred; no currency conversion.
- Query completion provides only `recently_fetched` freshness, with provider
  measurement time unknown. The default 120-second TTL is an observer policy,
  not a provider guarantee or polling recommendation. Resets cap freshness;
  passing one never creates a zero/recovery sample. Push receipt time cannot
  prove provider freshness. Replay keeps original anchors; failures age prior
  data and preserve independent query-health clocks. Tokens do not clear a
  failed quota query, even in replay with claimed query timestamps. A new epoch
  starts with unknown query health; late responses cannot clear a newer failure.
  Turn failures leave quota-query health independent. Account-change events
  invalidate prior samples. Old epochs stay historical.
- Conflicting source candidates stay visible. Window semantics are unknown,
  so burn, quota shares and exhaustion ETA are unavailable. Ordinary-usage
  permission remains nullable. No policy is inferred from active sessions.

The installed token notification has `threadId`, `turnId` and token counts,
with no payer field. The quota response's optional `accountId` does not bind a
turn to that account. Even successful self-observation leaves the join unknown.

## Synthetic replay and checks

`python3 account-usage.py replay --now <unix-seconds> < explicit-fixture.jsonl`
accepts only bounded, versioned `synthetic:true` envelopes on stdin. No file path,
command, session search, credential discovery or remote input from data executes.
Each envelope has `schema_version:1`, `producer_version:"0.164.0"`, `source`,
`scope`, `epoch`, `sample_id`, `observed_at`, `kind` (`quota`, `tokens`, `health`),
and its protocol `payload` or health `code`. Explicit query envelopes also have
`fetch_started_at` and `fetch_completed_at` in Unix seconds. Token events cannot
claim query timestamps. Source/scope/sample
aliases use lowercase letters, digits, underscores and hyphens. Imports remain
synthetic assertions, not provider attestation. The tests contain examples.

Run `python3 -m unittest discover -s tests -v`. Acceptance covers replay/idle
staleness, omitted fields, query failures, epoch changes, conflicts, unresolved
shared-pool joins, token/context separation, unsafe payloads, protocol gates and
child teardown. Claude/Orca adapters, temporal request bindings, user policy
evaluation, financial reconciliation and rate estimation are deferred. This
suite does not establish those capabilities or prove live acquisition compliance.

## Validation record (2026-10-25 Asia/Singapore)

The installed `0.164.0` version, stdio help and all eleven reviewed schema hashes
matched through the selected setup launcher. Earlier isolated checks of the
initial seven hashes also passed with empty temporary HOME/CODEX_HOME/XDG paths.
The final attempt had action-specific authorization for normal launcher
config/mode activation and at most one new synthetic turn.

**Live acquisition remains blocked.** The single final bounded `selftest`
completed in 1.812 seconds, including protocol checks and child teardown.
`initialize` succeeded; `account/read` with `refreshToken:false` returned
**`-32603` / `internal_error` / `routing_discovery_failed`**. The cause is an
exact method/code/message match to the reviewed source, not an inference from
the numeric code. The lower-level failure and authentication state remain
unknown; this result does not establish an authorization denial.

There was 1 account-read request, 0 quota queries, 0 thread-start requests,
0 turn-start requests, 0 quota samples, 0 token samples and 0 observed tool
items. The child terminated and was reaped. No bootstrap account notification
arrived in this attempt. The one-turn allowance remains unused, and there was
no retry or bypass of the failed account read.

Earlier attempts retained an unclassified account RPC error and then
`-32603` / `internal_error`; their causes remain unknown. A subsequent direct
snapshot stopped on an initialization `account/updated` before receiving the
account response, with no quota/thread/turn requests. That observer bootstrap
failure is now corrected and covered by synthetic behavior tests. Those tests
also verify that bootstrap notifications cannot mask an account-read error,
pre-binding quota pushes are discarded, and later account notifications still
require revalidation and invalidate prior samples.

The [exact 0.164.0 protocol](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server-protocol/src/protocol/v2/account.rs)
supports this request and `refreshToken:false`. The
[account handler](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/request_processors/account_processor/workspace_routing.rs)
maps multiple routing failures, including authorization rejection, to the same
[internal-error code](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/error_code.rs).
The same handler can emit an initialization account notification asynchronously
and checks account-owner generation during and after account-read discovery.
This supports waiting for the initial read response before binding. The
notification itself has no account identity; it never validates a quota or
session payer. A separate quota method remains gated on successful account read.

Real quota, token delivery and payer binding remain unverified. No existing
sessions are mapped. Synthetic lifecycle tests do not establish successful live
acquisition. No login, forced refresh, credential/session inspection, remote
input or telemetry change was performed. Private fixed/numeric diagnostic
evidence stays outside the repository; this record contains no raw protocol
data or account measurements. No commit or push was made.

Final checks: all 152 discovered tests completed successfully (1 skipped), Bash
syntax checks passed for setup and the three launchers, and `git diff --check`
passed. The source/documentation diff was reviewed without private state.
