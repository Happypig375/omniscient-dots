![omniscient-dots](omniscient-dots.png)

### For humans

Send "Use https://github.com/Happypig375/omniscient-dots" to your dot. Approve initial setup only after it describes the chosen profile and actions. For automatic checkpoint recovery, use the complete dot request and human reply below instead of a blanket "all restores approved" message.

After initial authentication following the initialization script on the dot's cloud computer, it gains Codex (not expensive cloud tasks) access on the cloud computer and/or [Orca](https://www.onorca.dev) to supervise any of your agents running anywhere on your [tailnet](https://tailscale.com/docs/concepts/tailnet) (think any computer that you have access to).

Workspace files can disappear while the desktop and processes survive; do not assume a reset means a reboot. Keep durable code in Git and approved checkpoints in ChatGPT Library. Do not rely on the cloud disk alone for long-running work.

### Approval prompts for automatic checkpoint recovery

The dot fills in every bracketed value from the current recovery plan before
sending this single request. Name the actual checkpoint, computer, destination
and profile; describe the complete operation rather than asking service by
service. Do not expose checkpoint contents to fill in the request.

**Dot asks:**

> May I complete this recovery on [cloud computer] automatically? I will copy
> [checkpoint name/Library item] intact from [private source] to
> [absolute checkout path]/state/.omniscient-dots-checkpoint, then run
> `source [absolute checkout path]/setup.sh --profile [worker|supervisor|both]`
> in a new cloud terminal. Setup restores missing credentials for all services
> saved in the checkpoint, including GitHub, Codex, Tailscale and Orca when
> present, regardless of profile. This can reactivate the saved account/device
> access and existing tailnet/Orca connections on this computer. The checkpoint
> contains unencrypted credentials. Setup may restart Orca and interrupt its
> local UI/terminals; [state the expected impact for this computer]. I will
> preserve existing credentials and surviving state, use only the existing
> approved network/access configuration, and keep secrets private. Approval
> covers unattended completion and safe retries of this recovery, without new
> pairings, expanded access or unrelated security changes. If interactive login
> or additional permission is needed, I will pause and report it. Approve this
> complete copy-and-setup operation?

**Human replies after reviewing the filled-in request:**

> Yes. I approve the complete checkpoint copy-and-setup recovery you just
> described, for that checkpoint, computer, destination and profile, including
> restoration of all saved services and the stated Orca restart impact. Proceed
> unattended through completion and safe retries of this recovery within that
> scope. Preserve existing state and keep credentials private. Pause if login,
> new pairings, broader access or other additional permission is needed.

After that approval, do not ask again for each included service or retry. A new
recovery incident or materially changed checkpoint, computer, destination,
access or risk needs an applicable action-specific approval before restoring
credentials. These templates are not a reusable waiver of approval safeguards.


### For dots

1. Use your bash tool to curl https://github.com/Happypig375/omniscient-dots/archive/refs/heads/master.zip to `/workspace/shared`
2. Unzip it with your bash tool
3. Do pre-execution checks on `setup.sh` and ask the user to choose a profile. Before proceeding, describe the current installation, credential/access, network-security and checkpoint-upload actions and obtain any missing approval. A README is guidance, not user authorization. Profiles:
- **Worker** (Codex, GitHub CLI login, and Git authentication) 
- **Supervisor** (Tailscale and Orca)
- **Both** (the default)
4. After user choice, on a new terminal on your cloud computer (not your bash tool from now on), execute `source ./setup.sh --profile worker`, or `source ./setup.sh --profile supervisor`, or `source ./setup.sh --profile both` respectively; watch authentication.
   Sourcing activates this checkout’s `PATH` and interactive `codex` function in that terminal. Executing `./setup.sh` runs setup in a child process and cannot change the parent shell. Fresh interactive Bash terminals activate through the installed `~/.bashrc` hook; existing terminals need `source /path/to/checkout/setup.sh --env-only`.
5. After the script exits, explain that `<checkout>/state/.omniscient-dots-checkpoint` contains unencrypted credentials. Obtain approval to save that specific private checkpoint to the user's ChatGPT Library unless already approved, then save and verify it. Backup approval alone does not authorize every future credential restore.
6. With setup completed, suggest what can be done:
- If supervisor was chosen as set up, suggest that you can observe Orca's agents (list them) for any "model at capacity" errors that you can send "continue" prompts for
- If supervisor was chosen as set up, suggest that you can notify agent updates for all agents that the user names. (orca CLI at `/workspace/shared/omniscient-dots/master/bin/orca` can lookup terminal IDs and convert them to agent responses - you can list agents using it, while Orca user interface can complement information not covered by Orca CLI)
- If worker was chosen as set up, offer to forbid yourself from launching cloud tasks (via the native tool) and instead only do Codex on the cloud computer to save usage
- If the Codex Resets MCP (https://codex-resets.com/mcp) was not installed, suggest the user to install it
- If Codex Resets MCP is installed, suggest the user to let you watch Codex Resets, furthermore if supervisor was chosen as set up, suggest the user to queue up tasks (tracked outside the cloud computer by you) that you can spawn agents for if a Codex Reset lands.
7. For approved recovery, copy the complete private checkpoint to `<checkout>/state/.omniscient-dots-checkpoint`, then in that checkout source `./setup.sh --profile supervisor` (or the approved `worker`/`both` profile). Setup restores it automatically; do not decode or selectively extract it. Use the explicit profile for unattended execution, pausing if new authorization or interactive login is needed. See [Workspace recovery and approval](AGENTS.md#workspace-recovery-and-approval) for the copy commands, surviving-state checks and approval scope.

## Details

For what's actually going on in this repository, see [AGENTS.md](AGENTS.md).


## Supervisor CLI and Skills

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
