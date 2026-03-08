"""Cost Optimizer — intelligent model selection recommendations.

Analyzes session event patterns and recommends cheaper model alternatives
where task complexity doesn't require expensive models. Helps reduce costs
by 30-70% without meaningful quality degradation.

Features:
  - Task complexity scoring based on token patterns and tool usage
  - Model tier classification with capability mapping
  - Per-event and per-session optimization recommendations
  - Savings estimation with confidence levels
  - Migration plan generation for gradual rollout

Example::

    from agentlens.cost_optimizer import CostOptimizer

    optimizer = CostOptimizer()

    # Analyze a session's events
    events = [
        AgentEvent(model="gpt-4o", tokens_in=500, tokens_out=100,
                   event_type="llm_call"),
        AgentEvent(model="gpt-4o", tokens_in=200, tokens_out=50,
                   event_type="llm_call"),
    ]
    report = optimizer.analyze(events)
    print(report.total_savings_pct)   # e.g. 45.2
    print(report.recommendations)     # per-event suggestions
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agentlens.models import AgentEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ModelTier(str, Enum):
    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"
    FLAGSHIP = "flagship"


@dataclass
class ModelInfo:
    name: str
    tier: ModelTier
    input_cost_per_1m: float
    output_cost_per_1m: float
    max_context: int = 128_000
    strengths: list[str] = field(default_factory=list)

    @property
    def avg_cost_per_1m(self) -> float:
        return (self.input_cost_per_1m + self.output_cost_per_1m) / 2


MODEL_REGISTRY: dict[str, ModelInfo] = {
    "gpt-4o-mini": ModelInfo("gpt-4o-mini", ModelTier.ECONOMY, 0.15, 0.60, 128_000,
                             ["classification", "extraction", "simple_qa", "formatting"]),
    "gpt-3.5-turbo": ModelInfo("gpt-3.5-turbo", ModelTier.ECONOMY, 0.50, 1.50, 16_385,
                               ["classification", "extraction", "simple_qa", "formatting"]),
    "claude-3-haiku": ModelInfo("claude-3-haiku", ModelTier.ECONOMY, 0.25, 1.25, 200_000,
                                ["classification", "extraction", "simple_qa", "formatting", "summarization"]),
    "gpt-4o": ModelInfo("gpt-4o", ModelTier.STANDARD, 2.50, 10.00, 128_000,
                        ["reasoning", "code", "analysis", "creative", "summarization"]),
    "claude-3-sonnet": ModelInfo("claude-3-sonnet", ModelTier.STANDARD, 3.00, 15.00, 200_000,
                                 ["reasoning", "code", "analysis", "creative", "summarization"]),
    "claude-3.5-sonnet": ModelInfo("claude-3.5-sonnet", ModelTier.STANDARD, 3.00, 15.00, 200_000,
                                   ["reasoning", "code", "analysis", "creative", "summarization"]),
    "gpt-4-turbo": ModelInfo("gpt-4-turbo", ModelTier.PREMIUM, 10.00, 30.00, 128_000,
                             ["reasoning", "code", "analysis", "creative", "complex_qa"]),
    "gpt-4": ModelInfo("gpt-4", ModelTier.PREMIUM, 30.00, 60.00, 8_192,
                       ["reasoning", "code", "analysis", "creative", "complex_qa"]),
    "claude-3-opus": ModelInfo("claude-3-opus", ModelTier.FLAGSHIP, 15.00, 75.00, 200_000,
                               ["deep_reasoning", "research", "complex_code", "nuanced_analysis"]),
}


class ComplexityLevel(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ComplexityAssessment:
    level: ComplexityLevel
    score: float
    factors: dict[str, float] = field(default_factory=dict)
    recommended_tier: ModelTier = ModelTier.ECONOMY
    reasoning: str = ""


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Recommendation:
    rec_id: str = field(default_factory=_new_id)
    event_id: str = ""
    current_model: str = ""
    recommended_model: str = ""
    current_tier: ModelTier = ModelTier.STANDARD
    recommended_tier: ModelTier = ModelTier.ECONOMY
    complexity: ComplexityAssessment | None = None
    estimated_savings_usd: float = 0.0
    savings_pct: float = 0.0
    confidence: Confidence = Confidence.MEDIUM
    reason: str = ""
    risk: str = ""

    @property
    def is_downgrade(self) -> bool:
        tiers = list(ModelTier)
        return tiers.index(self.recommended_tier) < tiers.index(self.current_tier)


@dataclass
class MigrationStep:
    phase: int
    description: str
    models_to_change: list[str] = field(default_factory=list)
    target_model: str = ""
    estimated_savings_pct: float = 0.0
    risk_level: str = "low"


@dataclass
class OptimizationReport:
    report_id: str = field(default_factory=_new_id)
    timestamp: datetime = field(default_factory=_utcnow)
    total_events: int = 0
    optimizable_events: int = 0
    recommendations: list[Recommendation] = field(default_factory=list)
    current_cost_usd: float = 0.0
    optimized_cost_usd: float = 0.0
    total_savings_usd: float = 0.0
    total_savings_pct: float = 0.0
    model_usage: dict[str, int] = field(default_factory=dict)
    tier_distribution: dict[str, int] = field(default_factory=dict)
    migration_plan: list[MigrationStep] = field(default_factory=list)
    summary: str = ""

    @property
    def has_savings(self) -> bool:
        return self.total_savings_usd > 0.001


class ComplexityAnalyzer:
    FACTOR_WEIGHTS: dict[str, float] = {
        "output_ratio": 0.25, "token_volume": 0.20, "has_tool_call": 0.15,
        "has_decision": 0.20, "event_type": 0.20,
    }
    EVENT_COMPLEXITY: dict[str, float] = {
        "llm_call": 0.3, "tool_call": 0.2, "decision": 0.7, "error": 0.1,
        "generic": 0.2, "planning": 0.8, "code_generation": 0.7,
        "summarization": 0.4, "classification": 0.15, "extraction": 0.2,
        "formatting": 0.1, "translation": 0.3,
    }

    def assess(self, event: AgentEvent) -> ComplexityAssessment:
        factors: dict[str, float] = {}
        total = event.tokens_in + event.tokens_out
        factors["output_ratio"] = (min(event.tokens_out / max(event.tokens_in, 1), 2.0) / 2.0) if total > 0 else 0.0
        factors["token_volume"] = min(total / 10_000, 1.0)
        factors["has_tool_call"] = 1.0 if event.tool_call is not None else 0.0
        factors["has_decision"] = 1.0 if event.decision_trace is not None else 0.0
        factors["event_type"] = self.EVENT_COMPLEXITY.get(event.event_type, 0.3)

        score = max(0.0, min(1.0, sum(factors[k] * self.FACTOR_WEIGHTS[k] for k in self.FACTOR_WEIGHTS)))

        if score < 0.15:
            level, tier = ComplexityLevel.TRIVIAL, ModelTier.ECONOMY
        elif score < 0.30:
            level, tier = ComplexityLevel.LOW, ModelTier.ECONOMY
        elif score < 0.50:
            level, tier = ComplexityLevel.MEDIUM, ModelTier.STANDARD
        elif score < 0.75:
            level, tier = ComplexityLevel.HIGH, ModelTier.PREMIUM
        else:
            level, tier = ComplexityLevel.CRITICAL, ModelTier.FLAGSHIP

        return ComplexityAssessment(level=level, score=round(score, 4),
                                    factors={k: round(v, 4) for k, v in factors.items()},
                                    recommended_tier=tier, reasoning=self._explain(level, factors))

    def _explain(self, level: ComplexityLevel, factors: dict[str, float]) -> str:
        drivers = [k for k, v in sorted(factors.items(), key=lambda x: x[1], reverse=True)[:2] if v > 0.2]
        if not drivers:
            return f"{level.value} complexity — minimal resource needs"
        return f"{level.value} complexity driven by {' and '.join(d.replace('_', ' ') for d in drivers)}"


def _event_cost(event: AgentEvent, model_info: ModelInfo | None = None) -> float:
    if model_info is None and event.model:
        model_info = MODEL_REGISTRY.get(event.model)
    if model_info is None:
        return 0.0
    return (event.tokens_in / 1_000_000) * model_info.input_cost_per_1m + \
           (event.tokens_out / 1_000_000) * model_info.output_cost_per_1m


def _hypothetical_cost(event: AgentEvent, model_info: ModelInfo) -> float:
    return (event.tokens_in / 1_000_000) * model_info.input_cost_per_1m + \
           (event.tokens_out / 1_000_000) * model_info.output_cost_per_1m


class CostOptimizer:
    """Analyzes events and recommends cheaper model alternatives."""

    def __init__(self, custom_models: dict[str, ModelInfo] | None = None,
                 aggressive: bool = False, min_savings_pct: float = 10.0):
        self.models = dict(MODEL_REGISTRY)
        if custom_models:
            self.models.update(custom_models)
        self.aggressive = aggressive
        self.min_savings_pct = min_savings_pct
        self._analyzer = ComplexityAnalyzer()

    def register_model(self, name: str, info: ModelInfo) -> None:
        self.models[name] = info

    def analyze(self, events: list[AgentEvent]) -> OptimizationReport:
        report = OptimizationReport(total_events=len(events))
        recs: list[Recommendation] = []
        current_total = optimized_total = 0.0
        model_usage: dict[str, int] = {}
        tier_dist: dict[str, int] = {}
        analyzable_types = {"llm_call", "generic", "decision", "planning", "code_generation",
                            "summarization", "classification", "extraction", "formatting", "translation"}

        for event in events:
            if not event.model:
                continue
            mi = self.models.get(event.model)
            if mi is None:
                continue
            model_usage[event.model] = model_usage.get(event.model, 0) + 1
            tier_dist[mi.tier.value] = tier_dist.get(mi.tier.value, 0) + 1
            cost = _event_cost(event, mi)
            current_total += cost

            if event.event_type not in analyzable_types:
                optimized_total += cost
                continue

            assessment = self._analyzer.assess(event)
            tiers = list(ModelTier)
            if tiers.index(mi.tier) <= tiers.index(assessment.recommended_tier):
                optimized_total += cost
                continue

            candidate = self._find_best_candidate(event, mi, assessment.recommended_tier)
            if candidate is None:
                optimized_total += cost
                continue

            new_cost = _hypothetical_cost(event, candidate)
            savings = cost - new_cost
            savings_pct = (savings / cost * 100) if cost > 0 else 0
            if savings_pct < self.min_savings_pct:
                optimized_total += cost
                continue

            confidence = self._assess_confidence(assessment, mi, candidate)
            if confidence == Confidence.LOW and not self.aggressive:
                optimized_total += cost
                continue

            optimized_total += new_cost
            report.optimizable_events += 1
            recs.append(Recommendation(
                event_id=event.event_id, current_model=event.model,
                recommended_model=candidate.name, current_tier=mi.tier,
                recommended_tier=candidate.tier, complexity=assessment,
                estimated_savings_usd=round(savings, 6), savings_pct=round(savings_pct, 1),
                confidence=confidence,
                reason=self._reason(assessment, mi, candidate),
                risk=self._risk(assessment, mi, candidate)))

        report.recommendations = recs
        report.current_cost_usd = round(current_total, 6)
        report.optimized_cost_usd = round(optimized_total, 6)
        report.total_savings_usd = round(current_total - optimized_total, 6)
        report.total_savings_pct = round((report.total_savings_usd / current_total * 100) if current_total > 0 else 0, 1)
        report.model_usage = model_usage
        report.tier_distribution = tier_dist
        report.migration_plan = self._migration_plan(recs)
        report.summary = self._summary(report)
        return report

    def analyze_session_events(self, events: list[AgentEvent], session_id: str = "") -> OptimizationReport:
        if session_id:
            events = [e for e in events if e.session_id == session_id]
        return self.analyze(events)

    def quick_estimate(self, events: list[AgentEvent]) -> dict[str, Any]:
        current_cost = potential_savings = 0.0
        overprovisioned = 0
        for event in events:
            if not event.model:
                continue
            mi = self.models.get(event.model)
            if mi is None:
                continue
            cost = _event_cost(event, mi)
            current_cost += cost
            assessment = self._analyzer.assess(event)
            tiers = list(ModelTier)
            if tiers.index(mi.tier) > tiers.index(assessment.recommended_tier):
                c = self._find_best_candidate(event, mi, assessment.recommended_tier)
                if c:
                    s = cost - _hypothetical_cost(event, c)
                    if s > 0:
                        potential_savings += s
                        overprovisioned += 1
        return {"current_cost": round(current_cost, 6), "potential_savings": round(potential_savings, 6),
                "savings_pct": round((potential_savings / current_cost * 100) if current_cost > 0 else 0, 1),
                "overprovisioned_count": overprovisioned, "total_events": len(events)}

    def suggest_model(self, event: AgentEvent) -> str | None:
        if not event.model:
            return None
        mi = self.models.get(event.model)
        if mi is None:
            return None
        assessment = self._analyzer.assess(event)
        tiers = list(ModelTier)
        if tiers.index(mi.tier) <= tiers.index(assessment.recommended_tier):
            return None
        c = self._find_best_candidate(event, mi, assessment.recommended_tier)
        return c.name if c else None

    def _find_best_candidate(self, event: AgentEvent, current: ModelInfo, target_tier: ModelTier) -> ModelInfo | None:
        tiers = list(ModelTier)
        idx = tiers.index(target_tier)
        candidates = [m for m in self.models.values()
                      if tiers.index(m.tier) <= idx and m.name != current.name
                      and m.max_context >= (event.tokens_in + event.tokens_out)]
        if not candidates:
            return None
        prov = current.name.split("-")[0]
        same = [c for c in candidates if c.name.split("-")[0] == prov]
        pool = same if same else candidates
        pool.sort(key=lambda m: m.avg_cost_per_1m)
        return pool[0]

    def _assess_confidence(self, a: ComplexityAssessment, cur: ModelInfo, cand: ModelInfo) -> Confidence:
        gap = list(ModelTier).index(cur.tier) - list(ModelTier).index(cand.tier)
        if a.score < 0.30 and gap <= 1:
            return Confidence.HIGH
        if a.score < 0.30 and gap == 2:
            return Confidence.MEDIUM
        if a.score < 0.50 and gap <= 1:
            return Confidence.MEDIUM
        return Confidence.LOW

    def _reason(self, a: ComplexityAssessment, cur: ModelInfo, cand: ModelInfo) -> str:
        return (f"Task complexity is {a.level.value} ({a.reasoning}). "
                f"{cur.name} ({cur.tier.value} tier) is overprovisioned; "
                f"{cand.name} ({cand.tier.value} tier) can handle this workload.")

    def _risk(self, a: ComplexityAssessment, cur: ModelInfo, cand: ModelInfo) -> str:
        gap = list(ModelTier).index(cur.tier) - list(ModelTier).index(cand.tier)
        if gap <= 1 and a.score < 0.25:
            return "Very low risk — task well within cheaper model's capabilities"
        if gap <= 1:
            return "Low risk — minor capability reduction unlikely to affect output"
        if gap == 2 and a.score < 0.35:
            return "Moderate risk — two-tier downgrade, monitor output quality"
        return "Higher risk — significant capability reduction, A/B test recommended"

    def _migration_plan(self, recs: list[Recommendation]) -> list[MigrationStep]:
        if not recs:
            return []
        plan: list[MigrationStep] = []
        for label, conf, risk in [
            ("Quick wins — high-confidence downgrades with minimal risk", Confidence.HIGH, "low"),
            ("Validated switches — A/B test before full rollout", Confidence.MEDIUM, "medium"),
            ("Experimental — requires quality monitoring and rollback plan", Confidence.LOW, "high"),
        ]:
            group = [r for r in recs if r.confidence == conf]
            if group:
                models = list({r.current_model for r in group})
                targets = list({r.recommended_model for r in group})
                plan.append(MigrationStep(
                    phase=len(plan) + 1, description=label, models_to_change=models,
                    target_model=targets[0] if len(targets) == 1 else ", ".join(targets),
                    estimated_savings_pct=round(sum(r.savings_pct for r in group) / len(group), 1),
                    risk_level=risk))
        return plan

    def _summary(self, report: OptimizationReport) -> str:
        if not report.has_savings:
            return (f"Analyzed {report.total_events} events — "
                    f"model selection is already well-optimized. "
                    f"Current cost: ${report.current_cost_usd:.4f}.")
        hc = sum(1 for r in report.recommendations if r.confidence == Confidence.HIGH)
        return (f"Analyzed {report.total_events} events across {len(report.model_usage)} models. "
                f"Found {report.optimizable_events} optimization opportunities ({hc} high-confidence). "
                f"Potential savings: ${report.total_savings_usd:.4f} ({report.total_savings_pct}% reduction). "
                f"Current: ${report.current_cost_usd:.4f} → Optimized: ${report.optimized_cost_usd:.4f}.")
