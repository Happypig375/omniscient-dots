"""Small, observational usage ledger. No filesystem, network or subprocess access."""

import copy
import hashlib
import json
import math
import re

CONTRACT = "codex-app-server-0.164.0/subset-1"
MAX_RECORD = 1_048_576
MAX_SAMPLES = 4096
ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class UsageError(ValueError):
    """Messages are fixed codes; never include input values."""


def require(condition, code="invalid_payload"):
    if not condition:
        raise UsageError(code)


def obj(value):
    require(isinstance(value, dict))
    return value


def number(value, low=0, high=10**18, integer=False):
    require(type(value) in (int, float))
    require(low <= value <= high and math.isfinite(value) and (not integer or type(value) is int))
    return value


def optional_number(value, **kwargs):
    return None if value is None else number(value, **kwargs)


def alias(value):
    require(isinstance(value, str) and ALIAS.fullmatch(value), "invalid_alias")
    return value


def boolean(value):
    require(value is None or type(value) is bool)
    return value


def loads(line):
    require(isinstance(line, (str, bytes)) and len(line) <= MAX_RECORD, "record_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_key")
            result[key] = value
        return result

    try:
        return obj(json.loads(line, object_pairs_hook=pairs,
                              parse_constant=lambda _: require(False, "nonfinite")))
    except (ValueError, RecursionError, UnicodeError) as error:
        if isinstance(error, UsageError):
            raise
        raise UsageError("invalid_json") from None


def health(code):
    """Endpoint health is independent of agent auth and coding capacity."""
    states = {
        "not_queried": ("unknown", "unknown", "unknown"),
        "ok": ("ok", "not_rejected", "valid"),
        "401": ("ok", "rejected", "unavailable"),
        "403": ("ok", "scope_or_access_refused", "unavailable"),
        "429": ("throttled", "unknown", "unavailable"),
        "timeout": ("timeout", "unknown", "unavailable"),
        "transport": ("failed", "unknown", "unavailable"),
        "invalid_payload": ("ok", "unknown", "invalid"),
        "rpc_error": ("unknown", "unknown", "unavailable"),
        "revalidation_required": ("unknown", "unknown", "unvalidated"),
    }
    require(isinstance(code, str) and code in states, "invalid_health")
    transport, auth, data = states[code]
    return {"code": code, "transport": transport, "query_auth": auth,
            "data": data, "agent_auth": "unknown"}


class Ledger:
    def __init__(self, ttl=120):
        self.ttl = number(ttl, low=1, high=3600)
        self.samples = []
        self.receipts = 0
        self.seen = {}
        self.sources = {}
        self.ids = {}

    def pseudonym(self, kind, value):
        # Raw protocol IDs are used only as in-memory equality keys, never hashed
        # into a public identifier, persisted, or displayed.
        require(isinstance(value, str) and 0 < len(value) <= 256)
        key = (kind, value)
        if key not in self.ids:
            self.ids[key] = f"{kind}_{1 + sum(k[0] == kind for k in self.ids)}"
        return self.ids[key]

    def context(self, source, scope, epoch, observed_at, *, sample_id,
                fetch_started_at=None, fetch_completed_at=None, synthetic=False):
        require(type(synthetic) is bool)
        number(epoch, integer=True)
        observed_at = number(observed_at)
        started = optional_number(fetch_started_at)
        completed = optional_number(fetch_completed_at)
        require((started is None) == (completed is None), "invalid_times")
        if started is not None:
            require(started <= completed <= observed_at, "invalid_times")
        return {"source": alias(source), "scope": alias(scope), "epoch": epoch,
                "sample_id": alias(sample_id), "observed_at": observed_at,
                "fetch_started_at": started, "fetch_completed_at": completed,
                "source_event_at": None, "producer_version": "0.164.0",
                "adapter_contract": CONTRACT, "synthetic": synthetic,
                "scope_confidence": "user_declared" if synthetic else "collector_connection_only"}

    def source(self, ctx):
        source = self.sources.get(ctx["source"])
        if source is None or ctx["epoch"] > source["epoch"]:
            # A new connection/account epoch cannot inherit old query success
            # or auth health merely because a stream event arrived.
            source = {**health("not_queried"), "epoch": ctx["epoch"]}
            self.sources[ctx["source"]] = source
        return source

    def failure(self, ctx, code, retry_not_before=None):
        state = health(code)
        retry = optional_number(retry_not_before)
        source = self.source(ctx)
        if ctx["epoch"] < source["epoch"] or ctx["observed_at"] < source.get("last_attempt_at", 0):
            return
        source.update(state)
        source["last_attempt_at"] = ctx["observed_at"]
        source["retry_not_before"] = retry
        source["failure_count"] = source.get("failure_count", 0) + 1
        if code in ("401", "403", "429", "invalid_payload"):
            source["last_transport_success_at"] = ctx["observed_at"]

    def add(self, ctx, rows):
        require(len(self.samples) + len(rows) <= MAX_SAMPLES, "ledger_full")
        # Dedup by originating query/event identity, not payload alone. New
        # queries returning identical values can still be new observations.
        key = (ctx["source"], ctx["epoch"], ctx["sample_id"])
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        self.receipts += 1
        if key in self.seen:
            require(self.seen[key] == digest, "conflicting_replay")
            return
        self.seen[key] = digest
        for row in rows:
            self.samples.append({**copy.deepcopy(ctx), **row})
        source = self.source(ctx)
        if ctx["epoch"] < source["epoch"]:
            return  # A late old-generation response is historical only.
        if ctx["fetch_completed_at"] is None:
            # A token/push event says nothing about the quota query's auth.
            source["last_stream_event_at"] = max(ctx["observed_at"], source.get("last_stream_event_at", 0))
            return
        if ctx["observed_at"] < source.get("last_attempt_at", 0):
            return  # Late responses do not clear a newer query failure.
        source.update(health("ok"))
        source.update(last_attempt_at=ctx["observed_at"],
                      last_valid_sample_at=ctx["observed_at"], failure_count=0,
                      retry_not_before=None)
        if ctx["fetch_completed_at"] is not None:
            source["last_transport_success_at"] = ctx["fetch_completed_at"]

    def quota(self, payload, ctx):
        payload = obj(payload)
        rows = []
        buckets = payload.get("rateLimitsByLimitId")
        if buckets is None:
            buckets = {"legacy": obj(payload.get("rateLimits"))}
        obj(buckets)
        require(len(buckets) <= 64)
        permission = boolean(payload.get("ordinaryUsageAllowed"))
        for meter_id, snapshot in buckets.items():
            snapshot = obj(snapshot)
            meter = self.pseudonym("meter", meter_id)
            for window in ("primary", "secondary"):
                data = snapshot.get(window)
                if data is None:
                    continue  # Omission never renews another field.
                data = obj(data)
                rows.append({"lane": "account_quota", "meter": meter, "window": window,
                             "value": number(data.get("usedPercent"), high=100, integer=True),
                             "unit": "percent_used",
                             "duration_minutes": optional_number(data.get("windowDurationMins"), integer=True),
                             "reset_at": optional_number(data.get("resetsAt"), integer=True),
                             "window_semantics": "unknown", "ordinary_usage_allowed": permission,
                             "account_identity_present": bool(payload.get("accountId")),
                             "session_payer_binding": "unknown"})
            # Credits are not API dollars or subscription percentages. Do not
            # guess a denomination or copy free-form balances/private details.
            credits = snapshot.get("credits")
            if credits is not None:
                credits = obj(credits)
                rows.append({"lane": "account_credit_availability", "meter": meter,
                             "has_credits": boolean(credits.get("hasCredits")),
                             "unlimited": boolean(credits.get("unlimited")),
                             "balance": None, "unit": "unknown", "balance_support": "deferred"})
        self.add(ctx, rows)

    def tokens(self, payload, ctx):
        require(ctx["fetch_completed_at"] is None, "token_query_times_forbidden")
        payload = obj(payload)
        usage = obj(payload.get("tokenUsage"))
        row = {"lane": "session_tokens", "session": self.pseudonym("session", payload.get("threadId")),
               "turn": self.pseudonym("turn", payload.get("turnId")),
               "session_payer_binding": "unknown", "pool": None,
               "temporality": "provider_total_and_last", "child_inclusion": "unknown",
               "context_capacity_tokens": optional_number(usage.get("modelContextWindow"), integer=True)}
        for group in ("total", "last"):
            counts = obj(usage.get(group))
            row[group] = {key: number(counts.get(key), integer=True) for key in
                          ("inputTokens", "cachedInputTokens", "outputTokens",
                           "reasoningOutputTokens", "totalTokens")}
            row[group]["cacheWriteInputTokens"] = optional_number(counts.get("cacheWriteInputTokens"), integer=True)
        # Cached input and reasoning output are subcategories, never added to
        # totalTokens again. modelContextWindow is capacity, not tokens spent.
        self.add(ctx, [row])

    def replay(self, envelope):
        require(type(envelope.get("schema_version")) is int and envelope.get("schema_version") == 1
                and envelope.get("synthetic") is True,
                "synthetic_envelope_required")
        require(envelope.get("producer_version") == "0.164.0", "unsupported_producer")
        ctx = self.context(envelope.get("source"), envelope.get("scope"), envelope.get("epoch"),
                           envelope.get("observed_at"), sample_id=envelope.get("sample_id"),
                           fetch_started_at=envelope.get("fetch_started_at"),
                           fetch_completed_at=envelope.get("fetch_completed_at"), synthetic=True)
        kind = envelope.get("kind")
        if kind == "quota":
            self.quota(envelope.get("payload"), ctx)
        elif kind == "tokens":
            self.tokens(envelope.get("payload"), ctx)
        elif kind == "health":
            self.failure(ctx, envelope.get("code"), envelope.get("retry_not_before"))
        else:
            raise UsageError("unsupported_adapter")

    def freshness(self, sample, now):
        if sample["observed_at"] > now:
            return {"state": "quarantined", "reason": "future_observation", "fresh_until": None}
        source = self.sources.get(sample["source"], {})
        if source.get("epoch", sample["epoch"]) != sample["epoch"] or source.get("code") == "revalidation_required":
            return {"state": "unvalidated", "reason": "epoch_or_contract_changed", "fresh_until": None}
        anchor = sample["fetch_completed_at"]
        if anchor is None:
            return {"state": "unknown", "reason": "event_received_source_time_unknown", "fresh_until": None}
        deadline = anchor + self.ttl
        if sample.get("reset_at") is not None:
            deadline = min(deadline, sample["reset_at"])
        return {"state": "recently_fetched" if now < deadline else "stale",
                "reason": "query_receipt_provider_measurement_time_unknown", "fresh_until": deadline}

    def report(self, now):
        now = number(now)
        rows = []
        for sample in self.samples:
            row = copy.deepcopy(sample)
            row["freshness"] = self.freshness(sample, now)
            row["age_since_observation_seconds"] = max(0, now - sample["observed_at"])
            row["conflict"] = False
            if row["lane"] == "account_quota":
                for other in self.samples:
                    keys = ("scope", "epoch", "meter", "window", "duration_minutes", "reset_at")
                    if other.get("lane") == "account_quota" and all(other.get(k) == row.get(k) for k in keys):
                        # Queries from distinct sources have no comparable
                        # measurement clocks. Preserve rather than choose one.
                        if other["source"] != row["source"] and other["value"] != row["value"]:
                            row["conflict"] = True
                row["rate"] = {"value": None, "reason": "conflict" if row["conflict"] else "window_semantics_unknown"}
            rows.append(row)
        source_health = copy.deepcopy(self.sources)
        for source in source_health.values():
            if source.get("last_attempt_at", 0) > now:
                source.update(health("revalidation_required"))
                source["health_time_status"] = "future_observation"
        return {"schema_version": 1, "adapter_contract": CONTRACT, "as_of": now,
                "freshness_policy": {"ttl_seconds": self.ttl, "origin": "observer_policy_not_provider_guarantee"},
                "samples": rows, "source_health": source_health, "receipts": self.receipts,
                "coverage": "explicit_sources_only_other_hosts_and_external_usage_unknown",
                "identity_scope": "aliases_are_run_local_unless_synthetic_user_declared",
                "priority_policy": "unspecified", "execution": "disabled",
                "api_cost": "unavailable_no_token_to_quota_or_dollar_conversion"}
