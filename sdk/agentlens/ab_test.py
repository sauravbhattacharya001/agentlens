"""A/B Test Analyzer — experiment framework for comparing agent variants.

Run controlled experiments comparing models, prompts, or configurations.
Includes statistical significance testing (Welch's t-test, Mann-Whitney U),
sample size estimation, effect size (Cohen's d), and experiment reporting.

Usage::

    from agentlens.ab_test import ABTestAnalyzer, Experiment, Variant

    analyzer = ABTestAnalyzer()
    exp = analyzer.create_experiment("gpt4-vs-claude", hypothesis="GPT-4 has lower latency")
    exp.add_variant("gpt4", description="OpenAI GPT-4")
    exp.add_variant("claude", description="Anthropic Claude")

    # Record observations
    exp.record("gpt4", metric="latency_ms", value=230)
    exp.record("claude", metric="latency_ms", value=310)
    # ... more observations ...

    result = analyzer.analyze("gpt4-vs-claude", metric="latency_ms")
    print(result.winner, result.p_value, result.significant)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class ExperimentStatus(Enum):
    """Lifecycle status of an experiment."""
    DRAFT = "draft"
    RUNNING = "running"
    STOPPED = "stopped"
    CONCLUDED = "concluded"


class SignificanceLevel(Enum):
    """Common significance thresholds."""
    RELAXED = 0.10
    STANDARD = 0.05
    STRICT = 0.01
    VERY_STRICT = 0.001


class EffectSize(Enum):
    """Cohen's d interpretation."""
    NEGLIGIBLE = "negligible"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    VERY_LARGE = "very_large"


@dataclass
class Observation:
    """A single metric observation for a variant."""
    metric: str
    value: float
    timestamp: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)


@dataclass
class Variant:
    """An experimental variant (treatment or control)."""
    name: str
    description: str = ""
    is_control: bool = False
    observations: List[Observation] = field(default_factory=list)

    def values(self, metric: str) -> List[float]:
        """Get all values for a specific metric."""
        return [o.value for o in self.observations if o.metric == metric]

    def count(self, metric: str) -> int:
        return len(self.values(metric))

    def mean(self, metric: str) -> float:
        vals = self.values(metric)
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    def variance(self, metric: str) -> float:
        vals = self.values(metric)
        if len(vals) < 2:
            return 0.0
        m = self.mean(metric)
        return sum((v - m) ** 2 for v in vals) / (len(vals) - 1)

    def std(self, metric: str) -> float:
        return math.sqrt(self.variance(metric))

    def metrics(self) -> List[str]:
        return list(set(o.metric for o in self.observations))


@dataclass
class Experiment:
    """A complete A/B test experiment."""
    name: str
    hypothesis: str = ""
    status: ExperimentStatus = ExperimentStatus.DRAFT
    variants: Dict[str, Variant] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    tags: List[str] = field(default_factory=list)

    def add_variant(self, name: str, description: str = "", is_control: bool = False) -> Variant:
        if name in self.variants:
            raise ValueError(f"Variant '{name}' already exists")
        v = Variant(name=name, description=description, is_control=is_control)
        self.variants[name] = v
        return v

    def record(self, variant: str, metric: str, value: float, metadata: Optional[Dict] = None) -> Observation:
        if variant not in self.variants:
            raise KeyError(f"Unknown variant '{variant}'")
        if self.status not in (ExperimentStatus.DRAFT, ExperimentStatus.RUNNING):
            raise RuntimeError(f"Cannot record in {self.status.value} experiment")
        if self.status == ExperimentStatus.DRAFT:
            self.status = ExperimentStatus.RUNNING
            self.started_at = time.time()
        obs = Observation(metric=metric, value=value, metadata=metadata or {})
        self.variants[variant].observations.append(obs)
        return obs

    def start(self) -> None:
        self.status = ExperimentStatus.RUNNING
        self.started_at = time.time()

    def stop(self) -> None:
        self.status = ExperimentStatus.STOPPED
        self.stopped_at = time.time()

    def conclude(self) -> None:
        self.status = ExperimentStatus.CONCLUDED
        self.stopped_at = self.stopped_at or time.time()

    def metrics(self) -> List[str]:
        all_metrics: set = set()
        for v in self.variants.values():
            all_metrics.update(v.metrics())
        return sorted(all_metrics)

    def variant_names(self) -> List[str]:
        return list(self.variants.keys())

    def control(self) -> Optional[Variant]:
        for v in self.variants.values():
            if v.is_control:
                return v
        return None

    def total_observations(self) -> int:
        return sum(len(v.observations) for v in self.variants.values())

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "hypothesis": self.hypothesis,
            "status": self.status.value,
            "tags": self.tags,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "total_observations": self.total_observations(),
            "variants": {
                name: {
                    "description": v.description,
                    "is_control": v.is_control,
                    "observation_count": len(v.observations),
                    "metrics": {
                        m: {"count": v.count(m), "mean": v.mean(m), "std": v.std(m)}
                        for m in v.metrics()
                    },
                }
                for name, v in self.variants.items()
            },
        }


@dataclass
class TestResult:
    """Statistical test result comparing two variants."""
    experiment: str
    metric: str
    variant_a: str
    variant_b: str
    mean_a: float
    mean_b: float
    std_a: float
    std_b: float
    n_a: int
    n_b: int
    t_statistic: float
    p_value: float
    significant: bool
    alpha: float
    cohens_d: float
    effect_size: EffectSize
    winner: Optional[str]
    improvement_pct: float
    power: Optional[float]
    confidence_interval: tuple

    def to_dict(self) -> Dict:
        return {
            "experiment": self.experiment,
            "metric": self.metric,
            "variant_a": self.variant_a,
            "variant_b": self.variant_b,
            "mean_a": round(self.mean_a, 4),
            "mean_b": round(self.mean_b, 4),
            "std_a": round(self.std_a, 4),
            "std_b": round(self.std_b, 4),
            "n_a": self.n_a,
            "n_b": self.n_b,
            "t_statistic": round(self.t_statistic, 4),
            "p_value": round(self.p_value, 6),
            "significant": self.significant,
            "alpha": self.alpha,
            "cohens_d": round(self.cohens_d, 4),
            "effect_size": self.effect_size.value,
            "winner": self.winner,
            "improvement_pct": round(self.improvement_pct, 2),
            "confidence_interval": (round(self.confidence_interval[0], 4), round(self.confidence_interval[1], 4)),
        }

    def summary(self) -> str:
        lines = [
            f"A/B Test: {self.experiment} — {self.metric}",
            f"  {self.variant_a}: mean={self.mean_a:.4f} (n={self.n_a})",
            f"  {self.variant_b}: mean={self.mean_b:.4f} (n={self.n_b})",
            f"  t={self.t_statistic:.4f}, p={self.p_value:.6f} (α={self.alpha})",
            f"  Cohen's d={self.cohens_d:.4f} ({self.effect_size.value})",
            f"  CI=({self.confidence_interval[0]:.4f}, {self.confidence_interval[1]:.4f})",
        ]
        if self.significant and self.winner:
            lines.append(f"  ✅ Winner: {self.winner} ({self.improvement_pct:+.2f}%)")
        elif self.significant:
            lines.append(f"  ✅ Significant difference ({self.improvement_pct:+.2f}%)")
        else:
            lines.append("  ⚪ No significant difference")
        return "\n".join(lines)


@dataclass
class ExperimentReport:
    """Full experiment report across all metrics and variant pairs."""
    experiment: str
    hypothesis: str
    status: str
    total_observations: int
    variants: List[str]
    metrics: List[str]
    results: List[TestResult]
    recommendations: List[str]
    overall_winner: Optional[str]

    def to_dict(self) -> Dict:
        return {
            "experiment": self.experiment,
            "hypothesis": self.hypothesis,
            "status": self.status,
            "total_observations": self.total_observations,
            "variants": self.variants,
            "metrics": self.metrics,
            "results": [r.to_dict() for r in self.results],
            "recommendations": self.recommendations,
            "overall_winner": self.overall_winner,
        }

    def summary(self) -> str:
        lines = [
            f"═══ Experiment Report: {self.experiment} ═══",
            f"Hypothesis: {self.hypothesis}" if self.hypothesis else "",
            f"Status: {self.status} | Observations: {self.total_observations}",
            f"Variants: {', '.join(self.variants)}",
            "",
        ]
        for r in self.results:
            lines.append(r.summary())
            lines.append("")
        if self.recommendations:
            lines.append("Recommendations:")
            for rec in self.recommendations:
                lines.append(f"  • {rec}")
        if self.overall_winner:
            lines.append(f"\n🏆 Overall Winner: {self.overall_winner}")
        return "\n".join(l for l in lines if l is not None)


def _t_cdf_approx(t: float, df: float) -> float:
    """Approximate the CDF of Student's t-distribution (no scipy needed).

    Uses the regularized incomplete beta function relationship:
        CDF(t, df) = 1 - 0.5 * I_x(df/2, 0.5)
    where x = df / (df + t^2).

    The incomplete beta is computed via a continued fraction expansion.
    """
    if df <= 0:
        return 0.5
    x = df / (df + t * t)
    a = df / 2.0
    b = 0.5

    # Regularized incomplete beta via Lentz continued fraction
    ibeta = _regularized_incomplete_beta(x, a, b)

    if t >= 0:
        return 1.0 - 0.5 * ibeta
    else:
        return 0.5 * ibeta


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Compute the regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0

    # Use the continued fraction representation
    # For better convergence, flip if needed
    if x > (a + 1) / (a + b + 2):
        return 1.0 - _regularized_incomplete_beta(1 - x, b, a)

    ln_prefix = (
        a * math.log(x) + b * math.log(1 - x)
        - math.log(a)
        - _log_beta(a, b)
    )
    prefix = math.exp(ln_prefix)

    # Lentz's algorithm for the continued fraction
    cf = _beta_cf(x, a, b)
    return prefix * cf


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_cf(x: float, a: float, b: float, max_iter: int = 200, tol: float = 1e-10) -> float:
    """Evaluate the continued fraction for the incomplete beta."""
    tiny = 1e-30
    f = 1.0
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    f = d

    for m in range(1, max_iter + 1):
        # Even step
        numerator = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        f *= c * d

        # Odd step
        numerator = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = c * d
        f *= delta

        if abs(delta - 1.0) < tol:
            break

    return f


def _welch_t_test(vals_a: List[float], vals_b: List[float]) -> tuple:
    """Welch's t-test (unequal variances). Returns (t_stat, df, p_value)."""
    n_a, n_b = len(vals_a), len(vals_b)
    if n_a < 2 or n_b < 2:
        return (0.0, 1.0, 1.0)

    mean_a = sum(vals_a) / n_a
    mean_b = sum(vals_b) / n_b
    var_a = sum((v - mean_a) ** 2 for v in vals_a) / (n_a - 1)
    var_b = sum((v - mean_b) ** 2 for v in vals_b) / (n_b - 1)

    se = math.sqrt(var_a / n_a + var_b / n_b) if (var_a / n_a + var_b / n_b) > 0 else 1e-10
    t_stat = (mean_a - mean_b) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / n_a + var_b / n_b) ** 2
    denom = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    df = num / denom if denom > 0 else 1.0

    # Two-tailed p-value
    p_value = 2.0 * (1.0 - _t_cdf_approx(abs(t_stat), df))
    p_value = max(0.0, min(1.0, p_value))

    return (t_stat, df, p_value)


def _mann_whitney_u(vals_a: List[float], vals_b: List[float]) -> tuple:
    """Mann-Whitney U test (non-parametric). Returns (U, p_value)."""
    n_a, n_b = len(vals_a), len(vals_b)
    if n_a == 0 or n_b == 0:
        return (0.0, 1.0)

    # Count how many times a value from A exceeds one from B
    u_a = 0.0
    for a in vals_a:
        for b in vals_b:
            if a > b:
                u_a += 1
            elif a == b:
                u_a += 0.5

    u = min(u_a, n_a * n_b - u_a)
    # Normal approximation for large samples
    mu = n_a * n_b / 2.0
    sigma = math.sqrt(n_a * n_b * (n_a + n_b + 1) / 12.0)
    if sigma == 0:
        return (u, 1.0)
    z = (u - mu) / sigma
    # Two-tailed p from normal distribution approximation
    p_value = 2.0 * _normal_cdf(-abs(z))
    return (u, max(0.0, min(1.0, p_value)))


def _normal_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _cohens_d(vals_a: List[float], vals_b: List[float]) -> float:
    """Cohen's d effect size."""
    n_a, n_b = len(vals_a), len(vals_b)
    if n_a < 2 or n_b < 2:
        return 0.0
    mean_a = sum(vals_a) / n_a
    mean_b = sum(vals_b) / n_b
    var_a = sum((v - mean_a) ** 2 for v in vals_a) / (n_a - 1)
    var_b = sum((v - mean_b) ** 2 for v in vals_b) / (n_b - 1)
    pooled_std = math.sqrt(((n_a - 1) * var_a + (n_b - 1) * var_b) / (n_a + n_b - 2))
    if pooled_std == 0:
        return 0.0
    return (mean_a - mean_b) / pooled_std


def _interpret_effect_size(d: float) -> EffectSize:
    """Interpret Cohen's d magnitude."""
    d = abs(d)
    if d < 0.2:
        return EffectSize.NEGLIGIBLE
    elif d < 0.5:
        return EffectSize.SMALL
    elif d < 0.8:
        return EffectSize.MEDIUM
    elif d < 1.2:
        return EffectSize.LARGE
    else:
        return EffectSize.VERY_LARGE


def required_sample_size(effect_size: float = 0.5, alpha: float = 0.05, power: float = 0.80) -> int:
    """Estimate required sample size per variant for a two-sample t-test.

    Uses the approximation: n = (z_alpha/2 + z_beta)^2 * 2 / d^2
    where d is the expected Cohen's d.
    """
    if effect_size <= 0:
        return 0
    z_alpha = _normal_quantile(1 - alpha / 2)
    z_beta = _normal_quantile(power)
    n = math.ceil(((z_alpha + z_beta) ** 2) * 2 / (effect_size ** 2))
    return max(n, 2)


def _normal_quantile(p: float) -> float:
    """Approximate normal quantile (Beasley-Springer-Moro algorithm)."""
    if p <= 0:
        return -6.0
    if p >= 1:
        return 6.0
    if p == 0.5:
        return 0.0

    # Rational approximation
    t = math.sqrt(-2.0 * math.log(min(p, 1 - p)))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    result = t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)

    if p < 0.5:
        return -result
    return result


class ABTestAnalyzer:
    """Manages A/B test experiments for agent variant comparison."""

    def __init__(self, default_alpha: float = 0.05):
        self.default_alpha = default_alpha
        self.experiments: Dict[str, Experiment] = {}

    def create_experiment(
        self,
        name: str,
        hypothesis: str = "",
        tags: Optional[List[str]] = None,
    ) -> Experiment:
        """Create a new experiment."""
        if name in self.experiments:
            raise ValueError(f"Experiment '{name}' already exists")
        exp = Experiment(name=name, hypothesis=hypothesis, tags=tags or [])
        self.experiments[name] = exp
        return exp

    def get_experiment(self, name: str) -> Experiment:
        if name not in self.experiments:
            raise KeyError(f"Unknown experiment '{name}'")
        return self.experiments[name]

    def list_experiments(self, status: Optional[ExperimentStatus] = None) -> List[Experiment]:
        exps = list(self.experiments.values())
        if status is not None:
            exps = [e for e in exps if e.status == status]
        return exps

    def delete_experiment(self, name: str) -> None:
        if name not in self.experiments:
            raise KeyError(f"Unknown experiment '{name}'")
        del self.experiments[name]

    def analyze(
        self,
        experiment_name: str,
        metric: str,
        variant_a: Optional[str] = None,
        variant_b: Optional[str] = None,
        alpha: Optional[float] = None,
        test: str = "welch",
    ) -> TestResult:
        """Analyze a metric comparing two variants.

        Args:
            experiment_name: Name of the experiment.
            metric: Metric to compare.
            variant_a: First variant (defaults to first non-control).
            variant_b: Second variant (defaults to control or second variant).
            alpha: Significance level.
            test: Statistical test — "welch" (default) or "mann_whitney".

        Returns:
            TestResult with statistical analysis.
        """
        alpha = alpha or self.default_alpha
        exp = self.get_experiment(experiment_name)
        names = list(exp.variants.keys())

        if len(names) < 2:
            raise ValueError("Need at least 2 variants to compare")

        # Auto-select variants
        if variant_a is None or variant_b is None:
            ctrl = exp.control()
            if ctrl and variant_a is None and variant_b is None:
                variant_b = ctrl.name
                variant_a = [n for n in names if n != ctrl.name][0]
            else:
                variant_a = variant_a or names[0]
                variant_b = variant_b or names[1]

        va = exp.variants[variant_a]
        vb = exp.variants[variant_b]
        vals_a = va.values(metric)
        vals_b = vb.values(metric)

        if len(vals_a) < 2 or len(vals_b) < 2:
            raise ValueError(f"Need ≥2 observations per variant (got {len(vals_a)}, {len(vals_b)})")

        if test == "mann_whitney":
            u_stat, p_value = _mann_whitney_u(vals_a, vals_b)
            t_stat = u_stat  # store U as the statistic
        else:
            t_stat, df, p_value = _welch_t_test(vals_a, vals_b)

        d = _cohens_d(vals_a, vals_b)
        effect = _interpret_effect_size(d)
        mean_a, mean_b = va.mean(metric), vb.mean(metric)
        significant = p_value < alpha

        # Determine winner (variant with better mean)
        winner = None
        if significant:
            winner = variant_a if mean_a > mean_b else variant_b

        # Improvement percentage
        base = mean_b if mean_b != 0 else 1e-10
        improvement = ((mean_a - mean_b) / abs(base)) * 100

        # Confidence interval for the difference
        se = math.sqrt(va.variance(metric) / len(vals_a) + vb.variance(metric) / len(vals_b))
        z = _normal_quantile(1 - alpha / 2)
        diff = mean_a - mean_b
        ci = (diff - z * se, diff + z * se)

        return TestResult(
            experiment=experiment_name,
            metric=metric,
            variant_a=variant_a,
            variant_b=variant_b,
            mean_a=mean_a,
            mean_b=mean_b,
            std_a=va.std(metric),
            std_b=vb.std(metric),
            n_a=len(vals_a),
            n_b=len(vals_b),
            t_statistic=t_stat,
            p_value=p_value,
            significant=significant,
            alpha=alpha,
            cohens_d=d,
            effect_size=effect,
            winner=winner,
            improvement_pct=improvement,
            power=None,
            confidence_interval=ci,
        )

    def analyze_all(
        self,
        experiment_name: str,
        alpha: Optional[float] = None,
    ) -> ExperimentReport:
        """Analyze all metrics for all variant pairs in an experiment."""
        alpha = alpha or self.default_alpha
        exp = self.get_experiment(experiment_name)
        names = list(exp.variants.keys())
        metrics = exp.metrics()
        results: List[TestResult] = []
        recommendations: List[str] = []

        # Compare all pairs for all metrics
        for i, va_name in enumerate(names):
            for vb_name in names[i + 1:]:
                for metric in metrics:
                    va = exp.variants[va_name]
                    vb = exp.variants[vb_name]
                    if va.count(metric) >= 2 and vb.count(metric) >= 2:
                        try:
                            result = self.analyze(
                                experiment_name, metric, va_name, vb_name, alpha=alpha,
                            )
                            results.append(result)
                        except ValueError:
                            pass

        # Determine overall winner by counting wins
        wins: Dict[str, int] = {}
        for r in results:
            if r.significant and r.winner:
                wins[r.winner] = wins.get(r.winner, 0) + 1

        overall_winner = max(wins, key=wins.get) if wins else None

        # Generate recommendations
        for r in results:
            if r.significant:
                recommendations.append(
                    f"For {r.metric}: {r.winner} outperforms "
                    f"({r.improvement_pct:+.1f}%, p={r.p_value:.4f}, d={r.cohens_d:.2f} {r.effect_size.value})"
                )
            elif r.n_a < 30 or r.n_b < 30:
                needed = required_sample_size(effect_size=0.5, alpha=alpha)
                recommendations.append(
                    f"For {r.metric}: no significant difference yet. "
                    f"Consider collecting ≥{needed} observations per variant."
                )

        return ExperimentReport(
            experiment=experiment_name,
            hypothesis=exp.hypothesis,
            status=exp.status.value,
            total_observations=exp.total_observations(),
            variants=names,
            metrics=metrics,
            results=results,
            recommendations=recommendations,
            overall_winner=overall_winner,
        )

    def export_experiments(self) -> Dict:
        """Export all experiments as a dict."""
        return {
            name: exp.to_dict()
            for name, exp in self.experiments.items()
        }
