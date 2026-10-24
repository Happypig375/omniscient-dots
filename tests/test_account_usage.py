import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from usage_ledger import Ledger, UsageError, health, loads

spec = importlib.util.spec_from_file_location("account_usage", REPO / "account-usage.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def quota(value=40, reset=2000, weekly=True):
    snapshot = {"primary": {"usedPercent": value, "windowDurationMins": 300, "resetsAt": reset}}
    if weekly:
        snapshot["secondary"] = {"usedPercent": 80, "windowDurationMins": 10080, "resetsAt": 9000}
    return {"rateLimits": snapshot, "rateLimitsByLimitId": {"codex": snapshot}, "accountId": "PRIVATE_ACCOUNT"}


def tokens(thread="PRIVATE_THREAD", turn="PRIVATE_TURN"):
    return {"threadId": thread, "turnId": turn, "tokenUsage": {
        "total": {"inputTokens": 100, "cachedInputTokens": 40, "outputTokens": 20,
                  "reasoningOutputTokens": 10, "totalTokens": 120},
        "last": {"inputTokens": 50, "cachedInputTokens": 10, "outputTokens": 5,
                 "reasoningOutputTokens": 2, "totalTokens": 55}, "modelContextWindow": 1000}}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(ttl=120)

    def ctx(self, name="query_1", at=1000, source="source_1", scope="shared_pool", epoch=1, fetched=True):
        return self.ledger.context(source, scope, epoch, at, sample_id=name,
            fetch_started_at=at - 2 if fetched else None,
            fetch_completed_at=at if fetched else None, synthetic=True)

    def test_mirror_is_not_counted_twice_and_unknown_permission_stays_unknown(self):
        self.ledger.quota(quota(), self.ctx())
        report = self.ledger.report(1010)
        self.assertEqual(len(report["samples"]), 2)
        self.assertIsNone(report["samples"][0]["ordinary_usage_allowed"])
        self.assertEqual(report["priority_policy"], "unspecified")
        self.assertEqual(report["execution"], "disabled")

    def test_tokens_subcategories_context_and_quota_stay_separate(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.tokens(tokens(), self.ctx("event_1", fetched=False))
        row = self.ledger.report(1010)["samples"][-1]
        self.assertEqual(row["total"]["totalTokens"], 120)
        self.assertEqual(row["total"]["inputTokens"], 100)
        self.assertEqual(row["total"]["cachedInputTokens"], 40)
        self.assertEqual(row["total"]["inputTokens"] + row["total"]["outputTokens"],
                         row["total"]["totalTokens"])
        self.assertEqual(row["context_capacity_tokens"], 1000)
        self.assertEqual(row["session_payer_binding"], "unknown")
        self.assertIsNone(row["pool"])
        self.assertEqual(row["child_inclusion"], "unknown")

    def test_missing_optional_token_count_is_unknown_instead_of_zero(self):
        self.ledger.tokens(tokens(), self.ctx("event_1", fetched=False))
        row = self.ledger.report(1010)["samples"][0]
        self.assertIsNone(row["total"]["cacheWriteInputTokens"])
        self.assertIsNone(row["last"]["cacheWriteInputTokens"])

    def test_replay_redraw_does_not_renew_freshness(self):
        context = self.ctx()
        self.ledger.quota(quota(), context)
        self.ledger.quota(quota(), {**context, "observed_at": 1100})
        report = self.ledger.report(1121)
        self.assertEqual(report["receipts"], 2)
        self.assertEqual(len(report["samples"]), 2)
        self.assertEqual(report["samples"][0]["freshness"]["state"], "stale")
        self.assertEqual(report["samples"][0]["observed_at"], 1000)

    def test_new_query_same_value_is_a_new_weak_fresh_sample(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.quota(quota(), self.ctx("query_2", at=1100))
        rows = self.ledger.report(1150)["samples"]
        self.assertEqual(rows[0]["freshness"]["state"], "stale")
        self.assertEqual(rows[-1]["freshness"]["state"], "recently_fetched")
        self.assertIsNone(rows[-1]["source_event_at"])

    def test_omitted_weekly_field_does_not_refresh_prior_weekly(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.quota(quota(45, weekly=False), self.ctx("query_2", at=1110))
        rows = self.ledger.report(1125)["samples"]
        weekly = [r for r in rows if r["window"] == "secondary"]
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]["freshness"]["state"], "stale")
        self.assertEqual(rows[-1]["freshness"]["state"], "recently_fetched")

    def test_reset_expires_old_window_without_recovery(self):
        self.ledger.quota(quota(reset=1010), self.ctx())
        row = self.ledger.report(1010)["samples"][0]
        self.assertEqual(row["value"], 40)
        self.assertEqual(row["freshness"]["state"], "stale")
        self.assertIsNone(row["ordinary_usage_allowed"])

    def test_push_receipt_cannot_prove_freshness(self):
        self.ledger.quota(quota(), self.ctx(fetched=False))
        report = self.ledger.report(1000)
        self.assertEqual(report["samples"][0]["freshness"]["state"], "unknown")
        self.assertEqual(report["source_health"]["source_1"]["query_auth"], "unknown")

    def test_query_health_independent_from_agent_capacity_and_previous_sample(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.failure(self.ctx("fail_1", at=1100), "401")
        report = self.ledger.report(1125)
        self.assertEqual(report["samples"][0]["value"], 40)
        self.assertEqual(report["samples"][0]["freshness"]["state"], "stale")
        source = report["source_health"]["source_1"]
        self.assertEqual(source["query_auth"], "rejected")
        self.assertEqual(source["agent_auth"], "unknown")
        self.assertEqual(source["last_valid_sample_at"], 1000)

    def test_stream_event_cannot_clear_query_auth_failure(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.failure(self.ctx("fail_1", at=1010), "401")
        self.ledger.tokens(tokens(), self.ctx("event_1", at=1015, fetched=False))
        self.assertEqual(self.ledger.report(1020)["source_health"]["source_1"]["query_auth"], "rejected")

    def test_tokens_cannot_claim_quota_query_completion_times(self):
        self.ledger.failure(self.ctx(), "401")
        with self.assertRaisesRegex(UsageError, "token_query_times_forbidden"):
            self.ledger.tokens(tokens(), self.ctx("event_1", at=1010))
        self.assertEqual(self.ledger.report(1010)["source_health"]["source_1"]["query_auth"], "rejected")

    def test_new_epoch_stream_does_not_inherit_old_query_health(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.tokens(tokens(), self.ctx("event_1", at=1010, epoch=2, fetched=False))
        report = self.ledger.report(1010)
        self.assertEqual(report["source_health"]["source_1"]["code"], "not_queried")
        self.assertNotIn("last_valid_sample_at", report["source_health"]["source_1"])
        self.assertEqual(report["samples"][0]["freshness"]["state"], "unvalidated")

    def test_late_query_and_stream_do_not_regress_newer_health_clocks(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.failure(self.ctx("failure_1", at=1020), "401")
        self.ledger.quota(quota(), self.ctx("late_query", at=1010))
        self.ledger.tokens(tokens(), self.ctx("event_1", at=1030, fetched=False))
        self.ledger.tokens(tokens(), self.ctx("late_event", at=1015, fetched=False))
        source = self.ledger.report(1040)["source_health"]["source_1"]
        self.assertEqual(source["query_auth"], "rejected")
        self.assertEqual(source["last_attempt_at"], 1020)
        self.assertEqual(source["last_stream_event_at"], 1030)

    def test_distinct_query_failures_and_retry_deadline(self):
        self.assertEqual(len({json.dumps(health(c), sort_keys=True) for c in
                              ("401", "403", "429", "timeout", "invalid_payload")}), 5)
        self.ledger.failure(self.ctx(), "429", retry_not_before=1300)
        self.assertEqual(self.ledger.report(1000)["source_health"]["source_1"]["retry_not_before"], 1300)

    def test_future_observation_quarantined(self):
        self.ledger.quota(quota(), self.ctx(at=1100))
        self.assertEqual(self.ledger.report(1000)["samples"][0]["freshness"]["state"], "quarantined")
        source = self.ledger.report(1000)["source_health"]["source_1"]
        self.assertEqual(source["code"], "revalidation_required")
        self.assertEqual(source["query_auth"], "unknown")

    def test_version_or_epoch_change_does_not_relabel_running_samples(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.failure(self.ctx("change_1", at=1010), "revalidation_required")
        report = self.ledger.report(1015)
        self.assertEqual(report["samples"][0]["producer_version"], "0.164.0")
        self.assertEqual(report["samples"][0]["freshness"]["state"], "unvalidated")

    def test_late_old_generation_response_stays_historical(self):
        self.ledger.quota(quota(), self.ctx(epoch=2))
        self.ledger.quota(quota(50), self.ctx("late_1", epoch=1, at=1010))
        report = self.ledger.report(1015)
        self.assertEqual(report["source_health"]["source_1"]["epoch"], 2)
        self.assertEqual(report["samples"][-1]["freshness"]["state"], "unvalidated")

    def test_shared_declared_pool_conflict_preserves_candidates(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.quota(quota(44), self.ctx("query_2", source="source_2", at=1010))
        rows = self.ledger.report(1010)["samples"]
        self.assertEqual(len({r["scope"] for r in rows}), 1)
        primary = [r for r in rows if r["window"] == "primary"]
        self.assertTrue(all(r["conflict"] for r in primary))
        self.assertTrue(all(r["rate"]["value"] is None for r in primary))
        self.assertEqual([r["value"] for r in primary], [40, 44])

    def test_different_windows_and_scopes_not_conflicted(self):
        self.ledger.quota(quota(), self.ctx())
        self.ledger.quota(quota(44), self.ctx("query_2", source="source_2", scope="other_pool"))
        self.assertFalse(any(r["conflict"] for r in self.ledger.report(1001)["samples"]))

    def test_decrease_is_preserved_without_negative_spending_or_burn(self):
        self.ledger.quota(quota(40), self.ctx())
        self.ledger.quota(quota(30), self.ctx("query_2", at=1060))
        row = self.ledger.report(1060)["samples"][-2]
        self.assertEqual(row["value"], 30)
        self.assertEqual(row["rate"]["reason"], "window_semantics_unknown")

    def test_private_fields_are_dropped_before_report(self):
        payload = quota()
        payload.update(email="SECRET_EMAIL", token="SECRET_TOKEN", body="SECRET_BODY",
                       path="/private/transcript", command="$(touch /tmp/never)")
        self.ledger.quota(payload, self.ctx())
        self.ledger.tokens(tokens(), self.ctx("event_1", fetched=False))
        report = json.dumps(self.ledger.report(1001))
        for secret in ("PRIVATE_ACCOUNT", "PRIVATE_THREAD", "PRIVATE_TURN", "SECRET", "/private/", "touch"):
            self.assertNotIn(secret, report)

    def test_invalid_and_oversized_json_safe_codes(self):
        for line in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}', 'bad private body', 'x' * (2**20 + 1)):
            with self.subTest(line=line[:20]), self.assertRaises(UsageError) as caught:
                loads(line)
            self.assertNotIn("private", str(caught.exception))

    def test_reject_invalid_numbers_and_times_atomically(self):
        for value in (-1, 101, True, float("nan"), "40", 10**1000):
            with self.subTest(value=value), self.assertRaises(UsageError):
                self.ledger.quota(quota(value), self.ctx())
        self.assertFalse(self.ledger.samples)
        with self.assertRaisesRegex(UsageError, "invalid_times"):
            self.ledger.context("a", "b", 1, 10, sample_id="q", fetch_started_at=12, fetch_completed_at=11)
        with self.assertRaisesRegex(UsageError, "invalid_health"):
            health([])

    def test_conflicting_replay_and_unsupported_version(self):
        self.ledger.quota(quota(), self.ctx())
        with self.assertRaisesRegex(UsageError, "conflicting_replay"):
            self.ledger.quota(quota(50), self.ctx())
        with self.assertRaisesRegex(UsageError, "unsupported_producer"):
            self.ledger.replay({"schema_version": 1, "synthetic": True, "producer_version": "0.163.0"})

    def test_api_cost_and_credits_are_not_subscription_percentages(self):
        payload = quota()
        payload["rateLimitsByLimitId"]["codex"]["credits"] = {
            "balance": "SECRET_BALANCE", "hasCredits": True, "unlimited": False}
        self.ledger.quota(payload, self.ctx())
        report = self.ledger.report(1000)
        row = report["samples"][-1]
        self.assertEqual(row["lane"], "account_credit_availability")
        self.assertIsNone(row["balance"])
        self.assertNotIn("SECRET_BALANCE", json.dumps(report))
        self.assertIn("no_token_to_quota", report["api_cost"])


class LiveBoundaryTests(unittest.TestCase):
    def test_complete_synthetic_runtime_lifecycle_and_redaction(self):
        instances = []

        class Client:
            def __init__(self, command, timeout, cwd=None):
                self.methods = []
                self.requests = []
                self.closed = False
                self.process = SimpleNamespace(poll=lambda: 0)
                self.event_number = 0
                self.cwd = cwd
                instances.append(self)

            def send(self, message):
                self.notification = message

            def request(self, method, params):
                self.methods.append(method)
                self.requests.append((method, params))
                if method == "initialize":
                    return {}
                if method == "account/read":
                    return {"account": {"type": "chatgpt", "email": "PRIVATE_EMAIL", "planType": "pro"}}
                if method == "account/rateLimits/read":
                    return quota()
                if method == "thread/start":
                    return {"thread": {"id": "PRIVATE_THREAD"}, "model": live.MODEL, "reasoningEffort": "xhigh",
                            "sandbox": {"type": "readOnly"}, "approvalsReviewer": "user"}
                if method == "turn/start":
                    return {"turn": {"id": "PRIVATE_TURN"}}
                raise AssertionError(method)

            def receive(self):
                if self.event_number == 0:
                    self.on_event({"method": "thread/tokenUsage/updated", "params": tokens()})
                else:
                    self.on_event({"method": "turn/completed", "params": {
                        "threadId": "PRIVATE_THREAD", "turn": {"id": "PRIVATE_TURN", "status": "completed"}}})
                self.event_number += 1

            def close(self):
                self.closed = True

        with patch.object(live, "protocol_check", return_value={"version": "0.164.0"}), patch.object(live, "StdioClient", Client):
            result = live.observe(Path("/explicit/setup.sh"), selftest=True)
        evidence = result["evidence"]
        self.assertEqual(evidence["status"], "observed")
        self.assertTrue(evidence["quota_observed"] and evidence["token_event_observed"])
        self.assertEqual(evidence["session_payer_binding"], "unknown")
        self.assertTrue(instances[0].closed)
        requests = dict(instances[0].requests)
        self.assertEqual(requests["account/read"], {"refreshToken": False})
        self.assertTrue(requests["thread/start"]["ephemeral"])
        self.assertEqual(requests["thread/start"]["sandbox"], "read-only")
        self.assertEqual(requests["thread/start"]["cwd"], instances[0].cwd)
        self.assertTrue(evidence["read_only_sandbox_confirmed"])
        self.assertFalse(evidence["tool_activity_observed"])
        self.assertEqual(requests["turn/start"]["effort"], "xhigh")
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def runtime_result(self, *, fail=None, early=None, sandbox="readOnly", complete=True, failure=None,
                       before=None, token_only=False, selftest=True, model=live.MODEL, effort="xhigh", reviewer="user"):
        class Client:
            def __init__(self, command, timeout, cwd=None):
                self.methods = []
                self.closed = False
                self.process = SimpleNamespace(poll=lambda: 0 if self.closed else None)

            def send(self, message):
                pass

            def request(self, method, params):
                self.methods.append(method)
                for message in (before or {}).get(method, []):
                    self.on_event(message)
                if fail == method:
                    if failure is not None:
                        raise failure
                    raise UsageError("rpc_error")
                if method == "initialize":
                    return {}
                if method == "account/read":
                    return {"account": {"type": "chatgpt"}}
                if method == "account/rateLimits/read":
                    return quota()
                if method == "thread/start":
                    return {"thread": {"id": "PRIVATE_THREAD"}, "model": model, "reasoningEffort": effort,
                            "sandbox": {"type": sandbox}, "approvalsReviewer": reviewer}
                if method == "turn/start":
                    for message in early or []:
                        self.on_event(message)
                    return {"turn": {"id": "PRIVATE_TURN"}}
                raise AssertionError(method)

            def receive(self):
                if fail == "events":
                    raise UsageError("timeout")
                if fail == "account_change":
                    self.on_event({"method": "account/updated", "params": {}})
                    return
                self.on_event({"method": "turn/completed", "params": {
                    "threadId": "PRIVATE_THREAD", "turn": {"id": "PRIVATE_TURN",
                    "status": "completed" if complete else "failed"}}})

            def close(self):
                self.closed = True

        with patch.object(live, "protocol_check", return_value={"version": "0.164.0"}), patch.object(live, "StdioClient", Client):
            return live.observe(Path("/explicit/setup.sh"), selftest=selftest and not token_only,
                                token_selftest=token_only)

    def test_token_selftest_skips_account_and_quota_and_keeps_payer_unknown(self):
        updates = [
            {"method": "account/updated", "params": {"accountId": "PRIVATE_ACCOUNT", "authMode": "chatgpt"}},
            {"method": "account/rateLimits/updated", "params": quota(99)}]
        result = self.runtime_result(token_only=True, before={
            method: updates for method in ("initialize", "thread/start", "turn/start")}, early=[
                {"method": "thread/tokenUsage/updated", "params": tokens()}])
        evidence, report = result["evidence"], result["report"]
        self.assertEqual(evidence["request_methods"], ["initialize", "thread/start", "turn/start"])
        self.assertEqual(evidence["status"], "observed")
        self.assertEqual(evidence["coverage"], {"account": "not_queried", "quota": "not_queried", "tokens": "observed"})
        self.assertFalse(evidence["quota_queried"] or evidence["quota_observed"])
        self.assertIsNone(evidence["account_identity_present"])
        self.assertEqual(evidence["session_payer_binding"], "unknown")
        self.assertTrue(evidence["child_terminated"])
        self.assertEqual([s["lane"] for s in report["samples"]], ["session_tokens"])
        self.assertEqual(report["samples"][0]["session_payer_binding"], "unknown")
        self.assertIsNone(report["samples"][0]["pool"])
        self.assertEqual(report["source_health"]["source_1"]["code"], "not_queried")
        self.assertEqual(report["source_health"]["source_1"]["query_auth"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_token_selftest_failure_never_becomes_quota_failure(self):
        for failure in ("thread/start", "turn/start", "events"):
            with self.subTest(failure=failure):
                result = self.runtime_result(token_only=True, fail=failure)
                evidence = result["evidence"]
                self.assertEqual(evidence["status"], "blocked")
                self.assertEqual(evidence["coverage"], {"account": "not_queried", "quota": "not_queried", "tokens": "failed"})
                self.assertTrue(evidence["child_terminated"])
                self.assertFalse(result["report"]["source_health"])
        result = self.runtime_result(token_only=True, complete=False, early=[
            {"method": "thread/tokenUsage/updated", "params": tokens()}])
        self.assertEqual(result["evidence"]["blocker"], "selftest_turn_failed")
        self.assertEqual(result["evidence"]["coverage"]["tokens"], "observed")
        self.assertEqual(result["report"]["source_health"]["source_1"]["code"], "not_queried")

    def test_account_or_quota_errors_never_fall_back_to_token_selftest(self):
        for selftest in (False, True):
            for method in ("account/read", "account/rateLimits/read"):
                with self.subTest(selftest=selftest, method=method):
                    result = self.runtime_result(selftest=selftest, fail=method, failure=live.RpcError({
                        "code": 403, "message": "PRIVATE permission denied"}, method))
                    evidence = result["evidence"]
                    self.assertEqual(evidence["request_methods"], ["initialize", "account/read"] +
                                     ([method] if method == "account/rateLimits/read" else []))
                    self.assertEqual(evidence["status"], "blocked")
                    self.assertEqual(evidence["coverage"]["tokens"], "not_requested")
                    self.assertEqual(evidence["coverage"]["account" if method == "account/read" else "quota"], "failed")
                    self.assertTrue(evidence["child_terminated"])

    def test_snapshot_still_stops_after_quota_query(self):
        result = self.runtime_result(selftest=False)
        self.assertEqual(result["evidence"]["request_methods"], ["initialize", "account/read", "account/rateLimits/read"])
        self.assertEqual(result["evidence"]["coverage"], {"account": "observed", "quota": "observed", "tokens": "not_requested"})
        self.assertEqual(result["evidence"]["status"], "observed")

    def test_turn_failure_does_not_change_successful_quota_query_health(self):
        for failure in ("thread/start", "turn/start", "events"):
            with self.subTest(failure=failure):
                result = self.runtime_result(fail=failure)
                self.assertEqual(result["evidence"]["status"], "blocked")
                self.assertEqual(result["evidence"]["coverage"]["quota"], "observed")
                self.assertEqual(result["evidence"]["coverage"]["tokens"], "failed")
                self.assertEqual(result["report"]["source_health"]["source_1"]["code"], "ok")

    def test_account_read_failure_does_not_fabricate_quota_query_attempt(self):
        result = self.runtime_result(fail="account/read")
        self.assertEqual(result["evidence"]["failure_stage"], "account_read")
        self.assertEqual(result["report"]["source_health"], {})

    def test_quota_failure_records_only_the_query_failure(self):
        result = self.runtime_result(fail="account/rateLimits/read")
        self.assertEqual(result["evidence"]["failure_stage"], "quota_query")
        self.assertEqual(result["report"]["source_health"]["source_1"]["code"], "rpc_error")
        self.assertNotIn("thread/start", result["evidence"]["request_methods"])

    def test_account_scope_change_invalidates_prior_query(self):
        result = self.runtime_result(fail="account_change")
        self.assertEqual(result["evidence"]["blocker"], "account_scope_changed")
        self.assertTrue(result["evidence"]["child_terminated"])
        self.assertEqual(result["report"]["source_health"]["source_1"]["code"], "revalidation_required")
        self.assertEqual(result["report"]["samples"][0]["freshness"]["state"], "unvalidated")

    def test_bootstrap_notifications_wait_for_account_response_before_collecting(self):
        bootstrap = [
            {"method": "account/rateLimits/updated", "params": quota(99)},
            {"method": "account/updated", "params": {"authMode": "chatgpt", "planType": "PRIVATE_PLAN"}}]
        result = self.runtime_result(before={"initialize": bootstrap, "account/read": bootstrap}, early=[
            {"method": "thread/tokenUsage/updated", "params": tokens()}])
        evidence = result["evidence"]
        self.assertEqual(evidence["status"], "observed")
        self.assertEqual(evidence["bootstrap_account_notifications"], 2)
        self.assertEqual(evidence["request_methods"], [
            "initialize", "account/read", "account/rateLimits/read", "thread/start", "turn/start"])
        self.assertTrue(evidence["child_terminated"])
        self.assertEqual(len(result["report"]["samples"]), 3)
        self.assertNotIn(99, [s.get("value") for s in result["report"]["samples"]])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_bootstrap_notification_cannot_mask_account_error_or_authorize_quota(self):
        for message, cause in (
                ("workspace routing discovery unauthorized (401)", "routing_authentication_rejected"),
                ("account changed during workspace routing discovery", "account_scope_changed")):
            with self.subTest(cause=cause):
                result = self.runtime_result(fail="account/read", failure=live.RpcError({
                    "code": -32603, "message": message}, "account/read"), before={"account/read": [
                        {"method": "account/updated", "params": {"authMode": "chatgpt"}},
                        {"method": "account/rateLimits/updated", "params": quota(99)}]})
                evidence = result["evidence"]
                self.assertEqual(evidence["blocker"], "rpc_error")
                self.assertEqual(evidence["rpc_code"], -32603)
                self.assertEqual(evidence["rpc_cause"], cause)
                self.assertIsNone(evidence["account_identity_present"])
                self.assertEqual(evidence["request_methods"], ["initialize", "account/read"])
                self.assertTrue(evidence["child_terminated"])
                self.assertFalse(result["report"]["samples"])
                self.assertFalse(result["report"]["source_health"])

    def test_account_notification_after_binding_stops_inflight_quota_response(self):
        result = self.runtime_result(before={"account/rateLimits/read": [
            {"method": "account/updated", "params": {"authMode": "chatgpt"}}]})
        evidence = result["evidence"]
        self.assertEqual(evidence["blocker"], "account_scope_changed")
        self.assertEqual(evidence["bootstrap_account_notifications"], 0)
        self.assertEqual(evidence["failure_stage"], "quota_query")
        self.assertEqual(evidence["request_methods"], ["initialize", "account/read", "account/rateLimits/read"])
        self.assertTrue(evidence["child_terminated"])
        self.assertFalse(result["report"]["samples"])
        self.assertEqual(result["report"]["source_health"]["source_1"]["code"], "revalidation_required")

    def test_wrong_effective_sandbox_stops_before_turn(self):
        for token_only in (False, True):
            for settings, blocker in (({"sandbox": "workspaceWrite"}, "execution_policy_mismatch"),
                                      ({"reviewer": "auto_review"}, "execution_policy_mismatch"),
                                      ({"model": "other"}, "model_or_effort_mismatch"),
                                      ({"effort": "low"}, "model_or_effort_mismatch")):
                with self.subTest(token_only=token_only, settings=settings):
                    result = self.runtime_result(token_only=token_only, **settings)
                    self.assertEqual(result["evidence"]["blocker"], blocker)
                    self.assertNotIn("turn/start", result["evidence"]["request_methods"])
                    self.assertTrue(result["evidence"]["child_terminated"])

    def test_early_events_are_bound_only_after_turn_response(self):
        for token_only in (False, True):
            with self.subTest(token_only=token_only):
                result = self.runtime_result(token_only=token_only, early=[
                    {"method": "thread/tokenUsage/updated", "params": tokens()},
                    {"method": "turn/completed", "params": {
                        "threadId": "PRIVATE_THREAD", "turn": {"id": "PRIVATE_TURN", "status": "completed"}}}])
                self.assertEqual(result["evidence"]["status"], "observed")
                self.assertTrue(result["evidence"]["token_event_observed"])
                self.assertTrue(result["evidence"]["child_terminated"])
                self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_early_other_turn_event_is_rejected_without_token_sample(self):
        for token_only in (False, True):
            with self.subTest(token_only=token_only):
                result = self.runtime_result(token_only=token_only, early=[
                    {"method": "thread/tokenUsage/updated", "params": tokens(turn="OTHER_PRIVATE")}])
                self.assertEqual(result["evidence"]["blocker"], "unexpected_turn")
                self.assertFalse(any(s["lane"] == "session_tokens" for s in result["report"]["samples"]))
                self.assertTrue(result["evidence"]["child_terminated"])

    def test_early_tool_activity_stops_collection(self):
        for token_only in (False, True):
            with self.subTest(token_only=token_only):
                result = self.runtime_result(token_only=token_only, early=[{"method": "item/started", "params": {
                    "threadId": "PRIVATE_THREAD", "turnId": "PRIVATE_TURN", "item": {
                        "type": "commandExecution", "command": "PRIVATE_COMMAND"}}}])
                self.assertEqual(result["evidence"]["blocker"], "unexpected_tool_activity")
                self.assertTrue(result["evidence"]["tool_activity_observed"])
                self.assertTrue(result["evidence"]["child_terminated"])
                self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_missing_token_event_remains_unknown_with_good_query_health(self):
        result = self.runtime_result()
        self.assertEqual(result["evidence"]["blocker"], "token_event_missing")
        self.assertFalse(result["evidence"]["token_event_observed"])
        self.assertEqual(result["report"]["source_health"]["source_1"]["code"], "ok")
        result = self.runtime_result(token_only=True)
        self.assertEqual(result["evidence"]["blocker"], "token_event_missing")
        self.assertEqual(result["evidence"]["coverage"], {"account": "not_queried", "quota": "not_queried", "tokens": "failed"})
        self.assertTrue(result["evidence"]["child_terminated"])
        self.assertFalse(result["report"]["source_health"])

    def test_failed_account_read_has_unknown_identity_and_no_session(self):
        result = self.runtime_result(fail="account/read")
        evidence = result["evidence"]
        self.assertIsNone(evidence["account_identity_present"])
        self.assertFalse(evidence["new_thread_created"] or evidence["new_turn_started"])
        self.assertEqual(evidence["new_thread_state"], "not_requested")
        self.assertEqual(evidence["new_turn_state"], "not_requested")
        self.assertFalse(result["report"]["samples"])

    def test_missing_creation_response_cannot_establish_unused_turn_allowance(self):
        for method, thread_state, turn_state in (
                ("thread/start", "outcome_unknown", "not_requested"),
                ("turn/start", "created", "outcome_unknown")):
            with self.subTest(method=method):
                result = self.runtime_result(fail=method, failure=UsageError("timeout"))
                evidence = result["evidence"]
                self.assertEqual(evidence["new_thread_state"], thread_state)
                self.assertEqual(evidence["new_turn_state"], turn_state)
                self.assertEqual(evidence["request_methods"].count("turn/start"), int(method == "turn/start"))

    def test_account_rpc_categories_stop_before_dependent_requests(self):
        for code, message, category, cause in (
                (-32601, "PRIVATE", "unsupported_method", "unknown"),
                (-32603, "workspace routing discovery unauthorized (401)", "internal_error", "routing_authentication_rejected"),
                (-32603, "PRIVATE unknown internal failure", "internal_error", "unknown")):
            with self.subTest(code=code, cause=cause):
                error = live.RpcError({"code": code, "message": message, "data": "PRIVATE"}, "account/read")
                result = self.runtime_result(fail="account/read", failure=error)
                evidence = result["evidence"]
                self.assertEqual(evidence["rpc_code"], code)
                self.assertEqual(evidence["rpc_category"], category)
                self.assertEqual(evidence["rpc_cause"], cause)
                self.assertEqual(evidence["request_methods"], ["initialize", "account/read"])
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_rpc_diagnostic_retains_only_numeric_code(self):
        client = object.__new__(live.StdioClient)
        client.sequence = 0
        client.methods = []
        client.send = lambda message: None
        client.receive = lambda: {"id": 1, "error": {
            "code": -32603, "message": "PRIVATE_EMAIL PRIVATE_TOKEN", "data": {"private": "PRIVATE_BODY"}}}
        with self.assertRaises(live.RpcError) as caught:
            client.request("account/read", {"refreshToken": False})
        self.assertEqual(caught.exception.rpc_code, -32603)
        self.assertEqual(caught.exception.rpc_category, "internal_error")
        self.assertEqual(caught.exception.rpc_cause, "unknown")
        self.assertEqual(str(caught.exception), "rpc_error")
        self.assertNotIn("PRIVATE", repr(caught.exception.__dict__))
        for code in (True, "PRIVATE", 10**40, None):
            self.assertIsNone(live.RpcError({"code": code}).rpc_code)
            self.assertEqual(live.RpcError({"code": code}).rpc_category, "unknown")

    def test_rpc_categories_do_not_infer_auth_from_http_lookalikes_or_text(self):
        for code, category in ((-32601, "unsupported_method"), (-32602, "invalid_params"),
                               (-32600, "invalid_request"), (-32700, "parse_error"),
                               (-32603, "internal_error"), (401, "unknown"),
                               (403, "unknown"), (-32001, "overloaded")):
            with self.subTest(code=code):
                error = live.RpcError({"code": code, "message": "PRIVATE unauthorized unsupported method",
                                       "data": {"accountId": "PRIVATE", "httpStatusCode": 401}})
                self.assertEqual(error.rpc_code, code)
                self.assertEqual(error.rpc_category, category)
                self.assertEqual(error.rpc_cause, "unknown")
                self.assertNotIn("PRIVATE", repr(error.__dict__))

    def test_rpc_cause_matches_only_exact_reviewed_method_code_and_message(self):
        cases = (("account/read", -32603, "workspace routing discovery timed out", "routing_timeout"),
                 ("account/read", -32603, "workspace routing discovery failed", "routing_discovery_failed"),
                 ("account/read", -32603, "account changed during workspace routing discovery", "account_scope_changed"),
                 ("account/rateLimits/read", -32600, "codex account authentication required to read rate limits", "authentication_required"),
                 ("account/rateLimits/read", -32600, "chatgpt authentication required to read rate limits", "authentication_mode_unsupported"))
        for method, code, message, cause in cases:
            with self.subTest(cause=cause):
                self.assertEqual(live.RpcError({"code": code, "message": message}, method).rpc_cause, cause)
                for changed_method, changed_code, changed_message in (
                        (None, code, message), (method, 401, message),
                        (method, code, message + " PRIVATE"), (method, code, [message])):
                    error = live.RpcError({"code": changed_code, "message": changed_message}, changed_method)
                    self.assertEqual(error.rpc_cause, "unknown")
                    self.assertNotIn("PRIVATE", repr(error.__dict__))

    def test_version_probe_stops_before_starting_server(self):
        for token_only in (False, True):
            with self.subTest(token_only=token_only), patch.object(live, "checked_command", return_value="codex-cli 0.163.0"), patch.object(live, "StdioClient") as client:
                with self.assertRaisesRegex(UsageError, "unsupported_producer"):
                    live.observe(Path("/explicit/setup.sh"), token_selftest=token_only)
                client.assert_not_called()

    def test_schema_gate_rejects_missing_fields(self):
        def command(args, **kwargs):
            if "--version" in args:
                return "codex-cli 0.164.0"
            if "--help" in args:
                return "--listen stdio:// generate-json-schema"
            directory = Path(args[-1]) / "v2"
            directory.mkdir()
            for name in live.SCHEMAS:
                (directory / (name + ".json")).write_text('{"properties":{}}')
            return ""
        for token_only in (False, True):
            with self.subTest(token_only=token_only), patch.object(live, "checked_command", side_effect=command), patch.object(live, "StdioClient") as client:
                with self.assertRaisesRegex(UsageError, "installed_schema_incompatible"):
                    live.observe(Path("/explicit/setup.sh"), token_selftest=token_only)
                client.assert_not_called()

    def test_token_selftest_cli_selects_explicit_mode_and_evidence_only(self):
        result = self.runtime_result(token_only=True, early=[
            {"method": "thread/tokenUsage/updated", "params": tokens()}])
        with patch.object(sys, "argv", ["account-usage.py", "token-selftest", "--launcher",
                                       "/explicit/setup.sh", "--evidence-only"]), \
                patch.object(live, "observe", return_value=result) as observe, patch("builtins.print") as output:
            self.assertEqual(live.main(), 0)
        observe.assert_called_once_with(Path("/explicit/setup.sh"), selftest=False, timeout=90, token_selftest=True)
        self.assertEqual(json.loads(output.call_args.args[0]), result["evidence"])

    def test_stdio_refuses_server_refresh_request_and_terminates_child(self):
        code = 'import json,time; print(json.dumps({"id":42,"method":"account/chatgptAuthTokens/refresh","params":{}}),flush=True); time.sleep(5)'
        client = live.StdioClient([sys.executable, "-u", "-c", code], timeout=2)
        try:
            with self.assertRaisesRegex(UsageError, "server_request_refused"):
                client.receive()
        finally:
            client.close()
        self.assertIsNotNone(client.process.poll())

    def test_stdio_timeout_is_bounded_and_reaped(self):
        client = live.StdioClient([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)
        try:
            with self.assertRaisesRegex(UsageError, "timeout"):
                client.receive()
        finally:
            client.close()
        self.assertIsNotNone(client.process.poll())

    def test_refresh_and_unapproved_methods_cannot_be_sent(self):
        client = object.__new__(live.StdioClient)
        for method, params in (("account/read", {"refreshToken": True}), ("thread/list", {}), ("account/logout", {})):
            with self.subTest(method=method), self.assertRaises(UsageError):
                client.request(method, params)

    def test_synthetic_cli_runs_without_home_discovery_or_executing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            envelope = {"schema_version": 1, "synthetic": True, "producer_version": "0.164.0",
                "source": "source_1", "scope": "shared_pool", "epoch": 1, "sample_id": "query_1",
                "observed_at": 1000, "fetch_started_at": 999, "fetch_completed_at": 1000,
                "kind": "quota", "payload": quota(), "command": "touch " + str(marker)}
            env = {"HOME": directory, "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run([sys.executable, str(REPO / "account-usage.py"), "replay", "--now", "1001"],
                input=json.dumps(envelope) + "\n", text=True, capture_output=True, env=env, check=True)
            report = json.loads(result.stdout)
            self.assertEqual(report["samples"][0]["value"], 40)
            self.assertEqual(report["samples"][0]["freshness"]["state"], "recently_fetched")
            self.assertFalse(marker.exists())
            self.assertFalse(list(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
