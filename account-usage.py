#!/usr/bin/env python3
"""Explicit synthetic replay or bounded Codex stdio observation; stdlib only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

from usage_ledger import Ledger, MAX_RECORD, UsageError, loads, obj, require

VERSION = "0.164.0"
MODEL = "gpt-6.1-sol"
PROMPT = "Reply with exactly OK. Do not use any tools or read any files."
INSTRUCTIONS = (
    "This is a bounded synthetic observability test. Reply exactly OK to the user. "
    "Do not use tools, inspect files, sessions, credentials or environment, "
    "make network requests, or launch other agents."
)
SCHEMAS = {
    "GetAccountParams": {"refreshToken"},
    "GetAccountResponse": {"account", "requiresOpenaiAuth"},
    "GetAccountRateLimitsResponse": {"rateLimits", "rateLimitsByLimitId"},
    "ThreadTokenUsageUpdatedNotification": {"threadId", "turnId", "tokenUsage"},
    "ThreadStartParams": {"model", "ephemeral", "cwd", "config", "developerInstructions", "baseInstructions", "sandbox"},
    "ThreadStartResponse": {"model", "reasoningEffort", "thread", "sandbox", "approvalsReviewer"},
    "TurnStartParams": {"threadId", "input", "effort"},
    "TurnStartResponse": {"turn"},
    "TurnCompletedNotification": {"threadId", "turn"},
    "ItemStartedNotification": {"threadId", "turnId", "item"},
    "AccountRateLimitsUpdatedNotification": {"rateLimits"},
}
# Exact installed stable schema subset, generated into temporary directories.
# A new build/schema requires an explicit compatibility review, not a wildcard.
SCHEMA_HASHES = {
    "GetAccountParams": "30b545275f60975b55bd6fbaafd70f15505801d14168ee82d3931b6e93c78aab",
    "GetAccountResponse": "612109fe4c80e62a6f6a36f056761bc2b4f60f9b811edf23bbb86004274ec7a3",
    "GetAccountRateLimitsResponse": "9c0def7f8ab2396122af939dffbf01b34b8d66082fe4129dd20917bfd4313fdc",
    "ThreadTokenUsageUpdatedNotification": "aba4f6c7e4a19b2b842c08ee793b57000c07dafd57b922ad0d8e7c76609108c2",
    "ThreadStartParams": "e9c6d3cc18d049bfbc0249add3808fb8e5a27e1a0b5a423767aafbc3859a9428",
    "ThreadStartResponse": "5c52c38fc5a0f7937ba84cf370d39d877211628e7e8e766187efbe9eddf80bdd",
    "TurnStartParams": "835e0d4ca1a0ddc4fb6db1e2b3249dd67f07c50ac2506092ba81543efc92829b",
    "TurnStartResponse": "6ce0f9ca622a54830c523793d841318f09c604211ec0d24b315c75edd7ffcbfa",
    "TurnCompletedNotification": "963c4b492eecd8975a3134200b9a2b834f82841243736283c9c18d88d60106a8",
    "ItemStartedNotification": "c5dc7bc43ffd6164b0920004e15b97f0d2b91f44e21547615122dbd6b104faf1",
    "AccountRateLimitsUpdatedNotification": "fb62719b2edf989399a744e06e38b1828bf81bd10afaf6910d91c39b2e48d952",
}


class RpcError(UsageError):
    """Keep only a bounded code and fixed categories; discard text and data."""

    def __init__(self, error, method=None):
        super().__init__("rpc_error")
        code = error.get("code") if isinstance(error, dict) else None
        self.rpc_code = code if type(code) is int and -(2**31) <= code < 2**31 else None
        # JSON-RPC codes are not HTTP status codes. In particular -32603 does
        # not establish authentication failure. Unknown codes stay unknown.
        self.rpc_category = {
            -32700: "parse_error", -32600: "invalid_request",
            -32601: "unsupported_method", -32602: "invalid_params",
            -32603: "internal_error",
            -32001: "overloaded",
        }.get(self.rpc_code, "unknown")
        # Exact fixed messages reviewed in rust-v0.164.0 account_processor and
        # workspace_routing. No substring/HTTP-code extraction or arbitrary
        # structured error data: unknown messages remain unknown.
        self.rpc_cause = "unknown"
        message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str):
            self.rpc_cause = {
                ("account/read", -32603, "workspace routing discovery unauthorized (401)"):
                    "routing_authentication_rejected",
                ("account/read", -32603, "workspace routing discovery timed out"):
                    "routing_timeout",
                ("account/read", -32603, "workspace routing discovery failed"):
                    "routing_discovery_failed",
                ("account/read", -32603, "account changed during workspace routing discovery"):
                    "account_scope_changed",
                ("account/rateLimits/read", -32600, "codex account authentication required to read rate limits"):
                    "authentication_required",
                ("account/rateLimits/read", -32600, "chatgpt authentication required to read rate limits"):
                    "authentication_mode_unsupported",
            }.get((method, self.rpc_code, message), "unknown")


def checked_command(command, timeout=20):
    # Only launcher/help/schema commands assembled by this program. Drop stderr
    # and errors rather than quote them: diagnostics can contain private data.
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise UsageError("protocol_check_timeout") from None
    except OSError:
        raise UsageError("launcher_unavailable") from None
    require(result.returncode == 0, "launcher_check_failed")
    require(len(result.stdout) <= 4 * MAX_RECORD, "protocol_output_too_large")
    return result.stdout.decode("utf-8", errors="strict")


def protocol_check(launcher):
    """Generate schemas from the running launcher, never from private storage."""
    command = [str(launcher), "codex"]
    version = checked_command(command + ["--version"]).strip()
    require(version == "codex-cli " + VERSION, "unsupported_producer")
    help_text = checked_command(command + ["app-server", "--help"])
    require("--listen" in help_text and "stdio://" in help_text and "generate-json-schema" in help_text,
            "unsupported_stdio_protocol")
    with tempfile.TemporaryDirectory(prefix="od-usage-schema-") as directory:
        checked_command(command + ["app-server", "generate-json-schema", "--out", directory])
        hashes = {}
        for name, fields in SCHEMAS.items():
            path = Path(directory) / "v2" / (name + ".json")
            try:
                content = path.read_bytes()
            except OSError:
                raise UsageError("installed_schema_missing") from None
            schema = loads(content)
            require(fields <= obj(schema.get("properties")).keys(), "installed_schema_incompatible")
            hashes[name] = hashlib.sha256(content).hexdigest()
            require(hashes[name] == SCHEMA_HASHES[name], "installed_schema_unreviewed")
        return {"version": VERSION, "schema_sha256": hashes, "transport": "stdio"}


class StdioClient:
    """Single child, finite deadline, no listeners, no raw event persistence."""

    def __init__(self, command, timeout=90, cwd=None):
        self.deadline = time.monotonic() + timeout
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, start_new_session=True, cwd=cwd)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.sequence = 0
        self.event_count = 0
        self.on_event = lambda message: None
        self.methods = []

    def send(self, message):
        try:
            self.process.stdin.write(json.dumps(message).encode() + b"\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise UsageError("transport_closed") from None

    def receive(self):
        while b"\n" not in self.buffer:
            left = self.deadline - time.monotonic()
            require(left > 0, "timeout")
            require(bool(self.selector.select(left)), "timeout")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            require(bool(chunk), "transport_closed")
            self.buffer += chunk
            require(len(self.buffer) <= MAX_RECORD, "record_too_large")
        line, self.buffer = self.buffer.split(b"\n", 1)
        self.event_count += 1
        require(self.event_count <= 4096, "event_limit")
        message = loads(line)
        if "method" in message and "id" in message:
            # Do not fulfill auth refresh, tool approvals, elicitation, or any
            # server request. Stop rather than invoke another capability.
            self.send({"id": message["id"], "error": {"code": -32601, "message": "Observer refuses server requests"}})
            raise UsageError("server_request_refused")
        if "method" in message:
            self.on_event(message)
        return message

    def request(self, method, params):
        require(method in {"initialize", "account/read", "account/rateLimits/read", "thread/start", "turn/start"},
                "method_not_allowed")
        if method == "account/read":
            require(params == {"refreshToken": False}, "refresh_forbidden")
        self.sequence += 1
        request_id = self.sequence
        self.methods.append(method)
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    # Do not parse free-form RPC error messages for HTTP codes.
                    raise RpcError(message["error"], method)
                return obj(message.get("result"))

    def close(self):
        self.selector.close()
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
        self.process.stdout.close()


def observe(launcher, selftest=False, timeout=90, ttl=120, *, token_selftest=False):
    require(not (selftest and token_selftest), "conflicting_modes")
    capability = protocol_check(launcher)
    ledger = Ledger(ttl)
    evidence = {"capability": capability, "new_session_only": True, "auth_refresh_requested": False,
                "account_identity_present": None, "quota_observed": False, "quota_queried": False,
                "coverage": {"account": "not_queried", "quota": "not_queried", "tokens": "not_requested"},
                "new_thread_created": False, "new_turn_started": False,
                "new_thread_state": "not_requested", "new_turn_state": "not_requested",
                "bootstrap_account_notifications": 0,
                "token_event_observed": False, "session_payer_binding": "unknown",
                "binding_gap": "No request-bound account identity on thread token events",
                "turn_status": "not_started", "tool_activity_observed": False,
                "automatic_action_policy": "disabled"}
    # Start the process and new thread outside any repository. This does not
    # suppress installed global config; no existing-session RPCs are sent.
    with tempfile.TemporaryDirectory(prefix="od-usage-task-") as directory:
        client = StdioClient([str(launcher), "codex", "-c", 'model_reasoning_effort="xhigh"',
                              "-c", 'sandbox_mode="read-only"', "-c", 'approvals_reviewer="user"',
                              "app-server", "--listen", "stdio://"], timeout, cwd=directory)
        own_thread = None
        own_turn = None
        account_bound = False
        notifications = 0
        pending_turn_events = []
        phase = "initialize"

        def context(sample_id, start=None):
            now = time.time()
            return ledger.context("source_1", "scope_1", 1, now, sample_id=sample_id,
                                  fetch_started_at=start, fetch_completed_at=now if start is not None else None)

        def event(message):
            nonlocal notifications
            method = message.get("method")
            # Token-only collection makes no account-scope assertion. Neither
            # account notifications nor quota pushes supply a turn's payer.
            if token_selftest and method in ("account/updated", "account/rateLimits/updated"):
                return
            params = obj(message.get("params", {}))
            if method in ("thread/tokenUsage/updated", "turn/completed", "item/started", "item/completed"):
                require(own_thread is not None, "unexpected_thread")
                require(params.get("threadId") == own_thread, "unexpected_thread")
                if own_turn is None:
                    # Notifications may precede the turn/start response. Keep
                    # only the fields needed to validate that response's turn.
                    require(len(pending_turn_events) < 64, "event_limit")
                    if method.startswith("item/"):
                        item_type = obj(params.get("item")).get("type")
                        if item_type not in ("userMessage", "agentMessage", "reasoning", "plan"):
                            evidence["tool_activity_observed"] = True
                            raise UsageError("unexpected_tool_activity")
                        pending_turn_events.append({"method": method, "params": {
                            "threadId": own_thread, "turnId": params.get("turnId"), "item": {"type": item_type}}})
                    elif method == "turn/completed":
                        turn = obj(params.get("turn"))
                        pending_turn_events.append({"method": method, "params": {
                            "threadId": own_thread, "turn": {"id": turn.get("id"), "status": turn.get("status")}}})
                    else:
                        pending_turn_events.append({"method": method, "params": {
                            "threadId": own_thread, "turnId": params.get("turnId"), "tokenUsage": params.get("tokenUsage")}})
                    return
            if method == "thread/tokenUsage/updated":
                require(params.get("turnId") == own_turn, "unexpected_turn")
                notifications += 1
                ledger.tokens(params, context(f"event_{notifications}"))
                evidence["token_event_observed"] = True
                evidence["coverage"]["tokens"] = "observed"
            elif method == "account/rateLimits/updated":
                # No quota sample may cross the initial account-read boundary.
                if not account_bound:
                    return
                # Push receipt lacks a provider measurement time; never label
                # this as a fresh fetch or renew the explicit query sample.
                notifications += 1
                ledger.quota(params, context(f"event_{notifications}"))
            elif method == "account/updated":
                # 0.164.0 also emits this asynchronously on initialization.
                # Before binding, trust only the pending account/read result:
                # its handler checks for owner changes during routing discovery.
                # The notification itself supplies no account identity.
                if not account_bound:
                    evidence["bootstrap_account_notifications"] += 1
                    return
                raise UsageError("account_scope_changed")
            elif method == "turn/completed":
                require(params.get("threadId") == own_thread, "unexpected_thread")
                turn = obj(params.get("turn"))
                require(turn.get("id") == own_turn, "unexpected_turn")
                status = turn.get("status")
                evidence["turn_status"] = status if status in ("completed", "failed", "interrupted") else "unknown"
            elif method in ("item/started", "item/completed"):
                require(params.get("turnId") == own_turn, "unexpected_turn")
                item_type = obj(params.get("item")).get("type")
                if item_type not in ("userMessage", "agentMessage", "reasoning", "plan"):
                    evidence["tool_activity_observed"] = True
                    raise UsageError("unexpected_tool_activity")
            elif method == "error":
                raise UsageError("turn_error")

        client.on_event = event
        try:
            client.request("initialize", {"clientInfo": {"name": "od_usage_observer", "version": "1"}})
            client.send({"method": "initialized", "params": {}})
            if not token_selftest:
                phase = "account_read"
                account = client.request("account/read", {"refreshToken": False}).get("account")
                mode = obj(account).get("type") if account is not None else None
                evidence["auth_mode"] = mode if mode in ("chatgpt", "apiKey") else "unknown"
                require(mode == "chatgpt", "chatgpt_quota_unavailable")
                # Inspect identity presence only; do not extract email, IDs or plan.
                evidence["account_identity_present"] = account is not None
                evidence["coverage"]["account"] = "observed"
                account_bound = True
                phase = "quota_query"
                start = time.time()
                evidence["quota_queried"] = True
                quota = client.request("account/rateLimits/read", {})
                ledger.quota(quota, context("query_1", start))
                evidence["quota_observed"] = any(s["lane"] == "account_quota" and s["sample_id"] == "query_1"
                                                   for s in ledger.samples)
                evidence["coverage"]["quota"] = "observed" if evidence["quota_observed"] else "unavailable"
                evidence["quota_account_id_present"] = bool(quota.get("accountId"))
                del quota, account
            phase = "snapshot_complete"
            if selftest or token_selftest:
                phase = "thread_start"
                evidence["new_thread_state"] = "outcome_unknown"
                thread = client.request("thread/start", {
                    "model": MODEL, "cwd": directory, "ephemeral": True,
                    "sandbox": "read-only", "approvalPolicy": "on-request", "approvalsReviewer": "user",
                    "config": {"model_reasoning_effort": "xhigh"},
                    "baseInstructions": INSTRUCTIONS, "developerInstructions": INSTRUCTIONS})
                own_thread = obj(thread.get("thread")).get("id")
                require(isinstance(own_thread, str), "invalid_thread_response")
                evidence["new_thread_created"] = True
                evidence["new_thread_state"] = "created"
                evidence["model_confirmed"] = thread.get("model") == MODEL
                evidence["reasoning_effort_confirmed"] = thread.get("reasoningEffort") == "xhigh"
                require(evidence["model_confirmed"] and evidence["reasoning_effort_confirmed"], "model_or_effort_mismatch")
                sandbox = obj(thread.get("sandbox"))
                evidence["read_only_sandbox_confirmed"] = sandbox.get("type") == "readOnly"
                evidence["approval_reviewer_confirmed"] = thread.get("approvalsReviewer") == "user"
                require(evidence["read_only_sandbox_confirmed"] and evidence["approval_reviewer_confirmed"],
                        "execution_policy_mismatch")
                phase = "turn_start"
                evidence["new_turn_state"] = "outcome_unknown"
                turn = client.request("turn/start", {"threadId": own_thread,
                    "input": [{"type": "text", "text": PROMPT}], "effort": "xhigh", "model": MODEL})
                own_turn = obj(turn.get("turn")).get("id")
                require(isinstance(own_turn, str), "invalid_turn_response")
                evidence["new_turn_started"] = True
                evidence["new_turn_state"] = "started"
                for message in pending_turn_events:
                    event(message)
                pending_turn_events.clear()
                phase = "turn_events"
                while evidence["turn_status"] == "not_started":
                    client.receive()
                require(evidence["turn_status"] == "completed", "selftest_turn_failed")
                require(evidence["token_event_observed"], "token_event_missing")
            observed = evidence["token_event_observed"] if token_selftest else evidence["quota_observed"]
            evidence["status"] = "observed" if observed else "partial"
            if not token_selftest and not evidence["quota_observed"]:
                evidence["quota_gap"] = "No supported quota windows in query response"
        except UsageError as error:
            evidence["status"] = "blocked"
            evidence["blocker"] = str(error)
            evidence["failure_stage"] = phase
            if phase == "account_read":
                evidence["coverage"]["account"] = "failed"
            elif phase == "quota_query":
                evidence["coverage"]["quota"] = "failed"
            elif phase in ("thread_start", "turn_start", "turn_events") and not evidence["token_event_observed"]:
                evidence["coverage"]["tokens"] = "failed"
            if isinstance(error, RpcError):
                evidence["rpc_code"] = error.rpc_code
                evidence["rpc_category"] = error.rpc_category
                evidence["rpc_cause"] = error.rpc_cause
            # Turn/transport failures after the query do not revoke or renew
            # independent quota-query health. Account change does invalidate it.
            if str(error) == "account_scope_changed":
                ledger.failure(context("failure_1"), "revalidation_required")
            elif phase == "quota_query":
                code = str(error) if str(error) in ("timeout", "rpc_error", "invalid_payload") else "transport"
                ledger.failure(context("failure_1"), code)
        finally:
            evidence["request_methods"] = client.methods
            client.close()
            evidence["child_terminated"] = client.process.poll() is not None
    return {"evidence": evidence, "report": ledger.report(time.time())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay", help="Read only synthetic usage envelopes from stdin")
    replay.add_argument("--now", type=float, required=True)
    replay.add_argument("--ttl", type=float, default=120)
    for name in ("probe", "snapshot", "selftest", "token-selftest"):
        live = sub.add_parser(name, help=("One ephemeral token test without account/quota reads"
                                        if name == "token-selftest" else
                                        "Explicit local launcher; selftest creates one ephemeral turn"))
        live.add_argument("--launcher", type=Path, required=True)
        live.add_argument("--timeout", type=int, choices=range(10, 121), default=90, metavar="10..120")
        live.add_argument("--evidence-only", action="store_true", help="Exclude private metric values")
    args = parser.parse_args()
    try:
        if args.command == "replay":
            import sys
            ledger = Ledger(args.ttl)
            count = 0
            while line := sys.stdin.buffer.readline(MAX_RECORD + 1):
                count += 1
                require(count <= 4096, "record_limit")
                ledger.replay(loads(line))
            result = ledger.report(args.now)
        else:
            require(args.launcher.is_absolute(), "absolute_launcher_required")
            if args.command == "probe":
                result = protocol_check(args.launcher)
            else:
                result = observe(args.launcher, selftest=args.command == "selftest", timeout=args.timeout,
                                 token_selftest=args.command == "token-selftest")
                if args.evidence_only:
                    result = result["evidence"]
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 2 if result.get("status", result.get("evidence", {}).get("status")) in ("blocked", "partial") else 0
    except UsageError as error:
        print(json.dumps({"status": "blocked", "blocker": str(error)}))
        return 2
    except (OSError, UnicodeError):
        # Never print arbitrary paths, error bodies, account metadata or stderr.
        print(json.dumps({"status": "blocked", "blocker": "input_or_protocol_check_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
