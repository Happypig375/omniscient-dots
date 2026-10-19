#!/usr/bin/env bash
# Run: bash ./setup.sh, or source ./setup.sh to configure this terminal too
# Reopen a configured terminal: ./setup.sh shell
# Reload just the environment: source ./setup.sh --env-only
# Start or resume local Codex: ./setup.sh codex [<argument> ...]
# Tailnet support: ./setup.sh remote {import,resume,share,pair}
# Checkpoint: ./setup.sh checkpoint {save,restore} [<file>]

# The same file is sourced by ~/.bashrc and used as the optional shell rcfile.
# Load the user's usual rcfile once before applying this environment.
if [[ ${BASH_SOURCE[0]} != "$0" && ${ORCA_DOTS_LOAD_BASHRC:-} == 1 ]]; then
    unset ORCA_DOTS_LOAD_BASHRC
    [[ ! -f ~/.bashrc ]] || source ~/.bashrc
    ORCA_DOTS_LOAD_BASHRC=1
fi
_orca_load_environment() {
    local autoexport=0
    unset ALL_PROXY all_proxy CODEX_CA_CERTIFICATE
    [[ $- == *a* ]] && autoexport=1
    set -a
    # /etc/environment is trusted and shell-compatible in this environment.
    if ! source /etc/environment; then
        (( autoexport )) || set +a
        return 1
    fi
    (( autoexport )) || set +a
    # Codex's dedicated CA variable takes precedence over SSL_CERT_FILE. Use
    # this environment's CA rather than an inherited path from another shell.
    if [[ -n ${CODEX_CA_CERTIFICATE:-${SSL_CERT_FILE:-${CODEX_PROXY_CERT:-}}} ]]; then
        export CODEX_CA_CERTIFICATE="${CODEX_CA_CERTIFICATE:-${SSL_CERT_FILE:-${CODEX_PROXY_CERT:-}}}"
    fi
    export ORCA_DOTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    export GH_CONFIG_DIR="$ORCA_DOTS_DIR/state/gh"
    export CODEX_HOME="$ORCA_DOTS_DIR/state/codex"
    export CODEX_INSTALL_DIR="$ORCA_DOTS_DIR/bin"
    unset CODEX_SQLITE_HOME
    export XDG_CONFIG_HOME="$ORCA_DOTS_DIR/state/config"
    export XDG_DATA_HOME="$ORCA_DOTS_DIR/state/data"
    export XDG_CACHE_HOME="$ORCA_DOTS_DIR/state/cache"
    case "${PATH:-}" in
        "$ORCA_DOTS_DIR/bin"|"$ORCA_DOTS_DIR/bin":*) ;;
        *) export PATH="$ORCA_DOTS_DIR/bin${PATH:+:$PATH}" ;;
    esac
    hash -r
    if [[ ${ORCA_SYSTEM_SSH_PATH:-} == "$ORCA_DOTS_DIR/bin/ssh" ]]; then
        unset ORCA_SYSTEM_SSH_PATH
    fi
    export ORCA_USER_DATA_PATH="$ORCA_DOTS_DIR/bin/orca.AppImage.config/orca"
    if [[ $- == *i* ]]; then
        # Keep terminal invocations on the same setup path. The writable CLI
        # in bin/ takes precedence over the preinstalled executable.
        function codex {
            "$ORCA_DOTS_DIR/setup.sh" codex "$@"
        }
    fi
}
_orca_load_environment || {
    unset -f _orca_load_environment
    return 1 2>/dev/null || exit 1
}
unset -f _orca_load_environment
# Startup hooks and launchers explicitly load only the environment. Recognize
# the previous ~/.bashrc hook as well, so it can be upgraded on the next setup.
if [[ ${1:-} == --env-only || ${ORCA_DOTS_LOAD_BASHRC:-} == 1 ||
      ( ${BASH_SOURCE[0]} != "$0" && ${BASH_SOURCE[1]:-} == "$HOME/.bashrc" ) ]]; then
    unset ORCA_DOTS_LOAD_BASHRC
    return 0 2>/dev/null || exit 0
fi
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    # The environment above stays in the caller. Run the same installer in a
    # child Bash so its exits, execs, strict options and logging traps stay there.
    if bash "$ORCA_DOTS_DIR/setup.sh" "$@"; then
        return 0
    else
        return $?
    fi
fi

set -Eeuo pipefail
umask 077
ROOT=$ORCA_DOTS_DIR

checkpoint() {
    python3 "$ROOT/persistence.py" "$@"
}

trust_browser_ca() (
    fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
    for tool in certutil openssl; do
        command -v "$tool" >/dev/null || fail "Required command: $tool (certutil is supplied by libnss3-tools)."
    done
    ca=${CODEX_PROXY_CERT:-${SSL_CERT_FILE:-}}
    [[ -n $ca && -r $ca ]] || fail 'No readable proxy CA in CODEX_PROXY_CERT or SSL_CERT_FILE.'
    constraints=$(openssl x509 -in "$ca" -noout -ext basicConstraints)
    [[ $constraints == *CA:TRUE* ]] || fail "The certificate is not a CA: $ca"
    openssl x509 -in "$ca" -noout -subject -fingerprint -sha256

    # Keep both older and newer Chromium/Electron NSS stores beside this setup.
    mkdir -p "$ROOT/state/browser-trust"
    for generation in legacy modern; do
        case "$generation" in
            legacy) db="$HOME/.pki/nssdb" ;;
            modern) db="$HOME/.local/share/pki/nssdb" ;;
        esac
        persistent="$ROOT/state/browser-trust/$generation"
        mkdir -p "$(dirname -- "$db")"
        repair_link=0
        if [[ -L $db && ! -e $db ]]; then
            previous=$(readlink -- "$db")
            # A moved setup can leave links to its old, now-missing storage.
            # Only repair links created with this setup's storage layout.
            [[ $previous == /*/state/browser-trust/"$generation" ]] ||
                fail "Existing $db is managed elsewhere; import the CA there manually."
            repair_link=1
        elif [[ -e $db || -L $db ]]; then
            if [[ ! $db -ef $persistent ]]; then
                [[ ! -L $db && -d $db ]] || fail "Existing $db is managed elsewhere; import the CA there manually."
                [[ ! -e $persistent ]] || fail "Both $db and $persistent exist; reconcile them before rerunning."
                command -v pgrep >/dev/null || fail 'pgrep is required before relocating an existing browser database.'
                if pgrep -u "$(id -u)" -x 'chromium|chrome|orca|Orca' >/dev/null; then
                    fail 'Close Chromium and Orca, then rerun setup to relocate their existing trust databases.'
                fi
                mv -T -- "$db" "$persistent"
                if ! ln -s -- "$persistent" "$db"; then
                    mv -T -- "$persistent" "$db"
                    fail "Could not link $db to persistent storage."
                fi
            fi
        fi
        mkdir -p "$persistent"
        chmod 700 "$persistent"
        if [[ ! -f $persistent/cert9.db ]]; then
            certutil -N --empty-password -d "sql:$persistent"
        fi
        nickname='orca-for-dots environment proxy CA'
        if certutil -L -d "sql:$persistent" -n "$nickname" >/dev/null 2>&1; then
            certutil -D -d "sql:$persistent" -n "$nickname"
        fi
        certutil -A -d "sql:$persistent" -n "$nickname" -t 'C,,' -i "$ca"
        if (( repair_link )); then
            # Import successfully before replacing the dangling link.
            if [[ $db -ef $persistent ]]; then
                : # Creating the database restored a link to this setup.
            elif [[ -w $(dirname -- "$db") ]]; then
                ln -sfnT -- "$persistent" "$db"
            else
                # Home may be mounted read-only while the old setup path is
                # still writable. Restore that target without changing home.
                mkdir -p "$(dirname -- "$previous")"
                ln -sT -- "$persistent" "$previous"
            fi
            printf 'Browser trust link restored: %s resolves to %s\n' "$db" "$persistent"
        else
            [[ -e $db || -L $db ]] || ln -s -- "$persistent" "$db"
        fi
    done
    printf 'Proxy CA trusted for HTTPS in Chromium/Electron. Restart Chromium and Orca.\n'
    printf 'Trust databases persist in %s/state/browser-trust/.\n' "$ROOT"
)

login_github() (
    # gh's browser child otherwise inherits the terminal used for device prompts.
    local browser_dir
    browser_dir=$(mktemp -d "$ROOT/.github-browser.XXXXXX")
    trap 'rm -rf -- "$browser_dir"' EXIT
    cat > "$browser_dir/open" <<'SH'
#!/usr/bin/env bash
if [[ -n ${ORCA_LOGIN_GH_BROWSER:-} ]]; then
    export GH_BROWSER=$ORCA_LOGIN_GH_BROWSER
else
    unset GH_BROWSER
fi
# Reuse gh's browser selection, including BROWSER and the platform default.
# Redirect before starting the browser so its descendants stay quiet as well.
exec gh browse "$@" </dev/null >/dev/null 2>&1
SH
    chmod 700 "$browser_dir/open"
    ORCA_LOGIN_GH_BROWSER=${GH_BROWSER:-} GH_BROWSER="\"$browser_dir/open\"" \
        gh auth login --hostname github.com --git-protocol https --web --insecure-storage
)

configure_github_git() (
    local git_config="$XDG_CONFIG_HOME/git/config" identity
    identity=$(gh api --hostname github.com user --jq '{login, id, name}')
    python3 - "$git_config" "$identity" <<'PY'
from pathlib import Path
import json, re, subprocess, sys

config = Path(sys.argv[1])
account = json.loads(sys.argv[2])
login, account_id = account.get('login'), account.get('id')
if (not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9-]+', login)
        or type(account_id) is not int or account_id <= 0):
    sys.exit('GitHub did not return a valid account identity.')
name = (account.get('name') or login).strip() or login
if any(ord(character) < 32 or character in '<>' for character in name):
    sys.exit('The GitHub display name cannot be used as a Git author name.')
email = f'{account_id}+{login}@users.noreply.github.com'
config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
config.touch(mode=0o600, exist_ok=True)
config.chmod(0o600)
for key, value in (('user.name', name), ('user.email', email)):
    subprocess.run(['git', 'config', '--file', str(config), '--replace-all', key, value], check=True)
print(f'GitHub Git identity: {name} <{email}>')
PY
    # Keep the credential helper beside the rest of this setup's settings.
    # Git reads this XDG global config in configured terminals and Orca.
    GIT_CONFIG_GLOBAL="$git_config" gh auth setup-git --hostname github.com
    printf 'GitHub HTTPS authentication uses gh; Git defaults saved in %s\n' "$git_config"
)

update_codex() (
    for tool in curl sh tar; do
        command -v "$tool" >/dev/null || { printf 'Error: Codex installation requires %s.\n' "$tool" >&2; exit 1; }
    done
    mkdir -p "$ROOT/bin" "$CODEX_HOME"
    chmod 700 "$CODEX_HOME"
    local download_dir
    download_dir=$(mktemp -d "$ROOT/.codex-update.XXXXXX")
    trap 'rm -rf -- "$download_dir"' EXIT
    printf 'Installing or updating the latest stable Codex CLI in %s/bin\n' "$ROOT"
    # Use OpenAI's standalone installer: no npm or write access to /opt is
    # needed. It verifies downloads, caches releases and switches symlinks.
    curl --fail --silent --show-error --location --connect-timeout 10 --max-time 60 \
        https://chatgpt.com/codex/install.sh --output "$download_dir/install.sh"
    # Limit installer command discovery to this CLI and system tools. The
    # managed /opt binary can be mistaken for an npm install, which makes the
    # installer try to edit a read-only home profile despite our existing PATH.
    PATH="$ROOT/bin:/usr/bin:/bin" CODEX_INSTALL_DIR="$ROOT/bin" CODEX_NON_INTERACTIVE=1 \
        CODEX_INSTALL_DAEMON_ONLY=0 CODEX_INSTALL_DEFER_SELECTION=0 \
        sh "$download_dir/install.sh" --release latest
    "$ROOT/bin/codex" --version
)

configure_codex() {
    mkdir -p "$CODEX_HOME"
    chmod 700 "$CODEX_HOME"
    # Use this folder's saved login instead of inherited environment credentials.
    unset OPENAI_API_KEY CODEX_API_KEY CODEX_ACCESS_TOKEN
    unset OPENAI_IDENTITY_TOKEN_FILE OPENAI_IDENTITY_TOKEN_AUDIENCE
    python3 - "$CODEX_HOME" <<'PY'
from pathlib import Path
import json, re, sys, tomllib

home = Path(sys.argv[1])
p = home / 'config.toml'
s = p.read_text() if p.exists() else ''
# Keep unrelated settings and comments. Managed paths are refreshed if moved.
table = re.search(r'(?m)^[ \t]*\[', s)
head, tables = (s[:table.start()], s[table.start():]) if table else (s, '')
head = re.sub(r'(?m)^[ \t]*(?:cli_auth_credentials_store|sqlite_home|log_dir|sandbox_mode|approval_policy|approvals_reviewer)[ \t]*=.*(?:\n|$)', '', head)
settings = {'cli_auth_credentials_store': 'file', 'sqlite_home': str(home),
            'log_dir': str(home / 'log'), 'sandbox_mode': 'workspace-write',
            'approval_policy': 'on-request', 'approvals_reviewer': 'auto_review'}
head = ''.join(f'{key} = {json.dumps(value)}\n' for key, value in settings.items()) + head
# Preserve other history and sandbox options, including additional writable
# roots. Keep /tmp writable with the workspace-write default.
for section, key, value in (
    ('history', 'persistence', '"save-all"'),
    ('sandbox_workspace_write', 'exclude_slash_tmp', 'false'),
):
    dotted = re.escape(section + '.' + key)
    head = re.sub(r'(?m)^[ \t]*' + dotted + r'[ \t]*=.*(?:\n|$)', '', head)
    table = re.search(r'(?m)^[ \t]*\[' + re.escape(section) + r'\][ \t]*(?:#[^\n]*)?(?:\n|$)', tables)
    if table:
        rest = tables[table.end():]
        following = re.search(r'(?m)^[ \t]*\[', rest)
        end = following.start() if following else len(rest)
        body = re.sub(r'(?m)^[ \t]*' + re.escape(key) + r'[ \t]*=.*(?:\n|$)', '', rest[:end])
        tables = tables[:table.end()].rstrip('\n') + f'\n{key} = {value}\n' + body + rest[end:]
    else:
        head = f'{section}.{key} = {value}\n' + head
s = head.rstrip('\n') + '\n\n' + tables
tomllib.loads(s)  # Refuse to replace the config with invalid TOML.
p.write_text(s)
p.chmod(0o600)
PY
}

tailscale_status() {
    # NeedsLogin and other stopped states may return a nonzero exit code while
    # still supplying valid status JSON. Error text alone does not mean ready.
    tailscale status --json > "$TMP/status.json" 2>/dev/null || true
    python3 - "$TMP/status.json" <<'PY'
import json, sys

try:
    with open(sys.argv[1]) as status_file:
        status = json.load(status_file)
    valid = isinstance(status, dict) and isinstance(status.get('BackendState'), str) and bool(status['BackendState'])
except (OSError, ValueError):
    valid = False
sys.exit(0 if valid else 1)
PY
}

check_tailscale_proxy() {
    python3 - "${1:-1055}" <<'PY'
import errno, socket, sys

address = ('127.0.0.1', int(sys.argv[1]))
with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(address)
        listener.listen(1)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            sys.exit(f'Tailscale proxy {address[0]}:{address[1]} is already in use. '
                     'An earlier tailscaled may be running from another installation. '
                     f'Identify the listener with: ss -ltnp "sport = :{address[1]}". '
                     'Reuse its state or stop only that instance before rerunning setup.')
        sys.exit(f'Cannot listen on Tailscale proxy {address[0]}:{address[1]}: {error}')
PY
}

prepare_orca_launch() {
    python3 - "$ROOT" <<'PY'
from pathlib import Path
import os, signal, sys, time

PROC = Path('/proc')

def process_start(pid):
    try:
        fields = (PROC / str(pid) / 'stat').read_text().rpartition(')')[2].split()
        return fields[19] if fields[0] not in ('Z', 'X') else None
    except (OSError, IndexError):
        return None

def desktop_instances(profile, image):
    instances = {}
    for process in PROC.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            # Match the Electron main process; renderers, CLI commands and
            # headless servers must not be stopped with the desktop.
            executable = os.readlink(process / 'exe').removesuffix(' (deleted)')
            if Path(executable).name.lower() not in ('orca', 'orca-ide'):
                continue
            arguments = (process / 'cmdline').read_bytes().split(b'\0')
            if any(argument == b'--serve' or argument == b'--type' or argument.startswith(b'--type=')
                   for argument in arguments):
                continue
            environment = dict(entry.split(b'=', 1) for entry in (process / 'environ').read_bytes().split(b'\0')
                               if b'=' in entry)
            if environment.get(b'ELECTRON_RUN_AS_NODE') == b'1' or environment.get(b'ORCA_ORCAD_SIDECAR') == b'1':
                continue
            configured_profile = environment.get(b'ORCA_USER_DATA_PATH')
            if configured_profile:
                matches = Path(os.fsdecode(configured_profile)).resolve() == profile
            else:
                appimage = environment.get(b'APPIMAGE')
                matches = bool(appimage) and Path(os.fsdecode(appimage)).resolve() == image
            pid = int(process.name)
            started = process_start(pid)
            if matches and started is not None:
                instances[pid] = started
        except (OSError, ValueError):
            continue  # A process may exit while its metadata is being read.
    return instances

def request_exit(pid, started):
    descriptor = None
    try:
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            descriptor = os.pidfd_open(pid)
        if process_start(pid) != started:
            return
        if descriptor is not None:
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)

def prepare_launch(root, timeout=20):
    profile = Path(os.environ.get('ORCA_USER_DATA_PATH', root / 'bin/orca.AppImage.config/orca')).resolve()
    image = (root / 'bin/orca.AppImage').resolve()
    instances = desktop_instances(profile, image)
    if not instances:
        return 0
    print('Closing Orca before relaunching...', flush=True)
    for pid, started in instances.items():
        request_exit(pid, started)
    deadline = time.monotonic() + timeout
    while any(process_start(pid) == started for pid, started in instances.items()):
        if time.monotonic() >= deadline:
            print('Orca did not exit before the shutdown deadline.', file=sys.stderr)
            return 1
        time.sleep(0.2)
    if desktop_instances(profile, image):
        print('Another Orca instance started during relaunch. Rerun setup to review it.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    try:
        sys.exit(prepare_launch(Path(sys.argv[1])))
    except KeyboardInterrupt:
        sys.exit(130)
    except OSError as error:
        sys.exit(f'Could not prepare the Orca relaunch: {error}')
PY
}

configure_orca_desktop() (
    python3 "$ROOT/orca-locks.py" "$ORCA_USER_DATA_PATH"
    # Use the installed build's own profile schema, locks and SQLite writer.
    digest=$(sha256sum "$ROOT/bin/orca.AppImage")
    cache="$ROOT/state/orca-cli-images/${digest%% *}"
    [[ -x "$cache/resources/bin/orca-ide" ]] || "$ROOT/bin/orca" --version
    electron=''
    for candidate in orca-ide orca Orca; do
        if [[ -f "$cache/$candidate" && -x "$cache/$candidate" ]]; then
            electron="$cache/$candidate"
            break
        fi
    done
    [[ -n $electron ]] || fail 'Cannot locate the bundled Orca runtime for profile setup.'
    unset NODE_OPTIONS NODE_REPL_EXTERNAL_MODULE
    ELECTRON_RUN_AS_NODE=1 "$electron" - "$cache/resources/app.asar.unpacked/out" "$ORCA_USER_DATA_PATH" <<'JS'
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const [out, userData] = process.argv.slice(2);
let phase = 'loading the installed profile helpers';
let maintenance, authority, temporary;
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function configure() {
    const profilePath = name => path.join(out, 'main/persistence/profile-state', name + '.js');
    const {acquireProfileStateMaintenance} = require(profilePath('profile-state-access'));
    const {getActiveProfileStateLocation} = require(profilePath('profile-state-active-location'));
    // This native offline reader checks recovery evidence and JSON/SQLite agreement.
    const {readAgentHookSettingsFromProfileState: validateStorage} = require(profilePath('profile-state-offline-settings'));
    const {classifyProfileStateStorage} = require(profilePath('profile-state-storage-classification'));
    const {readProfileStateDomains} = require(profilePath('profile-state-domain-reader'));
    const {getDefaultPersistedState, getDefaultUIState} = require(path.join(out, 'shared/constants.js'));
    const {getDefaultOnboardingState, ONBOARDING_FLOW_VERSION} = require(path.join(out, 'shared/onboarding-defaults.js'));
    const {DEFAULT_LOCAL_ORCA_PROFILE_ID} = require(path.join(out, 'shared/orca-profiles.js'));
    phase = 'waiting for exclusive access to the stopped Orca profile';
    maintenance = acquireProfileStateMaintenance(userData);
    phase = 'resolving the active Orca profile';
    let location = getActiveProfileStateLocation(userData);
    if (!location) {
        // Follow Orca's offline CLI bootstrap, including its legacy-state checks.
        const legacy = {dataFile: path.join(userData, 'orca-data.json'),
            databaseFile: path.join(userData, 'profile-state.db'), profileId: DEFAULT_LOCAL_ORCA_PROFILE_ID};
        const storage = classifyProfileStateStorage(legacy.dataFile, legacy.databaseFile);
        if (storage !== 'json-only' && storage !== 'neither') throw new Error('Profile state has no active profile index');
        validateStorage(legacy);
        const {ensureActiveOrcaProfile} = require(path.join(out, 'main/orca-profiles/profile-index-store.js'));
        const profile = ensureActiveOrcaProfile(userData);
        location = {dataFile: profile.dataFile, databaseFile: profile.stateDatabaseFile, profileId: profile.profile.id};
    }
    fs.mkdirSync(path.dirname(location.dataFile), {recursive: true, mode: 0o700});
    maintenance.assertProfile(location.profileId, location.dataFile, location.databaseFile);
    phase = 'validating the saved profile state';
    validateStorage(location);
    const storage = classifyProfileStateStorage(location.dataFile, location.databaseFile);
    let state, snapshot, previous, previousUI;
    if (storage === 'neither' || storage === 'json-only') {
        state = fs.existsSync(location.dataFile)
            ? JSON.parse(fs.readFileSync(location.dataFile, 'utf8')) : getDefaultPersistedState(os.homedir());
        if (!record(state)) throw new Error('Invalid profile state');
        previous = state.onboarding;
        previousUI = state.ui;
    } else {
        snapshot = readProfileStateDomains(location.databaseFile, location.profileId, ['onboarding', 'ui']);
        if (snapshot.kind === 'unreadable') throw snapshot.error;
        previous = snapshot.values.get('onboarding');
        previousUI = snapshot.values.get('ui');
    }
    const updates = new Map();
    if (record(previous) && Number.isFinite(previous.closedAt) && previous.closedAt >= 0) {
        console.log('Orca onboarding is already closed.');
    } else {
        const defaults = getDefaultOnboardingState();
        updates.set('onboarding', {...defaults, ...(record(previous) ? previous : {}),
            flowVersion: ONBOARDING_FLOW_VERSION, closedAt: Date.now(), outcome: 'dismissed', lastCompletedStep: -1,
            checklist: {...defaults.checklist, ...(record(previous?.checklist) ? previous.checklist : {}), dismissed: true}});
    }
    if (previousUI?.windowMaximized !== false) {
        updates.set('ui', {...(record(previousUI) ? previousUI : getDefaultUIState()), windowMaximized: false});
    }
    console.log('Orca window will be fitted and checked after launch.');
    if (!updates.size) return;
    phase = 'saving the desktop settings';
    if (state) {
        for (const [domain, value] of updates) state[domain] = value;
        temporary = path.join(path.dirname(location.dataFile), `.setup-desktop-${crypto.randomUUID()}.tmp`);
        fs.writeFileSync(temporary, JSON.stringify(state), {flag: 'wx', mode: 0o600});
        fs.renameSync(temporary, location.dataFile);
        temporary = undefined;
    } else {
        const chunks = path.join(out, 'main/chunks');
        const files = fs.readdirSync(chunks).filter(name => name.startsWith('profile-state-sqlite-authority-') && name.endsWith('.js'));
        if (files.length !== 1) throw new Error('Unsupported profile writer');
        const Authority = Object.values(require(path.join(chunks, files[0]))).find(value =>
            typeof value === 'function' && typeof value.prototype?.writeSerializedDomains === 'function'
            && typeof value.prototype?.initializeFromRevision === 'function');
        if (!Authority) throw new Error('Unsupported profile writer');
        authority = new Authority(location.databaseFile, location.profileId);
        authority.initializeFromRevision(snapshot.revision);
        authority.writeSerializedDomains(Array.from(updates, ([domain, value]) =>
            ({domain, payload: JSON.stringify(value)})));
    }
    if (updates.has('onboarding')) console.log('Orca first-run onboarding dismissed.');
}

try {
    configure();
} catch (error) {
    console.error(`Could not configure Orca desktop while ${phase}.`);
    if (error?.code === 'profile-state-access-refused') {
        // Access errors contain lock ownership metadata, not saved profile payloads.
        console.error(error.message);
    }
    // Other profile errors can include private payloads; do not log their contents.
    process.exitCode = 1;
} finally {
    try {
        if (temporary) fs.rmSync(temporary, {force: true});
        authority?.close();
    } finally {
        maintenance?.release();
    }
}
JS
)

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    cat <<'USAGE'
Usage: ./setup.sh [--profile {worker,supervisor,both}]
       ./setup.sh <command> [<argument> ...]

Default setup (no command):
  ./setup.sh                                Ask which tools to configure
  ./setup.sh --profile worker               Codex, GitHub login, Git authentication
  ./setup.sh --profile supervisor           Tailscale and Orca (no worker logins)
  ./setup.sh --profile both                 All tools and desktop launch
  source ./setup.sh                         Also configure this Bash terminal

  Without --profile, choose Worker, Supervisor, or Both (default) interactively.
  Noninteractive setup requires --profile. Common setup includes Bash startup,
  browser CA trust, and checkpoints. Supervisor downloads require preinstalled gh.

  Steps for Both in execution order, with commands for running individual steps:
    1. Load the environment, check requirements, and restore files from the checkpoint.
       source ./setup.sh --env-only         Reload only the environment
       ./setup.sh checkpoint restore [<file>]
                                            Restore missing files from a checkpoint

    2. Configure Bash startup and browser CA trust.

    3. Check the GitHub login, refresh the checkpoint, and configure Git.

    4. Install or update Codex, check its login, and refresh the checkpoint.
       ./setup.sh codex update              Install or update the writable Codex CLI

    5. Install or reuse Tailscale, connect the tailnet, and refresh the checkpoint.
       Then install or reuse Orca.

    6. Close the desktop and clear stale locks when no Orca process is running.

    7. Reuse checkpoint connections; import servers only when starting without a checkpoint.
       ./setup.sh remote import [--no-add] [--json]
                                [--port <port>]... [--timeout <seconds>]
                                            Discover and add tailnet Orca servers
       Run remote import separately to add servers after restoring a checkpoint.
       Use --no-add to preview; --json requires --no-add. Repeat --port to probe
       more ports (default: 6768). Timeouts default to 5 seconds, maximum 60.

    8. Dismiss onboarding, resume bridges, launch Orca, and fit and verify its window.
       ./setup.sh remote resume             Restart saved forwarding bridges
       ./setup.sh desktop fit [--window <window-id>] [--timeout <seconds>]
                                            Fit and verify Orca's visible window

    9. Save the checkpoint on exit, including if setup fails.
       ./setup.sh checkpoint save [<file>]  Refresh or export the checkpoint

  The checkpoint is state/.omniscient-dots-checkpoint. Existing native files take
  precedence. Setup initializes it after restore and refreshes it after logins
  and when it exits.
  Checkpoint commands use this default unless <file> is supplied.
  Git commits use your GitHub account's name and private noreply email.

Additional commands:
  ./setup.sh shell                          Open a configured interactive Bash
  ./setup.sh codex [<argument> ...]         Run local Codex with persistent sessions
  ./setup.sh remote share                   Share the running Orca desktop
  ./setup.sh remote pair <connection-name>  Save a connection from a pasted access link
  ./setup.sh desktop diagnose               Inspect Orca window gaps and panel space
  ./setup.sh --help                         Show this help (also -h)

  Local Codex keeps the current directory as its project and uses Approve for me:
  workspace-write with automatic approval review. Full setup and codex save these
  defaults. Local interactive launches also use --no-daemon by default.
  For native server commands, use bin/orca environment list, bin/orca serve,
  and bin/tailscale serve status.

Syntax:
  <value> is a placeholder; [...] is optional; {...,...} lists choices;
  ... repeats the preceding argument. Omit these markers in actual commands.
  <connection-name> is your chosen local name; the pasted link supplies its address.
  <argument> values are passed to Codex. Paths containing spaces must be quoted.
  Add --help to checkpoint, remote, or desktop commands for their full syntax.
USAGE
    exit
fi
if [[ ${1:-} == shell ]]; then
    ORCA_DOTS_LOAD_BASHRC=1 exec bash --rcfile "$ROOT/setup.sh" -i
fi
if [[ ${1:-} == checkpoint ]]; then
    shift
    checkpoint "$@"
    exit
fi
if [[ ${1:-} == desktop ]]; then
    shift
    exec python3 "$ROOT/orca-window.py" --profile "$ORCA_USER_DATA_PATH" "$@"
fi
if [[ ${1:-} == codex ]]; then
    shift
    if [[ ${1:-} == update && $# -eq 1 ]]; then
        update_codex
        exit
    fi
    configure_codex
    if [[ -n ${CODEX_CA_CERTIFICATE:-} && ! -r $CODEX_CA_CERTIFICATE ]]; then
        printf 'Error: the Codex CA file from /etc/environment is unreadable: %s\n' "$CODEX_CA_CERTIFICATE" >&2
        exit 1
    fi

    # Apply Approve for me above project/profile config. Later -c options or
    # explicit sandbox/approval flags can still override defaults for this run.
    codex_local_config=(-c 'sandbox_mode="workspace-write"'
                        -c 'approval_policy="on-request"'
                        -c 'approvals_reviewer="auto_review"')
    codex_defaults=(--no-daemon)
    for argument in "$@"; do
        case "$argument" in
            --) break ;;
            --no-daemon) codex_defaults=() ;;
            --remote|--remote=*) codex_defaults=(); codex_local_config=() ;;
        esac
    done
    # Locate the subcommand after any leading options. Shared-server and
    # administrative commands should receive their original arguments.
    codex_subcommand=''
    codex_option_value=0
    for argument in "$@"; do
        if (( codex_option_value )); then
            codex_option_value=0
            continue
        fi
        case "$argument" in
            --) break ;;
            -c|--config|--enable|--disable|-i|--image|-m|--model|--local-provider|-p|--profile|-s|--sandbox|-C|--cd|--add-dir|-a|--ask-for-approval|--remote-auth-token-env)
                codex_option_value=1 ;;
            -*) ;;
            *) codex_subcommand=$argument; break ;;
        esac
    done
    case "$codex_subcommand" in
        exec|e|review|doctor)
            codex_defaults=() ;;
        agents|queue|app-server|exec-server|remote-control|login|logout|mcp|plugin|completion|update|sandbox|debug|apply|a|archive|delete|migrate-rollouts|unarchive|cloud|features|help|app)
            codex_defaults=(); codex_local_config=() ;;
    esac
    # exec resolves the writable CLI on PATH (or the preinstalled fallback
    # before setup), bypassing the interactive Bash function installed above.
    exec codex "${codex_local_config[@]}" "${codex_defaults[@]}" "$@"
fi
if [[ ${1:-} == remote ]]; then
    shift
    remote_status=0
    python3 "$ROOT/remote-orca.py" "$@" || remote_status=$?
    # Help and argument errors do not change connections or the checkpoint.
    [[ $remote_status != 2 ]] || exit "$remote_status"
    for argument in "$@"; do
        case "$argument" in
            --) break ;;
            -h|--help) exit "$remote_status" ;;
        esac
    done
    case ${1:-} in
        pair|import)
            # Save even if the connection check failed after persisting a link.
            save_remote_checkpoint=1
            for argument in "$@"; do
                [[ $argument != --no-add ]] || save_remote_checkpoint=0
            done
            if (( save_remote_checkpoint )); then
                if ! checkpoint --quiet save; then
                    [[ $remote_status != 0 ]] || remote_status=1
                fi
            fi
            ;;
    esac
    exit "$remote_status"
fi
SETUP_PROFILE=''
if [[ ${1:-} == --profile && $# == 2 ]]; then
    case "$2" in
        worker|supervisor|both) SETUP_PROFILE=$2; shift 2 ;;
        *) printf 'Invalid profile: %s (choose worker, supervisor, or both).\n' "$2" >&2; exit 2 ;;
    esac
fi
if (( $# )); then
    printf 'Unknown setup argument: %s\nRun ./setup.sh --help for usage. Pair servers with ./setup.sh remote pair <connection-name>.\n' "$1" >&2
    exit 2
fi

# Choose before logging, restoring checkpoints, or changing tool configuration.
if [[ -z $SETUP_PROFILE ]]; then
    if [[ ! -t 0 ]]; then
        printf 'Choose tools with --profile worker, supervisor, or both when stdin is not a terminal.\n' >&2
        exit 2
    fi
    printf 'Which tools should setup configure?\n'
    printf '  1) Worker     — Codex, GitHub CLI login, Git authentication\n'
    printf '  2) Supervisor — Tailscale, Orca\n'
    printf '  3) Both       — all tools (default)\n'
    while true; do
        read -r -p 'Select [1/2/3]: ' selection || exit 1
        case "$selection" in
            1|worker) SETUP_PROFILE=worker; break ;;
            2|supervisor) SETUP_PROFILE=supervisor; break ;;
            ''|3|both) SETUP_PROFILE=both; break ;;
            *) printf 'Choose 1, 2, or 3.\n' ;;
        esac
    done
fi
SETUP_WORKER=0
SETUP_SUPERVISOR=0
[[ $SETUP_PROFILE == supervisor ]] || SETUP_WORKER=1
[[ $SETUP_PROFILE == worker ]] || SETUP_SUPERVISOR=1

# Keep the real terminal available for interactive authorization. Only setup
# diagnostics go through tee; never enable shell tracing or dump the environment.
command -v tee >/dev/null || { printf 'Error: logging requires tee (coreutils).\n' >&2; exit 1; }
mkdir -p "$ROOT/state/logs"
SETUP_LOG=$(mktemp "$ROOT/state/logs/setup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.log")
exec 3>&1 4>&2
exec > >(tee -a "$SETUP_LOG") 2>&1
logger_pid=$!
SETUP_STAGE=initialization
TMP=''
CHECKPOINT_READY=0
log() { printf '[%(%Y-%m-%dT%H:%M:%S%z)T] [%s] %s\n' -1 "$SETUP_STAGE" "$*"; }
stage() { SETUP_STAGE=$1; log 'Starting'; }
fail() { log "ERROR at setup.sh line ${BASH_LINENO[0]}: $*" >&2; exit 1; }
finish() {
    local status=$?
    trap - EXIT
    set +e
    if (( CHECKPOINT_READY )); then
        checkpoint save || log 'Could not refresh the checkpoint; the previous file was kept.'
    fi
    [[ -z $TMP ]] || rm -rf -- "$TMP"
    log "Finished with exit status $status. Setup log: $SETUP_LOG"
    exec 1>&3 2>&4
    wait "$logger_pid"
    exit "$status"
}
trap 'log "ERROR: exit status $? at setup.sh line $LINENO. See $SETUP_LOG" >&2' ERR
trap finish EXIT
trap 'log "Interrupted"; exit 130' INT
trap 'log "Terminated"; exit 143' TERM
log "Setup log: $SETUP_LOG"
log "Selected tools: $SETUP_PROFILE"
log "Kernel and architecture: $(uname -srmo)"
log "Bash: $BASH_VERSION"
if command -v sha256sum >/dev/null; then
    log 'Input file SHA-256 hashes (contents are not logged):'
    sha256sum "$ROOT/setup.sh" "$ROOT/remote-orca.py" "$ROOT/orca-locks.py" "$ROOT/orca-window.py" "$ROOT/persistence.py" "$ROOT/bin/orca-desktop" "$ROOT/bin/orca" "$ROOT/bin/ssh" /etc/environment
fi

stage environment
log 'Loaded /etc/environment proxy settings and persistent application paths'

stage requirements
for tool in gh git ssh curl python3 tar certutil openssl; do
    command -v "$tool" >/dev/null || fail "Required preinstalled command: $tool"
    log "$tool: $(command -v "$tool")"
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'This script targets the current Linux x86_64 environment.'
gh --version
git --version
ssh -V
curl --version
python3 --version
openssl version
mkdir -p "$ROOT/bin" "$GH_CONFIG_DIR" "$CODEX_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME"
chmod 700 "$ROOT/state" "$GH_CONFIG_DIR" "$CODEX_HOME"
stage credential-storage
# Prevent environment tokens from hiding a missing persistent login.
unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
if (( SETUP_WORKER )); then configure_codex; fi
stage checkpoint-restore
# Remember the starting state before save creates a checkpoint on the first run.
SETUP_FROM_CHECKPOINT=0
[[ ! -f "$ROOT/state/.omniscient-dots-checkpoint" ]] || SETUP_FROM_CHECKPOINT=1
checkpoint restore
checkpoint save
CHECKPOINT_READY=1
if (( SETUP_WORKER )); then
stage credential-storage
log "Codex login, settings, sessions, history and database: $CODEX_HOME"
log 'Codex uses Approve for me: workspace-write, on-request approvals, automatic review'
log 'Local interactive Codex uses --no-daemon and reloads /etc/environment on each launch'
fi
stage bash-startup
python3 - "$ROOT" <<'PY'
from pathlib import Path
import os, re, shlex, stat, sys, tempfile

root = Path(sys.argv[1])
bashrc = Path.home() / '.bashrc'
# Follow an existing symlink so the user's startup-file arrangement is preserved.
target = bashrc.resolve()
previous = target.read_text() if target.exists() else ''
begin = '# BEGIN orca-for-dots environment'
end = '# END orca-for-dots environment'
pattern = re.compile(r'(?ms)^' + re.escape(begin) + r'\n.*?^' + re.escape(end) + r'(?:\n|$)')
if begin in previous or end in previous:
    if previous.count(begin) != 1 or previous.count(end) != 1 or not pattern.search(previous):
        sys.exit('Cannot update the orca-for-dots block in ~/.bashrc: check its BEGIN/END markers.')
contents = pattern.sub('', previous).rstrip('\n')
setup = shlex.quote(str(root / 'setup.sh'))
block = f'{begin}\nif [[ -r {setup} ]]; then\n    source {setup} --env-only\nfi\n{end}\n'
updated = contents + ('\n\n' if contents else '') + block
if updated == previous:
    print(f'Bash startup already configured: {bashrc}')
    sys.exit(0)
mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
if target.exists():
    backups = root / 'state' / 'bashrc-backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    fd, backup = tempfile.mkstemp(prefix='bashrc-', dir=backups)
    with os.fdopen(fd, 'w') as output:
        output.write(previous)
    print(f'Existing Bash startup file backed up to {backup}')
fd, temporary = tempfile.mkstemp(prefix='.orca-bashrc-', dir=target.parent)
try:
    with os.fdopen(fd, 'w') as output:
        output.write(updated)
        os.fchmod(output.fileno(), mode)
    os.replace(temporary, target)
finally:
    Path(temporary).unlink(missing_ok=True)
print(f'Bash startup configured: {bashrc} sources {root / "setup.sh"}')
PY
stage browser-ca-trust
trust_browser_ca
stage network
log 'Checking /etc/environment networking'
network_urls=(https://github.com)
if (( SETUP_WORKER )); then network_urls+=(https://auth.openai.com https://chatgpt.com); fi
for url in "${network_urls[@]}"; do
    http_status=$(curl --silent --show-error --location --output /dev/null --write-out '%{http_code}' --connect-timeout 10 --max-time 30 "$url") ||
        fail "Cannot reach $url using /etc/environment. Check the proxy service, address and CA certificate."
    log "Connection completed: $url (HTTP $http_status; transport check only)"
done
if (( SETUP_WORKER )); then
stage github-login
log "Credential directory: $GH_CONFIG_DIR"
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
    log 'Authorization required; device codes and login output appear only in the terminal'
    printf 'Copy the code printed below, then press Enter to open the browser in this environment.\n'
    printf 'If the browser cannot open, use https://github.com/login/device in another browser.\n'
    login_github >&3 2>&4 ||
        fail 'GitHub login failed. Check proxy/networking and complete browser authorization.'
fi
gh auth status --hostname github.com >&3 2>&4
[[ -s "$GH_CONFIG_DIR/hosts.yml" ]] || fail 'GitHub credentials were not persisted locally.'
log 'GitHub authentication succeeded and credentials are persisted'
checkpoint save
stage github-git
configure_github_git
stage codex-update
update_codex
hash -r
log "Codex CLI: $(command -v codex)"
stage codex-login
log "Credential directory: $CODEX_HOME"
if ! codex login status >/dev/null 2>&1; then
    log 'Authorization required; device codes and login output appear only in the terminal'
    printf 'Open the device authorization link printed below in a browser OUTSIDE this environment.\n'
    BROWSER=true codex login --device-auth >&3 2>&4 || fail 'Codex login failed. Check proxy/networking and complete device authorization.'
fi
codex login status >&3 2>&4
[[ -s "$CODEX_HOME/auth.json" ]] || fail 'Codex credentials were not persisted locally.'
log 'Codex authentication succeeded and credentials are persisted'
checkpoint save

fi

if (( ! SETUP_SUPERVISOR )); then
    log 'Worker setup complete. Start Codex with ./setup.sh codex.'
    exit 0
fi

TMP=$(mktemp -d "$ROOT/.download.XXXXXX")
stage tailscale-binaries
# Discover release filenames rather than assuming how the fork packages them.
if [[ ! -x "$ROOT/bin/tailscale" || ! -x "$ROOT/bin/tailscaled" ]]; then
    gh api repos/Happypig375/tailscale/releases/latest > "$TMP/release.json"
    release_tag=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$TMP/release.json")
    log "Downloading Happypig375/tailscale release: $release_tag"
    python3 - "$TMP/release.json" > "$TMP/assets" <<'PY'
import json, re, sys
assets = [a['name'] for a in json.load(open(sys.argv[1]))['assets']]
archives = [n for n in assets if re.search(r'linux.*(amd64|x86_64)', n, re.I) and n.endswith(('.tgz', '.tar.gz'))]
if len(archives) == 1:
    print(archives[0])
else:
    for binary in ('tailscale', 'tailscaled'):
        matches = [n for n in assets if re.fullmatch(binary + r'(?:[-_]linux[-_](?:amd64|x86_64))?', n, re.I)]
        if len(matches) != 1:
            sys.exit('Cannot uniquely select release binaries. Assets: ' + ', '.join(assets))
        print(matches[0])
PY
    while IFS= read -r asset; do
        log "Downloading release asset: $asset"
        gh release download "$release_tag" --repo Happypig375/tailscale --pattern "$asset" --dir "$TMP"
        case "$asset" in *.tgz|*.tar.gz) tar -xzf "$TMP/$asset" -C "$TMP" ;; esac
    done < "$TMP/assets"
    for binary in tailscale tailscaled; do
        mapfile -t found < <(find "$TMP" -type f \( -name "$binary" -o -name "${binary}-linux-amd64" -o -name "${binary}_linux_amd64" -o -name "${binary}-linux-x86_64" -o -name "${binary}_linux_x86_64" \))
        [[ ${#found[@]} == 1 ]] || fail "Cannot uniquely locate $binary in release."
        install -m 700 "${found[0]}" "$ROOT/bin/$binary"
    done
else
    log 'Reusing installed Tailscale binaries'
fi
tailscale version
stage tailscale-daemon
# The fork stores its LocalAPI endpoint and tailnet state beside these binaries.
tailscale_state="$ROOT/bin/tailscale-sandbox-state"
if [[ -L $tailscale_state || ( -e $tailscale_state && ! -d $tailscale_state ) ]]; then
    fail "Tailscale state must be a local directory: $tailscale_state"
fi
if ! tailscale_status; then
    migration_status=0
    python3 "$ROOT/tailscale-reuse.py" "$ROOT" || migration_status=$?
    (( migration_status != 2 )) || fail 'Tailscale state migration failed; original state is preserved. Check the migration error before restarting.'
fi
if ! tailscale_status; then
    check_tailscale_proxy
    log "Starting tailscaled; daemon log: $ROOT/state/tailscaled.log"
    nohup tailscaled --tun=userspace-networking --outbound-http-proxy-listen=127.0.0.1:1055 \
        >> "$ROOT/state/tailscaled.log" 2>&1 < /dev/null &
    daemon_pid=$!
    log "tailscaled PID: $daemon_pid"
    ready=0
    for ((i=0; i<30; i++)); do
        kill -0 "$daemon_pid" 2>/dev/null || fail "tailscaled exited; see $ROOT/state/tailscaled.log"
        if tailscale_status; then ready=1; break; fi
        sleep 1
    done
    (( ready )) || fail "tailscaled did not become ready; see $ROOT/state/tailscaled.log"
else
    log 'Reusing running tailscaled'
fi
tailscale version --daemon || log 'Could not compare the client and running daemon versions'
stage tailnet-login
if ! python3 - "$TMP/status.json" <<'PY'
import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get('BackendState') == 'Running' else 1)
PY
then
    log 'Starting or authorizing the tailnet connection; authorization output appears only in the terminal'
    tailscale up --accept-dns=false --accept-routes=false >&3 2>&4
fi
log 'Reading tailnet status'
tailscale status
checkpoint save
stage orca-download
if [[ ! -x "$ROOT/bin/orca.AppImage" ]]; then
    log 'Downloading stablyai/orca latest release asset: orca-linux.AppImage'
    gh release download --repo stablyai/orca --pattern orca-linux.AppImage --dir "$TMP"
    install -m 700 "$TMP/orca-linux.AppImage" "$ROOT/bin/orca.AppImage"
else
    log 'Reusing installed Orca AppImage'
fi
if command -v sha256sum >/dev/null; then
    sha256sum "$ROOT/bin/orca.AppImage"
fi
# AppImage portable mode keeps Orca's own profile beside the downloaded app.
mkdir -p "$ROOT/bin/orca.AppImage.config"
printf '\nLogins and settings persist in %s.\n' "$ROOT"
printf 'New interactive Bash terminals load these settings through ~/.bashrc.\n'
printf 'To reload this terminal without repeating setup, source ./setup.sh --env-only.\n'
printf 'Use ./setup.sh shell for a configured child terminal.\n'
printf 'Use ./setup.sh codex to start Codex, or ./setup.sh codex resume to reopen a saved session.\n'
# Finish every interactive setup step before the desktop can take focus.
stage orca-shutdown
prepare_orca_launch >&3 2>&4 || fail 'Could not close Orca before relaunching it.'
python3 "$ROOT/orca-locks.py" "$ORCA_USER_DATA_PATH"
stage remote-connections
if (( SETUP_FROM_CHECKPOINT )); then
    log 'Reusing checkpoint connections; skipping discovery of additional Orca servers'
    log 'To discover and add servers, run ./setup.sh remote import.'
else
    log 'Finding tailnet Orca servers; paste their access links at the terminal prompts'
    # Keep interactive pairing output and credentials out of the setup log.
    python3 "$ROOT/remote-orca.py" import >&3 2>&4
fi
printf 'To let other computers connect here, run ./setup.sh remote share.\n'
stage orca-settings
configure_orca_desktop
stage orca-startup
printf 'Launching Orca with the saved connections; its child terminals inherit the proxy and login paths.\n'
log 'Appending to the Orca application log'
nohup "$ROOT/bin/orca-desktop" >> "$ROOT/state/orca.log" 2>&1 < /dev/null &
printf 'Orca PID: %s; log: %s/state/orca.log\n' "$!" "$ROOT"
stage orca-window
python3 "$ROOT/orca-window.py" --profile "$ORCA_USER_DATA_PATH" fit
