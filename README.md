![omniscient-dots](omniscient-dots.png)

### For humans

Send "Use https://github.com/Happypig375/omniscient-dots" to your Dot, then "All setups, backups and restores are approved." when it asks for approval. After initial authentication following the initialization script on the dot's cloud computer, it gains Codex (not expensive cloud tasks) access on the cloud computer and/or [Orca](https://www.onorca.dev) to supervise any of your agents running anywhere on your [tailnet](https://tailscale.com/docs/concepts/tailnet) (think any computer that you have access to).

Remember that the cloud computer restarts and wipes its disk often. Do not run long-running tasks on the cloud computer without checkpointing on a remote server.

### For dots

1. Use your bash tool to curl https://github.com/Happypig375/omniscient-dots/archive/refs/heads/master.zip to `/workspace/shared`
2. Unzip it with your bash tool
3. Do pre-execution checks on `setup.sh`, and also prompt the user to choose one of the following:
- **Worker** (Codex, GitHub CLI login, and Git authentication) 
- **Supervisor** (Tailscale and Orca)
- **Both** (the default)
4. After user choice, on a new terminal on your cloud computer (not your bash tool from now on), execute `source ./setup.sh --profile worker`, or `source ./setup.sh --profile supervisor`, or `source ./setup.sh --profile both` respectively; watch authentication.
   Sourcing activates this checkout’s `PATH` and interactive `codex` function in that terminal. Executing `./setup.sh` runs setup in a child process and cannot change the parent shell. Fresh interactive Bash terminals activate through the installed `~/.bashrc` hook; existing terminals need `source /path/to/checkout/setup.sh --env-only`.
5. After the script exits, with user approval of saving `/workspace/shared/omniscient-dots-master/state/.omniscient-dots-checkpoint` to the user's ChatGPT Library and restoring that file from ChatGPT Library back to cloud computer automatically, execute the save.
6. With setup completed, suggest what can be done:
- If supervisor was chosen as set up, suggest that you can observe Orca's agents (list them) for any "model at capacity" errors that you can send "continue" prompts for
- If supervisor was chosen as set up, suggest that you can notify agent updates for all agents that the user names. (orca CLI at `/workspace/shared/omniscient-dots/master/bin/orca` can lookup terminal IDs and convert them to agent responses - you can list agents using it, while Orca user interface can complement information not covered by Orca CLI)
- If worker was chosen as set up, offer to forbid yourself from launching cloud tasks (via the native tool) and instead only do Codex on the cloud computer to save usage
- If the Codex Resets MCP (https://codex-resets.com/mcp) was not installed, suggest the user to install it
- If Codex Resets MCP is installed, suggest the user to let you watch Codex Resets, furthermore if supervisor was chosen as set up, suggest the user to queue up tasks (tracked outside the cloud computer by you) that you can spawn agents for if a Codex Reset lands.
7. Continuously observe for cloud computer resets (most obviously orca disappearing and `curl https://example.com` failing to connect; it may also manifest as random file disappearances inside the cloud computer `/workspace`), and if it happens, first restore `.omniscient-dots-checkpoint` from ChatGPT Library to `/workspace/shared/omniscient-dots-master/state/.omniscient-dots-checkpoint`, then execute these instructions from step 1 while using `source ./setup.sh` in place of any of the 3 commands in step 4, while retaining approval of the user from step 3.

## Details

For what's actually going on in this repository, see [AGENTS.md](AGENTS.md).
