"""Autonomous Session Autopsy for AgentLens.

When a session performs poorly or fails, manually investigating what went
wrong across multiple analysis dimensions (anomalies, health, drift,
errors, costs) is tedious and error-prone.  This module orchestrates all
available analysis engines autonomously, correlates their findings into
unified **evidence chains**, assigns root-cause hypotheses with confidence
scores, and produces a prioritized remediation playbook.

Think of it as an AI-powered incident investigator that runs automatically
when things go wrong.

Features:

- **Multi-engine orchestration** — runs anomaly detection, health scoring,
  error fingerprinting, drift detection, and cost analysis in one call
- **Evidence chain construction** — links findings across engines into
  causal chains (e.g., token surge → cost spike → budget exceeded)
- **Root-cause hypotheses** — ranks possible causes by confidence score
  based on evidence weight and corroboration across engines
- **Severity classification** — P0-P4 incident priority based on impact
- **Remediation playbook** — prioritized actions with effort estimates
- **Comparison mode** — autopsy a failing session against a known-good one
- **CLI integration** — ``agentlens-cli autopsy <session_id>``

Example::

    from agentlens.autopsy import SessionAutopsy, AutopsyConfig

    autopsy = SessionAutopsy()

    # Feed baseline sessions for comparison
    for s in historical_sessions:
        autopsy.add_baseline(s)

    # Investigate a problematic session
    report = autopsy.investigate(bad_session)
    print(report.render())
    print(f"Priority: {report.priority.value}")
    print(f"Root causes: {len(report.hypotheses)}")
    for h in report.hypotheses:
        print(f"  [{h.confidence:.0%}] {h.title}: {h.explanation}")
    for action in report.playbook:
        print(f"  [{action.effort}] {action.description}")

Pure Python, stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ───────────────────────────────────────────────────────────


class IncidentPriority(Enum):
    """Incident priority level (P0 = most critical)."""
    P0 = "P0"  # Critical outage / complete failure
    P1 = "P1"  # Major degradation, user-visible
    P2 = "P2"  # Moderate issues, some impact
    P3 = "P3"  # Minor issues, minimal impact
    P4 = "P4"  # Cosmetic / informational

    @property
    def label(self) -> str:
        labels = {
            "P0": "Critical", "P1": "Major", "P2": "Moderate",
            "P3": "Minor", "P4": "Informational",
        }
        return labels.get(self.value, self.value)


class EvidenceSource(Enum):
    """Which analysis engine produced a finding."""
    ANOMALY = "anomaly_detection"
    HEALTH = "health_scoring"
    ERROR = "error_analysis"
    DRIFT = "drift_detection"
    COST = "cost_analysis"
    LATENCY = "latency_analysis"
    TOKEN = "token_analysis"
    TOOL = "tool_analysis"


class EffortLevel(Enum):
    """Estimated effort to implement a remediation action."""
    QUICK_FIX = "quick_fix"      # < 1 hour
    SMALL = "small"              # 1-4 hours
    MEDIUM = "medium"            # 1-2 days
    LARGE = "large"              # 1+ weeks


class CausalRelation(Enum):
    """How two evidence items relate causally."""
    CAUSES = "causes"
    CORRELATES = "correlates"
    EXACERBATES = "exacerbates"
    SYMPTOM_OF = "symptom_of"


# ── Data models ─────────────────────────────────────────────────────


@dataclass
class Evidence:
    """A single finding from an analysis engine."""
    source: EvidenceSource
    title: str
    detail: str
    severity_weight: float   # 0.0 - 1.0, how bad is this finding
    metric_name: str = ""
    observed_value: float = 0.0
    expected_value: float = 0.0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "title": self.title,
            "detail": self.detail,
            "severity_weight": round(self.severity_weight, 3),
            "metric_name": self.metric_name,
            "observed_value": round(self.observed_value, 4),
            "expected_value": round(self.expected_value, 4),
            "tags": self.tags,
        }


@dataclass
class CausalLink:
    """A causal relationship between two evidence items."""
    from_evidence: Evidence
    to_evidence: Evidence
    relation: CausalRelation
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_evidence.title,
            "to": self.to_evidence.title,
            "relation": self.relation.value,
            "explanation": self.explanation,
        }


@dataclass
class Hypothesis:
    """A root-cause hypothesis with supporting evidence."""
    title: str
    explanation: str
    confidence: float          # 0.0 - 1.0
    supporting_evidence: list[Evidence] = field(default_factory=list)
    causal_chain: list[CausalLink] = field(default_factory=list)
    category: str = ""         # e.g., "model_issue", "tool_failure", "overload"

    @property
    def evidence_count(self) -> int:
        return len(self.supporting_evidence)

    @property
    def avg_severity(self) -> float:
        if not self.supporting_evidence:
            return 0.0
        return sum(e.severity_weight for e in self.supporting_evidence) / len(self.supporting_evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "explanation": self.explanation,
            "confidence": round(self.confidence, 3),
            "category": self.category,
            "evidence_count": self.evidence_count,
            "avg_severity": round(self.avg_severity, 3),
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "causal_chain": [c.to_dict() for c in self.causal_chain],
        }


@dataclass
class RemediationAction:
    """A recommended action to address root causes."""
    description: str
    effort: EffortLevel
    priority: int              # 1 = highest priority
    addresses: list[str]       # which hypothesis titles this addresses
    expected_impact: str       # what improvement to expect
    category: str = ""         # "config", "prompt", "infra", "code", "monitoring"

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "effort": self.effort.value,
            "priority": self.priority,
            "addresses": self.addresses,
            "expected_impact": self.expected_impact,
            "category": self.category,
        }


@dataclass
class AutopsyReport:
    """Complete autopsy investigation report."""
    session_id: str
    priority: IncidentPriority
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    causal_links: list[CausalLink] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    playbook: list[RemediationAction] = field(default_factory=list)
    health_score: float = 0.0
    anomaly_count: int = 0
    error_count: int = 0
    engines_run: list[str] = field(default_factory=list)

    @property
    def hypothesis_count(self) -> int:
        return len(self.hypotheses)

    @property
    def top_hypothesis(self) -> Hypothesis | None:
        if not self.hypotheses:
            return None
        return max(self.hypotheses, key=lambda h: h.confidence)

    @property
    def action_count(self) -> int:
        return len(self.playbook)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "priority": self.priority.value,
            "priority_label": self.priority.label,
            "summary": self.summary,
            "health_score": round(self.health_score, 2),
            "anomaly_count": self.anomaly_count,
            "error_count": self.error_count,
            "hypothesis_count": self.hypothesis_count,
            "top_hypothesis": self.top_hypothesis.to_dict() if self.top_hypothesis else None,
            "engines_run": self.engines_run,
            "evidence": [e.to_dict() for e in self.evidence],
            "causal_links": [c.to_dict() for c in self.causal_links],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "playbook": [a.to_dict() for a in self.playbook],
        }

    def render(self) -> str:
        """Human-readable autopsy report."""
        lines: list[str] = []
        lines.append(f"═══ Session Autopsy: {self.session_id} ═══")
        lines.append(f"Priority: {self.priority.value} ({self.priority.label})")
        lines.append(f"Health Score: {self.health_score:.1f}/100")
        lines.append(f"Summary: {self.summary}")
        lines.append("")

        if self.evidence:
            lines.append(f"── Evidence ({len(self.evidence)} findings) ──")
            for e in sorted(self.evidence, key=lambda x: -x.severity_weight):
                sev = "🔴" if e.severity_weight >= 0.7 else "🟡" if e.severity_weight >= 0.4 else "🟢"
                lines.append(f"  {sev} [{e.source.value}] {e.title}")
                lines.append(f"     {e.detail}")
            lines.append("")

        if self.causal_links:
            lines.append(f"── Causal Links ({len(self.causal_links)}) ──")
            for link in self.causal_links:
                arrow = "→" if link.relation == CausalRelation.CAUSES else "↔"
                lines.append(f"  {link.from_evidence.title} {arrow} {link.to_evidence.title}")
                lines.append(f"     ({link.relation.value}) {link.explanation}")
            lines.append("")

        if self.hypotheses:
            lines.append(f"── Root-Cause Hypotheses ({len(self.hypotheses)}) ──")
            for i, h in enumerate(sorted(self.hypotheses, key=lambda x: -x.confidence), 1):
                conf = f"{h.confidence:.0%}"
                lines.append(f"  #{i} [{conf}] {h.title}")
                lines.append(f"     {h.explanation}")
                lines.append(f"     Evidence: {h.evidence_count} items, avg severity {h.avg_severity:.2f}")
            lines.append("")

        if self.playbook:
            lines.append(f"── Remediation Playbook ({len(self.playbook)} actions) ──")
            for a in sorted(self.playbook, key=lambda x: x.priority):
                effort_icon = {"quick_fix": "⚡", "small": "🔧", "medium": "🛠️", "large": "🏗️"}
                icon = effort_icon.get(a.effort.value, "•")
                lines.append(f"  {icon} P{a.priority}: {a.description}")
                lines.append(f"     Effort: {a.effort.value} | Impact: {a.expected_impact}")
            lines.append("")

        lines.append(f"Engines used: {', '.join(self.engines_run)}")
        return "\n".join(lines)


# ── Configuration ───────────────────────────────────────────────────


@dataclass
class AutopsyConfig:
    """Configuration for autopsy investigations.

    Attributes:
        anomaly_warning_threshold: Z-score for anomaly warning.
        anomaly_critical_threshold: Z-score for anomaly critical.
        health_poor_threshold: Health score below this triggers investigation.
        error_rate_concern: Error rate above this is flagged.
        latency_concern_ms: Average latency above this is flagged.
        token_waste_threshold: Tokens-per-event above this is flagged.
        tool_failure_concern: Tool failure rate above this is flagged.
        min_baseline_sessions: Minimum baselines needed for comparison.
    """
    anomaly_warning_threshold: float = 2.0
    anomaly_critical_threshold: float = 3.0
    health_poor_threshold: float = 70.0
    error_rate_concern: float = 0.05
    latency_concern_ms: float = 3000.0
    token_waste_threshold: float = 6000.0
    tool_failure_concern: float = 0.10
    min_baseline_sessions: int = 3


# ── Autopsy Engine ──────────────────────────────────────────────────


class SessionAutopsy:
    """Autonomous session investigation engine.

    Orchestrates multiple analysis engines to investigate why a session
    performed poorly, constructing evidence chains and root-cause
    hypotheses with confidence scores.

    Usage::

        autopsy = SessionAutopsy()

        # Add baseline sessions for comparison
        for s in good_sessions:
            autopsy.add_baseline(s)

        # Investigate a bad session
        report = autopsy.investigate(bad_session)
        print(report.render())
    """

    def __init__(self, config: AutopsyConfig | None = None) -> None:
        self.config = config or AutopsyConfig()
        self._baseline_metrics: list[dict[str, float]] = []

    def add_baseline(self, session: Any) -> None:
        """Add a session to the baseline pool for comparison.

        Args:
            session: Session object with events attribute.
        """
        from agentlens._metrics import extract_session_metrics
        metrics = extract_session_metrics(session)
        self._baseline_metrics.append(metrics)

    def add_baseline_metrics(self, metrics: dict[str, float]) -> None:
        """Add raw metrics dict to the baseline pool."""
        self._baseline_metrics.append(metrics)

    @property
    def baseline_count(self) -> int:
        return len(self._baseline_metrics)

    def clear_baseline(self) -> None:
        """Remove all baseline data."""
        self._baseline_metrics.clear()

    def investigate(self, session: Any) -> AutopsyReport:
        """Run a full autopsy investigation on a session.

        Orchestrates anomaly detection, health scoring, error analysis,
        latency profiling, token analysis, and tool analysis. Correlates
        findings into evidence chains and produces root-cause hypotheses.

        Args:
            session: Session object with events, session_id attributes.

        Returns:
            AutopsyReport with evidence, hypotheses, and remediation playbook.
        """
        from agentlens._metrics import extract_session_metrics

        session_id = getattr(session, "session_id", "unknown")
        metrics = extract_session_metrics(session)
        events = getattr(session, "events", []) or []

        evidence: list[Evidence] = []
        engines_run: list[str] = []

        # ── Engine 1: Health Scoring ──
        health_score = self._run_health_engine(events, evidence, engines_run)

        # ── Engine 2: Anomaly Detection ──
        anomaly_count = self._run_anomaly_engine(metrics, evidence, engines_run)

        # ── Engine 3: Error Analysis ──
        error_count = self._run_error_engine(events, metrics, evidence, engines_run)

        # ── Engine 4: Latency Analysis ──
        self._run_latency_engine(events, metrics, evidence, engines_run)

        # ── Engine 5: Token Analysis ──
        self._run_token_engine(metrics, evidence, engines_run)

        # ── Engine 6: Tool Analysis ──
        self._run_tool_engine(events, metrics, evidence, engines_run)

        # ── Correlate findings ──
        causal_links = self._build_causal_links(evidence)

        # ── Generate hypotheses ──
        hypotheses = self._generate_hypotheses(evidence, causal_links, metrics)

        # ── Determine priority ──
        priority = self._assess_priority(health_score, anomaly_count,
                                          error_count, evidence)

        # ── Build remediation playbook ──
        playbook = self._build_playbook(hypotheses, evidence)

        # ── Generate summary ──
        summary = self._generate_summary(priority, health_score,
                                          anomaly_count, error_count,
                                          hypotheses)

        return AutopsyReport(
            session_id=session_id,
            priority=priority,
            summary=summary,
            evidence=evidence,
            causal_links=causal_links,
            hypotheses=hypotheses,
            playbook=playbook,
            health_score=health_score,
            anomaly_count=anomaly_count,
            error_count=error_count,
            engines_run=engines_run,
        )

    def investigate_metrics(
        self,
        metrics: dict[str, float],
        session_id: str = "unknown",
    ) -> AutopsyReport:
        """Investigate raw metrics without a full session object.

        Useful when you only have aggregated metrics (e.g., from an API).
        Runs anomaly, token, and latency engines (not health or error
        engines which need raw events).

        Args:
            metrics: Dict of metric name → value.
            session_id: Identifier for the report.

        Returns:
            AutopsyReport (subset of engines).
        """
        evidence: list[Evidence] = []
        engines_run: list[str] = []

        anomaly_count = self._run_anomaly_engine(metrics, evidence, engines_run)
        self._run_latency_engine([], metrics, evidence, engines_run)
        self._run_token_engine(metrics, evidence, engines_run)

        causal_links = self._build_causal_links(evidence)
        hypotheses = self._generate_hypotheses(evidence, causal_links, metrics)
        priority = self._assess_priority(50.0, anomaly_count, 0, evidence)
        playbook = self._build_playbook(hypotheses, evidence)
        summary = self._generate_summary(priority, 50.0, anomaly_count, 0, hypotheses)

        return AutopsyReport(
            session_id=session_id,
            priority=priority,
            summary=summary,
            evidence=evidence,
            causal_links=causal_links,
            hypotheses=hypotheses,
            playbook=playbook,
            health_score=50.0,
            anomaly_count=anomaly_count,
            error_count=0,
            engines_run=engines_run,
        )

    # ── Analysis engines ────────────────────────────────────────────

    def _run_health_engine(
        self,
        events: list,
        evidence: list[Evidence],
        engines: list[str],
    ) -> float:
        """Run health scoring and collect evidence."""
        engines.append("health_scoring")

        from agentlens.health import HealthScorer, HealthGrade
        scorer = HealthScorer()
        raw_events = self._events_to_dicts(events)
        report = scorer.score(raw_events)

        health_score = report.overall_score

        if report.grade in (HealthGrade.POOR, HealthGrade.CRITICAL):
            evidence.append(Evidence(
                source=EvidenceSource.HEALTH,
                title=f"Poor health grade: {report.grade.value}",
                detail=f"Overall health score {health_score:.1f}/100. "
                       f"{report.error_count} errors across {report.event_count} events.",
                severity_weight=1.0 - (health_score / 100.0),
                metric_name="health_score",
                observed_value=health_score,
                expected_value=85.0,
                tags=["health", "overall"],
            ))

        # Check individual metric scores
        for m in report.metrics:
            if m.score < 50.0:
                evidence.append(Evidence(
                    source=EvidenceSource.HEALTH,
                    title=f"Low {m.name} score: {m.score:.0f}/100",
                    detail=m.detail,
                    severity_weight=(100.0 - m.score) / 100.0,
                    metric_name=m.name,
                    observed_value=m.value,
                    expected_value=m.threshold,
                    tags=["health", m.name],
                ))

        return health_score

    def _run_anomaly_engine(
        self,
        metrics: dict[str, float],
        evidence: list[Evidence],
        engines: list[str],
    ) -> int:
        """Run anomaly detection against baseline and collect evidence."""
        engines.append("anomaly_detection")

        if len(self._baseline_metrics) < self.config.min_baseline_sessions:
            return 0

        from agentlens.anomaly import (
            AnomalyDetector, AnomalyDetectorConfig, AnomalySeverity,
        )

        config = AnomalyDetectorConfig(
            warning_threshold=self.config.anomaly_warning_threshold,
            critical_threshold=self.config.anomaly_critical_threshold,
            min_samples=self.config.min_baseline_sessions,
        )
        detector = AnomalyDetector(config)
        for bm in self._baseline_metrics:
            detector.add_sample(bm)

        report = detector.analyze_metrics(metrics)

        for anomaly in report.anomalies:
            sev = 0.9 if anomaly.severity == AnomalySeverity.CRITICAL else 0.6
            evidence.append(Evidence(
                source=EvidenceSource.ANOMALY,
                title=f"Anomaly: {anomaly.kind.value}",
                detail=anomaly.description,
                severity_weight=sev,
                metric_name=anomaly.metric_name,
                observed_value=anomaly.observed,
                expected_value=anomaly.expected,
                tags=["anomaly", anomaly.kind.value, anomaly.severity.value],
            ))

        return report.anomaly_count

    def _run_error_engine(
        self,
        events: list,
        metrics: dict[str, float],
        evidence: list[Evidence],
        engines: list[str],
    ) -> int:
        """Analyze errors in the session."""
        engines.append("error_analysis")

        error_rate = metrics.get("error_rate", 0.0)
        event_count = int(metrics.get("event_count", 0))
        error_count = int(error_rate * event_count) if event_count > 0 else 0

        if error_rate > self.config.error_rate_concern:
            evidence.append(Evidence(
                source=EvidenceSource.ERROR,
                title=f"High error rate: {error_rate:.1%}",
                detail=f"{error_count} errors out of {event_count} events "
                       f"(threshold: {self.config.error_rate_concern:.1%}).",
                severity_weight=min(1.0, error_rate / self.config.error_rate_concern * 0.5 + 0.3),
                metric_name="error_rate",
                observed_value=error_rate,
                expected_value=self.config.error_rate_concern,
                tags=["error", "rate"],
            ))

        # Identify error types from events
        error_types: dict[str, int] = {}
        for e in events:
            et = getattr(e, "event_type", "") or ""
            if "error" in et.lower():
                error_name = getattr(e, "error_type", None) or et
                error_types[error_name] = error_types.get(error_name, 0) + 1

        if error_types:
            dominant = max(error_types, key=error_types.get)  # type: ignore[arg-type]
            count = error_types[dominant]
            if count >= 2:
                evidence.append(Evidence(
                    source=EvidenceSource.ERROR,
                    title=f"Recurring error: {dominant}",
                    detail=f"Error '{dominant}' occurred {count} times. "
                           f"May indicate a systematic issue.",
                    severity_weight=min(1.0, 0.3 + count * 0.1),
                    metric_name="recurring_error",
                    observed_value=float(count),
                    expected_value=0.0,
                    tags=["error", "recurring", dominant],
                ))

        return error_count

    def _run_latency_engine(
        self,
        events: list,
        metrics: dict[str, float],
        evidence: list[Evidence],
        engines: list[str],
    ) -> None:
        """Analyze latency patterns."""
        engines.append("latency_analysis")

        avg_latency = metrics.get("avg_latency_ms", 0.0)
        p95_latency = metrics.get("p95_latency_ms", 0.0)

        if avg_latency > self.config.latency_concern_ms:
            evidence.append(Evidence(
                source=EvidenceSource.LATENCY,
                title=f"High average latency: {avg_latency:.0f}ms",
                detail=f"Average latency of {avg_latency:.0f}ms exceeds "
                       f"concern threshold of {self.config.latency_concern_ms:.0f}ms.",
                severity_weight=min(1.0, avg_latency / (self.config.latency_concern_ms * 2)),
                metric_name="avg_latency_ms",
                observed_value=avg_latency,
                expected_value=self.config.latency_concern_ms,
                tags=["latency", "average"],
            ))

        if p95_latency > self.config.latency_concern_ms * 2:
            evidence.append(Evidence(
                source=EvidenceSource.LATENCY,
                title=f"High P95 latency: {p95_latency:.0f}ms",
                detail=f"P95 latency of {p95_latency:.0f}ms indicates severe "
                       f"tail latency issues.",
                severity_weight=min(1.0, p95_latency / (self.config.latency_concern_ms * 4)),
                metric_name="p95_latency_ms",
                observed_value=p95_latency,
                expected_value=self.config.latency_concern_ms * 2,
                tags=["latency", "p95", "tail"],
            ))

        # Detect latency spikes within the session
        if events:
            durations = []
            for e in events:
                dur = getattr(e, "duration_ms", None)
                if dur is not None:
                    durations.append(dur)

            if len(durations) >= 3:
                mean_d = sum(durations) / len(durations)
                if mean_d > 0:
                    variance = sum((d - mean_d) ** 2 for d in durations) / len(durations)
                    cv = math.sqrt(variance) / mean_d if mean_d > 0 else 0
                    if cv > 1.5:
                        evidence.append(Evidence(
                            source=EvidenceSource.LATENCY,
                            title="High latency variance",
                            detail=f"Coefficient of variation {cv:.2f} indicates "
                                   f"highly inconsistent response times "
                                   f"(min {min(durations):.0f}ms, max {max(durations):.0f}ms).",
                            severity_weight=min(0.7, cv * 0.3),
                            metric_name="latency_cv",
                            observed_value=cv,
                            expected_value=0.5,
                            tags=["latency", "variance"],
                        ))

    def _run_token_engine(
        self,
        metrics: dict[str, float],
        evidence: list[Evidence],
        engines: list[str],
    ) -> None:
        """Analyze token usage patterns."""
        engines.append("token_analysis")

        tpe = metrics.get("tokens_per_event", 0.0)
        total = metrics.get("total_tokens", 0.0)

        if tpe > self.config.token_waste_threshold:
            evidence.append(Evidence(
                source=EvidenceSource.TOKEN,
                title=f"Token waste: {tpe:.0f} tokens/event",
                detail=f"Average of {tpe:.0f} tokens per event exceeds "
                       f"threshold of {self.config.token_waste_threshold:.0f}. "
                       f"Total: {total:.0f} tokens.",
                severity_weight=min(1.0, tpe / (self.config.token_waste_threshold * 2)),
                metric_name="tokens_per_event",
                observed_value=tpe,
                expected_value=self.config.token_waste_threshold,
                tags=["tokens", "waste"],
            ))

        # Detect if total tokens is unusually high compared to baseline
        if self._baseline_metrics and len(self._baseline_metrics) >= 3:
            baseline_totals = [bm.get("total_tokens", 0) for bm in self._baseline_metrics]
            if baseline_totals:
                avg_total = sum(baseline_totals) / len(baseline_totals)
                if avg_total > 0 and total > avg_total * 3:
                    evidence.append(Evidence(
                        source=EvidenceSource.TOKEN,
                        title=f"Token usage {total / avg_total:.1f}x baseline",
                        detail=f"Total tokens ({total:.0f}) is "
                               f"{total / avg_total:.1f}x the baseline average "
                               f"({avg_total:.0f}).",
                        severity_weight=min(0.8, (total / avg_total - 1) * 0.2),
                        metric_name="total_tokens",
                        observed_value=total,
                        expected_value=avg_total,
                        tags=["tokens", "surge"],
                    ))

    def _run_tool_engine(
        self,
        events: list,
        metrics: dict[str, float],
        evidence: list[Evidence],
        engines: list[str],
    ) -> None:
        """Analyze tool usage and failures."""
        engines.append("tool_analysis")

        tfr = metrics.get("tool_failure_rate", 0.0)
        tcr = metrics.get("tool_call_rate", 0.0)

        if tfr > self.config.tool_failure_concern:
            evidence.append(Evidence(
                source=EvidenceSource.TOOL,
                title=f"Tool failure rate: {tfr:.1%}",
                detail=f"Tool failure rate of {tfr:.1%} exceeds "
                       f"concern threshold of {self.config.tool_failure_concern:.1%}.",
                severity_weight=min(1.0, tfr / self.config.tool_failure_concern * 0.5 + 0.3),
                metric_name="tool_failure_rate",
                observed_value=tfr,
                expected_value=self.config.tool_failure_concern,
                tags=["tool", "failure"],
            ))

        # Identify failing tools
        tool_failures: dict[str, int] = {}
        tool_calls: dict[str, int] = {}
        for e in events:
            tc = getattr(e, "tool_call", None)
            if tc is not None:
                tool_name = getattr(tc, "tool_name", None) or ""
                if not tool_name and isinstance(tc, dict):
                    tool_name = tc.get("tool_name", "")
                if tool_name:
                    tool_calls[tool_name] = tool_calls.get(tool_name, 0) + 1

                    # Check if tool had error output
                    tool_output = getattr(tc, "tool_output", None)
                    if tool_output is None and isinstance(tc, dict):
                        tool_output = tc.get("tool_output")
                    if isinstance(tool_output, dict) and tool_output.get("error"):
                        tool_failures[tool_name] = tool_failures.get(tool_name, 0) + 1

                    et = getattr(e, "event_type", "") or ""
                    if "error" in et.lower():
                        tool_failures[tool_name] = tool_failures.get(tool_name, 0) + 1

        for tool_name, failures in tool_failures.items():
            total_calls = tool_calls.get(tool_name, failures)
            rate = failures / total_calls if total_calls > 0 else 1.0
            if rate > 0.3:
                evidence.append(Evidence(
                    source=EvidenceSource.TOOL,
                    title=f"Failing tool: {tool_name} ({rate:.0%} failure)",
                    detail=f"Tool '{tool_name}' failed {failures}/{total_calls} "
                           f"calls ({rate:.0%}). May be misconfigured or "
                           f"target service is unavailable.",
                    severity_weight=min(0.9, rate * 0.8),
                    metric_name=f"tool_failure:{tool_name}",
                    observed_value=rate,
                    expected_value=0.0,
                    tags=["tool", "failure", tool_name],
                ))

    # ── Correlation & Hypothesis Generation ─────────────────────────

    def _build_causal_links(self, evidence: list[Evidence]) -> list[CausalLink]:
        """Identify causal relationships between evidence items."""
        links: list[CausalLink] = []
        evidence_by_tag: dict[str, list[Evidence]] = {}
        for e in evidence:
            for t in e.tags:
                evidence_by_tag.setdefault(t, []).append(e)

        # Rule: Token surge → cost impact
        token_evidence = [e for e in evidence if "tokens" in e.tags and "surge" in e.tags]
        cost_evidence = [e for e in evidence if "waste" in e.tags]
        for te in token_evidence:
            for ce in cost_evidence:
                links.append(CausalLink(
                    from_evidence=te,
                    to_evidence=ce,
                    relation=CausalRelation.EXACERBATES,
                    explanation="Token surge increases per-event token waste.",
                ))

        # Rule: Tool failure → error rate
        tool_ev = [e for e in evidence if "tool" in e.tags and "failure" in e.tags]
        error_ev = [e for e in evidence if "error" in e.tags and "rate" in e.tags]
        for te in tool_ev:
            for ee in error_ev:
                links.append(CausalLink(
                    from_evidence=te,
                    to_evidence=ee,
                    relation=CausalRelation.CAUSES,
                    explanation="Tool failures directly contribute to the error rate.",
                ))

        # Rule: High latency → health degradation
        lat_ev = [e for e in evidence if "latency" in e.tags and "average" in e.tags]
        health_ev = [e for e in evidence if "health" in e.tags and "overall" in e.tags]
        for le in lat_ev:
            for he in health_ev:
                links.append(CausalLink(
                    from_evidence=le,
                    to_evidence=he,
                    relation=CausalRelation.CAUSES,
                    explanation="High latency degrades overall health score.",
                ))

        # Rule: Latency variance → P95 spikes
        var_ev = [e for e in evidence if "variance" in e.tags]
        p95_ev = [e for e in evidence if "p95" in e.tags]
        for ve in var_ev:
            for pe in p95_ev:
                links.append(CausalLink(
                    from_evidence=ve,
                    to_evidence=pe,
                    relation=CausalRelation.CAUSES,
                    explanation="High latency variance causes P95 spikes.",
                ))

        # Rule: Error rate → health
        for ee in error_ev:
            for he in health_ev:
                links.append(CausalLink(
                    from_evidence=ee,
                    to_evidence=he,
                    relation=CausalRelation.CAUSES,
                    explanation="High error rate directly degrades health score.",
                ))

        return links

    def _generate_hypotheses(
        self,
        evidence: list[Evidence],
        causal_links: list[CausalLink],
        metrics: dict[str, float],
    ) -> list[Hypothesis]:
        """Generate root-cause hypotheses from evidence and causal links."""
        hypotheses: list[Hypothesis] = []

        # Group evidence by theme
        tool_evidence = [e for e in evidence if e.source == EvidenceSource.TOOL]
        error_evidence = [e for e in evidence if e.source == EvidenceSource.ERROR]
        latency_evidence = [e for e in evidence if e.source == EvidenceSource.LATENCY]
        token_evidence = [e for e in evidence if e.source == EvidenceSource.TOKEN]
        anomaly_evidence = [e for e in evidence if e.source == EvidenceSource.ANOMALY]

        # Hypothesis 1: Tool infrastructure failure
        if tool_evidence:
            failing_tools = [e.title for e in tool_evidence if "Failing tool" in e.title]
            conf = min(0.95, 0.4 + len(tool_evidence) * 0.15)
            if error_evidence:
                conf = min(0.95, conf + 0.1)  # corroboration bonus

            relevant_links = [l for l in causal_links
                              if l.from_evidence in tool_evidence]

            hypotheses.append(Hypothesis(
                title="Tool Infrastructure Failure",
                explanation="One or more tools are failing at high rates, "
                            "causing cascading errors and degraded session quality. "
                            + (f"Affected tools: {', '.join(failing_tools)}."
                               if failing_tools else ""),
                confidence=conf,
                supporting_evidence=tool_evidence + error_evidence[:2],
                causal_chain=relevant_links,
                category="tool_failure",
            ))

        # Hypothesis 2: Model performance degradation
        if latency_evidence and (token_evidence or anomaly_evidence):
            all_ev = latency_evidence + token_evidence + anomaly_evidence[:2]
            conf = min(0.90, 0.3 + len(all_ev) * 0.12)

            hypotheses.append(Hypothesis(
                title="Model Performance Degradation",
                explanation="Latency increases combined with token usage changes "
                            "suggest the underlying model is performing poorly — "
                            "possible model update, capacity issues, or prompt "
                            "complexity increase.",
                confidence=conf,
                supporting_evidence=all_ev,
                causal_chain=[l for l in causal_links
                              if l.from_evidence in latency_evidence],
                category="model_issue",
            ))

        # Hypothesis 3: Prompt or input quality issue
        if token_evidence and error_evidence:
            all_ev = token_evidence + error_evidence
            conf = min(0.85, 0.35 + len(all_ev) * 0.1)

            hypotheses.append(Hypothesis(
                title="Prompt or Input Quality Issue",
                explanation="High token usage combined with errors suggests "
                            "prompts may be too complex, malformed, or triggering "
                            "unexpected model behavior.",
                confidence=conf,
                supporting_evidence=all_ev,
                causal_chain=[],
                category="prompt_issue",
            ))

        # Hypothesis 4: Overload / capacity issue
        if latency_evidence and not tool_evidence:
            conf = min(0.80, 0.25 + len(latency_evidence) * 0.15)
            var_ev = [e for e in latency_evidence if "variance" in e.tags]
            if var_ev:
                conf = min(0.85, conf + 0.1)

            hypotheses.append(Hypothesis(
                title="System Overload or Capacity Issue",
                explanation="Latency issues without tool failures suggest "
                            "the system or model provider is under heavy load. "
                            + ("High variance indicates intermittent congestion."
                               if var_ev else ""),
                confidence=conf,
                supporting_evidence=latency_evidence,
                causal_chain=[],
                category="capacity",
            ))

        # Hypothesis 5: Systematic error pattern
        recurring = [e for e in error_evidence if "recurring" in e.tags]
        if recurring:
            conf = min(0.90, 0.5 + len(recurring) * 0.15)
            hypotheses.append(Hypothesis(
                title="Systematic Error Pattern",
                explanation="The same error is recurring multiple times, "
                            "indicating a systematic issue rather than "
                            "random failures.",
                confidence=conf,
                supporting_evidence=recurring + error_evidence[:2],
                causal_chain=[],
                category="systematic_error",
            ))

        # Hypothesis 6: Anomalous behavior (drift from baseline)
        if anomaly_evidence and not tool_evidence and not error_evidence:
            conf = min(0.75, 0.3 + len(anomaly_evidence) * 0.12)
            hypotheses.append(Hypothesis(
                title="Behavioral Drift from Baseline",
                explanation="Session metrics deviate significantly from "
                            "historical baselines without clear tool or error "
                            "issues. May indicate changed input distribution, "
                            "model updates, or environmental changes.",
                confidence=conf,
                supporting_evidence=anomaly_evidence,
                causal_chain=[],
                category="drift",
            ))

        # Sort by confidence descending
        hypotheses.sort(key=lambda h: -h.confidence)
        return hypotheses

    def _assess_priority(
        self,
        health_score: float,
        anomaly_count: int,
        error_count: int,
        evidence: list[Evidence],
    ) -> IncidentPriority:
        """Determine incident priority from investigation findings."""
        max_severity = max((e.severity_weight for e in evidence), default=0.0)
        total_evidence = len(evidence)

        # P0: Critical — multiple severe findings
        if health_score < 40 or (max_severity >= 0.9 and total_evidence >= 4):
            return IncidentPriority.P0
        # P1: Major — poor health or many anomalies
        if health_score < 60 or (anomaly_count >= 3 and max_severity >= 0.6):
            return IncidentPriority.P1
        # P2: Moderate — some issues
        if health_score < 75 or total_evidence >= 3:
            return IncidentPriority.P2
        # P3: Minor — few issues
        if total_evidence >= 1:
            return IncidentPriority.P3
        # P4: Informational
        return IncidentPriority.P4

    def _build_playbook(
        self,
        hypotheses: list[Hypothesis],
        evidence: list[Evidence],
    ) -> list[RemediationAction]:
        """Generate prioritized remediation actions."""
        actions: list[RemediationAction] = []
        seen: set[str] = set()

        for hyp in hypotheses:
            if hyp.category == "tool_failure" and "tool_check" not in seen:
                seen.add("tool_check")
                failing = [e.metric_name.replace("tool_failure:", "")
                           for e in hyp.supporting_evidence
                           if e.metric_name.startswith("tool_failure:")]
                actions.append(RemediationAction(
                    description=f"Check tool availability and configuration"
                                + (f" ({', '.join(failing)})" if failing else ""),
                    effort=EffortLevel.QUICK_FIX,
                    priority=1,
                    addresses=[hyp.title],
                    expected_impact="Restore tool functionality, reduce error rate",
                    category="infra",
                ))

            if hyp.category == "model_issue" and "model_check" not in seen:
                seen.add("model_check")
                actions.append(RemediationAction(
                    description="Review model provider status and consider "
                                "fallback to alternate model",
                    effort=EffortLevel.QUICK_FIX,
                    priority=2,
                    addresses=[hyp.title],
                    expected_impact="Reduce latency and improve response quality",
                    category="infra",
                ))

            if hyp.category == "prompt_issue" and "prompt_opt" not in seen:
                seen.add("prompt_opt")
                actions.append(RemediationAction(
                    description="Audit and optimize prompts — reduce complexity, "
                                "add input validation, test with recent failures",
                    effort=EffortLevel.SMALL,
                    priority=3,
                    addresses=[hyp.title],
                    expected_impact="Reduce token usage and error rate",
                    category="prompt",
                ))

            if hyp.category == "capacity" and "scale" not in seen:
                seen.add("scale")
                actions.append(RemediationAction(
                    description="Review rate limits and consider scaling "
                                "or adding request queuing",
                    effort=EffortLevel.MEDIUM,
                    priority=4,
                    addresses=[hyp.title],
                    expected_impact="Reduce latency variability and timeouts",
                    category="infra",
                ))

            if hyp.category == "systematic_error" and "error_fix" not in seen:
                seen.add("error_fix")
                actions.append(RemediationAction(
                    description="Investigate and fix the recurring error pattern — "
                                "add retry logic or input sanitization",
                    effort=EffortLevel.SMALL,
                    priority=2,
                    addresses=[hyp.title],
                    expected_impact="Eliminate recurring failures",
                    category="code",
                ))

            if hyp.category == "drift" and "monitor" not in seen:
                seen.add("monitor")
                actions.append(RemediationAction(
                    description="Set up continuous drift monitoring with alerts "
                                "for significant behavioral changes",
                    effort=EffortLevel.MEDIUM,
                    priority=5,
                    addresses=[hyp.title],
                    expected_impact="Early detection of future regressions",
                    category="monitoring",
                ))

        # Always add a monitoring action if there are findings
        if evidence and "monitoring_general" not in seen:
            seen.add("monitoring_general")
            actions.append(RemediationAction(
                description="Add health check alerts to catch similar issues "
                            "proactively",
                effort=EffortLevel.SMALL,
                priority=len(actions) + 1,
                addresses=["general"],
                expected_impact="Faster incident detection in future",
                category="monitoring",
            ))

        return actions

    def _generate_summary(
        self,
        priority: IncidentPriority,
        health_score: float,
        anomaly_count: int,
        error_count: int,
        hypotheses: list[Hypothesis],
    ) -> str:
        """Generate a one-line summary of the investigation."""
        parts = [f"{priority.value} ({priority.label})"]
        parts.append(f"health {health_score:.0f}/100")
        if anomaly_count:
            parts.append(f"{anomaly_count} anomalies")
        if error_count:
            parts.append(f"{error_count} errors")
        if hypotheses:
            top = hypotheses[0]
            parts.append(f"likely cause: {top.title} ({top.confidence:.0%})")
        return " | ".join(parts)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _events_to_dicts(events: list) -> list[dict]:
        """Convert event objects to dicts for HealthScorer."""
        result: list[dict] = []
        for e in events:
            d: dict[str, Any] = {}
            d["event_type"] = getattr(e, "event_type", "generic")
            d["duration_ms"] = getattr(e, "duration_ms", None)
            d["tokens_in"] = getattr(e, "tokens_in", 0)
            d["tokens_out"] = getattr(e, "tokens_out", 0)

            tc = getattr(e, "tool_call", None)
            if tc is not None:
                if hasattr(tc, "model_dump"):
                    d["tool_call"] = tc.model_dump()
                elif isinstance(tc, dict):
                    d["tool_call"] = tc
                else:
                    d["tool_call"] = {
                        "tool_name": getattr(tc, "tool_name", ""),
                        "tool_output": getattr(tc, "tool_output", None),
                    }
            else:
                d["tool_call"] = None
            result.append(d)
        return result
