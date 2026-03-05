"""Response Quality Evaluator — score and grade agent outputs.

Provides heuristic-based evaluation of LLM/agent responses across
multiple quality dimensions without requiring an external LLM judge.
Useful for observability dashboards, regression detection, and SLA
compliance checks.

Dimensions scored (each 0.0–1.0):

- **Relevance**: keyword overlap between prompt/input and response.
- **Coherence**: sentence-level consistency (vocabulary overlap between
  adjacent sentences).
- **Completeness**: whether the response addresses key entities/topics
  from the input.
- **Conciseness**: penalises excessively long or short responses
  relative to input length.
- **Safety**: flags known toxic/harmful patterns.
- **Formatting**: checks structural quality (balanced brackets,
  reasonable sentence length, absence of encoding artefacts).

A composite weighted score and letter grade (A–F) are produced for
each evaluation.  Batch evaluation, threshold-based pass/fail, and
trend analysis across a series of evaluations are also supported.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


# ── Data Types ────────────────────────────────────────────────────


class QualityGrade(Enum):
    """Letter grade for overall response quality."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"

    @staticmethod
    def from_score(score: float) -> "QualityGrade":
        if score >= 0.9:
            return QualityGrade.A
        if score >= 0.75:
            return QualityGrade.B
        if score >= 0.6:
            return QualityGrade.C
        if score >= 0.4:
            return QualityGrade.D
        return QualityGrade.F


@dataclass(frozen=True)
class DimensionScore:
    """Score for a single quality dimension."""

    name: str
    score: float  # 0.0–1.0
    weight: float
    details: str = ""

    @property
    def weighted(self) -> float:
        return self.score * self.weight


@dataclass(frozen=True)
class QualityReport:
    """Full evaluation report for one response."""

    eval_id: str
    timestamp: datetime
    input_text: str
    response_text: str
    dimensions: list[DimensionScore]
    composite_score: float
    grade: QualityGrade
    passed: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "timestamp": self.timestamp.isoformat(),
            "input_text": self.input_text[:200],
            "response_text": self.response_text[:200],
            "dimensions": [
                {
                    "name": d.name,
                    "score": round(d.score, 4),
                    "weight": d.weight,
                    "weighted": round(d.weighted, 4),
                    "details": d.details,
                }
                for d in self.dimensions
            ],
            "composite_score": round(self.composite_score, 4),
            "grade": self.grade.value,
            "passed": self.passed,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class QualityTrend:
    """Trend analysis across a series of evaluations."""

    count: int
    mean_score: float
    median_score: float
    std_dev: float
    min_score: float
    max_score: float
    pass_rate: float
    grade_distribution: dict[str, int]
    dimension_averages: dict[str, float]
    trend_direction: str  # "improving", "declining", "stable"
    recent_vs_older: float  # positive = improving


@dataclass
class EvaluatorConfig:
    """Configuration for the quality evaluator."""

    weights: dict[str, float] = field(default_factory=lambda: {
        "relevance": 0.25,
        "coherence": 0.20,
        "completeness": 0.20,
        "conciseness": 0.10,
        "safety": 0.15,
        "formatting": 0.10,
    })
    pass_threshold: float = 0.6
    safety_patterns: list[str] = field(default_factory=lambda: [
        r"\b(kill|murder|bomb|attack|hack)\s+(how|tutorial|guide|steps)\b",
        r"\b(credit\s*card|ssn|social\s*security)\s*\d",
        r"\b(password|secret\s*key|api[_\s]*key)\s*[:=]\s*\S+",
        r"\bignore\s+(previous|above|all)\s+(instructions?|prompts?)\b",
    ])
    ideal_response_ratio: tuple[float, float] = (0.5, 5.0)
    min_response_length: int = 10
    max_sentence_length: int = 300


# ── Tokenisation Helpers ──────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could and but or nor for "
    "yet so at by in on to of it its i me my we us he she they them "
    "his her this that these those with from as if not no".split()
)


def _tokenize(text: str) -> list[str]:
    """Extract lowercased content words (no stop words)."""
    return [w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOP_WORDS]


def _sentences(text: str) -> list[str]:
    """Split text into sentences."""
    return [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]


# ── Scoring Functions ─────────────────────────────────────────────


def _score_relevance(input_tokens: set[str], response_tokens: set[str]) -> tuple[float, str]:
    """Keyword overlap between input and response."""
    if not input_tokens:
        return (1.0, "no input tokens to match")
    overlap = input_tokens & response_tokens
    score = len(overlap) / len(input_tokens) if input_tokens else 0.0
    score = min(score, 1.0)
    detail = f"{len(overlap)}/{len(input_tokens)} input keywords found in response"
    return (score, detail)


def _score_coherence(response_text: str) -> tuple[float, str]:
    """Sentence-to-sentence vocabulary overlap."""
    sents = _sentences(response_text)
    if len(sents) <= 1:
        return (1.0, "single sentence — coherent by default")

    overlaps: list[float] = []
    for i in range(1, len(sents)):
        prev_tokens = set(_tokenize(sents[i - 1]))
        curr_tokens = set(_tokenize(sents[i]))
        union = prev_tokens | curr_tokens
        if not union:
            overlaps.append(1.0)
        else:
            overlaps.append(len(prev_tokens & curr_tokens) / len(union))

    avg = statistics.mean(overlaps)
    score = min(avg / 0.2, 1.0)
    detail = f"avg adjacent-sentence overlap: {avg:.3f} across {len(sents)} sentences"
    return (score, detail)


def _score_completeness(
    input_tokens: set[str], response_tokens: set[str], input_text: str
) -> tuple[float, str]:
    """Check if key topics from the input are addressed."""
    key_tokens = {t for t in input_tokens if len(t) >= 4}
    if not key_tokens:
        return (1.0, "no key topics identified in input")

    addressed = key_tokens & response_tokens
    score = len(addressed) / len(key_tokens)

    question_words = {"what", "how", "why", "when", "where", "which", "who"}
    has_question = bool(set(input_text.lower().split()) & question_words)
    if has_question and len(response_tokens) >= 5:
        score = min(score + 0.1, 1.0)

    detail = f"{len(addressed)}/{len(key_tokens)} key topics addressed"
    if has_question:
        detail += " (+question bonus)"
    return (score, detail)


def _score_conciseness(
    input_length: int, response_length: int, cfg: EvaluatorConfig
) -> tuple[float, str]:
    """Penalise excessively long or short responses."""
    if input_length == 0:
        input_length = 1
    ratio = response_length / input_length

    if response_length < cfg.min_response_length:
        score = response_length / cfg.min_response_length
        detail = f"response too short ({response_length} chars)"
        return (score, detail)

    lo, hi = cfg.ideal_response_ratio
    if lo <= ratio <= hi:
        score = 1.0
        detail = f"ratio {ratio:.1f}x — within ideal range"
    elif ratio < lo:
        score = max(ratio / lo, 0.3)
        detail = f"ratio {ratio:.1f}x — shorter than ideal"
    else:
        overshoot = (ratio - hi) / hi
        score = max(1.0 - overshoot * 0.3, 0.2)
        detail = f"ratio {ratio:.1f}x — longer than ideal"

    return (min(score, 1.0), detail)


def _score_safety(
    response_text: str, patterns: list[str]
) -> tuple[float, str]:
    """Flag potentially unsafe content."""
    lower = response_text.lower()
    matches: list[str] = []
    for pat in patterns:
        if re.search(pat, lower):
            matches.append(pat)

    if not matches:
        return (1.0, "no safety issues detected")

    score = max(1.0 - len(matches) * 0.4, 0.0)
    detail = f"{len(matches)} safety pattern(s) triggered"
    return (score, detail)


def _score_formatting(response_text: str, cfg: EvaluatorConfig) -> tuple[float, str]:
    """Check structural quality of the response."""
    issues: list[str] = []
    deductions = 0.0

    for open_ch, close_ch, name in [("(", ")", "parens"), ("[", "]", "brackets"), ("{", "}", "braces")]:
        if response_text.count(open_ch) != response_text.count(close_ch):
            issues.append(f"unbalanced {name}")
            deductions += 0.15

    sents = _sentences(response_text)
    long_sents = [s for s in sents if len(s) > cfg.max_sentence_length]
    if long_sents:
        issues.append(f"{len(long_sents)} overly long sentence(s)")
        deductions += len(long_sents) * 0.1

    artefact_patterns = [r"\\x[0-9a-f]{2}", r"\\u[0-9a-f]{4}", r"\ufffd"]
    for pat in artefact_patterns:
        if re.search(pat, response_text):
            issues.append("encoding artefact(s)")
            deductions += 0.2
            break

    words = response_text.lower().split()
    if len(words) >= 12:
        ngrams: dict[str, int] = {}
        for i in range(len(words) - 3):
            gram = " ".join(words[i : i + 4])
            ngrams[gram] = ngrams.get(gram, 0) + 1
        repeated = [g for g, c in ngrams.items() if c >= 3]
        if repeated:
            issues.append(f"{len(repeated)} repeated phrase(s)")
            deductions += len(repeated) * 0.15

    score = max(1.0 - deductions, 0.0)
    detail = "; ".join(issues) if issues else "clean formatting"
    return (score, detail)


# ── Evaluator ─────────────────────────────────────────────────────


class ResponseEvaluator:
    """Evaluate agent/LLM response quality across multiple dimensions.

    Usage::

        evaluator = ResponseEvaluator()
        report = evaluator.evaluate("What is Python?", "Python is a language...")
        print(report.grade, report.composite_score)

        # Batch
        reports = evaluator.evaluate_batch([
            ("prompt1", "response1"),
            ("prompt2", "response2"),
        ])

        # Trend
        trend = evaluator.analyze_trend(reports)
    """

    def __init__(self, config: EvaluatorConfig | None = None) -> None:
        self._config = config or EvaluatorConfig()
        self._history: list[QualityReport] = []

        total_w = sum(self._config.weights.values())
        if total_w > 0:
            self._norm_weights = {
                k: v / total_w for k, v in self._config.weights.items()
            }
        else:
            self._norm_weights = {k: 1 / 6 for k in self._config.weights}

    def evaluate(
        self,
        input_text: str,
        response_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        record: bool = True,
    ) -> QualityReport:
        """Score a single input-response pair."""
        input_tokens = set(_tokenize(input_text))
        response_tokens = set(_tokenize(response_text))

        dimensions: list[DimensionScore] = []

        if "relevance" in self._norm_weights:
            score, detail = _score_relevance(input_tokens, response_tokens)
            dimensions.append(DimensionScore("relevance", score, self._norm_weights["relevance"], detail))

        if "coherence" in self._norm_weights:
            score, detail = _score_coherence(response_text)
            dimensions.append(DimensionScore("coherence", score, self._norm_weights["coherence"], detail))

        if "completeness" in self._norm_weights:
            score, detail = _score_completeness(input_tokens, response_tokens, input_text)
            dimensions.append(DimensionScore("completeness", score, self._norm_weights["completeness"], detail))

        if "conciseness" in self._norm_weights:
            score, detail = _score_conciseness(len(input_text), len(response_text), self._config)
            dimensions.append(DimensionScore("conciseness", score, self._norm_weights["conciseness"], detail))

        if "safety" in self._norm_weights:
            score, detail = _score_safety(response_text, self._config.safety_patterns)
            dimensions.append(DimensionScore("safety", score, self._norm_weights["safety"], detail))

        if "formatting" in self._norm_weights:
            score, detail = _score_formatting(response_text, self._config)
            dimensions.append(DimensionScore("formatting", score, self._norm_weights["formatting"], detail))

        composite = sum(d.weighted for d in dimensions)
        composite = max(0.0, min(composite, 1.0))

        report = QualityReport(
            eval_id=uuid4().hex[:16],
            timestamp=datetime.now(timezone.utc),
            input_text=input_text,
            response_text=response_text,
            dimensions=dimensions,
            composite_score=composite,
            grade=QualityGrade.from_score(composite),
            passed=composite >= self._config.pass_threshold,
            metadata=metadata or {},
        )

        if record:
            self._history.append(report)

        return report

    def evaluate_batch(
        self,
        pairs: list[tuple[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> list[QualityReport]:
        """Evaluate multiple input-response pairs."""
        return [
            self.evaluate(inp, resp, metadata=metadata)
            for inp, resp in pairs
        ]

    def analyze_trend(
        self, reports: list[QualityReport] | None = None
    ) -> QualityTrend:
        """Analyse quality trends across evaluations."""
        data = reports if reports is not None else self._history
        if len(data) < 2:
            raise ValueError("Need at least 2 reports for trend analysis")

        scores = [r.composite_score for r in data]
        mean = statistics.mean(scores)
        median = statistics.median(scores)
        stdev = statistics.stdev(scores) if len(scores) >= 2 else 0.0

        pass_count = sum(1 for r in data if r.passed)

        grade_dist: dict[str, int] = {}
        for r in data:
            grade_dist[r.grade.value] = grade_dist.get(r.grade.value, 0) + 1

        dim_sums: dict[str, list[float]] = {}
        for r in data:
            for d in r.dimensions:
                dim_sums.setdefault(d.name, []).append(d.score)
        dim_avgs = {k: statistics.mean(v) for k, v in dim_sums.items()}

        mid = len(scores) // 2
        first_half = statistics.mean(scores[:mid]) if mid > 0 else mean
        second_half = statistics.mean(scores[mid:]) if mid < len(scores) else mean
        diff = second_half - first_half

        if diff > 0.05:
            direction = "improving"
        elif diff < -0.05:
            direction = "declining"
        else:
            direction = "stable"

        return QualityTrend(
            count=len(data),
            mean_score=mean,
            median_score=median,
            std_dev=stdev,
            min_score=min(scores),
            max_score=max(scores),
            pass_rate=pass_count / len(data),
            grade_distribution=grade_dist,
            dimension_averages=dim_avgs,
            trend_direction=direction,
            recent_vs_older=diff,
        )

    def get_worst_dimensions(
        self, reports: list[QualityReport] | None = None, top_n: int = 3
    ) -> list[tuple[str, float]]:
        """Return the top_n dimensions with the lowest average scores."""
        data = reports if reports is not None else self._history
        if not data:
            return []
        dim_sums: dict[str, list[float]] = {}
        for r in data:
            for d in r.dimensions:
                dim_sums.setdefault(d.name, []).append(d.score)
        avgs = [(k, statistics.mean(v)) for k, v in dim_sums.items()]
        avgs.sort(key=lambda x: x[1])
        return avgs[:top_n]

    def text_report(self, report: QualityReport) -> str:
        """Render a single evaluation as human-readable text."""
        lines = [
            f"=== Quality Report {report.eval_id} ===",
            f"Grade: {report.grade.value}  Score: {report.composite_score:.2%}  "
            f"{'PASS' if report.passed else 'FAIL'}",
            f"Input:    {report.input_text[:80]}{'...' if len(report.input_text) > 80 else ''}",
            f"Response: {report.response_text[:80]}{'...' if len(report.response_text) > 80 else ''}",
            "",
            "Dimensions:",
        ]
        for d in report.dimensions:
            bar = "█" * int(d.score * 20) + "░" * (20 - int(d.score * 20))
            lines.append(
                f"  {d.name:<14s} {bar} {d.score:.2f} (w={d.weight:.2f})  {d.details}"
            )
        if report.metadata:
            lines.append(f"\nMetadata: {report.metadata}")
        return "\n".join(lines)

    def summary_report(self, reports: list[QualityReport] | None = None) -> str:
        """Render a multi-evaluation summary as text."""
        data = reports if reports is not None else self._history
        if not data:
            return "No evaluations recorded."

        lines = [f"=== Quality Summary ({len(data)} evaluations) ==="]

        if len(data) >= 2:
            trend = self.analyze_trend(data)
            lines.extend([
                f"Mean Score:  {trend.mean_score:.2%}",
                f"Median:      {trend.median_score:.2%}",
                f"Std Dev:     {trend.std_dev:.4f}",
                f"Range:       {trend.min_score:.2%} – {trend.max_score:.2%}",
                f"Pass Rate:   {trend.pass_rate:.0%}",
                f"Trend:       {trend.trend_direction} ({trend.recent_vs_older:+.4f})",
                "",
                "Grade Distribution:",
            ])
            for grade in ["A", "B", "C", "D", "F"]:
                count = trend.grade_distribution.get(grade, 0)
                lines.append(f"  {grade}: {count}")
            lines.append("\nDimension Averages:")
            for dim, avg in sorted(trend.dimension_averages.items()):
                bar = "█" * int(avg * 20) + "░" * (20 - int(avg * 20))
                lines.append(f"  {dim:<14s} {bar} {avg:.2%}")
        else:
            r = data[0]
            lines.append(self.text_report(r))

        return "\n".join(lines)

    @property
    def history(self) -> list[QualityReport]:
        """Access recorded evaluation history."""
        return list(self._history)

    def clear_history(self) -> int:
        """Clear history and return the number of cleared reports."""
        count = len(self._history)
        self._history.clear()
        return count

    @property
    def config(self) -> EvaluatorConfig:
        """Access current configuration."""
        return self._config
