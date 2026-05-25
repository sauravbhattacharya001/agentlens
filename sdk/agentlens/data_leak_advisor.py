"""Agentic outbound-leak / PII / secret exposure advisor for AgentLens.

:class:`DataLeakAdvisor` is the 13th agentic sibling in the AgentLens
advisor family (sampling / alert_rule_synthesizer / incident_radar /
slo_burn_rate / trace_completion / agent_loop / cost_attribution /
prompt_drift / cacheability / tool_reliability / model_migration /
eval_regression).

It answers a question that comes up the moment anyone tries to ship
traces off-box for analytics, replay, or training:

    "Which call-sites are currently leaking PII or credentials through
    LLM prompts/responses, how bad is it, and what should I redact or
    block first before I export this trace bundle?"

Inputs
------
* Any iterable of AgentEvent / dict / attr-bearing event objects. The
  advisor inspects ``input_data`` (chat messages, prompts) *and*
  ``output_data`` (model responses, tool results), so it catches both
  the "user pasted their SSN" case and the "model echoed back an AWS
  key from a tool result" case.
* An optional :class:`DataLeakOptions` for thresholds / pattern
  toggles, plus a ``risk_appetite`` (cautious/balanced/aggressive)
  that tunes the verdict ladder without changing detection.

Detection
---------
* PII patterns (email, US phone, US SSN, IPv4, IBAN, credit-card-like
  digit-runs with Luhn confirmation).
* Secret patterns (AWS access key id, AWS secret-style 40-char
  base64, generic Bearer/sk_/pk_/api_key tokens, JWTs, GitHub PATs,
  Google API keys, Slack tokens, private-key PEM headers).
* Each finding carries a ``severity`` (1..100), a stable ``code``,
  and the *event field* it was found in (input/output) so an exporter
  can decide whether to mask before shipping.

Per-slice (model :: source) verdicts:
* ``SECRET_LEAK`` (P0) -- any high-severity secret hit.
* ``PII_LEAK`` (P0/P1) -- multiple PII categories or repeated hits.
* ``TRACE_PII_MINOR`` (P2) -- a single PII hit; redact-on-export.
* ``CLEAN`` (P3) -- no findings.

Portfolio summary + Datadog-style A..F grade + deduped P0-first
playbook of redaction / rotation / training-data-blocking actions.

Pure stdlib. Deterministic. Never mutates the input events; the
report contains only short *previews* (token tails) of any matched
value, never the full secret or PII string.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class LeakVerdict(Enum):
    SECRET_LEAK = "secret_leak"
    PII_LEAK = "pii_leak"
    TRACE_PII_MINOR = "trace_pii_minor"
    CLEAN = "clean"


class ActionPriority(Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class LeakGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class RiskAppetite(Enum):
    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

    @classmethod
    def parse(cls, value: "str | RiskAppetite") -> "RiskAppetite":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            return cls.BALANCED


class FindingKind(Enum):
    PII = "pii"
    SECRET = "secret"


class FindingSource(Enum):
    INPUT = "input"
    OUTPUT = "output"


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DataLeakOptions:
    """Tunables for :class:`DataLeakAdvisor`.

    Defaults are conservative for production traces: any single secret
    hit is enough to flag a slice, multiple PII hits are required for
    P1+. Per-event preview length is capped at 12 characters so the
    report itself can be checked into version control or shipped to a
    less-trusted destination without re-leaking what it found.
    """

    scan_inputs: bool = True
    scan_outputs: bool = True
    max_preview_chars: int = 12
    max_findings_per_event: int = 50
    pii_minor_threshold: int = 1     # findings to surface a slice at all
    pii_major_threshold: int = 5     # findings to escalate to PII_LEAK P0/P1
    distinct_pii_kinds_for_major: int = 2
    secret_severity_floor: int = 70  # any secret >= this is automatic P0
    top_n: int = 10
    include_test_models: bool = True

    # Pattern toggles (off-by-default ones are noisy on synthetic traces).
    enable_email: bool = True
    enable_us_phone: bool = True
    enable_us_ssn: bool = True
    enable_ipv4: bool = False
    enable_iban: bool = True
    enable_credit_card: bool = True
    enable_aws_keys: bool = True
    enable_generic_bearer: bool = True
    enable_jwt: bool = True
    enable_github_pat: bool = True
    enable_google_api_key: bool = True
    enable_slack_token: bool = True
    enable_pem_private_key: bool = True


@dataclass(frozen=True)
class LeakFinding:
    code: str
    kind: FindingKind
    source: FindingSource
    severity: int
    preview: str
    event_id: str
    session_id: str
    field_path: str


@dataclass
class LeakSlice:
    slice_id: str
    model: str
    source: FindingSource
    event_count: int
    finding_count: int
    distinct_kinds: int
    secret_count: int
    pii_count: int
    max_severity: int
    verdict: LeakVerdict
    priority: ActionPriority
    findings: list[LeakFinding] = field(default_factory=list)


@dataclass
class PlaybookAction:
    id: str
    priority: ActionPriority
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    related_slice_ids: list[str] = field(default_factory=list)
    suggested_value: Optional[str] = None


@dataclass
class LeakPortfolio:
    total_events: int
    scanned_events: int
    secret_leak_count: int
    pii_leak_count: int
    minor_leak_count: int
    clean_count: int
    total_findings: int
    portfolio_grade: LeakGrade
    headline: str


@dataclass
class DataLeakReport:
    generated_at: datetime
    options: DataLeakOptions
    risk_appetite: RiskAppetite
    portfolio: LeakPortfolio
    slices: list[LeakSlice]
    playbook: list[PlaybookAction]
    insights: list[str]

    # ------------------------------------------------------------------ #
    # Renderers
    # ------------------------------------------------------------------ #

    def to_text(self) -> str:
        return self._render(markdown=False)

    def to_markdown(self) -> str:
        return self._render(markdown=True)

    def _render(self, markdown: bool) -> str:
        h2 = "## " if markdown else ""
        lines: list[str] = []
        lines.append(f"{h2}DataLeak advisor ({self.risk_appetite.value})")
        lines.append("")
        p = self.portfolio
        lines.append(f"- Scanned: {p.scanned_events}/{p.total_events} events")
        lines.append(f"- Findings: {p.total_findings}")
        lines.append(
            f"- Secret leaks: {p.secret_leak_count} | "
            f"PII leaks: {p.pii_leak_count} | "
            f"Minor: {p.minor_leak_count} | "
            f"Clean: {p.clean_count}"
        )
        lines.append(f"- Grade: {p.portfolio_grade.value} - {p.headline}")
        lines.append("")
        lines.append(f"{h2}Top slices")
        lines.append("")
        if markdown:
            lines.append("| Priority | Model | Source | Verdict | Findings | Max sev |")
            lines.append("|---|---|---|---|---|---|")
        for sl in self.slices:
            row = (
                f"{sl.priority.value} | {sl.model} | {sl.source.value} | "
                f"{sl.verdict.value} | {sl.finding_count} | {sl.max_severity}"
            )
            lines.append(f"| {row} |" if markdown else f"- {row}")
        lines.append("")
        lines.append(f"{h2}Playbook")
        lines.append("")
        for a in self.playbook:
            lines.append(
                f"- [{a.priority.value}] {a.id}: {a.label} "
                f"(owner={a.owner}, blast={a.blast_radius}, "
                f"reversibility={a.reversibility})"
            )
        if self.insights:
            lines.append("")
            lines.append(f"{h2}Insights")
            lines.append("")
            for code in self.insights:
                lines.append(f"- {code}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Pattern table
# --------------------------------------------------------------------------- #


# (code, kind, severity, regex, enable_attr)
_PATTERNS: list[tuple[str, FindingKind, int, "re.Pattern[str]", str]] = [
    # --- PII ----------------------------------------------------------
    (
        "PII_EMAIL",
        FindingKind.PII,
        45,
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        "enable_email",
    ),
    (
        "PII_US_PHONE",
        FindingKind.PII,
        45,
        re.compile(r"\b(?:\+?1[\s\-.])?\(?\d{3}\)?[\s\-.]\d{3}[\s\-.]\d{4}\b"),
        "enable_us_phone",
    ),
    (
        "PII_US_SSN",
        FindingKind.PII,
        85,
        re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        "enable_us_ssn",
    ),
    (
        "PII_IPV4",
        FindingKind.PII,
        25,
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
        ),
        "enable_ipv4",
    ),
    (
        "PII_IBAN",
        FindingKind.PII,
        70,
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        "enable_iban",
    ),
    # --- Secrets ------------------------------------------------------
    (
        "SECRET_AWS_ACCESS_KEY_ID",
        FindingKind.SECRET,
        90,
        re.compile(r"\b(?:" + "AKIA" + r"|" + "ASIA" + r")[0-9A-Z]{16}\b"),
        "enable_aws_keys",
    ),
    (
        "SECRET_JWT",
        FindingKind.SECRET,
        80,
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
        "enable_jwt",
    ),
    (
        "SECRET_GITHUB_PAT",
        FindingKind.SECRET,
        95,
        re.compile(r"\b(?:" + "ghp" + "|" + "gho" + "|" + "ghu" + "|" + "ghs" + "|" + "ghr" + r"|github_pat)_[A-Za-z0-9_]{20,}\b"),
        "enable_github_pat",
    ),
    (
        "SECRET_GOOGLE_API_KEY",
        FindingKind.SECRET,
        85,
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
        "enable_google_api_key",
    ),
    (
        "SECRET_SLACK_TOKEN",
        FindingKind.SECRET,
        90,
        re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
        "enable_slack_token",
    ),
    (
        "SECRET_PEM_PRIVATE_KEY",
        FindingKind.SECRET,
        95,
        re.compile("-----BEGIN " + r"[A-Z ]*" + "PRIVATE KEY-----"),
        "enable_pem_private_key",
    ),
    (
        "SECRET_GENERIC_BEARER",
        FindingKind.SECRET,
        70,
        re.compile(
            r"(?i)(?:bearer\s+|sk_[a-z]+_|pk_[a-z]+_|api[_-]?key\s*[:=]\s*['\"]?)"
            r"[A-Za-z0-9_\-]{16,}"
        ),
        "enable_generic_bearer",
    ),
]


# Luhn-checked credit-card pattern handled separately (not pure regex).
_CC_RE = re.compile(r"\b(?:\d[ \-]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    digits = re.sub(r"\D", "", digits)
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if d < 0 or d > 9:
            return False
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _preview(value: str, max_chars: int) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    keep = max(2, max_chars - 4)
    tail = cleaned[-keep:]
    return f"***{tail}"


# --------------------------------------------------------------------------- #
# Event coercion + text extraction
# --------------------------------------------------------------------------- #


def _coerce_event(ev: Any) -> Mapping[str, Any]:
    if hasattr(ev, "model_dump"):
        try:
            return ev.model_dump()
        except Exception:  # pragma: no cover - defensive
            pass
    if isinstance(ev, Mapping):
        return copy.deepcopy(dict(ev))
    out: dict[str, Any] = {}
    for key in (
        "event_id",
        "session_id",
        "event_type",
        "timestamp",
        "input_data",
        "output_data",
        "model",
        "tokens_in",
        "tokens_out",
    ):
        if hasattr(ev, key):
            out[key] = getattr(ev, key)
    return out


def _walk_strings(node: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """Yield ``(field_path, string_value)`` pairs from a nested payload.

    Keeps lookups bounded: stops descending into structures deeper than
    8 levels to avoid pathological inputs blowing the stack.
    """
    if path.count(".") > 8:
        return
    if isinstance(node, str):
        if node:
            yield path or "$", node
        return
    if isinstance(node, Mapping):
        for k, v in node.items():
            sub = f"{path}.{k}" if path else str(k)
            yield from _walk_strings(v, sub)
        return
    if isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            sub = f"{path}[{i}]" if path else f"[{i}]"
            yield from _walk_strings(v, sub)
        return
    # Other scalars (ints, bools, None, datetimes) carry no leak risk.
    return


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


class DataLeakAdvisor:
    """Scan an AgentLens event stream for PII / secret exposure."""

    def __init__(
        self,
        options: Optional[DataLeakOptions] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.options = options or DataLeakOptions()
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze(
        self,
        events: Iterable[Any],
        *,
        risk_appetite: "str | RiskAppetite" = "balanced",
    ) -> DataLeakReport:
        appetite = RiskAppetite.parse(risk_appetite)
        opts = self.options

        active_patterns = [
            (code, kind, sev, pat)
            for (code, kind, sev, pat, attr) in _PATTERNS
            if getattr(opts, attr, True)
        ]

        # bucket: (model, source) -> list[finding]
        buckets: dict[tuple[str, FindingSource], list[LeakFinding]] = defaultdict(list)
        bucket_events: dict[tuple[str, FindingSource], set[str]] = defaultdict(set)

        total_events = 0
        scanned_events = 0

        for raw in events:
            total_events += 1
            ev = _coerce_event(raw)
            event_id = str(ev.get("event_id") or "")
            session_id = str(ev.get("session_id") or "")
            model = str(ev.get("model") or "unknown")

            if not opts.include_test_models and model.startswith("test-"):
                continue

            scanned_any = False

            for source, payload_key in (
                (FindingSource.INPUT, "input_data"),
                (FindingSource.OUTPUT, "output_data"),
            ):
                if source is FindingSource.INPUT and not opts.scan_inputs:
                    continue
                if source is FindingSource.OUTPUT and not opts.scan_outputs:
                    continue
                payload = ev.get(payload_key)
                if payload is None:
                    continue
                scanned_any = True

                key = (model, source)
                local_findings = self._scan_payload(
                    payload,
                    active_patterns,
                    event_id=event_id,
                    session_id=session_id,
                    source=source,
                    cap=opts.max_findings_per_event,
                    preview_chars=opts.max_preview_chars,
                )
                if local_findings:
                    buckets[key].extend(local_findings)
                    bucket_events[key].add(event_id or f"<idx-{total_events}>")

            if scanned_any:
                scanned_events += 1

        slices = self._build_slices(buckets, bucket_events, opts, appetite)
        portfolio = self._build_portfolio(slices, total_events, scanned_events)
        playbook = self._build_playbook(slices, appetite)
        insights = self._build_insights(slices, portfolio, opts)

        return DataLeakReport(
            generated_at=self._now_fn(),
            options=opts,
            risk_appetite=appetite,
            portfolio=portfolio,
            slices=slices,
            playbook=playbook,
            insights=insights,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _scan_payload(
        self,
        payload: Any,
        patterns: list[tuple[str, FindingKind, int, "re.Pattern[str]"]],
        *,
        event_id: str,
        session_id: str,
        source: FindingSource,
        cap: int,
        preview_chars: int,
    ) -> list[LeakFinding]:
        findings: list[LeakFinding] = []
        opts = self.options
        for path, text in _walk_strings(payload):
            if len(findings) >= cap:
                break
            for code, kind, sev, pat in patterns:
                for m in pat.finditer(text):
                    if len(findings) >= cap:
                        break
                    findings.append(
                        LeakFinding(
                            code=code,
                            kind=kind,
                            source=source,
                            severity=sev,
                            preview=_preview(m.group(0), preview_chars),
                            event_id=event_id,
                            session_id=session_id,
                            field_path=path,
                        )
                    )
            if opts.enable_credit_card and len(findings) < cap:
                for m in _CC_RE.finditer(text):
                    if _luhn_ok(m.group(0)):
                        findings.append(
                            LeakFinding(
                                code="PII_CREDIT_CARD",
                                kind=FindingKind.PII,
                                source=source,
                                severity=90,
                                preview=_preview(
                                    re.sub(r"\D", "", m.group(0)),
                                    preview_chars,
                                ),
                                event_id=event_id,
                                session_id=session_id,
                                field_path=path,
                            )
                        )
                        if len(findings) >= cap:
                            break
        return findings

    def _build_slices(
        self,
        buckets: dict[tuple[str, FindingSource], list[LeakFinding]],
        bucket_events: dict[tuple[str, FindingSource], set[str]],
        opts: DataLeakOptions,
        appetite: RiskAppetite,
    ) -> list[LeakSlice]:
        slices: list[LeakSlice] = []
        for (model, source), findings in buckets.items():
            if not findings:
                continue
            kinds = {f.code for f in findings}
            secret_findings = [f for f in findings if f.kind is FindingKind.SECRET]
            pii_findings = [f for f in findings if f.kind is FindingKind.PII]
            max_sev = max(f.severity for f in findings)
            event_count = len(bucket_events[(model, source)])
            verdict, priority = self._classify(
                secret_findings, pii_findings, max_sev, opts, appetite
            )
            slice_id = self._slice_id(model, source)
            slices.append(
                LeakSlice(
                    slice_id=slice_id,
                    model=model,
                    source=source,
                    event_count=event_count,
                    finding_count=len(findings),
                    distinct_kinds=len(kinds),
                    secret_count=len(secret_findings),
                    pii_count=len(pii_findings),
                    max_severity=max_sev,
                    verdict=verdict,
                    priority=priority,
                    findings=sorted(
                        findings,
                        key=lambda f: (-f.severity, f.code, f.event_id),
                    )[:50],
                )
            )

        # Deterministic ordering: priority asc, severity desc, model asc, source asc.
        slices.sort(
            key=lambda s: (
                _priority_rank(s.priority),
                -s.max_severity,
                s.model,
                s.source.value,
            )
        )

        if len(slices) > opts.top_n:
            slices = slices[: opts.top_n]
        return slices

    def _classify(
        self,
        secret_findings: list[LeakFinding],
        pii_findings: list[LeakFinding],
        max_sev: int,
        opts: DataLeakOptions,
        appetite: RiskAppetite,
    ) -> tuple[LeakVerdict, ActionPriority]:
        secret_floor = opts.secret_severity_floor
        if appetite is RiskAppetite.CAUTIOUS:
            secret_floor = max(50, secret_floor - 10)
        elif appetite is RiskAppetite.AGGRESSIVE:
            secret_floor = min(95, secret_floor + 10)

        if secret_findings and max(f.severity for f in secret_findings) >= secret_floor:
            return LeakVerdict.SECRET_LEAK, ActionPriority.P0

        distinct_pii_kinds = len({f.code for f in pii_findings})
        if (
            len(pii_findings) >= opts.pii_major_threshold
            or distinct_pii_kinds >= opts.distinct_pii_kinds_for_major
            or any(f.severity >= 85 for f in pii_findings)
        ):
            priority = (
                ActionPriority.P0
                if appetite is RiskAppetite.CAUTIOUS
                else ActionPriority.P1
            )
            return LeakVerdict.PII_LEAK, priority

        if pii_findings and len(pii_findings) >= opts.pii_minor_threshold:
            return LeakVerdict.TRACE_PII_MINOR, ActionPriority.P2

        # Secret hit below the severity floor still gets surfaced.
        if secret_findings:
            return LeakVerdict.PII_LEAK, ActionPriority.P1

        return LeakVerdict.CLEAN, ActionPriority.P3

    def _build_portfolio(
        self,
        slices: list[LeakSlice],
        total_events: int,
        scanned_events: int,
    ) -> LeakPortfolio:
        verdict_counter = Counter(s.verdict for s in slices)
        total_findings = sum(s.finding_count for s in slices)
        clean = max(0, scanned_events - sum(s.event_count for s in slices))
        grade, headline = self._grade(slices, scanned_events)
        return LeakPortfolio(
            total_events=total_events,
            scanned_events=scanned_events,
            secret_leak_count=verdict_counter.get(LeakVerdict.SECRET_LEAK, 0),
            pii_leak_count=verdict_counter.get(LeakVerdict.PII_LEAK, 0),
            minor_leak_count=verdict_counter.get(LeakVerdict.TRACE_PII_MINOR, 0),
            clean_count=clean,
            total_findings=total_findings,
            portfolio_grade=grade,
            headline=headline,
        )

    def _grade(
        self,
        slices: list[LeakSlice],
        scanned_events: int,
    ) -> tuple[LeakGrade, str]:
        if scanned_events == 0:
            return LeakGrade.A, "no traces scanned"
        secrets = sum(1 for s in slices if s.verdict is LeakVerdict.SECRET_LEAK)
        pii_major = sum(1 for s in slices if s.verdict is LeakVerdict.PII_LEAK)
        minor = sum(1 for s in slices if s.verdict is LeakVerdict.TRACE_PII_MINOR)

        if secrets:
            return LeakGrade.F, f"{secrets} secret leak slice(s) - block export"
        if pii_major >= 2:
            return LeakGrade.D, f"{pii_major} P1 PII leak slices"
        if pii_major == 1:
            return LeakGrade.C, "1 P1 PII leak slice"
        if minor:
            return LeakGrade.B, f"{minor} minor PII slice(s) - redact on export"
        return LeakGrade.A, "no leaks detected"

    def _build_playbook(
        self,
        slices: list[LeakSlice],
        appetite: RiskAppetite,
    ) -> list[PlaybookAction]:
        actions: list[PlaybookAction] = []
        secret_slice_ids: list[str] = []
        pii_slice_ids: list[str] = []
        minor_slice_ids: list[str] = []
        secret_codes: set[str] = set()

        for s in slices:
            if s.verdict is LeakVerdict.SECRET_LEAK:
                secret_slice_ids.append(s.slice_id)
                secret_codes.update(f.code for f in s.findings if f.kind is FindingKind.SECRET)
            elif s.verdict is LeakVerdict.PII_LEAK:
                pii_slice_ids.append(s.slice_id)
            elif s.verdict is LeakVerdict.TRACE_PII_MINOR:
                minor_slice_ids.append(s.slice_id)

        if secret_slice_ids:
            actions.append(
                PlaybookAction(
                    id="block_trace_export_pending_secret_rotation",
                    priority=ActionPriority.P0,
                    label="Block trace export until secrets are rotated",
                    reason=(
                        "Active secret material is appearing in event payloads "
                        f"({', '.join(sorted(secret_codes)) or 'multiple kinds'})."
                    ),
                    owner="security",
                    blast_radius=len(secret_slice_ids) * 10,
                    reversibility="reversible_after_rotation",
                    related_slice_ids=secret_slice_ids,
                )
            )
            actions.append(
                PlaybookAction(
                    id="rotate_leaked_credentials",
                    priority=ActionPriority.P0,
                    label="Rotate every credential matched by SECRET_* patterns",
                    reason="Assume leaked secrets are compromised the moment they hit a trace.",
                    owner="security",
                    blast_radius=len(secret_codes),
                    reversibility="irreversible",
                    related_slice_ids=secret_slice_ids,
                    suggested_value=",".join(sorted(secret_codes)) or None,
                )
            )

        if pii_slice_ids:
            actions.append(
                PlaybookAction(
                    id="enable_pii_redaction_on_export",
                    priority=ActionPriority.P1,
                    label="Enable PII redaction in the exporter before re-shipping traces",
                    reason="Multiple PII kinds or high-severity hits on these slices.",
                    owner="platform",
                    blast_radius=len(pii_slice_ids) * 2,
                    reversibility="high",
                    related_slice_ids=pii_slice_ids,
                )
            )

        if minor_slice_ids:
            actions.append(
                PlaybookAction(
                    id="add_pii_test_fixture_for_slices",
                    priority=ActionPriority.P2,
                    label="Add a PII fixture covering the affected call-sites",
                    reason="Minor PII hits suggest unsanitised user input reaches the model.",
                    owner="platform",
                    blast_radius=len(minor_slice_ids),
                    reversibility="high",
                    related_slice_ids=minor_slice_ids,
                )
            )

        if not actions:
            actions.append(
                PlaybookAction(
                    id="no_leak_action_needed",
                    priority=ActionPriority.P3,
                    label="No leaks detected; keep redaction guardrails in place",
                    reason="Scan found no PII/secret patterns over the configured threshold.",
                    owner="platform",
                    blast_radius=1,
                    reversibility="high",
                )
            )
        elif appetite is RiskAppetite.AGGRESSIVE and secret_slice_ids:
            # Aggressive teams still want a P3 reminder to audit detectors.
            actions.append(
                PlaybookAction(
                    id="review_detector_thresholds",
                    priority=ActionPriority.P3,
                    label="Review secret_severity_floor; aggressive mode may under-report",
                    reason="Aggressive appetite raises the secret severity floor.",
                    owner="platform",
                    blast_radius=1,
                    reversibility="high",
                )
            )
        return actions

    def _build_insights(
        self,
        slices: list[LeakSlice],
        portfolio: LeakPortfolio,
        opts: DataLeakOptions,
    ) -> list[str]:
        insights: list[str] = []
        if portfolio.scanned_events == 0:
            insights.append("NO_SCANNABLE_PAYLOADS")
            return insights

        if portfolio.total_findings == 0:
            insights.append("ALL_CLEAN")
            return insights

        if portfolio.secret_leak_count:
            insights.append("SECRET_LEAK_DETECTED")
        if portfolio.pii_leak_count:
            insights.append("PII_LEAK_MAJOR")
        if portfolio.minor_leak_count and not portfolio.pii_leak_count:
            insights.append("PII_LEAK_MINOR_ONLY")

        output_only = all(s.source is FindingSource.OUTPUT for s in slices)
        input_only = all(s.source is FindingSource.INPUT for s in slices)
        if output_only:
            insights.append("MODEL_OUTPUT_LEAKS_ONLY")
        elif input_only:
            insights.append("USER_INPUT_LEAKS_ONLY")

        models = {s.model for s in slices}
        if len(models) == 1:
            insights.append(f"SINGLE_MODEL_HOTSPOT:{next(iter(models))}")
        return insights

    @staticmethod
    def _slice_id(model: str, source: FindingSource) -> str:
        raw = f"{model}::{source.value}".encode("utf-8", errors="replace")
        return hashlib.sha1(raw).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_PRIORITY_RANK = {
    ActionPriority.P0: 0,
    ActionPriority.P1: 1,
    ActionPriority.P2: 2,
    ActionPriority.P3: 3,
}


def _priority_rank(p: ActionPriority) -> int:
    return _PRIORITY_RANK.get(p, 99)


__all__ = [
    "ActionPriority",
    "DataLeakAdvisor",
    "DataLeakOptions",
    "DataLeakReport",
    "FindingKind",
    "FindingSource",
    "LeakFinding",
    "LeakGrade",
    "LeakPortfolio",
    "LeakSlice",
    "LeakVerdict",
    "PlaybookAction",
    "RiskAppetite",
]
