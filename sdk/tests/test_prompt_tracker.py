"""Tests for PromptVersionTracker."""

import json
import pytest
from agentlens.prompt_tracker import (
    PromptVersionTracker,
    PromptVersion,
    Outcome,
    VersionStats,
    PromptDiff,
    PromptReport,
    DiffKind,
    _hash_template,
)


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

class TestRegistration:
    def test_register_first_version(self):
        t = PromptVersionTracker()
        v = t.register("test", "Hello {name}")
        assert v.prompt_name == "test"
        assert v.version_number == 1
        assert v.parent_version is None
        assert v.tags == []

    def test_register_increments_version(self):
        t = PromptVersionTracker()
        v1 = t.register("test", "v1 template")
        v2 = t.register("test", "v2 template")
        assert v2.version_number == 2
        assert v2.parent_version == v1.version_id

    def test_dedup_same_content(self):
        t = PromptVersionTracker()
        v1 = t.register("test", "same")
        v2 = t.register("test", "same")
        assert v1.version_id == v2.version_id
        assert len(t.get_versions("test")) == 1

    def test_dedup_disabled(self):
        t = PromptVersionTracker(dedup=False)
        t.register("test", "same")
        t.register("test", "same")
        assert len(t.get_versions("test")) == 2

    def test_register_with_tags_and_metadata(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl", tags=["v1", "prod"], metadata={"author": "alice"})
        assert v.tags == ["v1", "prod"]
        assert v.metadata == {"author": "alice"}

    def test_register_empty_name_raises(self):
        t = PromptVersionTracker()
        with pytest.raises(ValueError, match="prompt_name"):
            t.register("", "template")

    def test_register_empty_template_raises(self):
        t = PromptVersionTracker()
        with pytest.raises(ValueError, match="template"):
            t.register("test", "")

    def test_content_hash_deterministic(self):
        h1 = _hash_template("hello world")
        h2 = _hash_template("hello world")
        assert h1 == h2
        assert len(h1) == 12

    def test_different_prompts_independent(self):
        t = PromptVersionTracker()
        t.register("a", "template a")
        t.register("b", "template b")
        assert len(t.get_versions("a")) == 1
        assert len(t.get_versions("b")) == 1


# ------------------------------------------------------------------
# Outcome recording
# ------------------------------------------------------------------

class TestOutcomes:
    def test_record_outcome(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl")
        o = t.record_outcome(v.version_id, tokens=100, latency_ms=500, quality_score=0.9)
        assert o.tokens == 100
        assert o.quality_score == 0.9

    def test_record_unknown_version_raises(self):
        t = PromptVersionTracker()
        with pytest.raises(KeyError):
            t.record_outcome("nonexistent", tokens=10)

    def test_quality_out_of_range_raises(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl")
        with pytest.raises(ValueError, match="quality_score"):
            t.record_outcome(v.version_id, quality_score=1.5)

    def test_quality_negative_raises(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl")
        with pytest.raises(ValueError, match="quality_score"):
            t.record_outcome(v.version_id, quality_score=-0.1)

    def test_get_outcomes(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl")
        t.record_outcome(v.version_id, tokens=10)
        t.record_outcome(v.version_id, tokens=20)
        assert len(t.get_outcomes(v.version_id)) == 2

    def test_outcome_metadata(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl")
        o = t.record_outcome(v.version_id, metadata={"model": "gpt-4"})
        assert o.metadata == {"model": "gpt-4"}


# ------------------------------------------------------------------
# Querying
# ------------------------------------------------------------------

class TestQuerying:
    def test_get_latest(self):
        t = PromptVersionTracker()
        t.register("test", "v1")
        t.register("test", "v2")
        latest = t.get_latest("test")
        assert latest is not None
        assert latest.version_number == 2

    def test_get_latest_empty(self):
        t = PromptVersionTracker()
        assert t.get_latest("nonexistent") is None

    def test_get_version_by_id(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl")
        found = t.get_version(v.version_id)
        assert found.version_id == v.version_id

    def test_get_version_unknown_raises(self):
        t = PromptVersionTracker()
        with pytest.raises(KeyError):
            t.get_version("bad_id")

    def test_list_prompts(self):
        t = PromptVersionTracker()
        t.register("beta", "b")
        t.register("alpha", "a")
        assert t.list_prompts() == ["alpha", "beta"]

    def test_search_by_tag(self):
        t = PromptVersionTracker()
        t.register("a", "t1", tags=["prod"])
        t.register("a", "t2", tags=["dev"])
        t.register("b", "t3", tags=["prod"])
        results = t.search_by_tag("prod")
        assert len(results) == 2


# ------------------------------------------------------------------
# Diff
# ------------------------------------------------------------------

class TestDiff:
    def test_basic_diff(self):
        t = PromptVersionTracker()
        t.register("test", "line one\nline two")
        t.register("test", "line one\nline THREE")
        d = t.diff("test", 1, 2)
        assert d.kind == DiffKind.MODIFIED
        assert "THREE" in d.unified_diff
        assert d.char_delta != 0

    def test_unchanged_diff(self):
        t = PromptVersionTracker(dedup=False)
        t.register("test", "same")
        t.register("test", "same")
        d = t.diff("test", 1, 2)
        assert d.kind == DiffKind.UNCHANGED

    def test_diff_unknown_prompt_raises(self):
        t = PromptVersionTracker()
        with pytest.raises(KeyError):
            t.diff("nope", 1, 2)

    def test_diff_unknown_version_raises(self):
        t = PromptVersionTracker()
        t.register("test", "v1")
        with pytest.raises(KeyError):
            t.diff("test", 1, 99)

    def test_diff_line_delta(self):
        t = PromptVersionTracker()
        t.register("test", "a")
        t.register("test", "a\nb\nc")
        d = t.diff("test", 1, 2)
        assert d.line_delta == 2


# ------------------------------------------------------------------
# Report
# ------------------------------------------------------------------

class TestReport:
    def test_report_basic(self):
        t = PromptVersionTracker()
        v1 = t.register("test", "v1")
        v2 = t.register("test", "v2")
        t.record_outcome(v1.version_id, tokens=100, latency_ms=500, quality_score=0.7)
        t.record_outcome(v2.version_id, tokens=80, latency_ms=400, quality_score=0.9)
        r = t.report("test")
        assert r.total_versions == 2
        assert r.total_runs == 2
        assert r.best_version is not None
        assert r.best_version.version_number == 2  # higher quality

    def test_report_best_by_latency_no_quality(self):
        t = PromptVersionTracker()
        v1 = t.register("test", "v1")
        v2 = t.register("test", "v2")
        t.record_outcome(v1.version_id, latency_ms=500)
        t.record_outcome(v2.version_id, latency_ms=200)
        r = t.report("test")
        assert r.best_version is not None
        assert r.best_version.version_number == 2  # lower latency

    def test_report_quality_trend(self):
        t = PromptVersionTracker()
        v1 = t.register("test", "v1")
        v2 = t.register("test", "v2")
        t.record_outcome(v1.version_id, quality_score=0.5, latency_ms=100)
        t.record_outcome(v2.version_id, quality_score=0.8, latency_ms=90)
        r = t.report("test")
        assert len(r.quality_trend) == 2
        assert r.quality_trend[0]["avg_quality"] == 0.5

    def test_report_unknown_raises(self):
        t = PromptVersionTracker()
        with pytest.raises(KeyError):
            t.report("nonexistent")

    def test_report_no_outcomes(self):
        t = PromptVersionTracker()
        t.register("test", "v1")
        r = t.report("test")
        assert r.total_runs == 0
        assert r.best_version is None

    def test_version_stats_percentiles(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl")
        for lat in [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]:
            t.record_outcome(v.version_id, latency_ms=lat)
        r = t.report("test")
        s = r.version_stats[0]
        assert s.p50_latency_ms > 0
        assert s.p95_latency_ms >= s.p50_latency_ms

    def test_report_to_dict(self):
        t = PromptVersionTracker()
        v = t.register("test", "v1")
        t.record_outcome(v.version_id, tokens=50, latency_ms=100, quality_score=0.8)
        r = t.report("test")
        d = r.to_dict()
        assert d["prompt_name"] == "test"
        assert isinstance(d["version_stats"], list)
        # Ensure JSON-serializable
        json.dumps(d)


# ------------------------------------------------------------------
# Rollback
# ------------------------------------------------------------------

class TestRollback:
    def test_rollback_creates_new_version(self):
        t = PromptVersionTracker()
        t.register("test", "original")
        t.register("test", "modified")
        v3 = t.rollback("test", 1)
        assert v3.version_number == 3
        assert v3.template == "original"
        assert "rollback" in v3.tags

    def test_rollback_unknown_prompt_raises(self):
        t = PromptVersionTracker()
        with pytest.raises(KeyError):
            t.rollback("nope", 1)

    def test_rollback_unknown_version_raises(self):
        t = PromptVersionTracker()
        t.register("test", "v1")
        with pytest.raises(KeyError):
            t.rollback("test", 99)


# ------------------------------------------------------------------
# Export / Import
# ------------------------------------------------------------------

class TestExportImport:
    def test_roundtrip(self):
        t = PromptVersionTracker()
        v = t.register("test", "tmpl", tags=["a"])
        t.record_outcome(v.version_id, tokens=50, quality_score=0.8)
        data = t.export_json()

        t2 = PromptVersionTracker()
        count = t2.import_json(data)
        assert count == 1
        assert len(t2.get_versions("test")) == 1
        assert len(t2.get_outcomes(t2.get_versions("test")[0].version_id)) == 1

    def test_export_json_serializable(self):
        t = PromptVersionTracker()
        t.register("a", "template a")
        t.register("b", "template b")
        data = t.export_json()
        json.dumps(data)  # should not raise

    def test_import_empty(self):
        t = PromptVersionTracker()
        count = t.import_json({})
        assert count == 0


# ------------------------------------------------------------------
# Serialization
# ------------------------------------------------------------------

class TestSerialization:
    def test_version_to_dict(self):
        t = PromptVersionTracker()
        v = t.register("test", "hello", tags=["x"])
        d = v.to_dict()
        assert d["prompt_name"] == "test"
        assert d["template"] == "hello"
        json.dumps(d)

    def test_outcome_to_dict(self):
        t = PromptVersionTracker()
        v = t.register("test", "hello")
        o = t.record_outcome(v.version_id, tokens=10, quality_score=0.5)
        d = o.to_dict()
        assert d["tokens"] == 10
        json.dumps(d)

    def test_diff_to_dict(self):
        t = PromptVersionTracker()
        t.register("test", "a")
        t.register("test", "b")
        d = t.diff("test", 1, 2).to_dict()
        assert d["kind"] == "modified"
        json.dumps(d)
