"""AgentLens — Observability and Explainability for AI Agents."""

from agentlens.models import AgentEvent, ToolCall, DecisionTrace, Session
from agentlens.tracker import AgentTracker
from agentlens.decorators import track_agent, track_tool_call
from agentlens.transport import Transport
from agentlens.alerts import AlertRule, AlertManager, MetricAggregator, Alert, Severity, Condition
from agentlens.health import HealthScorer, HealthReport, HealthGrade, HealthThresholds, MetricScore
from agentlens.anomaly import AnomalyDetector, AnomalyDetectorConfig, Anomaly, AnomalyKind, AnomalySeverity, AnomalyReport, MetricBaseline
from agentlens.timeline import TimelineRenderer
from agentlens.span import Span
from agentlens.budget import TokenBudget, BudgetTracker, BudgetReport, BudgetStatus, BudgetExceededError, BudgetEntry, estimate_cost, set_custom_pricing, get_pricing
from agentlens.forecast import CostForecaster, UsageRecord, ForecastResult, SpendingSummary, BudgetAlert, DailyPrediction
from agentlens.compliance import ComplianceChecker, CompliancePolicy, ComplianceReport, ComplianceRule, RuleKind, RuleVerdict, RuleResult, strict_policy, permissive_policy
from agentlens.drift import DriftDetector, DriftReport, DriftStatus, DriftDirection, MetricDrift, ToolUsageDrift
from agentlens.sla import SLAEvaluator, SLObjective, SLAPolicy, SLAReport, ObjectiveResult, ObjectiveKind, ComplianceStatus, production_policy, development_policy
from agentlens.sampling import ProbabilisticSampler, RateLimitSampler, PrioritySampler, TailSampler, CompositeSampler, AlwaysSampler, NeverSampler, Sampler, SamplingDecision, SamplingReason, TraceContext, SamplerStats
from agentlens.evaluation import ResponseEvaluator, EvaluatorConfig, QualityReport, QualityTrend, QualityGrade, DimensionScore
from agentlens.postmortem import PostmortemGenerator, PostmortemConfig, PostmortemReport, Severity as PostmortemSeverity, RootCause, ImpactAssessment, Remediation, RemediationCategory, LessonLearned, TimelineEntry, IncidentPhase
from agentlens.rate_limiter import RateLimiter, RateLimit, RateLimitAction, RateLimitPolicy, CheckResult, RateLimitReport, WindowStats, openai_tier1_policy, anthropic_tier1_policy, conservative_policy
from agentlens.exporter import SessionExporter
from agentlens.prompt_tracker import PromptVersionTracker, PromptVersion, PromptDiff, PromptReport, VersionStats, Outcome, DiffKind
from agentlens.latency import LatencyProfiler, ProfilingSession, StepRecord, StepStatus, PercentileStats, SlowStepAlert, SessionReport, compute_percentiles
from agentlens.cost_optimizer import CostOptimizer, ComplexityAnalyzer, ComplexityLevel, ComplexityAssessment, ModelTier, ModelInfo, Confidence, Recommendation, MigrationStep, OptimizationReport, MODEL_REGISTRY as OPTIMIZER_MODEL_REGISTRY
from agentlens.capacity import CapacityPlanner, WorkloadSample, ResourceKind, ScalingAction, BottleneckSeverity, TrendDirection, WorkloadProjection, Bottleneck, ResourceSizing, ScalingRecommendation, CapacityReport
from agentlens.ab_test import ABTestAnalyzer, Experiment, ExperimentStatus, Variant, Observation, TestResult, ExperimentReport, EffectSize, SignificanceLevel, required_sample_size
from agentlens.error_fingerprint import ErrorFingerprinter, ErrorCluster, ErrorReport, ErrorOccurrence, Trend, Resolution
from agentlens.session_diff import SessionDiff, DiffReport, EventPair, AlignmentStatus, ToolCallDelta
from agentlens.group_analyzer import SessionGroupAnalyzer, GroupStats, ComparisonReport
from agentlens.heatmap import HeatmapBuilder, HeatmapBucket
from agentlens.collaboration import CollaborationAnalyzer, CollaborationConfig, CollaborationReport, CollaborationEvent, CollaborationPattern, TeamworkGrade, HandoffDetail, HandoffVerdict, BottleneckAgent, BottleneckSeverity, DelegationNode, WorkloadEntry, EngineResult
from agentlens.narrative import NarrativeGenerator, NarrativeConfig, NarrativeStyle, Narrative, NarrativeSection, ToolSummary
from agentlens.guardrails import Guardrails, GuardrailSuite, Violation, ValidationResult, SuiteReport, Severity as GuardrailSeverity
from agentlens.replayer import SessionReplayer, ReplayFrame, ReplayStats
from agentlens.correlation import SessionCorrelator, CorrelationReport, CorrelationKind, TemporalOverlap, SharedResource, ErrorPropagation, ResourceContention
from agentlens.flamegraph import Flamegraph, flamegraph_html
from agentlens.quota import QuotaManager, QuotaPolicy, QuotaCheck, QuotaReport, QuotaScope, QuotaWindow, QuotaAction
from agentlens.retry_tracker import RetryTracker, RetryReport, RetryChain, RetryStorm, RetryRecommendation, RetryOutcome
from agentlens.alert_rules import AlertRulesEngine, AlertRule as AlertRuleDef, AlertCondition, ThresholdCondition, RateCondition, AlertResult, AlertSeverity as RuleSeverity
from agentlens.autopsy import SessionAutopsy, AutopsyConfig, AutopsyReport, Evidence, EvidenceSource, Hypothesis, RemediationAction, IncidentPriority, EffortLevel, CausalRelation, CausalLink

__version__ = "0.1.0"
__all__ = [
    "init",
    "start_session",
    "end_session",
    "track",
    "explain",
    "export_session",
    "compare_sessions",
    "get_costs",
    "get_pricing",
    "set_pricing",
    "track_agent",
    "track_tool_call",
    "AgentEvent",
    "ToolCall",
    "DecisionTrace",
    "Session",
    "AlertRule",
    "AlertManager",
    "MetricAggregator",
    "Alert",
    "Severity",
    "Condition",
    "HealthScorer",
    "HealthReport",
    "HealthGrade",
    "HealthThresholds",
    "MetricScore",
    "AnomalyDetector",
    "AnomalyDetectorConfig",
    "Anomaly",
    "AnomalyKind",
    "AnomalySeverity",
    "AnomalyReport",
    "MetricBaseline",
    "TimelineRenderer",
    "Span",
    "TokenBudget",
    "BudgetTracker",
    "BudgetReport",
    "BudgetStatus",
    "BudgetExceededError",
    "BudgetEntry",
    "estimate_cost",
    "set_custom_pricing",
    "get_pricing",
    "CostForecaster",
    "UsageRecord",
    "ForecastResult",
    "SpendingSummary",
    "BudgetAlert",
    "DailyPrediction",
    "ComplianceChecker",
    "CompliancePolicy",
    "ComplianceReport",
    "ComplianceRule",
    "RuleKind",
    "RuleVerdict",
    "RuleResult",
    "strict_policy",
    "permissive_policy",
    "DriftDetector",
    "DriftReport",
    "DriftStatus",
    "DriftDirection",
    "MetricDrift",
    "ToolUsageDrift",
    "SLAEvaluator",
    "SLObjective",
    "SLAPolicy",
    "SLAReport",
    "ObjectiveResult",
    "ObjectiveKind",
    "ComplianceStatus",
    "production_policy",
    "development_policy",
    "ProbabilisticSampler",
    "RateLimitSampler",
    "PrioritySampler",
    "TailSampler",
    "CompositeSampler",
    "AlwaysSampler",
    "NeverSampler",
    "Sampler",
    "SamplingDecision",
    "SamplingReason",
    "TraceContext",
    "SamplerStats",
    "ResponseEvaluator",
    "EvaluatorConfig",
    "QualityReport",
    "QualityTrend",
    "QualityGrade",
    "DimensionScore",
    "RateLimiter",
    "RateLimit",
    "RateLimitAction",
    "RateLimitPolicy",
    "CheckResult",
    "RateLimitReport",
    "WindowStats",
    "openai_tier1_policy",
    "anthropic_tier1_policy",
    "conservative_policy",
    "SessionExporter",
    "PromptVersionTracker",
    "PromptVersion",
    "PromptDiff",
    "PromptReport",
    "VersionStats",
    "Outcome",
    "DiffKind",
    "CostOptimizer",
    "ComplexityAnalyzer",
    "ComplexityLevel",
    "ComplexityAssessment",
    "ModelTier",
    "ModelInfo",
    "Confidence",
    "Recommendation",
    "MigrationStep",
    "OptimizationReport",
    "CapacityPlanner",
    "WorkloadSample",
    "ResourceKind",
    "ScalingAction",
    "BottleneckSeverity",
    "TrendDirection",
    "WorkloadProjection",
    "Bottleneck",
    "ResourceSizing",
    "ScalingRecommendation",
    "CapacityReport",
    "ABTestAnalyzer",
    "Experiment",
    "ExperimentStatus",
    "Variant",
    "Observation",
    "ExperimentReport",
    "EffectSize",
    "SignificanceLevel",
    "required_sample_size",
    "ErrorFingerprinter",
    "ErrorCluster",
    "ErrorReport",
    "ErrorOccurrence",
    "Trend",
    "Resolution",
    "SessionDiff",
    "DiffReport",
    "EventPair",
    "AlignmentStatus",
    "ToolCallDelta",
    "HeatmapBuilder",
    "HeatmapBucket",
    # Guardrails
    "Guardrails",
    "GuardrailSuite",
    "Violation",
    "ValidationResult",
    "SuiteReport",
    "GuardrailSeverity",
    # Replayer
    "SessionReplayer",
    "ReplayFrame",
    "ReplayStats",
    # Correlation
    "SessionCorrelator",
    "CorrelationReport",
    "CorrelationKind",
    "TemporalOverlap",
    "SharedResource",
    "ErrorPropagation",
    "ResourceContention",
    # Flamegraph
    "Flamegraph",
    "flamegraph_html",
    # Quota
    "QuotaManager",
    "QuotaPolicy",
    "QuotaCheck",
    "QuotaReport",
    "QuotaScope",
    "QuotaWindow",
    "QuotaAction",
    # Retry tracking
    "RetryTracker",
    "RetryReport",
    "RetryChain",
    "RetryStorm",
    "RetryRecommendation",
    "RetryOutcome",
    # Alert rules engine
    "AlertRulesEngine",
    "AlertRuleDef",
    "AlertCondition",
    "ThresholdCondition",
    "RateCondition",
    "AlertResult",
    "RuleSeverity",
]

_tracker: AgentTracker | None = None


def _get_tracker(operation: str = "this operation") -> AgentTracker:
    """Return the global tracker, raising if the SDK is not initialized.

    Centralises the guard clause that was previously copy-pasted in every
    module-level convenience function.

    Args:
        operation: Name shown in the error message (e.g. ``"track"``).

    Raises:
        RuntimeError: If :func:`init` has not been called yet.
    """
    if _tracker is None:
        raise RuntimeError(f"Call agentlens.init() before {operation}()")
    return _tracker


def init(api_key: str = "default", endpoint: str = "http://localhost:3000") -> AgentTracker:
    """Initialize the AgentLens SDK.
    
    If the SDK was already initialized, the previous transport is closed
    (flushing any buffered events and stopping the background thread)
    before creating the new one.  This prevents resource leaks when
    ``init()`` is called multiple times (e.g. in tests or notebooks).
    
    Args:
        api_key: Your AgentLens API key.
        endpoint: The AgentLens backend URL.
    
    Returns:
        The global AgentTracker instance.
    """
    global _tracker
    # Clean up the previous tracker/transport to avoid leaking threads
    # and HTTP connections.
    if _tracker is not None:
        try:
            _tracker.transport.close()
        except Exception:
            pass
    transport = Transport(endpoint=endpoint, api_key=api_key)
    _tracker = AgentTracker(transport=transport)
    return _tracker


def start_session(agent_name: str = "default-agent", metadata: dict | None = None) -> Session:
    """Start a new tracking session.
    
    Args:
        agent_name: Name of the agent being tracked.
        metadata: Optional metadata dict.
    
    Returns:
        A Session object.
    """
    return _get_tracker("start_session").start_session(agent_name=agent_name, metadata=metadata)


def end_session(session_id: str | None = None) -> None:
    """End the current or specified session and flush pending events."""
    _get_tracker("end_session").end_session(session_id=session_id)


def track(
    event_type: str = "generic",
    input_data: dict | None = None,
    output_data: dict | None = None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    reasoning: str | None = None,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_output: dict | None = None,
    duration_ms: float | None = None,
) -> AgentEvent:
    """Track an agent event manually.
    
    Returns:
        The created AgentEvent.
    """
    return _get_tracker("track").track(
        event_type=event_type,
        input_data=input_data,
        output_data=output_data,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning=reasoning,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        duration_ms=duration_ms,
    )


def explain(session_id: str | None = None) -> str:
    """Get a human-readable explanation of the agent's behavior in the current/specified session.
    
    Returns:
        A string explanation.
    """
    return _get_tracker("explain").explain(session_id=session_id)


def export_session(session_id: str | None = None, format: str = "json"):
    """Export session data from the backend.

    Fetches the full session data (including all events) from the AgentLens
    backend and returns it in the requested format.

    Args:
        session_id: Session to export. Defaults to the current session.
        format: Export format — ``"json"`` returns a dict, ``"csv"`` returns
            a CSV string.

    Returns:
        A dict (for JSON) or a string (for CSV) with session data, events,
        and summary statistics.
    """
    return _get_tracker("export_session").export_session(session_id=session_id, format=format)


def compare_sessions(session_a: str, session_b: str) -> dict:
    """Compare two sessions side-by-side.

    Fetches comparison metrics from the AgentLens backend including
    token usage, event counts, tool usage, timing, and percentage deltas.

    Args:
        session_a: First session ID.
        session_b: Second session ID.

    Returns:
        A dict with ``session_a`` metrics, ``session_b`` metrics,
        ``deltas``, and ``shared`` breakdowns.
    """
    return _get_tracker("compare_sessions").compare_sessions(session_a=session_a, session_b=session_b)


def get_costs(session_id: str | None = None) -> dict:
    """Get cost breakdown for a session.

    Calculates costs using configured model pricing (per 1M tokens).

    Args:
        session_id: Session to get costs for. Defaults to the current session.

    Returns:
        A dict with ``total_cost``, ``total_input_cost``, ``total_output_cost``,
        ``model_costs``, ``event_costs``, ``currency``, and ``unmatched_models``.
    """
    return _get_tracker("get_costs").get_costs(session_id=session_id)


def get_pricing() -> dict:
    """Get the current model pricing configuration.

    Returns:
        A dict with ``pricing`` (current prices) and ``defaults`` (built-in defaults).
    """
    return _get_tracker("get_pricing").get_pricing()


def set_pricing(pricing: dict) -> dict:
    """Update model pricing configuration.

    Args:
        pricing: A dict mapping model names to pricing dicts with
            ``input_cost_per_1m`` and ``output_cost_per_1m`` keys.

    Returns:
        A dict with ``status`` and ``updated`` count.
    """
    return _get_tracker("set_pricing").set_pricing(pricing=pricing)
