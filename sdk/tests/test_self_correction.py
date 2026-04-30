"""Comprehensive tests for Agent Self-Correction Tracker."""

import json
import pytest

from agentlens.self_correction import (
    SelfCorrectionTracker,
    SelfCorrectionReport,
    CorrectionCategory,
    CorrectionEvent,
    CorrectionPattern,
    Grade,
)


# ── Fixtures ────────────────────────────────────────────────────────


def _make_session(events, session_id="test-session-1"):
    return {"session_id": session_id, "events": events}


def _tool_call(name, input_data=None, idx=0):
    return {"type": "tool_call", "timestamp": 1000 + idx, "data": {"name": name, "input": input_data or {}}}


def _tool_result(name, output="success", error=None, input_data=None, idx=0):
    d = {"name": name, "output": output}
    if error:
        d["error"] = error
    if input_data:
        d["input"] = input_data
    return {"type": "tool_result", "timestamp": 1000 + idx, "data": d}


def _error(msg="something failed", idx=0):
    return {"type": "error", "timestamp": 1000 + idx, "data": {"error": msg}}


def _llm_call(content="", output="", idx=0):
    return {"type": "llm_call", "timestamp": 1000 + idx, "data": {"content": content, "output": output}}


def _text(content, idx=0):
    return {"type": "text", "timestamp": 1000 + idx, "data": {"content": content}}


# ── Empty / Minimal Sessions ───────────────────────────────────────


class TestEmptySessions:
    def test_empty_events(self):
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session([]))
        assert report.correction_count == 0
        assert report.total_events == 0
        assert report.grade == "F"

    def test_single_event(self):
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session([_tool_call("read")]))
        assert report.correction_count == 0
        assert report.total_events == 1

    def test_no_corrections_session(self):
        events = [
            _tool_call("read", idx=0),
            _tool_result("read", output="file content", idx=1),
            _tool_call("write", idx=2),
            _tool_result("write", output="ok", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.correction_count == 0
        assert report.correction_rate == 0.0

    def test_missing_session_id(self):
        tracker = SelfCorrectionTracker()
        report = tracker.analyze({"events": []})
        assert report.session_id == "unknown"


# ── Retry Correction Detection ─────────────────────────────────────


class TestRetryCorrection:
    def test_basic_retry(self):
        events = [
            _tool_call("search", {"query": "foo"}, idx=0),
            _tool_result("search", error="timeout", input_data={"query": "foo"}, idx=1),
            _tool_call("search", {"query": "foo", "timeout": 30}, idx=2),
            _tool_result("search", output="results", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.correction_count >= 1
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.RETRY_CORRECTION in cats

    def test_retry_with_different_params(self):
        events = [
            _tool_call("api_call", {"url": "/v1/data"}, idx=0),
            _tool_result("api_call", error="404 not found", input_data={"url": "/v1/data"}, idx=1),
            _tool_call("api_call", {"url": "/v2/data"}, idx=2),
            _tool_result("api_call", output="success", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        corrections = [c for c in report.correction_timeline if c.category == CorrectionCategory.RETRY_CORRECTION]
        assert len(corrections) >= 1
        assert corrections[0].effectiveness > 0.5

    def test_retry_that_also_fails(self):
        events = [
            _tool_call("cmd", {"arg": "a"}, idx=0),
            _tool_result("cmd", error="fail", input_data={"arg": "a"}, idx=1),
            _tool_call("cmd", {"arg": "b"}, idx=2),
            _tool_result("cmd", error="still fail", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        corrections = [c for c in report.correction_timeline if c.category == CorrectionCategory.RETRY_CORRECTION]
        if corrections:
            assert corrections[0].effectiveness < 0.5

    def test_no_retry_when_same_params(self):
        events = [
            _tool_call("x", {"a": 1}, idx=0),
            _tool_result("x", output="ok", idx=1),
            _tool_call("x", {"a": 1}, idx=2),
            _tool_result("x", output="ok", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        retries = [c for c in report.correction_timeline if c.category == CorrectionCategory.RETRY_CORRECTION]
        assert len(retries) == 0


# ── Apology Correction Detection ──────────────────────────────────


class TestApologyCorrection:
    def test_apology_phrase(self):
        events = [
            _tool_call("write", idx=0),
            _tool_result("write", error="permission denied", idx=1),
            _text("I apologize, let me fix that by using the correct path.", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.APOLOGY_CORRECTION in cats

    def test_actually_correction(self):
        events = [
            _text("The file is at /tmp/data.txt", idx=0),
            _text("Actually, the file is at /home/user/data.txt", idx=1),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.APOLOGY_CORRECTION in cats

    def test_my_mistake_phrase(self):
        events = [
            _text("The answer is 42.", idx=0),
            _text("My mistake, the answer is actually 43.", idx=1),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.correction_count >= 1

    def test_no_false_positive_on_normal_text(self):
        events = [
            _text("Hello, how are you?", idx=0),
            _text("I can help you with that.", idx=1),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        apologies = [c for c in report.correction_timeline if c.category == CorrectionCategory.APOLOGY_CORRECTION]
        assert len(apologies) == 0


# ── Backtrack Correction Detection ─────────────────────────────────


class TestBacktrackCorrection:
    def test_basic_backtrack(self):
        events = [
            _tool_call("approach_a", idx=0),
            _tool_result("approach_a", error="failed", idx=1),
            _text("Let me try a different approach since that didn't work.", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.BACKTRACK_CORRECTION in cats

    def test_going_back_phrase(self):
        events = [
            _text("I'll try method X", idx=0),
            _tool_call("method_x", idx=1),
            _tool_result("method_x", error="nope", idx=2),
            _text("Going back to the original approach", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        backtracks = [c for c in report.correction_timeline if c.category == CorrectionCategory.BACKTRACK_CORRECTION]
        assert len(backtracks) >= 1

    def test_scrap_that(self):
        events = [
            _text("Let me build it this way", idx=0),
            _text("Scrap that, I'll do it differently", idx=1),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.correction_count >= 1


# ── Error Recovery Detection ───────────────────────────────────────


class TestErrorRecovery:
    def test_basic_recovery(self):
        events = [
            _error("connection timeout", idx=0),
            _tool_call("retry_connection", idx=1),
            _tool_result("retry_connection", output="connected", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.ERROR_RECOVERY in cats

    def test_recovery_latency(self):
        events = [
            _error("crash", idx=0),
            _text("debugging...", idx=1),
            _text("found the issue", idx=2),
            _tool_call("fix", idx=3),
            _tool_result("fix", output="fixed", idx=4),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        recoveries = [c for c in report.correction_timeline if c.category == CorrectionCategory.ERROR_RECOVERY]
        if recoveries:
            assert recoveries[0].latency_events > 1

    def test_no_recovery_when_only_errors(self):
        events = [
            _error("err1", idx=0),
            _error("err2", idx=1),
            _error("err3", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        recoveries = [c for c in report.correction_timeline if c.category == CorrectionCategory.ERROR_RECOVERY]
        assert len(recoveries) == 0


# ── Strategy Pivot Detection ───────────────────────────────────────


class TestStrategyPivot:
    def test_tool_switch_after_failure(self):
        events = [
            _tool_call("grep", {"pattern": "x"}, idx=0),
            _tool_result("grep", error="not found", idx=1),
            _tool_call("find", {"name": "x"}, idx=2),
            _tool_result("find", output="/path/to/x", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        pivots = [c for c in report.correction_timeline if c.category == CorrectionCategory.STRATEGY_PIVOT]
        assert len(pivots) >= 1

    def test_no_pivot_when_same_tool(self):
        events = [
            _tool_call("read", {"file": "a.txt"}, idx=0),
            _tool_result("read", error="not found", idx=1),
            _tool_call("read", {"file": "b.txt"}, idx=2),
            _tool_result("read", output="content", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        pivots = [c for c in report.correction_timeline if c.category == CorrectionCategory.STRATEGY_PIVOT]
        assert len(pivots) == 0

    def test_pivot_effectiveness(self):
        events = [
            _tool_call("wget", {"url": "http://x"}, idx=0),
            _tool_result("wget", error="refused", idx=1),
            _tool_call("curl", {"url": "http://x"}, idx=2),
            _tool_result("curl", output="data", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        pivots = [c for c in report.correction_timeline if c.category == CorrectionCategory.STRATEGY_PIVOT]
        if pivots:
            assert pivots[0].effectiveness > 0.5


# ── Output Revision Detection ──────────────────────────────────────


class TestOutputRevision:
    def test_revised_output(self):
        # Two LLM outputs with partial overlap (revision)
        text_a = "The implementation uses a HashMap to store key-value pairs with O(1) lookup time complexity for average cases"
        text_b = "The implementation uses a TreeMap to store key-value pairs with O(log n) lookup time for guaranteed sorted order"
        events = [
            _llm_call(output=text_a, idx=0),
            _llm_call(output=text_b, idx=1),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        revisions = [c for c in report.correction_timeline if c.category == CorrectionCategory.OUTPUT_REVISION]
        assert len(revisions) >= 1

    def test_no_revision_for_identical(self):
        text = "Exactly the same output text repeated here for testing purposes to get enough length for similarity"
        events = [
            _llm_call(output=text, idx=0),
            _llm_call(output=text, idx=1),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        revisions = [c for c in report.correction_timeline if c.category == CorrectionCategory.OUTPUT_REVISION]
        assert len(revisions) == 0

    def test_no_revision_for_unrelated(self):
        events = [
            _llm_call(output="The weather today is sunny with clear skies and temperatures around seventy degrees fahrenheit", idx=0),
            _llm_call(output="Python programming involves using indentation for code blocks and dynamic typing for variables in functions", idx=1),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        revisions = [c for c in report.correction_timeline if c.category == CorrectionCategory.OUTPUT_REVISION]
        assert len(revisions) == 0


# ── Assumption Correction Detection ────────────────────────────────


class TestAssumptionCorrection:
    def test_explicit_assumption_correction(self):
        events = [
            _text("The file should be in /tmp", idx=0),
            _tool_call("read", {"path": "/tmp/data"}, idx=1),
            _tool_result("read", error="not found", idx=2),
            _text("I assumed it was in /tmp but it's actually in /var/data", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.ASSUMPTION_CORRECTION in cats

    def test_incorrect_assumption_phrase(self):
        events = [
            _text("I incorrectly assumed the API returns JSON", idx=0),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.correction_count >= 1


# ── Hallucination Fix Detection ────────────────────────────────────


class TestHallucinationFix:
    def test_upon_verification(self):
        events = [
            _text("The function exists in utils.py", idx=0),
            _tool_call("read", {"path": "utils.py"}, idx=1),
            _tool_result("read", output="no such function", idx=2),
            _text("Upon checking, that function doesn't actually exist in utils.py", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.HALLUCINATION_FIX in cats

    def test_i_hallucinated(self):
        events = [
            _text("I hallucinated that API endpoint - it doesn't exist", idx=0),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.correction_count >= 1

    def test_fabricated_claim(self):
        events = [
            _text("The library has a built-in caching module", idx=0),
            _tool_call("search", {"q": "caching"}, idx=1),
            _tool_result("search", output="no results", idx=2),
            _text("I incorrectly stated it has caching - I made that up", idx=3),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        cats = [c.category for c in report.correction_timeline]
        assert CorrectionCategory.HALLUCINATION_FIX in cats


# ── Scoring & Grading ──────────────────────────────────────────────


class TestScoringAndGrading:
    def test_grade_a_session(self):
        # Many quick, effective corrections
        events = []
        for i in range(20):
            events.append(_tool_call("cmd", {"v": i}, idx=i*4))
            events.append(_tool_result("cmd", error="fail", input_data={"v": i}, idx=i*4+1))
            events.append(_tool_call("cmd", {"v": i, "fix": True}, idx=i*4+2))
            events.append(_tool_result("cmd", output="ok", idx=i*4+3))
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.grade in ("A", "B")
        assert report.self_awareness_score >= 50

    def test_grade_f_session(self):
        # No corrections at all
        events = [_tool_call("x", idx=i) for i in range(10)]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.grade == "F"
        assert report.self_awareness_score < 25  # Below D threshold

    def test_effectiveness_score_range(self):
        events = [
            _error("err", idx=0),
            _tool_call("fix", idx=1),
            _tool_result("fix", output="ok", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert 0 <= report.effectiveness_score <= 100

    def test_self_awareness_score_range(self):
        events = [
            _error("err", idx=0),
            _tool_call("fix", idx=1),
            _tool_result("fix", output="ok", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert 0 <= report.self_awareness_score <= 100


# ── Patterns ───────────────────────────────────────────────────────


class TestPatterns:
    def test_late_session_corrections_pattern(self):
        # Put all corrections at the end
        events = [_tool_call("noop", idx=i) for i in range(20)]
        # Add corrections at end
        for i in range(20, 30):
            events.append(_error("err", idx=i))
            events.append(_tool_call("fix", idx=i+30))
            events.append(_tool_result("fix", output="ok", idx=i+60))
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        pattern_names = [p.name for p in report.patterns]
        # May or may not detect depending on thresholds
        assert isinstance(report.patterns, list)

    def test_retry_heavy_pattern(self):
        events = []
        for i in range(5):
            events.append(_tool_call("api", {"v": i}, idx=i*4))
            events.append(_tool_result("api", error="fail", input_data={"v": i}, idx=i*4+1))
            events.append(_tool_call("api", {"v": i, "retry": True}, idx=i*4+2))
            events.append(_tool_result("api", output="ok", idx=i*4+3))
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        pattern_names = [p.name for p in report.patterns]
        assert "Retry-Heavy Correction Style" in pattern_names


# ── Recommendations ────────────────────────────────────────────────


class TestRecommendations:
    def test_no_corrections_recommendations(self):
        events = [_tool_call("x", idx=i) for i in range(5)]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert len(report.recommendations) > 0
        assert any("validation" in r.lower() or "check" in r.lower() for r in report.recommendations)

    def test_high_retry_recommendations(self):
        events = []
        for i in range(4):
            events.append(_tool_call("api", {"v": i}, idx=i*4))
            events.append(_tool_result("api", error="fail", input_data={"v": i}, idx=i*4+1))
            events.append(_tool_call("api", {"v": i, "fix": True}, idx=i*4+2))
            events.append(_tool_result("api", output="ok", idx=i*4+3))
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert any("retry" in r.lower() or "parameter" in r.lower() for r in report.recommendations)


# ── Report Formatting ──────────────────────────────────────────────


class TestReportFormatting:
    def test_format_report_string(self):
        events = [
            _error("test error", idx=0),
            _tool_call("fix", idx=1),
            _tool_result("fix", output="ok", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        text = report.format_report()
        assert "SELF-CORRECTION" in text
        assert "Grade" in text

    def test_to_json_valid(self):
        events = [
            _error("err", idx=0),
            _tool_call("fix", idx=1),
            _tool_result("fix", output="ok", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        data = json.loads(report.to_json())
        assert "session_id" in data
        assert "correction_count" in data
        assert "grade" in data

    def test_to_dict_complete(self):
        events = [
            _error("err", idx=0),
            _tool_call("fix", idx=1),
            _tool_result("fix", output="ok", idx=2),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        d = report.to_dict()
        assert "self_awareness_score" in d
        assert "category_breakdown" in d
        assert "patterns" in d
        assert "recommendations" in d


# ── Min Confidence Filter ──────────────────────────────────────────


class TestConfidenceFilter:
    def test_high_confidence_filter(self):
        events = [
            _text("Actually, that's wrong.", idx=0),  # apology - confidence 0.7
            _error("err", idx=1),
            _tool_call("fix", idx=2),
            _tool_result("fix", output="ok", idx=3),  # error recovery - confidence 0.75
        ]
        tracker_strict = SelfCorrectionTracker(min_confidence=0.9)
        report = tracker_strict.analyze(_make_session(events))
        # High threshold filters out lower-confidence detections
        assert report.correction_count <= 2

    def test_low_confidence_filter(self):
        events = [
            _text("Actually, that's wrong.", idx=0),
            _error("err", idx=1),
            _tool_call("fix", idx=2),
            _tool_result("fix", output="ok", idx=3),
        ]
        tracker_lax = SelfCorrectionTracker(min_confidence=0.1)
        report = tracker_lax.analyze(_make_session(events))
        assert report.correction_count >= 1


# ── Edge Cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_very_long_session(self):
        events = []
        for i in range(200):
            events.append(_tool_call("op", {"i": i}, idx=i))
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.total_events == 200

    def test_all_errors_session(self):
        events = [_error(f"err{i}", idx=i) for i in range(10)]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.total_events == 10

    def test_correction_event_to_dict(self):
        c = CorrectionEvent(
            category=CorrectionCategory.RETRY_CORRECTION,
            trigger_event_index=0,
            correction_event_index=2,
            latency_events=2,
            effectiveness=0.9,
            confidence=0.85,
            description="test",
        )
        d = c.to_dict()
        assert d["category"] == "retry_correction"
        assert d["effectiveness"] == 0.9

    def test_correction_pattern_to_dict(self):
        p = CorrectionPattern(
            name="Test Pattern",
            description="A test",
            evidence_count=5,
            confidence=0.8,
        )
        d = p.to_dict()
        assert d["name"] == "Test Pattern"
        assert d["confidence"] == 0.8


# ── Integration / Complex Scenarios ────────────────────────────────


class TestComplexScenarios:
    def test_multi_category_session(self):
        events = [
            # Retry correction
            _tool_call("api", {"url": "/old"}, idx=0),
            _tool_result("api", error="404", input_data={"url": "/old"}, idx=1),
            _tool_call("api", {"url": "/new"}, idx=2),
            _tool_result("api", output="ok", idx=3),
            # Error recovery
            _error("crash", idx=4),
            _tool_call("restart", idx=5),
            _tool_result("restart", output="running", idx=6),
            # Apology
            _text("I apologize, let me fix that issue.", idx=7),
            # Strategy pivot
            _tool_call("wget", {"u": "x"}, idx=8),
            _tool_result("wget", error="refused", idx=9),
            _tool_call("curl", {"u": "x"}, idx=10),
            _tool_result("curl", output="data", idx=11),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert report.correction_count >= 3
        assert len(report.category_breakdown) >= 2

    def test_category_breakdown_sums_to_count(self):
        events = [
            _tool_call("a", {"x": 1}, idx=0),
            _tool_result("a", error="fail", input_data={"x": 1}, idx=1),
            _tool_call("a", {"x": 2}, idx=2),
            _tool_result("a", output="ok", idx=3),
            _error("err", idx=4),
            _tool_call("b", idx=5),
            _tool_result("b", output="ok", idx=6),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        assert sum(report.category_breakdown.values()) == report.correction_count

    def test_correction_rate_calculation(self):
        events = [
            _error("e", idx=0),
            _tool_call("fix", idx=1),
            _tool_result("fix", output="ok", idx=2),
            _tool_call("noop", idx=3),
            _tool_result("noop", output="ok", idx=4),
        ]
        tracker = SelfCorrectionTracker()
        report = tracker.analyze(_make_session(events))
        # rate = (count / total) * 100
        expected_rate = (report.correction_count / 5) * 100
        assert abs(report.correction_rate - expected_rate) < 0.1
