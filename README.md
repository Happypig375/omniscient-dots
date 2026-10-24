![omniscient-dots](omniscient-dots.png)

With initialization and authentication for your dot, it gains Codex (instead of only expensive cloud tasks) access on the cloud computer and/or [Orca](https://www.onorca.dev) to supervise any of your agents running anywhere on your [tailnet](https://tailscale.com/docs/concepts/tailnet) (think any computer that you have access to).

> [!WARNING]
> Cloud computer files can disappear at any time while the desktop and processes survive, and the cloud computer may reboot losing all processes. The cloud computer disk is unreliable for long-running work. Therefore, you should keep durable documents in a remote (like GitHub or ChatGPT Library).

## Setup instructions

### 1. Human sends to dot

> Use https://github.com/Happypig375/omniscient-dots

The dot will read this README.

### 2. Dot asks for approval alongside pre-execution checks on setup.sh

> May I complete this recovery on [cloud computer] automatically? I will copy
> [checkpoint name/Library item] while it exists intact from [private source] to
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

Fill in every bracketed value, naming the actual checkpoint, computer, destination
and profile; describe the complete operation rather than asking service by
service.

### 3. Human sends approval

A short **Yes**, **Sure**, or **Approved** suffices for the specific pending,
fully described scope above. The assistant fills in the details; the human need
not repeat boilerplate. Choosing Worker, Supervisor or Both is a separate profile
choice, not approval to restore credentials. Resolve that choice before presenting
the exact recovery scope.

Reuse approval for safe retries within that scope. A new recovery incident or a
material change to source, computer, destination, access or risk requires applicable
action-specific approval. Pause for interactive login, new pairing or broader access.
Backup upload or replacement in Library requires separate consent naming its private
destination; recovery approval alone does not authorize an upload.

### 4. Dot continues setup

Follow [setup.md](setup.md).

## Repo maintenance details

For observational Codex quota/session usage and synthetic replay, see
[Usage tracking](USAGE.md). It reports unknown payer mappings and has no
automatic account or task actions.

Refer to [AGENTS.md](AGENTS.md). The mandatory
[completion gate](setup.md#mandatory-completion-gate) follows installation and
backup; exit zero alone does not complete the workflow.
