# Usage tracking: focused prior art

Reviewed 2026-10-25. Public `main` and docs are mutable; these are source findings,
not installed compatibility claims. No third-party software was installed or run.
Checkout and fetched `origin/master` both match `49fe53688715af15f5efce826d07b1dadfb9493a`.

| Project / official source | Useful existing behavior | Boundary for this task / reuse cost |
| --- | --- | --- |
| [ccusage](https://github.com/ccusage/ccusage/blob/main/apps/ccusage/README.md) | Multi-harness daily/session tokens, cost estimates, JSON and Claude block reports. | Local-log accounting is not an account-bound shared subscription quota ledger. Transcript discovery conflicts with this task. [MIT](https://github.com/ccusage/ccusage/blob/main/apps/ccusage/LICENSE); Node/pnpm workspace and pricing/data dependencies would add unnecessary integration surface. |
| [CodexBar Codex adapter](https://github.com/steipete/CodexBar/blob/main/docs/codex.md), [Claude adapter](https://github.com/steipete/CodexBar/blob/main/docs/claude.md) | Real quota windows, resets and balances separate from local cost history; account/workspace scoping, retained ages/errors, bounded app-server RPC, late-selection guards and query failure distinctions. | Closest integration reference. Does not establish a general request-to-payer evidence ledger. Auto sources include credentials, browser cookies and recovery paths outside this authorization. [MIT](https://github.com/steipete/CodexBar/blob/main/LICENSE); [Swift package](https://github.com/steipete/CodexBar/blob/main/Package.swift) and macOS/cookie integrations are expensive to embed in this Python/Linux repo. Reimplement only the small documented protocol subset. |
| [Claude Code Usage Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor/blob/main/README.md) | Current v4 documentation adds official statusline limits, freshness/provenance, JSON snapshots and explicit local estimates; richer than a token dashboard alone. | Its historical/P90 plan estimates cannot establish subscription allowance or payer identity. No general shared-account session binding established. [MIT](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor/blob/main/LICENSE); [dependencies](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor/blob/main/pyproject.toml) include NumPy, Pydantic, Rich and YAML. Prefer a small structured-input adapter, without its scanning/warehouse. |
| [Orca usage tracking](https://www.onorca.dev/docs/agents/usage-tracking), [public implementation](https://github.com/stablyai/orca/tree/main/src/main/rate-limits) | Existing account switcher, quota windows, credits and unavailable states; local estimated costs are separate. | Active selection is not proof of an existing session's payer. The reviewed installed handler does not refresh quota on account listing. Focused official docs/repo searches did not establish a public passive quota-export extension API. Embedding editor extensions or internal Electron RPC would not supply that contract. [MIT](https://github.com/stablyai/orca/blob/main/LICENSE), but vendoring the Electron application is disproportionate. Keep Orca live integration deferred. |
| [OpenUsage Claude](https://github.com/robinebers/openusage/blob/main/docs/providers/claude.md), [Codex](https://github.com/robinebers/openusage/blob/main/docs/providers/codex.md) | Account/organization cards and quota versus local-spend separation; excludes some ambiguous sessions. | Documents a terminal-history fallback to the currently signed-in account, including older sessions after switches: unacceptable as billing evidence here. Credential/keychain reads and token rotation also exceed scope. [MIT](https://github.com/robinebers/openusage/blob/main/LICENSE); native macOS app, no reuse proposed. |

Recommendation: implement a Python standard-library observer around the
[official Codex app-server protocol](https://developers.openai.com/codex/app-server),
with the exact installed generated schema as its compatibility gate. Observe
quota via `account/rateLimits/read` and tokens via `thread/tokenUsage/updated` in
one newly created ephemeral session. Use `account/read` with `refreshToken:false`;
never answer an auth-refresh request with credentials. Keep only allowlisted
numeric/provenance fields and run-local identity-presence aliases. If no
request-bound payer is supplied, report that join as unknown.

Keep subscription pools, API throughput, session tokens/context and monetary
spend in separate lanes. Preserve window duration/reset/unknown semantics,
epochs and original sample/fetch times. Failed queries age prior values; they
do not mean zero capacity or a signed-out agent. Replayed events cannot renew
freshness. Rate calculations require comparable fresh source observations;
rolling occupancy is not gross burn. None of the inspected tools establishes a
provider-neutral task-priority policy: this implementation will report facts,
with priorities unspecified and automatic action disabled.

No code is copied or dependency added, so no vendored-license obligations arise.
MIT reuse would require preserving notices and reviewing the exact locked
dependency graph and acquisition side effects first; this was a focused fit and
license review, not a dependency security audit. [USAGE.md](USAGE.md) documents
the implemented subset, validation outcome and deferred scope. Claude/Orca
adapters, request-bound payer attribution, user priority policies, financial
reconciliation and rate estimation remain deferred; the synthetic tests do not
establish those capabilities or live acquisition. The separate authorized
token-only evidence in [USAGE.md](USAGE.md#validation-record-2026-10-25-asiasingapore)
verifies token delivery only; account/quota acquisition remains blocked and payer
binding unknown. The one-turn authorization is consumed.
