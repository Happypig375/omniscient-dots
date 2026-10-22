Assuming user approval following [README.md](README.md),

1. Use your bash tool to curl https://github.com/Happypig375/omniscient-dots/archive/refs/heads/master.zip to `/workspace/shared`
2. Unzip it with your bash tool
3. On a new terminal on your cloud computer (not your bash tool from now on), execute `source ./setup.sh`; watch user choices and authentication. Sourcing activates this checkout’s `PATH` and interactive `codex` function in that terminal. Executing `./setup.sh` runs setup in a child process and cannot change the parent shell. Fresh interactive Bash terminals activate through the installed `~/.bashrc` hook; existing terminals need `source /path/to/checkout/setup.sh --env-only`.
4. After the script exits, save and verify `<checkout>/state/.omniscient-dots-checkpoint`. User approval for saving and restoration is already obtained.
5. With setup completed, suggest what can be done:
- If supervisor was chosen as set up, suggest that you can observe Orca's agents (list them) for any "model at capacity" errors that you can send "continue" prompts for
- If supervisor was chosen as set up, suggest that you can notify agent updates for all agents that the user names. (orca CLI at `/workspace/shared/omniscient-dots/master/bin/orca` can lookup terminal IDs and convert them to agent responses - you can list agents using it, while Orca user interface can complement information not covered by Orca CLI)
- If worker was chosen as set up, offer to forbid yourself from launching cloud tasks (via the native tool) and instead only do Codex on the cloud computer to save usage
- If the Codex Resets MCP (https://codex-resets.com/mcp) was not installed, suggest the user to install it
- If Codex Resets MCP is installed, suggest the user to let you watch Codex Resets, furthermore if supervisor was chosen as set up, suggest the user to queue up tasks (tracked outside the cloud computer by you) that you can spawn agents for if a Codex Reset lands.
6. For approved recovery, copy the complete private checkpoint to `<checkout>/state/.omniscient-dots-checkpoint`, then in that checkout source `./setup.sh --profile supervisor` (or the approved `worker`/`both` profile). Setup restores it automatically; do not decode or selectively extract it. Use the explicit profile for unattended execution, pausing if new authorization or interactive login is needed. See [Workspace recovery and approval](README.md) for the copy commands, surviving-state checks and approval scope.
  
#### Supervisor CLI and Skills

Supervisor and Both include an **Orca Install CLI and Skills** stage after the
AppImage download. Worker skips this stage. The existing `bin/orca` wrapper
remains the CLI entry point; setup verifies its help and both version-matched
skill guides without replacing it with an npm CLI or changing system launchers.
Every configured shell exports `ORCA_CLI_COMMAND` as this wrapper's absolute path,
which the official discovery skills prefer over their Linux `orca-ide` fallback.

The stage requires working Node 22+ with npm and npx. It reuses a compatible
installation; otherwise it downloads the official Linux x64 Node 24.22.1 archive
from `nodejs.org/dist/v24.22.1/`, checks SHA-256
`6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6`,
and installs it under `state/node/` (bundled npm 11.19.1). Configured terminals
put that runtime first on PATH, including in the current sourced shell after
installation. No system Node files or `bin/orca` are overwritten.

The UI-equivalent skill operation is:

```bash
bin/orca skills install --skill orca-cli --skill orchestration --agent codex,universal
```

Setup runs its verified pinned equivalent in a temporary HOME:

```bash
npx --yes skills@1.7.2 add https://github.com/stablyai/orca/tree/071747853f5995ee233b36751613f09a60007c2b --skill orca-cli --skill orchestration --global --agent codex --agent universal -y
```

It regenerates and verifies both discovery stubs each run, then publishes copies
from the installer’s shared `~/.agents/skills/` layout (also used for Codex)
to `state/codex/skills/` and `~/.agents/skills/`. No staged `.codex/skills/`
link is expected. Identical files are reused;
setup-owned unchanged files can be refreshed. Conflicting user skills, user
edits, and symlinked destinations stop the stage with their paths for manual
resolution. Unrelated skills and user files are preserved. Installation hashes
live in `state/orca-tools/skills.json`; npm downloads use `state/cache/npm/`.
Runtime, caches, skills, and installation metadata are excluded from the
credentials checkpoint by its explicit allowlist. Restore recovers credentials;
Supervisor/Both regenerates tools and skills afterward.

### Read-only supervisor inspection

With an already-running, already-paired runtime, load the matching guide first.
Inspection is passive-first: use existing runtime/session metadata and terminal
output before considering session input. These commands inspect one snapshot;
they do not start tasks or enable monitoring. Use the documented IDs from each
response in subsequent commands:

```bash
source /path/to/omniscient-dots/setup.sh --env-only
"$ORCA_CLI_COMMAND" skills get orca-cli --json
"$ORCA_CLI_COMMAND" skills get orchestration --json
"$ORCA_CLI_COMMAND" environment list
"$ORCA_CLI_COMMAND" status --environment <connection-name> --json
"$ORCA_CLI_COMMAND" repo list --environment <connection-name> --json
"$ORCA_CLI_COMMAND" worktree list --repo id:<repo-id> --environment <connection-name> --json
"$ORCA_CLI_COMMAND" terminal list --worktree path:<server-worktree-path> --environment <connection-name> --json
"$ORCA_CLI_COMMAND" terminal show --terminal <terminal-handle> --environment <connection-name> --json
"$ORCA_CLI_COMMAND" terminal read --terminal <terminal-handle> --screen --environment <connection-name> --json
```

Confirm selectors and flags against the installed guide/help before inspection.
The bundled help documents the forms above, including `terminal read --screen`.
Use a bounded default `terminal read` without `--screen` when retained output is
needed to inspect earlier errors. Check the returned `source`: `screen-unavailable`
is accumulated output, not a rendered screen; an absent field leaves that source
unverified. UI-only `draft` text is not submitted input or terminal output.
If the runtime is offline, stop and report it; this workflow does not open Orca,
resume bridges, launch agents, or install monitoring.

Record the selected environment, worktree, terminal, execution host, and any
explicit session/account metadata returned. Missing host or account identity is
unverified. Use structured quota or reset data only when explicit session-linked
evidence verifies the session's account on the execution host. Account lists,
selected defaults, and account-wide quotas alone do **not** establish that mapping.
The available help documents `account list [--json]` as host-local and rejects
`--environment`; it does not document an account-quota command. Do not invent one
or attribute local account data to a remote session.

Without verified mapping or supported structured quota data, use existing
terminal output for capacity, rate-limit, or quota errors. Report the observed
error and its context; do not infer a remaining quota, reset time, or account
identity from it. If neither source answers the question, report unknown.
Only if missing information blocks a specific decision may a targeted fallback
submit the session's supported `/status` or `/usage` command to that identified
session. This sends terminal input and is outside the passive snapshot above;
verify the target and command support first. Never inject these commands on a
recurring schedule or use them as polling. This guidance creates no monitoring
system, background watcher, or automatic retry/continue loop.
Redact terminal output before sharing it. See the [official CLI reference](https://www.onorca.dev/docs/cli/reference).

Run the isolated checks with `python3 -m unittest discover -s tests -v`.
The tests use temporary homes and mocked installers; they do not install live
skills, access credentials, launch Orca, or dispatch remote tasks.