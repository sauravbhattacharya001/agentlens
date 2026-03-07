"""Tests for agentlens.anomaly — statistical anomaly detection."""

from __future__ import annotations

import math

import pytest

from agentlens.anomaly import (
    Anomaly,
    AnomalyDetector,
    AnomalyDetectorConfig,
    AnomalyKind,
    AnomalyReport,
    AnomalySeverity,
    MetricBaseline,
)
from agentlens.models import AgentEvent, Session


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_session(
    session_id: str = "test-1",
    events: list[AgentEvent] | None = None,
    agent_name: str = "test-agent",
) -> Session:
    s = Session(session_id=session_id, agent_name=agent_name)
    for e in events or []:
        s.add_event(e)
    return s


def _make_event(
    event_type: str = "llm_call",
    tokens_in: int = 100,
    tokens_out: int = 50,
    duration_ms: float = 150.0,
    model: str = "gpt-4",
) -> AgentEvent:
    return AgentEvent(
        event_type=event_type,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        model=model,
    )


def _make_error_event(duration_ms: float = 100.0) -> AgentEvent:
    return AgentEvent(event_type="agent_error", duration_ms=duration_ms)


def _make_tool_event(error: bool = False, duration_ms: float = 50.0) -> AgentEvent:
    return AgentEvent(
        event_type="tool_error" if error else "tool_call",
        duration_ms=duration_ms,
    )


def _build_baseline_detector(
    samples: list[dict[str, float]] | None = None,
    config: AnomalyDetectorConfig | None = None,
) -> AnomalyDetector:
    """Return a detector with a ready baseline."""
    detector = AnomalyDetector(config=config)
    if samples is None:
        # 5 normal samples
        for _ in range(5):
            detector.add_sample({
                "avg_latency_ms": 150.0,
                "p95_latency_ms": 200.0,
                "total_tokens": 500.0,
                "tokens_per_event": 100.0,
                "error_rate": 0.02,
                "event_count": 5.0,
                "tool_failure_rate": 0.01,
            })
    else:
        for s in samples:
            detector.add_sample(s)
    return detector


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyKind
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyKind:
    def test_all_six_values_exist(self):
        expected = {
            "LATENCY_SPIKE", "TOKEN_SURGE", "ERROR_BURST",
            "EVENT_FLOOD", "EVENT_DROUGHT", "TOOL_FAILURE_SPIKE",
        }
        assert {m.name for m in AnomalyKind} == expected

    def test_values_are_lowercase_strings(self):
        for kind in AnomalyKind:
            assert kind.value == kind.value.lower()
            assert isinstance(kind.value, str)

    def test_unique_values(self):
        values = [k.value for k in AnomalyKind]
        assert len(values) == len(set(values))


# ═══════════════════════════════════════════════════════════════════════════
# AnomalySeverity
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalySeverity:
    def test_warning_and_critical_exist(self):
        assert AnomalySeverity.WARNING is not None
        assert AnomalySeverity.CRITICAL is not None

    def test_label_property_returns_capitalized(self):
        assert AnomalySeverity.WARNING.label == "Warning"
        assert AnomalySeverity.CRITICAL.label == "Critical"

    def test_value_strings(self):
        assert AnomalySeverity.WARNING.value == "warning"
        assert AnomalySeverity.CRITICAL.value == "critical"


# ═══════════════════════════════════════════════════════════════════════════
# Anomaly
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomaly:
    def _sample(self) -> Anomaly:
        return Anomaly(
            kind=AnomalyKind.LATENCY_SPIKE,
            severity=AnomalySeverity.WARNING,
            metric_name="avg_latency_ms",
            observed=500.0,
            expected=150.0,
            std_dev=50.0,
            z_score=7.0,
            description="Latency spike detected",
        )

    def test_construction_with_all_fields(self):
        a = self._sample()
        assert a.kind == AnomalyKind.LATENCY_SPIKE
        assert a.severity == AnomalySeverity.WARNING
        assert a.metric_name == "avg_latency_ms"
        assert a.observed == 500.0
        assert a.expected == 150.0
        assert a.std_dev == 50.0
        assert a.z_score == 7.0

    def test_to_dict_returns_correct_keys(self):
        d = self._sample().to_dict()
        expected_keys = {
            "kind", "severity", "metric_name",
            "observed", "expected", "std_dev", "z_score", "description",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_rounds_values(self):
        a = Anomaly(
            kind=AnomalyKind.TOKEN_SURGE,
            severity=AnomalySeverity.CRITICAL,
            metric_name="total_tokens",
            observed=1234.56789,
            expected=500.12345,
            std_dev=100.99999,
            z_score=7.27777,
            description="test",
        )
        d = a.to_dict()
        assert d["observed"] == 1234.5679
        assert d["expected"] == 500.1234
        assert d["std_dev"] == 101.0
        assert d["z_score"] == 7.28

    def test_description_is_non_empty(self):
        a = self._sample()
        assert len(a.description) > 0


# ═══════════════════════════════════════════════════════════════════════════
# MetricBaseline
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricBaseline:
    def _sample(self) -> MetricBaseline:
        return MetricBaseline(
            name="avg_latency_ms",
            mean=100.0,
            std_dev=20.0,
            min_val=60.0,
            max_val=140.0,
            sample_count=10,
        )

    def test_construction_and_fields(self):
        b = self._sample()
        assert b.name == "avg_latency_ms"
        assert b.mean == 100.0
        assert b.std_dev == 20.0
        assert b.sample_count == 10

    def test_z_score_calculation(self):
        b = self._sample()
        # (140 - 100) / 20 = 2.0
        assert b.z_score(140.0) == pytest.approx(2.0)
        # (60 - 100) / 20 = -2.0
        assert b.z_score(60.0) == pytest.approx(-2.0)

    def test_z_score_zero_std_dev_matching_mean(self):
        b = MetricBaseline("x", mean=5.0, std_dev=0.0, min_val=5.0, max_val=5.0, sample_count=3)
        assert b.z_score(5.0) == 0.0

    def test_z_score_zero_std_dev_different_value(self):
        b = MetricBaseline("x", mean=5.0, std_dev=0.0, min_val=5.0, max_val=5.0, sample_count=3)
        assert b.z_score(10.0) == float("inf")

    def test_coefficient_of_variation(self):
        b = self._sample()
        # 20 / 100 = 0.2
        assert b.coefficient_of_variation == pytest.approx(0.2)

    def test_coefficient_of_variation_zero_mean(self):
        b = MetricBaseline("x", mean=0.0, std_dev=1.0, min_val=0.0, max_val=0.0, sample_count=3)
        assert b.coefficient_of_variation == 0.0

    def test_to_dict_includes_cv(self):
        d = self._sample().to_dict()
        assert "cv" in d
        assert d["cv"] == pytest.approx(0.2)
        assert "sample_count" in d


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetectorConfig
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorConfig:
    def test_defaults(self):
        cfg = AnomalyDetectorConfig()
        assert cfg.warning_threshold == 2.0
        assert cfg.critical_threshold == 3.0
        assert cfg.min_samples == 3

    def test_custom_thresholds(self):
        cfg = AnomalyDetectorConfig(warning_threshold=1.5, critical_threshold=2.5, min_samples=5)
        assert cfg.warning_threshold == 1.5
        assert cfg.critical_threshold == 2.5
        assert cfg.min_samples == 5

    def test_all_checks_enabled_by_default(self):
        cfg = AnomalyDetectorConfig()
        assert cfg.check_latency is True
        assert cfg.check_tokens is True
        assert cfg.check_errors is True
        assert cfg.check_event_count is True
        assert cfg.check_tool_failures is True


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — basics
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorBasics:
    def test_construction_with_default_config(self):
        d = AnomalyDetector()
        assert d.config.warning_threshold == 2.0

    def test_construction_with_custom_config(self):
        cfg = AnomalyDetectorConfig(warning_threshold=1.0)
        d = AnomalyDetector(config=cfg)
        assert d.config.warning_threshold == 1.0

    def test_sample_count_starts_at_zero(self):
        d = AnomalyDetector()
        assert d.sample_count == 0

    def test_has_baseline_false_initially(self):
        d = AnomalyDetector()
        assert d.has_baseline is False

    def test_metric_names_empty_initially(self):
        d = AnomalyDetector()
        assert d.metric_names == []


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — add_sample
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorAddSample:
    def test_add_sample_increments_sample_count(self):
        d = AnomalyDetector()
        d.add_sample({"x": 1.0})
        assert d.sample_count == 1

    def test_add_sample_with_multiple_metrics(self):
        d = AnomalyDetector()
        d.add_sample({"x": 1.0, "y": 2.0})
        assert d.sample_count == 1
        assert "x" in d.metric_names
        assert "y" in d.metric_names

    def test_add_sample_ignores_non_numeric_values(self):
        d = AnomalyDetector()
        d.add_sample({"x": 1.0, "bad": "not_a_number", "z": None})
        assert d.metric_names == ["x"]

    def test_has_baseline_true_after_min_samples(self):
        d = AnomalyDetector()
        for i in range(3):
            d.add_sample({"x": float(i)})
        assert d.has_baseline is True

    def test_metric_names_populated_after_adding(self):
        d = AnomalyDetector()
        d.add_sample({"alpha": 1.0, "beta": 2.0})
        assert d.metric_names == ["alpha", "beta"]


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — get_baseline
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorGetBaseline:
    def test_returns_none_with_insufficient_samples(self):
        d = AnomalyDetector()
        d.add_sample({"x": 1.0})
        assert d.get_baseline("x") is None

    def test_returns_metric_baseline_with_enough_samples(self):
        d = AnomalyDetector()
        for v in [10.0, 20.0, 30.0]:
            d.add_sample({"x": v})
        b = d.get_baseline("x")
        assert isinstance(b, MetricBaseline)

    def test_mean_is_correct(self):
        d = AnomalyDetector()
        for v in [10.0, 20.0, 30.0]:
            d.add_sample({"x": v})
        b = d.get_baseline("x")
        assert b.mean == pytest.approx(20.0)

    def test_std_dev_is_correct_sample(self):
        d = AnomalyDetector()
        for v in [10.0, 20.0, 30.0]:
            d.add_sample({"x": v})
        b = d.get_baseline("x")
        # sample std dev of [10, 20, 30] = sqrt(200/2) = 10.0
        expected_std = math.sqrt(200.0 / 2.0)
        assert b.std_dev == pytest.approx(expected_std)

    def test_min_max_are_correct(self):
        d = AnomalyDetector()
        for v in [10.0, 20.0, 30.0]:
            d.add_sample({"x": v})
        b = d.get_baseline("x")
        assert b.min_val == 10.0
        assert b.max_val == 30.0


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — extract_metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorExtractMetrics:
    def test_extracts_from_session_with_events(self):
        s = _make_session(events=[_make_event(), _make_event()])
        m = AnomalyDetector.extract_metrics(s)
        assert "avg_latency_ms" in m
        assert "event_count" in m
        assert m["event_count"] == 2.0

    def test_empty_session_returns_zeros(self):
        s = _make_session(events=[])
        m = AnomalyDetector.extract_metrics(s)
        assert m["avg_latency_ms"] == 0.0
        assert m["error_rate"] == 0.0
        assert m["event_count"] == 0.0

    def test_avg_latency_calculated_correctly(self):
        events = [
            _make_event(duration_ms=100.0),
            _make_event(duration_ms=200.0),
            _make_event(duration_ms=300.0),
        ]
        s = _make_session(events=events)
        m = AnomalyDetector.extract_metrics(s)
        assert m["avg_latency_ms"] == pytest.approx(200.0)

    def test_error_rate_calculated_correctly(self):
        events = [
            _make_event(),
            _make_event(),
            _make_error_event(),
            _make_event(),
        ]
        s = _make_session(events=events)
        m = AnomalyDetector.extract_metrics(s)
        assert m["error_rate"] == pytest.approx(0.25)

    def test_tokens_per_event_calculated_correctly(self):
        events = [
            _make_event(tokens_in=100, tokens_out=50),
            _make_event(tokens_in=200, tokens_out=100),
        ]
        s = _make_session(events=events)
        m = AnomalyDetector.extract_metrics(s)
        # total = 450, per event = 225
        assert m["tokens_per_event"] == pytest.approx(225.0)

    def test_tool_failure_rate_calculated_correctly(self):
        events = [
            _make_tool_event(error=False),
            _make_tool_event(error=False),
            _make_tool_event(error=True),
            _make_event(),  # non-tool event
        ]
        s = _make_session(events=events)
        m = AnomalyDetector.extract_metrics(s)
        # 1 tool error out of 3 tool events (tool_error counts as tool event via "tool" in type)
        assert m["tool_failure_rate"] == pytest.approx(1.0 / 3.0)


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — analyze_metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorAnalyzeMetrics:
    def test_raises_value_error_with_insufficient_baseline(self):
        d = AnomalyDetector()
        with pytest.raises(ValueError, match="Need at least"):
            d.analyze_metrics({"x": 1.0})

    def test_no_anomalies_for_normal_metrics(self):
        # Use varied baseline so std_dev > 0
        samples = [
            {"avg_latency_ms": v, "error_rate": v / 10000}
            for v in [140.0, 145.0, 150.0, 155.0, 160.0]
        ]
        d = _build_baseline_detector(samples=samples)
        report = d.analyze_metrics({
            "avg_latency_ms": 152.0,
            "error_rate": 0.0150,
        })
        assert report.has_anomalies is False
        assert report.anomaly_count == 0

    def test_detects_latency_spike(self):
        # Build baseline with known stats: mean=100, all same → std=0
        # Use varied values so std > 0
        samples = [{"avg_latency_ms": v} for v in [100.0, 100.0, 100.0, 100.0, 120.0]]
        d = _build_baseline_detector(samples=samples)
        baseline = d.get_baseline("avg_latency_ms")
        # Push value well above baseline
        spike_value = baseline.mean + baseline.std_dev * 3.5
        report = d.analyze_metrics({"avg_latency_ms": spike_value})
        assert report.has_anomalies is True
        assert any(a.kind == AnomalyKind.LATENCY_SPIKE for a in report.anomalies)

    def test_detects_token_surge(self):
        samples = [{"total_tokens": v} for v in [500.0, 500.0, 500.0, 500.0, 550.0]]
        d = _build_baseline_detector(samples=samples)
        baseline = d.get_baseline("total_tokens")
        spike_value = baseline.mean + baseline.std_dev * 3.5
        report = d.analyze_metrics({"total_tokens": spike_value})
        assert any(a.kind == AnomalyKind.TOKEN_SURGE for a in report.anomalies)

    def test_detects_error_burst(self):
        samples = [{"error_rate": v} for v in [0.01, 0.02, 0.01, 0.02, 0.03]]
        d = _build_baseline_detector(samples=samples)
        baseline = d.get_baseline("error_rate")
        spike_value = baseline.mean + baseline.std_dev * 3.5
        report = d.analyze_metrics({"error_rate": spike_value})
        assert any(a.kind == AnomalyKind.ERROR_BURST for a in report.anomalies)

    def test_detects_event_flood(self):
        samples = [{"event_count": v} for v in [10.0, 10.0, 10.0, 12.0, 11.0]]
        d = _build_baseline_detector(samples=samples)
        baseline = d.get_baseline("event_count")
        spike_value = baseline.mean + baseline.std_dev * 3.5
        report = d.analyze_metrics({"event_count": spike_value})
        assert any(a.kind == AnomalyKind.EVENT_FLOOD for a in report.anomalies)

    def test_detects_event_drought(self):
        samples = [{"event_count": v} for v in [10.0, 10.0, 10.0, 12.0, 11.0]]
        d = _build_baseline_detector(samples=samples)
        baseline = d.get_baseline("event_count")
        drought_value = baseline.mean - baseline.std_dev * 3.5
        report = d.analyze_metrics({"event_count": drought_value})
        assert any(a.kind == AnomalyKind.EVENT_DROUGHT for a in report.anomalies)

    def test_critical_severity_for_z_above_3(self):
        samples = [{"avg_latency_ms": v} for v in [100.0, 100.0, 100.0, 100.0, 120.0]]
        d = _build_baseline_detector(samples=samples)
        baseline = d.get_baseline("avg_latency_ms")
        # z-score = 4.0 should be critical
        spike_value = baseline.mean + baseline.std_dev * 4.0
        report = d.analyze_metrics({"avg_latency_ms": spike_value})
        assert report.has_anomalies
        assert any(a.severity == AnomalySeverity.CRITICAL for a in report.anomalies)


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — analyze (Session objects)
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorAnalyze:
    def _baseline_detector(self) -> AnomalyDetector:
        d = AnomalyDetector()
        for _ in range(5):
            s = _make_session(events=[
                _make_event(duration_ms=150.0, tokens_in=100, tokens_out=50),
                _make_event(duration_ms=160.0, tokens_in=110, tokens_out=60),
            ])
            d.add_session(s)
        return d

    def test_works_with_session_objects(self):
        d = self._baseline_detector()
        s = _make_session(
            session_id="check-1",
            events=[_make_event(duration_ms=155.0)],
        )
        report = d.analyze(s)
        assert isinstance(report, AnomalyReport)

    def test_raises_value_error_without_baseline(self):
        d = AnomalyDetector()
        s = _make_session(events=[_make_event()])
        with pytest.raises(ValueError, match="Need at least"):
            d.analyze(s)

    def test_report_contains_correct_session_id(self):
        d = self._baseline_detector()
        s = _make_session(
            session_id="my-session",
            events=[_make_event(duration_ms=155.0)],
        )
        report = d.analyze(s)
        assert report.session_id == "my-session"


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyReport
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyReport:
    def _anomaly(
        self,
        kind: AnomalyKind = AnomalyKind.LATENCY_SPIKE,
        severity: AnomalySeverity = AnomalySeverity.WARNING,
    ) -> Anomaly:
        return Anomaly(
            kind=kind,
            severity=severity,
            metric_name="test",
            observed=100.0,
            expected=50.0,
            std_dev=10.0,
            z_score=5.0,
            description="test anomaly",
        )

    def test_anomaly_count(self):
        r = AnomalyReport(session_id="s1", anomalies=[self._anomaly(), self._anomaly()])
        assert r.anomaly_count == 2

    def test_has_anomalies_true(self):
        r = AnomalyReport(session_id="s1", anomalies=[self._anomaly()])
        assert r.has_anomalies is True

    def test_has_anomalies_false(self):
        r = AnomalyReport(session_id="s1")
        assert r.has_anomalies is False

    def test_max_severity_returns_highest(self):
        r = AnomalyReport(
            session_id="s1",
            anomalies=[
                self._anomaly(severity=AnomalySeverity.WARNING),
                self._anomaly(severity=AnomalySeverity.CRITICAL),
            ],
        )
        assert r.max_severity == AnomalySeverity.CRITICAL

    def test_max_severity_none_when_empty(self):
        r = AnomalyReport(session_id="s1")
        assert r.max_severity is None

    def test_by_kind_grouping(self):
        r = AnomalyReport(
            session_id="s1",
            anomalies=[
                self._anomaly(kind=AnomalyKind.LATENCY_SPIKE),
                self._anomaly(kind=AnomalyKind.TOKEN_SURGE),
                self._anomaly(kind=AnomalyKind.LATENCY_SPIKE),
            ],
        )
        groups = r.by_kind
        assert len(groups[AnomalyKind.LATENCY_SPIKE]) == 2
        assert len(groups[AnomalyKind.TOKEN_SURGE]) == 1

    def test_by_severity_grouping(self):
        r = AnomalyReport(
            session_id="s1",
            anomalies=[
                self._anomaly(severity=AnomalySeverity.WARNING),
                self._anomaly(severity=AnomalySeverity.CRITICAL),
                self._anomaly(severity=AnomalySeverity.WARNING),
            ],
        )
        groups = r.by_severity
        assert len(groups[AnomalySeverity.WARNING]) == 2
        assert len(groups[AnomalySeverity.CRITICAL]) == 1

    def test_summary_string_format_no_anomalies(self):
        r = AnomalyReport(session_id="s1")
        assert "no anomalies detected" in r.summary

    def test_summary_string_format_with_anomalies(self):
        r = AnomalyReport(
            session_id="s1",
            anomalies=[self._anomaly()],
        )
        assert "s1" in r.summary
        assert "1 anomalie(s)" in r.summary

    def test_critical_count_and_warning_count(self):
        r = AnomalyReport(
            session_id="s1",
            anomalies=[
                self._anomaly(severity=AnomalySeverity.CRITICAL),
                self._anomaly(severity=AnomalySeverity.WARNING),
                self._anomaly(severity=AnomalySeverity.WARNING),
            ],
        )
        assert r.critical_count == 1
        assert r.warning_count == 2

    def test_to_dict_serialization(self):
        r = AnomalyReport(
            session_id="s1",
            anomalies=[self._anomaly()],
        )
        d = r.to_dict()
        assert d["session_id"] == "s1"
        assert d["anomaly_count"] == 1
        assert d["has_anomalies"] is True
        assert isinstance(d["anomalies"], list)
        assert "summary" in d
        assert d["max_severity"] == "warning"


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — config checks
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorConfigChecks:
    def _spike_detector(self, **config_kwargs) -> tuple[AnomalyDetector, float]:
        """Return (detector, spike_value) with latency that will trigger."""
        cfg = AnomalyDetectorConfig(**config_kwargs)
        samples = [{"avg_latency_ms": v, "total_tokens": v * 3, "error_rate": v / 10000}
                    for v in [100.0, 100.0, 100.0, 100.0, 120.0]]
        d = _build_baseline_detector(samples=samples, config=cfg)
        b = d.get_baseline("avg_latency_ms")
        return d, b.mean + b.std_dev * 4.0

    def test_disabling_check_latency_skips_latency_anomalies(self):
        d, spike = self._spike_detector(check_latency=False)
        report = d.analyze_metrics({"avg_latency_ms": spike})
        assert not any(a.kind == AnomalyKind.LATENCY_SPIKE for a in report.anomalies)

    def test_disabling_check_tokens_skips_token_anomalies(self):
        cfg = AnomalyDetectorConfig(check_tokens=False)
        samples = [{"total_tokens": v} for v in [500.0, 500.0, 500.0, 500.0, 550.0]]
        d = _build_baseline_detector(samples=samples, config=cfg)
        b = d.get_baseline("total_tokens")
        spike = b.mean + b.std_dev * 4.0
        report = d.analyze_metrics({"total_tokens": spike})
        assert not any(a.kind == AnomalyKind.TOKEN_SURGE for a in report.anomalies)

    def test_disabling_check_errors_skips_error_anomalies(self):
        cfg = AnomalyDetectorConfig(check_errors=False)
        samples = [{"error_rate": v} for v in [0.01, 0.02, 0.01, 0.02, 0.03]]
        d = _build_baseline_detector(samples=samples, config=cfg)
        b = d.get_baseline("error_rate")
        spike = b.mean + b.std_dev * 4.0
        report = d.analyze_metrics({"error_rate": spike})
        assert not any(a.kind == AnomalyKind.ERROR_BURST for a in report.anomalies)

    def test_custom_thresholds_change_sensitivity(self):
        # With warning_threshold=1.0, even small deviations trigger
        cfg = AnomalyDetectorConfig(warning_threshold=1.0, critical_threshold=1.5)
        samples = [{"avg_latency_ms": v} for v in [100.0, 100.0, 100.0, 100.0, 120.0]]
        d = _build_baseline_detector(samples=samples, config=cfg)
        b = d.get_baseline("avg_latency_ms")
        # 1.2 sigma should trigger with threshold=1.0
        mild_spike = b.mean + b.std_dev * 1.2
        report = d.analyze_metrics({"avg_latency_ms": mild_spike})
        assert report.has_anomalies is True


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — reset
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorReset:
    def test_reset_clears_all_data(self):
        d = AnomalyDetector()
        for _ in range(5):
            d.add_sample({"x": 1.0})
        assert d.sample_count == 5
        d.reset()
        assert d.sample_count == 0
        assert d.metric_names == []

    def test_has_baseline_false_after_reset(self):
        d = AnomalyDetector()
        for _ in range(5):
            d.add_sample({"x": 1.0})
        assert d.has_baseline is True
        d.reset()
        assert d.has_baseline is False


# ═══════════════════════════════════════════════════════════════════════════
# AnomalyDetector — add_session
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorAddSession:
    def test_add_session_extracts_and_adds_metrics(self):
        d = AnomalyDetector()
        s = _make_session(events=[_make_event(), _make_event()])
        d.add_session(s)
        assert d.sample_count == 1
        assert "avg_latency_ms" in d.metric_names

    def test_add_session_with_multiple_sessions_builds_baseline(self):
        d = AnomalyDetector()
        for _ in range(3):
            s = _make_session(events=[_make_event(), _make_event()])
            d.add_session(s)
        assert d.has_baseline is True
        assert d.sample_count == 3
