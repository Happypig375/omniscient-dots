# Orca for dots

Set up Orca, persistent GitHub and Codex logins, and bidirectional Remote Orca
Server connections over a Tailscale tailnet. These scripts target a Linux x86_64
container and load its proxy and CA configuration from `/etc/environment`.

## Start here

Run these commands in your external Bash terminal. Replace
`/path/to/omniscient-dots` with the absolute path to your checkout throughout
this guide:

```bash
cd /path/to/omniscient-dots
source ./setup.sh
```

**No parameters are required.** Setup prepares browser CA trust, reuses or obtains
GitHub and Codex logins, configures Git with your GitHub identity and credentials,
installs a Bash startup hook, installs or updates Codex, downloads the Tailscale
fork and Orca AppImage, connects the tailnet, runs remote import, and then launches
the Orca desktop with the saved connections. Paste access links for discovered
servers when prompted, or press Enter to skip them. Reruns reuse saved logins and
Tailscale and Orca binaries; Codex checks the latest stable release on each run.
If Orca is already running with this folder's profile, setup asks you to press
Enter to relaunch it with the imported connections, or type
`skip` to keep it running.
Before launching, setup dismisses Orca's first-run onboarding wizard. Existing
completed or dismissed onboarding stays as saved. Setup also fits the window
to the current desktop's usable area, leaving the bottom bar and other panels
visible. It uses explicit bounds with maximization off, avoiding the margins
that this environment can add to maximized windows. Other profile settings are
preserved. These changes are made after any running desktop has exited.
Sourcing also configures your current terminal. `bash ./setup.sh` (or
`./setup.sh`) performs the same container setup.

Complete any GitHub, Codex, and Tailscale authorization prompts. For GitHub, copy
the device code and press Enter to open the browser in this environment. Restart
an already-open browser after setup imports the proxy CA. If the browser cannot
open, use `https://github.com/login/device` in another browser. For Codex, open
the printed device link in your normal browser outside this environment and
enter the code. Credentials are saved here regardless of which browser completes
authorization.

To connect both ways with another computer on your tailnet, follow
[Remote Orca Servers](#remote-orca-servers) after setup. The commands below use
`<connection-name>` for your chosen Orca connection name. SSH hosts, SSH
authentication, and `user@IP` arguments are unnecessary for these connections.

### Prerequisites

- A Linux x86_64 container, with a graphical display for the desktop.
- Bash, preinstalled `gh`, `git`, and OpenSSH (`/usr/bin/ssh`), Python 3.11 or
  newer, `curl`, `tar`, `openssl`, `certutil`, and the usual coreutils commands.
  On Debian, `certutil` comes from `libnss3-tools`.
- A working proxy and trusted, shell-compatible `/etc/environment` containing
  this environment's proxy and CA settings.
- GitHub, Codex, and Tailscale accounts. Both computers must be allowed to
  communicate on the tailnet.

## CLI locations

| CLI | From this folder | From any directory |
| --- | --- | --- |
| Orca | `bin/orca` | `/path/to/omniscient-dots/bin/orca` |
| Codex | `bin/codex` | `/path/to/omniscient-dots/bin/codex` |
| Tailscale | `bin/tailscale` | `/path/to/omniscient-dots/bin/tailscale` |
| SSH | `bin/ssh` | `/path/to/omniscient-dots/bin/ssh` |

After setup configures your terminal, `orca`, `codex`, `tailscale`, and `ssh` are
available through `PATH`. `bin/orca` launches the official CLI bundled in the downloaded
AppImage. `bin/orca-desktop` starts the graphical app; `bin/tailscaled` is the
Tailscale daemon.

## Commands

`setup.sh` handles environment loading, Bash startup configuration, browser CA
trust, and application setup. Both `bash ./setup.sh` and `source ./setup.sh`
perform the full setup for this container. Sourcing also applies the environment
to the calling terminal. Subcommands run only their selected action; they do not
repeat the full setup.

Replace placeholders in angle brackets with the described value, omitting the
brackets. Square brackets mark optional arguments.

| Command | Purpose | When needed |
| --- | --- | --- |
| `bash ./setup.sh` or `./setup.sh` | Full container setup, remote import, and desktop launch | First run; rerun to restore setup after a container restart or change |
| `source ./setup.sh` | Full container setup and configure the current Bash terminal | Recommended first run; also installs settings for future Bash terminals |
| `source ./setup.sh --env-only` | Reload only proxy and persistence settings | Automatic startup hooks and launchers; optional quick reload |
| `./setup.sh remote import [--no-add]` | Find tailnet Orca runtimes and add them using their access links | Add discovered servers by default; `--no-add` previews results |
| `./setup.sh remote share` | Publish the running local Orca desktop to the tailnet | To let another computer connect here |
| `./setup.sh remote pair <connection-name>` | Save and check a connection using a pasted access link | To connect to another Orca server; choose a local connection name |
| `./setup.sh remote resume` | Restart saved outgoing forwarding bridges | Optional recovery; the desktop launcher also does this |
| `./setup.sh shell` | Open an interactive Bash with this environment loaded | When you want proxy and persistence settings in a new terminal |
| `./setup.sh codex [<argument> ...]` | Run this folder's Codex with local persistent storage | For standalone Codex, including `codex resume` |
| `./setup.sh codex update` | Install or update the writable Codex CLI | Update Codex without repeating logins or desktop setup |
| `./setup.sh --help` | Show command usage | Optional reference |

Full setup runs `remote import` automatically. For bidirectional desktop use,
also run `remote share`. Use `remote pair <connection-name>` to add a particular
access link manually with a chosen name. The remaining remote helpers handle discovery
and userspace Tailscale routing. Use Orca's native CLI for server management and
headless serving. The former `ssh-config` mode and positional SSH targets are removed.
Existing SSH configuration and backups from earlier runs are left untouched.

## Terminal environment

Full setup adds a marked block to `~/.bashrc` that sources this folder's `setup.sh`
with `--env-only` by absolute path. Future interactive Bash terminals that read
`~/.bashrc` load
the settings automatically, from any working directory. Reruns update the same
block. Other startup settings and existing symlinks are preserved; changed files
are backed up under `state/bashrc-backups/` before each update. The hook skips
loading if this folder's `setup.sh` is unavailable. The hook's explicit
`--env-only` argument loads settings without repeating logins, CA imports, or
application startup whenever a terminal opens.

Sourcing performs full container setup, including installing the startup hook,
and keeps the environment in the current terminal:

```bash
source ./setup.sh
```

The installer runs in a child Bash so its strict options, logging traps, exits,
and command replacements do not take over the calling terminal. To reload only
the environment after setup, use `source ./setup.sh --env-only`.

Or open a configured child terminal:

```bash
./setup.sh shell
```

### Use the settings outside this folder

The working directory does not affect the settings. `setup.sh` resolves all paths
from its own location. After full setup, new interactive Bash terminals load it
automatically through `~/.bashrc`. In an already open Bash terminal, load it by
absolute path without repeating setup:

```bash
source /path/to/omniscient-dots/setup.sh --env-only
```

Then `gh`, `codex`, `orca`, and `orca-desktop` use this setup's environment even
when you work in another directory. Codex keeps the current working directory
as its project and saves its local state under this folder's `state/codex/`.
In interactive Bash, setup defines `codex` as a function that calls
`setup.sh codex`, refreshing the environment and applying the local launch
defaults each time. This folder's `bin/` is placed first on `PATH`, so applications
that resolve `codex` through `PATH` also use the local installation. Any
preinstalled Codex remains available at its original absolute path.

No manual `~/.bashrc` edit is needed. If the home directory is recreated or this
folder moves, rerun full setup to reinstall or refresh the startup hook.

For a configured child terminal from any directory, run:

```bash
/path/to/omniscient-dots/setup.sh shell
```

Existing terminals need the `source` command above. Programs launched from a
configured terminal inherit the settings; independently launched programs need
their own environment or this setup's launcher.

The environment loads proxy and CA settings from `/etc/environment`, clears
inherited `ALL_PROXY`/`all_proxy`, sets persistent application paths, and adds this
folder's `bin` directory to `PATH`. Applications must support the proxy variables;
this does not transparently route every network socket.
For Codex it also sets `CODEX_CA_CERTIFICATE` from `/etc/environment`'s explicit
value, or falls back to `SSL_CERT_FILE` and then `CODEX_PROXY_CERT`. An inherited
Codex-specific CA path is cleared before loading these settings.

Launch Orca through `bin/orca-desktop` to load these settings on later starts.
Its child processes inherit them. Fully quit and reopen an existing Orca process
after changing the environment scripts.

The desktop launcher defaults to dark mode using Electron's `--force-dark-mode`
flag. This applies when Orca's theme is set to System; an explicit Light or Dark
selection in Orca takes precedence. Fully quit Orca and reopen it through
`bin/orca-desktop` to apply the launcher preference.

On this X11 desktop, each full setup uses `xprop` to refresh Orca's saved window
bounds from the current workspace's work area, excluding space reserved by
panels. It uses no fixed resolution. If the display or its work area cannot be
queried, setup keeps the saved size. Later `bin/orca-desktop` launches restore
the saved bounds; rerun setup to fit them after changing the desktop size.

To investigate gaps around a maximized window, run this in the desktop's terminal
while Orca is maximized:

```bash
./setup.sh desktop diagnose
```

It reports the desktop work area, panel reservations, actual Orca window bounds,
space left at each edge, frame extents, size hints, and available Xfwm margin
settings. It leaves the window and desktop settings unchanged.

### Local SSH commands

Configured terminals use `bin/ssh` for ordinary SSH commands. Reload an already
open terminal to clear any cached path to the system client:

```bash
source ./setup.sh --env-only
```

The launcher runs `/usr/bin/ssh` with a private config in `state/ssh/config`.
This avoids `Bad owner or permissions` errors from system config includes
mounted with an owner such as `nobody`. It skips `/etc/ssh/ssh_config` and its
includes, and loads your normal `~/.ssh/config` through an `Include` directive
that retains OpenSSH's ownership and permission checks. See the
[OpenSSH `-F` option](https://man.openbsd.org/ssh.1).

Tailscale IPv4 addresses in `100.64.0.0/10`, IPv6 addresses in
`fd7a:115c:a1e0::/48`, and MagicDNS names ending in `.ts.net` automatically use
an SSH `ProxyCommand` through tailscaled's HTTP CONNECT proxy at
`127.0.0.1:1055`. Short peer names are recognized using `tailscale status --json`.
Literal IPs and full MagicDNS names work without a LocalAPI status lookup; the
proxy resolves MagicDNS itself. Other destinations use their normal SSH route.

SSH arguments, key selection, agent authentication, and host-key verification
use OpenSSH's normal behavior. Existing `ProxyCommand` and `ProxyJump` settings
take precedence over automatic routing. An explicit `ssh -F <config-file>` uses
your chosen config and its routing instead. You can add client defaults to
`state/ssh/config`; user settings are preserved while the automatic routing
include is refreshed. The daemon must be running and the destination must allow
SSH connections through its firewall and tailnet policy.

## Persistence

Keep the entire checkout folder to retain downloaded programs, logins,
settings, and local session history. Generated files are ignored by Git; a Git
clone alone does not restore them.

| Location relative to this folder | Contents |
| --- | --- |
| `state/gh/` | GitHub CLI settings and file-based credentials (`GH_CONFIG_DIR`) |
| `state/codex/` | Local Codex login, configuration, sessions, history, SQLite state, and logs (`CODEX_HOME`) |
| `state/codex/packages/standalone/` | Codex CLI releases and the current version selected by OpenAI's installer |
| `bin/codex` | Launcher symlink for the writable Codex CLI (`CODEX_INSTALL_DIR=bin/`) |
| `state/config/git/config` | GitHub commit identity and the `gh` Git credential helper |
| `state/ssh/config` | Local SSH client config, including the normal per-user SSH config |
| `state/ssh/tailscale.conf` | Generated rules for automatic SSH routing through the userspace Tailscale proxy |
| `bin/orca.AppImage.config/orca/` | Orca portable profile: application settings, managed accounts, session metadata, and remote server grants (`ORCA_USER_DATA_PATH`) |
| `state/config/`, `state/data/`, `state/cache/` | Other XDG configuration, data, and caches |
| `state/browser-trust/` | Persistent Chromium/Electron NSS certificate databases |
| `state/tailscale-backups/` | Previous startup files preserved when migrating existing Tailscale state |
| `state/bashrc-backups/` | Backups of Bash startup settings taken before hook updates |
| `state/remote-orca/` | Saved forwarding routes, discovery results, and remote-operation logs |
| `state/orca-cli-images/` | Extracted, version-specific bundled Orca CLI |
| `state/logs/`, `state/orca.log`, `state/tailscaled.log` | Setup and application diagnostics |
| `bin/`, beside the Tailscale binaries | Tailnet state and LocalAPI connection details maintained by the fork |

An empty local Tailscale state directory is a fresh setup; it does not require
migration. Setup initializes state here and requests a tailnet login as needed.
To preserve an older installation's identity, stop its daemon, wait for it to
exit, and explicitly select its state directory:

```bash
./setup.sh --tailscale-state-from /path/to/previous/tailscale-sandbox-state
```

Setup moves the selected state into this folder's `bin/tailscale-sandbox-state/`
and leaves the old path pointing here, so both installations' clients can reach
the same daemon. The authoritative state stays inside this folder. An existing
local identity is preserved; reconcile it before importing a different identity.
Unused local startup files are backed up before migration. Setup refuses to move
state while the earlier daemon holds its LocalAPI lock.

GitHub and standalone Codex credentials use private files. Keep this folder
private. The `.gitignore` allowlist tracks only the setup sources, launchers, and
this README, excluding all generated settings, credentials, binaries, and logs.

### GitHub identity for Git

After GitHub login, setup reads the authenticated account with `gh api user`.
Git commits default to its public display name (or username) and the private
`ID+USERNAME@users.noreply.github.com` address.

Setup also runs `gh auth setup-git`, so GitHub HTTPS fetches and pushes use the
saved GitHub CLI credentials. The identity and helper live in
`state/config/git/config`, loaded through `XDG_CONFIG_HOME` in configured
terminals and Orca. Repository-specific identity settings and explicit Git
environment overrides still take precedence. An existing `~/.gitconfig` is
also read after the XDG file and can override its defaults.

Rerunning setup refreshes the defaults from the active GitHub account. This
applies to future commits; existing commits retain their recorded author.
Commit signing remains controlled by your Git signing configuration.
See GitHub's [commit email reference](https://docs.github.com/en/account-and-profile/reference/email-addresses-reference)
and the [GitHub CLI credential helper](https://cli.github.com/manual/gh_auth_setup-git).

### Codex installation and updates

Setup installs a writable Codex CLI, including when a preinstalled copy is
read-only. It uses [OpenAI's standalone installer](https://developers.openai.com/codex/cli) with
`CODEX_INSTALL_DIR` set to this folder's `bin/` and `CODEX_HOME` set to
`state/codex/`. It installs the latest stable release without npm or root access.
The installer verifies downloads and reuses complete releases on reruns.

Full setup updates Codex before checking its login. To update just Codex, run:

```bash
./setup.sh codex update
```

In a configured interactive Bash, `codex update` runs the same action. New
launches use the selected version; an already-running Codex session continues
with its current executable. Saved logins, sessions, and settings stay in
`state/codex/`. Updating preserves these local settings.

The local installation still reads `/etc/codex/config.toml` defaults and
`/etc/codex/requirements.toml` restrictions. User settings and command-line
options can override ordinary defaults, while enforced requirements and host
restrictions still apply. Setup also continues to load `/etc/environment`.
See [configuration precedence](https://learn.chatgpt.com/docs/config-file/config-basic)
and [managed requirements](https://learn.chatgpt.com/docs/enterprise/managed-configuration).

### Local Codex sessions

`CODEX_HOME` controls Codex's session storage as well as its login and settings.
It is set to `state/codex/` within your checkout. Setup enables history
persistence and keeps the SQLite database and logs under that same directory.
It also clears an inherited `CODEX_SQLITE_HOME` that might point elsewhere.

Start or resume standalone Codex without configuring the parent terminal:

```bash
./setup.sh codex
./setup.sh codex resume
./setup.sh codex resume --last
```

After `source ./setup.sh` or `./setup.sh shell`, ordinary `codex` commands use the
same home. Transcript files normally appear under `state/codex/sessions/`, with
history in `state/codex/history.jsonl`. The explicit `setup.sh codex` launcher
and interactive Bash's `codex` function refresh the persistence configuration
before starting Codex and use the saved login instead of inherited API
credentials.

Orca may use its own managed Codex account homes inside its portable profile;
these also remain within this folder. Keep both `state/` and the portable Orca
profile. Sessions already started elsewhere retain their original storage;
this setup does not relocate them. Agent sessions running on a remote computer
are stored there, with that computer's own persistent configuration and agent
logins.

See the official OpenAI documentation for
[Codex state locations](https://learn.chatgpt.com/docs/config-file/environment-variables)
and [history configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

### Codex defaults

Full setup and `setup.sh codex` save these defaults in
`state/codex/config.toml`:

```toml
sandbox_mode = "workspace-write"
approval_policy = "on-request"
approvals_reviewer = "auto_review"
```

These settings select **Approve for me**: Codex works inside its workspace
sandbox and routes eligible requests for broader access to automatic review.
Setup preserves other settings, including additional writable roots under
`sandbox_workspace_write`.

System requirements in `/etc/codex/requirements.toml` still apply, including
managed networking and filesystem restrictions. Automatic review does not
expand those permissions. Setup does not change `/etc/codex` files.

Keep your project as the working directory and start Codex without extra flags:

```bash
/path/to/omniscient-dots/setup.sh codex
```

This refreshes the saved defaults before launching Codex. After full setup,
ordinary `codex` commands from any configured terminal use the same defaults.
In an already open Bash terminal, reload the function once with
`source /path/to/omniscient-dots/setup.sh --env-only`.
An already running session keeps its existing permissions; start a new invocation
to apply the updated configuration. Local interactive launches, `exec`, `review`,
and `doctor` also receive all three defaults through `-c` options, so ordinary
project and profile settings cannot replace them. Explicit sandbox or approval
flags, or later `-c` options, can override defaults for one invocation.
Administrator-enforced requirements and OS/container restrictions still apply.

Local interactive launches, including `resume` and `fork`, also receive
`--no-daemon` automatically. They run without the shared background server,
using the freshly loaded proxy and CA environment. An explicit `--no-daemon`
is passed through once. Explicit `--remote` connections, shared-server commands
such as `agents`, and administrative subcommands keep their original arguments.
Standalone `exec`, `review`, and `doctor` receive the permission settings without
`--no-daemon`.
In Bash, `command codex` bypasses the function and invokes the CLI on `PATH`
directly, without adding the launch options or refreshing this setup's settings.

Use `/status` inside Codex to check the effective sandbox and approval policy,
and `/debug-config` to inspect configuration layers and managed requirements.
`--no-daemon` controls the background server; approval prompts are configured
separately. See the official OpenAI documentation for
[sandbox configuration](https://learn.chatgpt.com/docs/config-file/config-advanced),
[automatic review](https://learn.chatgpt.com/docs/sandboxing/auto-review),
and [CLI flags](https://learn.chatgpt.com/docs/cli/reference).

These are standalone Codex defaults. Orca's native Codex sessions use Orca's
session permission settings separately.

### Codex account-routing timeouts

`account/read failed: workspace routing discovery timed out` happens while
Codex reads account-routing information during startup. It does not indicate
a denied write to `/tmp`. A timeout alone does not identify whether the proxy,
network policy, or service caused the failure. The default launch flags cannot
guarantee that this request succeeds.

The launcher reloads `/etc/environment` on every invocation, checks that the
selected Codex CA file is readable, and uses the saved login. Full setup checks
HTTPS transport to GitHub, `auth.openai.com`, and `chatgpt.com`, logging HTTP
status codes. Reaching those sites or passing `codex login status` does not
verify the authenticated account-routing request.

Retry through the configured launcher, then collect diagnostics if it still
fails:

```bash
./setup.sh codex
./setup.sh codex doctor --json
tail -n 60 state/codex/log/codex-tui.log
```

`doctor --json` emits a redacted report. Codex's runtime logs stay under
`state/codex/log/`; inspect logs for credentials before sharing them. See the
official OpenAI documentation for
[CA and diagnostic environment variables](https://learn.chatgpt.com/docs/config-file/environment-variables).

## Remote Orca Servers

Both computers can share their runtime and connect to the other computer's
runtime. Each direction uses a separate access link and saved grant. Remote
Orca Server connections provide access to files, terminals, and agents without
an SSH server.

Keep Orca running on both computers for the desktop flow. In Orca's settings,
**Remote Hosts is a category**; choose **Remote Orca Servers** within it.

### Import servers from the tailnet

Full setup runs this automatically before launching the desktop. This keeps the
terminal available for setup prompts and lets Orca load the saved connections
when it opens. To repeat import separately:

```bash
./setup.sh remote import
```

After import, full setup detects desktop instances using this folder's profile.
Press Enter at the relaunch prompt to request their normal exit and wait up to
20 seconds before starting Orca again. Type `skip` to leave the existing desktop
running. CLI commands, headless servers, and other profiles are excluded. If Orca
does not exit, close it normally and rerun setup.

This lists visible, online Tailscale IPv4 peers and checks their default Orca
runtime port, `6768`, through this container's Tailscale proxy. A successful Orca
handshake identifies a candidate. The command then adds candidates through Orca's
CLI, prompting for each new server's runtime access link. The prompt shows how
many characters were received and whether the link format and destination match.
The input line ends with `: `, for example, `Paste access link [waiting for paste]: `.
The access code stays hidden. Press Enter to submit, Ctrl-U to clear, or Enter
on an empty prompt to skip that server. Invalid links show a specific reason and
let you paste again. Existing saved connections are reused.

New connection names come from the peer's Tailscale DNS name, with port or numeric
suffixes when needed to keep names distinct. The command prints each saved Orca
connection name; use that name for `<connection-name>` in later CLI commands.
The access link must advertise the discovered server's tailnet IP and port.

To preview without adding anything:

```bash
./setup.sh remote import --no-add
./setup.sh remote import --no-add --json
```

Use `--port <port>` for another runtime port; repeat it to check multiple ports.
Use `--timeout <seconds>` to change the five-second deadline per probe (maximum
60 seconds). Up to eight probes run at once. Only the selected ports on peers
listed by Tailscale are checked. The server must expose its Orca listener to the
tailnet and allow the connection through its firewall and tailnet policy.

Discovery identifies reachable runtimes; the access link authorizes pairing.
The command does not obtain links from other computers or change their grants.
Results and import outcomes are saved in `state/remote-orca/discovery.json`.
Discovery runs during full setup and when you run `remote import` explicitly;
importing does not install a background scanner.

### Machine name and connection name

`<machine-name>` and `<tailnet-ip>` stand for the device name and Tailscale IPv4
address reported by your own tailnet. Use the server's address when generating
its access link.

| Where it appears | Meaning |
| --- | --- |
| Tailscale output such as `pong from <machine-name> (<tailnet-ip>)` | The machine's name reported by Tailscale |
| `<connection-name>` in `./setup.sh remote pair <connection-name>` | A connection name you choose and save locally in Orca |
| `--environment <connection-name>` in later Orca commands | Selects that previously saved Orca connection |
| `<tailnet-ip>` in the access link | The remote machine's tailnet IP address used for networking |

Replace `<connection-name>`, including the angle brackets, with your chosen Orca
connection name. Use that same name in subsequent `--environment <connection-name>` commands and when
selecting the connection in Orca. For an existing connection, use its saved name.
The pairing helper gets the destination address from the pasted access link;
the Tailscale machine name and the Orca connection name are independent.

### Allow another computer to connect here

1. Start this desktop through `bin/orca-desktop`, or let setup launch it.
2. In a terminal in this folder, run:

   ```bash
   ./setup.sh remote share
   ```

   This forwards the running desktop's runtime port through Tailscale Serve and
   prints this environment's tailnet address.
3. In this Orca, click **⚙️ → Remote Hosts → Remote Orca Servers**. Under
   **Advertise this app as a server**, click **New Link**. Use the printed
   tailnet connection address and generate an access link.
4. On the other computer, open **Remote Orca Servers → Add Server** and paste
   that link.

### Connect to another computer

1. On the remote computer, generate a runtime access link using that computer's
   Tailscale IPv4 address. Its Orca runtime must be running and reachable on
   the advertised port through its host firewall and tailnet policy.
2. In this environment, run:

   ```bash
   ./setup.sh remote import
   ```

3. Paste the remote computer's complete `orca://pair?code=...` access link. Check
   the character count and format status, then press Enter. The helper prints
   the saved connection name, adds it through Orca's bundled CLI, and checks it.
   The link supplies the actual remote IP, port, and pairing credentials.
4. Select the printed connection name in this Orca's
   **Remote Orca Servers** view. Restart Orca if it was already open when pairing
   completed.

To add one specific access link with a name you choose, use:

```bash
./setup.sh remote pair <connection-name>
```

The saved Orca connection name can contain letters, numbers, dots, dashes, or
underscores. Rerunning `pair` with the same name reuses the saved connection and
bridge. To replace an access link,
remove the saved server in Orca first, or run:

```bash
bin/orca environment rm --environment <connection-name>
./setup.sh remote pair <connection-name>
```

### Routing and recovery

This environment uses Tailscale userspace networking rather than a kernel TUN
interface. Incoming connections use Tailscale Serve. Outgoing Orca connections
use a background bridge from a private loopback port through tailscaled's HTTP
CONNECT proxy on `127.0.0.1:1055`.

Setup reuses a daemon reachable through its state directory, including migrated
state, and prints the client and running daemon versions. If that daemon
cannot be reached but port 1055 is occupied, setup reports the conflict before
starting another instance.

Pairing replaces only the access link's endpoint with the loopback endpoint;
Orca's token and pinned encryption key remain intact. This helper accepts private
`ws://` runtime access links advertised with a Tailscale IPv4 address in
`100.64.0.0/10`.

`remote pair` handles this container's routing before invoking Orca's native
`environment add`. To inspect or remove saved servers, use `bin/orca environment
list` and `bin/orca environment rm` directly.

To inspect or restore connections:

```bash
bin/tailscale serve status
bin/orca environment list
./setup.sh remote resume
bin/orca host name --environment <connection-name>
```

Here `--environment <connection-name>` selects the saved connection; `host name`
asks the remote runtime for its host name.

Outgoing bridges resume when `bin/orca-desktop` starts. Tailscale Serve forwarding
persists with the fork's tailnet state. Route metadata and logs live in
`state/remote-orca/`; pairing credentials remain in the private Orca profile.
Access links are not written to the helper's operation logs.

### Headless alternative

Fully close the local desktop before running:

```bash
source ./setup.sh --env-only
bin/tailscale serve --bg --tcp=6768 tcp://127.0.0.1:6768
./setup.sh remote resume
bin/orca serve --port 6768 --pairing-address "$(bin/tailscale ip -4)"
```

This runs Orca's official `serve` command in the foreground, advertises this
environment's tailnet IP on port `6768`, and prints a pairing link in the terminal.
Use another port consistently in the forwarding and `bin/orca serve` commands
to change it, and Ctrl-C to stop the server. The desktop and headless server
must not use the same profile simultaneously.

Both modes use the persistent paths above. Processes stay alive only while this
environment and those processes are running; no system service is installed.
To remove incoming forwarding on the default port:

```bash
bin/tailscale serve --bg --tcp=6768 off
```

These scripts configure the local environment. Configure sharing links,
agent logins, firewall, and tailnet access on each remote computer separately.

## Browser certificate trust

Full setup imports the proxy CA specified by `/etc/environment` into Chromium and
Electron's NSS trust databases automatically. There is no separate CA command.

Close Chromium and Orca first if their existing trust databases need relocation.
The script preserves those databases under `state/browser-trust/` and creates
symlinks from the normal home-directory locations. Restart the applications
after importing the CA. Other applications using those databases also trust the
CA; TLS certificate verification remains enabled.

If the setup folder moved and those symlinks still point to its missing old
`state/browser-trust/` directories, rerunning setup repairs them after preparing
the current trust databases. When the home directory is read-only, setup creates
a forwarding symlink at the writable old target instead. Existing links to other
databases are preserved.

If Chromium does not pick up the proxy automatically, start it from a configured
terminal, using the proxy variable supplied by `/etc/environment`, for example:

```bash
source ./setup.sh --env-only
chromium --proxy-server="$https_proxy" https://github.com/login/device
```

## Files and launchers

| Source | Role |
| --- | --- |
| `setup.sh` | Terminal environment, Bash startup hook, browser CA trust, application setup, and command dispatch |
| `remote-orca.py` | Tailnet forwarding, private pairing prompt with paste feedback, and outgoing bridges |
| `bin/orca-desktop` | Reopens the desktop with the environment and saved bridges |
| `bin/orca` | Runs the official CLI bundled in the downloaded AppImage |
| `bin/ssh` | Runs OpenSSH with local client config and automatic Tailscale proxy routing |

The Orca launchers use AppImage extraction without requiring FUSE. Chromium's sandbox
stays enabled. The CLI extracts each downloaded AppImage version once under
`state/orca-cli-images/`, using Orca's bundled runtime rather than a separate Node
installation. After setup or `source ./setup.sh --env-only`, inspect its
version-matched guides with:

```bash
orca --help
orca skills list
orca skills get orca-cli --full
orca environment list
orca host name --environment <connection-name>
```

## Logs and updates

Each full setup run creates a private
`state/logs/setup-<utc-timestamp>-<random-suffix>.log`. The terminal and log include
timestamped stages, tool versions and paths, kernel/architecture, source-file
hashes, release selection, and failures with their stage, source line, and exit
status. These provide a comparison when an environment update breaks setup.
Source files and `/etc/environment` are hashed, not dumped.

Interactive authorization output, device codes, and login details go directly
to the terminal rather than the setup log. Codex sessions are saved by Codex in
its persistent home; `setup.sh codex` and `setup.sh shell` are not recorded by
setup's logger. Remote helper actions have `state/remote-orca/operations.log`.
Orca and tailscaled append to `state/orca.log` and `state/tailscaled.log` across
restarts. Application logs may contain separate authorization details.

For diagnosis, share the failed stage and nearby output from the setup log.
Syntax and isolated browser-trust, launcher, and Tailscale lifecycle checks have
been run here. Run setup in your external terminal to check real authorization,
network access, and desktop behavior in that environment.

Codex uses OpenAI's installer and checks for updates on every full setup run.
Tailscale and Orca downloads come from the latest GitHub releases of
[`Happypig375/tailscale`](https://github.com/Happypig375/tailscale/releases) and
[`stablyai/orca`](https://github.com/stablyai/orca/releases). Their installed
binaries are reused on reruns. To update them, stop the affected programs,
remove only their downloaded binaries, and rerun setup. Preserve the adjacent Tailscale state,
Orca portable profile, and `state/`. The bundled CLI cache is selected by the
AppImage's SHA-256 hash.

Replacing a running Tailscale daemon requires a brief reconnect. To switch to
the already-installed `bin/tailscaled`, identify the process listening on 1055
with `ss -ltnp 'sport = :1055'`, stop that specific process with `kill -TERM <pid>`,
and rerun setup after it exits. Preserve `tailscale-sandbox-state/` so the new
process retains the tailnet identity and saved configuration. Use
`--tailscale-state-from` only when deliberately importing another installation's
saved identity; setup does not automatically stop or upgrade a running daemon.
When fetching a newer fork release, replace the downloaded
binaries while retaining this state directory.

## References

- [Orca Remote Servers](https://www.onorca.dev/docs/remote-servers)
- [Orca CLI reference](https://www.onorca.dev/docs/cli/reference)
- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [Chromium certificate management on Linux](https://chromium.googlesource.com/chromium/src.git/+/refs/heads/main/docs/linux/cert_management.md)
- [Codex authentication](https://developers.openai.com/codex/auth)
