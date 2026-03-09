"""Tests for the A/B Test Analyzer module."""

import math
import pytest
from agentlens.ab_test import (
    ABTestAnalyzer,
    Experiment,
    ExperimentStatus,
    Variant,
    Observation,
    TestResult,
    ExperimentReport,
    EffectSize,
    SignificanceLevel,
    required_sample_size,
    _welch_t_test,
    _mann_whitney_u,
    _cohens_d,
    _interpret_effect_size,
    _normal_cdf,
    _normal_quantile,
)


# ── Variant basics ──────────────────────────────────────────────

class TestVariant:
    def test_empty_variant(self):
        v = Variant(name="test")
        assert v.count("x") == 0
        assert v.mean("x") == 0.0
        assert v.variance("x") == 0.0
        assert v.std("x") == 0.0
        assert v.metrics() == []

    def test_single_observation(self):
        v = Variant(name="a")
        v.observations.append(Observation(metric="latency", value=100))
        assert v.count("latency") == 1
        assert v.mean("latency") == 100.0
        assert v.variance("latency") == 0.0  # need ≥2 for sample var

    def test_multiple_observations(self):
        v = Variant(name="a")
        for val in [10, 20, 30]:
            v.observations.append(Observation(metric="score", value=val))
        assert v.count("score") == 3
        assert v.mean("score") == 20.0
        assert abs(v.variance("score") - 100.0) < 1e-10
        assert abs(v.std("score") - 10.0) < 1e-10

    def test_multiple_metrics(self):
        v = Variant(name="a")
        v.observations.append(Observation(metric="x", value=1))
        v.observations.append(Observation(metric="y", value=2))
        assert sorted(v.metrics()) == ["x", "y"]
        assert v.values("x") == [1.0]
        assert v.values("y") == [2.0]


# ── Experiment lifecycle ────────────────────────────────────────

class TestExperiment:
    def test_create(self):
        exp = Experiment(name="test")
        assert exp.status == ExperimentStatus.DRAFT
        assert exp.total_observations() == 0

    def test_add_variants(self):
        exp = Experiment(name="test")
        exp.add_variant("a", is_control=True)
        exp.add_variant("b")
        assert len(exp.variants) == 2
        assert exp.control().name == "a"

    def test_duplicate_variant_raises(self):
        exp = Experiment(name="test")
        exp.add_variant("a")
        with pytest.raises(ValueError):
            exp.add_variant("a")

    def test_record_auto_starts(self):
        exp = Experiment(name="test")
        exp.add_variant("a")
        exp.add_variant("b")
        assert exp.status == ExperimentStatus.DRAFT
        exp.record("a", "x", 1.0)
        assert exp.status == ExperimentStatus.RUNNING

    def test_record_unknown_variant(self):
        exp = Experiment(name="test")
        with pytest.raises(KeyError):
            exp.record("ghost", "x", 1.0)

    def test_record_stopped_raises(self):
        exp = Experiment(name="test")
        exp.add_variant("a")
        exp.stop()
        with pytest.raises(RuntimeError):
            exp.record("a", "x", 1.0)

    def test_lifecycle_transitions(self):
        exp = Experiment(name="test")
        exp.start()
        assert exp.status == ExperimentStatus.RUNNING
        exp.stop()
        assert exp.status == ExperimentStatus.STOPPED
        exp.conclude()
        assert exp.status == ExperimentStatus.CONCLUDED

    def test_metrics(self):
        exp = Experiment(name="test")
        exp.add_variant("a")
        exp.record("a", "latency", 100)
        exp.record("a", "accuracy", 0.9)
        assert sorted(exp.metrics()) == ["accuracy", "latency"]

    def test_to_dict(self):
        exp = Experiment(name="test", hypothesis="A is better")
        exp.add_variant("a")
        exp.add_variant("b")
        exp.record("a", "x", 10)
        d = exp.to_dict()
        assert d["name"] == "test"
        assert d["hypothesis"] == "A is better"
        assert "a" in d["variants"]
        assert d["total_observations"] == 1

    def test_no_control(self):
        exp = Experiment(name="test")
        exp.add_variant("a")
        assert exp.control() is None


# ── Statistical functions ───────────────────────────────────────

class TestStatistics:
    def test_welch_identical(self):
        vals = [10.0, 10.0, 10.0, 10.0, 10.0]
        t, df, p = _welch_t_test(vals, vals[:])
        assert abs(t) < 1e-10
        assert p > 0.9

    def test_welch_different(self):
        a = [100.0, 102.0, 98.0, 101.0, 99.0, 100.5, 101.5, 98.5, 99.5, 100.0]
        b = [50.0, 52.0, 48.0, 51.0, 49.0, 50.5, 51.5, 48.5, 49.5, 50.0]
        t, df, p = _welch_t_test(a, b)
        assert p < 0.001

    def test_welch_small_sample(self):
        t, df, p = _welch_t_test([1.0], [2.0])
        assert p == 1.0  # too few samples

    def test_mann_whitney_empty(self):
        u, p = _mann_whitney_u([], [1.0])
        assert p == 1.0

    def test_mann_whitney_different(self):
        a = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
        b = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        u, p = _mann_whitney_u(a, b)
        assert p < 0.01

    def test_cohens_d_zero(self):
        vals = [5.0, 5.0, 5.0]
        assert _cohens_d(vals, vals[:]) == 0.0

    def test_cohens_d_large(self):
        a = [98.0, 100.0, 102.0, 101.0]
        b = [1.0, 0.0, 2.0, -1.0]
        d = _cohens_d(a, b)
        assert d > 1.0

    def test_effect_size_interpretation(self):
        assert _interpret_effect_size(0.1) == EffectSize.NEGLIGIBLE
        assert _interpret_effect_size(0.3) == EffectSize.SMALL
        assert _interpret_effect_size(0.6) == EffectSize.MEDIUM
        assert _interpret_effect_size(0.9) == EffectSize.LARGE
        assert _interpret_effect_size(1.5) == EffectSize.VERY_LARGE

    def test_normal_cdf_center(self):
        assert abs(_normal_cdf(0) - 0.5) < 1e-10

    def test_normal_cdf_tails(self):
        assert _normal_cdf(-3) < 0.01
        assert _normal_cdf(3) > 0.99

    def test_normal_quantile_roundtrip(self):
        for p in [0.1, 0.25, 0.5, 0.75, 0.9]:
            z = _normal_quantile(p)
            p_back = _normal_cdf(z)
            assert abs(p - p_back) < 0.01


# ── Sample size estimation ──────────────────────────────────────

class TestSampleSize:
    def test_medium_effect(self):
        n = required_sample_size(effect_size=0.5, alpha=0.05, power=0.80)
        assert 50 < n < 100  # ~64 per group

    def test_small_effect_needs_more(self):
        n_small = required_sample_size(effect_size=0.2)
        n_large = required_sample_size(effect_size=0.8)
        assert n_small > n_large

    def test_zero_effect(self):
        assert required_sample_size(effect_size=0) == 0

    def test_minimum_sample(self):
        n = required_sample_size(effect_size=10.0)
        assert n >= 2


# ── ABTestAnalyzer ──────────────────────────────────────────────

class TestABTestAnalyzer:
    def _make_analyzer_with_data(self):
        analyzer = ABTestAnalyzer()
        exp = analyzer.create_experiment("test", hypothesis="A is faster")
        exp.add_variant("A", is_control=False)
        exp.add_variant("B", is_control=True)
        import random
        random.seed(42)
        for _ in range(50):
            exp.record("A", "latency", random.gauss(100, 10))
            exp.record("B", "latency", random.gauss(120, 10))
        return analyzer

    def test_create_experiment(self):
        analyzer = ABTestAnalyzer()
        exp = analyzer.create_experiment("exp1")
        assert exp.name == "exp1"
        assert "exp1" in analyzer.experiments

    def test_duplicate_experiment_raises(self):
        analyzer = ABTestAnalyzer()
        analyzer.create_experiment("exp1")
        with pytest.raises(ValueError):
            analyzer.create_experiment("exp1")

    def test_get_experiment(self):
        analyzer = ABTestAnalyzer()
        analyzer.create_experiment("exp1")
        exp = analyzer.get_experiment("exp1")
        assert exp.name == "exp1"

    def test_get_unknown_raises(self):
        analyzer = ABTestAnalyzer()
        with pytest.raises(KeyError):
            analyzer.get_experiment("nope")

    def test_list_experiments(self):
        analyzer = ABTestAnalyzer()
        analyzer.create_experiment("a")
        analyzer.create_experiment("b")
        assert len(analyzer.list_experiments()) == 2

    def test_list_by_status(self):
        analyzer = ABTestAnalyzer()
        e1 = analyzer.create_experiment("a")
        e2 = analyzer.create_experiment("b")
        e2.start()
        assert len(analyzer.list_experiments(status=ExperimentStatus.DRAFT)) == 1
        assert len(analyzer.list_experiments(status=ExperimentStatus.RUNNING)) == 1

    def test_delete_experiment(self):
        analyzer = ABTestAnalyzer()
        analyzer.create_experiment("a")
        analyzer.delete_experiment("a")
        assert len(analyzer.experiments) == 0

    def test_delete_unknown_raises(self):
        analyzer = ABTestAnalyzer()
        with pytest.raises(KeyError):
            analyzer.delete_experiment("nope")

    def test_analyze_significant(self):
        analyzer = self._make_analyzer_with_data()
        result = analyzer.analyze("test", "latency")
        assert result.significant
        assert result.winner is not None
        assert result.p_value < 0.05
        assert result.cohens_d != 0

    def test_analyze_confidence_interval(self):
        analyzer = self._make_analyzer_with_data()
        result = analyzer.analyze("test", "latency")
        lo, hi = result.confidence_interval
        assert lo < hi

    def test_analyze_mann_whitney(self):
        analyzer = self._make_analyzer_with_data()
        result = analyzer.analyze("test", "latency", test="mann_whitney")
        assert result.p_value < 0.05

    def test_analyze_auto_selects_control(self):
        analyzer = self._make_analyzer_with_data()
        result = analyzer.analyze("test", "latency")
        assert result.variant_b == "B"  # control

    def test_analyze_too_few_raises(self):
        analyzer = ABTestAnalyzer()
        exp = analyzer.create_experiment("small")
        exp.add_variant("a")
        exp.add_variant("b")
        exp.record("a", "x", 1)
        exp.record("b", "x", 2)
        with pytest.raises(ValueError, match="≥2"):
            analyzer.analyze("small", "x")

    def test_analyze_too_few_variants_raises(self):
        analyzer = ABTestAnalyzer()
        exp = analyzer.create_experiment("solo")
        exp.add_variant("a")
        with pytest.raises(ValueError, match="at least 2"):
            analyzer.analyze("solo", "x")

    def test_analyze_all(self):
        analyzer = self._make_analyzer_with_data()
        report = analyzer.analyze_all("test")
        assert isinstance(report, ExperimentReport)
        assert len(report.results) >= 1
        assert report.overall_winner is not None

    def test_analyze_all_recommendations(self):
        analyzer = self._make_analyzer_with_data()
        report = analyzer.analyze_all("test")
        assert len(report.recommendations) > 0

    def test_export(self):
        analyzer = self._make_analyzer_with_data()
        data = analyzer.export_experiments()
        assert "test" in data
        assert data["test"]["total_observations"] == 100


# ── TestResult display ──────────────────────────────────────────

class TestResultDisplay:
    def _make_result(self, significant=True):
        return TestResult(
            experiment="exp",
            metric="latency",
            variant_a="A",
            variant_b="B",
            mean_a=100.0,
            mean_b=120.0,
            std_a=10.0,
            std_b=10.0,
            n_a=50,
            n_b=50,
            t_statistic=-5.0,
            p_value=0.001 if significant else 0.5,
            significant=significant,
            alpha=0.05,
            cohens_d=-2.0,
            effect_size=EffectSize.VERY_LARGE if significant else EffectSize.NEGLIGIBLE,
            winner="B" if significant else None,
            improvement_pct=-16.67,
            power=None,
            confidence_interval=(-24.0, -16.0),
        )

    def test_to_dict(self):
        r = self._make_result()
        d = r.to_dict()
        assert d["experiment"] == "exp"
        assert d["significant"] is True
        assert isinstance(d["confidence_interval"], tuple)

    def test_summary_significant(self):
        r = self._make_result(significant=True)
        s = r.summary()
        assert "Winner" in s
        assert "B" in s

    def test_summary_not_significant(self):
        r = self._make_result(significant=False)
        s = r.summary()
        assert "No significant" in s


# ── ExperimentReport display ────────────────────────────────────

class TestExperimentReport:
    def test_report_summary(self):
        report = ExperimentReport(
            experiment="test",
            hypothesis="A is better",
            status="running",
            total_observations=100,
            variants=["A", "B"],
            metrics=["latency"],
            results=[],
            recommendations=["Collect more data"],
            overall_winner="A",
        )
        s = report.summary()
        assert "test" in s
        assert "A is better" in s
        assert "Overall Winner: A" in s

    def test_report_to_dict(self):
        report = ExperimentReport(
            experiment="test",
            hypothesis="",
            status="concluded",
            total_observations=50,
            variants=["A", "B"],
            metrics=["x"],
            results=[],
            recommendations=[],
            overall_winner=None,
        )
        d = report.to_dict()
        assert d["experiment"] == "test"
        assert d["overall_winner"] is None


# ── Integration: non-significant results ────────────────────────

class TestNonSignificant:
    def test_similar_distributions(self):
        analyzer = ABTestAnalyzer()
        exp = analyzer.create_experiment("flat")
        exp.add_variant("a")
        exp.add_variant("b")
        import random
        random.seed(99)
        for _ in range(30):
            exp.record("a", "score", random.gauss(50, 10))
            exp.record("b", "score", random.gauss(50, 10))
        result = analyzer.analyze("flat", "score")
        # With identical distributions, should usually not be significant
        # (though random chance could occasionally make it so)
        assert result.p_value > 0.001  # at least not wildly significant


# ── Edge cases ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_observation_metadata(self):
        obs = Observation(metric="x", value=1.0, metadata={"model": "gpt-4"})
        assert obs.metadata["model"] == "gpt-4"

    def test_significance_levels(self):
        assert SignificanceLevel.STANDARD.value == 0.05
        assert SignificanceLevel.STRICT.value == 0.01

    def test_custom_alpha(self):
        analyzer = ABTestAnalyzer(default_alpha=0.01)
        assert analyzer.default_alpha == 0.01

    def test_tags(self):
        exp = Experiment(name="test", tags=["prod", "latency"])
        assert "prod" in exp.tags

    def test_variant_names(self):
        exp = Experiment(name="test")
        exp.add_variant("a")
        exp.add_variant("b")
        assert exp.variant_names() == ["a", "b"]
