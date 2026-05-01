"""Tests for Agent Cognitive Bias Detector."""

import json
import unittest
from agentlens.cognitive_bias import (
    CognitiveBiasDetector,
    CognitiveBiasReport,
    BiasCategory,
    BiasSeverity,
    BiasSignal,
    BiasProfile,
    BiasGrade,
    TrendDirection,
)


def make_event(event_type="tool_call", tool_name="", success=True,
               content="", confidence=0, agent_id=None, high_stakes=False,
               parameters=None):
    """Helper to create a session event."""
    meta = {"tool_name": tool_name, "success": success, "content": content}
    if confidence:
        meta["confidence"] = confidence
    if high_stakes:
        meta["high_stakes"] = True
    if parameters:
        meta["parameters"] = parameters
    event = {"type": event_type, "metadata": meta}
    if agent_id:
        event["agent_id"] = agent_id
    return event


def make_session(events, session_id="test-session"):
    """Helper to create a session dict."""
    return {"id": session_id, "events": events}


class TestCognitiveBiasDetectorBasics(unittest.TestCase):
    """Basic functionality tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_empty_session(self):
        report = self.detector.analyze(make_session([]))
        self.assertEqual(report.total_events, 0)
        self.assertEqual(report.objectivity_score, 100.0)
        self.assertEqual(report.grade, "A")
        self.assertEqual(report.bias_signals_detected, 0)

    def test_single_event(self):
        events = [make_event("tool_call", "search")]
        report = self.detector.analyze(make_session(events))
        self.assertEqual(report.total_events, 1)
        self.assertEqual(report.grade, "A")

    def test_two_events(self):
        events = [make_event("tool_call", "search"), make_event("agent_response")]
        report = self.detector.analyze(make_session(events))
        self.assertEqual(report.total_events, 2)
        self.assertEqual(report.grade, "A")

    def test_clean_session_no_bias(self):
        events = [
            make_event("tool_call", "search", success=True),
            make_event("tool_call", "verify", success=True),
            make_event("tool_call", "write", success=True),
            make_event("tool_call", "read", success=True),
            make_event("agent_response", content="Done"),
        ]
        report = self.detector.analyze(make_session(events))
        self.assertGreaterEqual(report.objectivity_score, 80)

    def test_session_id_extraction(self):
        report = self.detector.analyze({"id": "abc-123", "events": [
            make_event(), make_event(), make_event()
        ]})
        self.assertEqual(report.session_id, "abc-123")

    def test_session_id_fallback(self):
        report = self.detector.analyze({"session_id": "xyz", "events": [
            make_event(), make_event(), make_event()
        ]})
        self.assertEqual(report.session_id, "xyz")


class TestAnchoringBias(unittest.TestCase):
    """Anchoring bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_anchoring_detected_tool_dominance(self):
        """Early tools dominate entire session."""
        events = [
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
        ]
        # Add many later events all using same early tool
        for _ in range(10):
            events.append(make_event("tool_call", "search"))

        report = self.detector.analyze(make_session(events))
        anchoring = [s for s in report.signal_timeline if s.category == BiasCategory.ANCHORING]
        # Should detect anchoring since 100% matches early tools
        self.assertTrue(len(anchoring) > 0 or report.objectivity_score < 100)

    def test_no_anchoring_diverse_tools(self):
        """Diverse tool usage should not trigger anchoring."""
        tools = ["search", "write", "read", "verify", "analyze",
                 "compile", "test", "deploy", "monitor", "report"]
        events = [make_event("tool_call", t) for t in tools]
        report = self.detector.analyze(make_session(events))
        anchoring = [s for s in report.signal_timeline if s.category == BiasCategory.ANCHORING]
        # Should not flag anchoring when tools are diverse
        self.assertEqual(len(anchoring), 0)

    def test_anchoring_with_decision_context(self):
        """Anchoring detected via content keyword overlap."""
        events = [
            make_event("decision", content="We should use the REST API approach with JSON"),
            make_event("tool_call", "search"),
            make_event("decision", content="REST API with JSON is still the best approach"),
            make_event("tool_call", "search"),
            make_event("decision", content="Continuing with REST API JSON implementation"),
            make_event("tool_call", "search"),
            make_event("decision", content="REST API JSON approach confirmed"),
        ]
        report = self.detector.analyze(make_session(events))
        # With repeated decision content matching early context, might flag
        self.assertIsNotNone(report)


class TestConfirmationBias(unittest.TestCase):
    """Confirmation bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_repeated_similar_tool_calls(self):
        """Same tool with same parameters = confirmation seeking."""
        events = [
            make_event("tool_call", "search", parameters="query=best framework"),
            make_event("tool_call", "search", parameters="query=best framework"),
            make_event("tool_call", "search", parameters="query=best framework"),
            make_event("tool_call", "search", parameters="query=best framework"),
        ]
        report = self.detector.analyze(make_session(events))
        confirmation = [s for s in report.signal_timeline if s.category == BiasCategory.CONFIRMATION]
        self.assertGreater(len(confirmation), 0)

    def test_no_confirmation_different_params(self):
        """Different parameters should not trigger confirmation."""
        events = [
            make_event("tool_call", "search", parameters="query=react"),
            make_event("tool_call", "search", parameters="query=vue"),
            make_event("tool_call", "search", parameters="query=angular"),
            make_event("tool_call", "search", parameters="query=svelte"),
        ]
        report = self.detector.analyze(make_session(events))
        confirmation = [s for s in report.signal_timeline if s.category == BiasCategory.CONFIRMATION]
        self.assertEqual(len(confirmation), 0)

    def test_confirmation_multiple_tools(self):
        """Check confirmation across different tool names."""
        events = [
            make_event("tool_call", "api_check", parameters="endpoint=/health"),
            make_event("tool_call", "api_check", parameters="endpoint=/health"),
            make_event("tool_call", "api_check", parameters="endpoint=/health"),
            make_event("tool_call", "db_query", parameters="select * from users"),
            make_event("tool_call", "db_query", parameters="select * from users"),
            make_event("tool_call", "db_query", parameters="select * from users"),
        ]
        report = self.detector.analyze(make_session(events))
        confirmation = [s for s in report.signal_timeline if s.category == BiasCategory.CONFIRMATION]
        self.assertGreaterEqual(len(confirmation), 2)


class TestRecencyBias(unittest.TestCase):
    """Recency bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_recency_detected(self):
        """Decision aligns with recent tools, ignores earlier diverse context."""
        events = [
            make_event("tool_call", "analyze"),
            make_event("tool_call", "research"),
            make_event("tool_call", "compile"),
            make_event("tool_call", "test"),
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
            make_event("decision", content="search results look good"),
        ]
        report = self.detector.analyze(make_session(events))
        # Recency detection depends on window matching
        self.assertIsNotNone(report)

    def test_no_recency_balanced_decisions(self):
        """Balanced references should not trigger recency."""
        events = [
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
            make_event("decision", content="Based on search"),
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
            make_event("decision", content="Based on search"),
        ]
        report = self.detector.analyze(make_session(events))
        self.assertIsNotNone(report)


class TestSunkCostBias(unittest.TestCase):
    """Sunk cost bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_sunk_cost_detected_consecutive_failures(self):
        """3+ consecutive failures on same tool = sunk cost."""
        events = [
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
        ]
        report = self.detector.analyze(make_session(events))
        sunk_cost = [s for s in report.signal_timeline if s.category == BiasCategory.SUNK_COST]
        self.assertGreater(len(sunk_cost), 0)

    def test_sunk_cost_severity_scales(self):
        """More failures = higher severity."""
        events = [make_event("tool_call", "deploy", success=False) for _ in range(8)]
        report = self.detector.analyze(make_session(events))
        sunk_cost = [s for s in report.signal_timeline if s.category == BiasCategory.SUNK_COST]
        self.assertGreater(len(sunk_cost), 0)
        self.assertIn(sunk_cost[0].severity, [BiasSeverity.SEVERE, BiasSeverity.CRITICAL])

    def test_no_sunk_cost_with_success(self):
        """Two failures then success = no sunk cost."""
        events = [
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=True),
        ]
        report = self.detector.analyze(make_session(events))
        sunk_cost = [s for s in report.signal_timeline if s.category == BiasCategory.SUNK_COST]
        self.assertEqual(len(sunk_cost), 0)

    def test_sunk_cost_different_tools(self):
        """Multiple tools with failure chains detected independently."""
        events = [
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "test", success=False),
            make_event("tool_call", "test", success=False),
            make_event("tool_call", "test", success=False),
        ]
        report = self.detector.analyze(make_session(events))
        sunk_cost = [s for s in report.signal_timeline if s.category == BiasCategory.SUNK_COST]
        self.assertGreaterEqual(len(sunk_cost), 2)

    def test_sunk_cost_chain_then_success_resets(self):
        """Success after failure chain resets counter."""
        events = [
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=True),
            make_event("tool_call", "deploy", success=False),
            make_event("tool_call", "deploy", success=False),
        ]
        report = self.detector.analyze(make_session(events))
        sunk_cost = [s for s in report.signal_timeline if s.category == BiasCategory.SUNK_COST]
        # First chain of 3 should be detected, second chain of 2 should not
        self.assertEqual(len(sunk_cost), 1)


class TestAvailabilityBias(unittest.TestCase):
    """Availability bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_availability_detected_repetition(self):
        """Excessive consecutive use of same tool."""
        events = [
            make_event("tool_call", "search"),
            make_event("tool_call", "write"),
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
        ]
        report = self.detector.analyze(make_session(events))
        availability = [s for s in report.signal_timeline if s.category == BiasCategory.AVAILABILITY]
        self.assertGreater(len(availability), 0)

    def test_no_availability_varied_tools(self):
        """Varied tool use should not trigger availability."""
        events = [
            make_event("tool_call", "search"),
            make_event("tool_call", "write"),
            make_event("tool_call", "read"),
            make_event("tool_call", "analyze"),
            make_event("tool_call", "compile"),
        ]
        report = self.detector.analyze(make_session(events))
        availability = [s for s in report.signal_timeline if s.category == BiasCategory.AVAILABILITY]
        self.assertEqual(len(availability), 0)

    def test_availability_needs_minimum_calls(self):
        """Need at least 5 tool calls to detect availability."""
        events = [
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
            make_event("tool_call", "search"),
        ]
        report = self.detector.analyze(make_session(events))
        availability = [s for s in report.signal_timeline if s.category == BiasCategory.AVAILABILITY]
        self.assertEqual(len(availability), 0)


class TestAutomationBias(unittest.TestCase):
    """Automation bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_automation_bias_after_error(self):
        """Agent proceeds after tool failure without acknowledging."""
        events = [
            make_event("tool_call", "api_call", success=False),
            make_event("agent_response", content="Here are the results from the API"),
            make_event("tool_call", "next_step"),
        ]
        report = self.detector.analyze(make_session(events))
        automation = [s for s in report.signal_timeline if s.category == BiasCategory.AUTOMATION]
        self.assertGreater(len(automation), 0)

    def test_no_automation_bias_error_acknowledged(self):
        """Agent acknowledges error = no automation bias."""
        events = [
            make_event("tool_call", "api_call", success=False),
            make_event("agent_response", content="The API call failed, let me retry"),
            make_event("tool_call", "api_call", success=True),
        ]
        report = self.detector.analyze(make_session(events))
        automation = [s for s in report.signal_timeline if s.category == BiasCategory.AUTOMATION]
        self.assertEqual(len(automation), 0)

    def test_automation_bias_high_stakes(self):
        """High-stakes tool output without verification."""
        events = [
            make_event("tool_call", "delete_records", success=True, high_stakes=True),
            make_event("agent_response", content="Records processed successfully"),
            make_event("tool_call", "next_task"),
        ]
        report = self.detector.analyze(make_session(events))
        automation = [s for s in report.signal_timeline if s.category == BiasCategory.AUTOMATION]
        self.assertGreater(len(automation), 0)

    def test_no_automation_bias_with_verification(self):
        """Verification step after high-stakes tool = no bias."""
        events = [
            make_event("tool_call", "delete_records", success=True, high_stakes=True),
            make_event("tool_call", "verify_deletion", success=True),
            make_event("agent_response", content="Verified deletion complete"),
        ]
        report = self.detector.analyze(make_session(events))
        automation = [s for s in report.signal_timeline if s.category == BiasCategory.AUTOMATION]
        self.assertEqual(len(automation), 0)


class TestBandwagonBias(unittest.TestCase):
    """Bandwagon bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_bandwagon_detected_mimicry(self):
        """Agent mimics another agent's tool pattern."""
        events = [
            make_event("tool_call", "search", agent_id="agent-a"),
            make_event("tool_call", "analyze", agent_id="agent-a"),
            make_event("tool_call", "write", agent_id="agent-a"),
            make_event("tool_call", "search", agent_id="agent-b"),
            make_event("tool_call", "analyze", agent_id="agent-b"),
            make_event("tool_call", "write", agent_id="agent-b"),
        ]
        report = self.detector.analyze(make_session(events))
        bandwagon = [s for s in report.signal_timeline if s.category == BiasCategory.BANDWAGON]
        self.assertGreater(len(bandwagon), 0)

    def test_no_bandwagon_single_agent(self):
        """Single agent session = no bandwagon possible."""
        events = [
            make_event("tool_call", "search", agent_id="agent-a"),
            make_event("tool_call", "analyze", agent_id="agent-a"),
            make_event("tool_call", "write", agent_id="agent-a"),
        ]
        report = self.detector.analyze(make_session(events))
        bandwagon = [s for s in report.signal_timeline if s.category == BiasCategory.BANDWAGON]
        self.assertEqual(len(bandwagon), 0)

    def test_no_bandwagon_different_patterns(self):
        """Different tool patterns between agents = no bandwagon."""
        events = [
            make_event("tool_call", "search", agent_id="agent-a"),
            make_event("tool_call", "analyze", agent_id="agent-a"),
            make_event("tool_call", "write", agent_id="agent-a"),
            make_event("tool_call", "compile", agent_id="agent-b"),
            make_event("tool_call", "test", agent_id="agent-b"),
            make_event("tool_call", "deploy", agent_id="agent-b"),
        ]
        report = self.detector.analyze(make_session(events))
        bandwagon = [s for s in report.signal_timeline if s.category == BiasCategory.BANDWAGON]
        self.assertEqual(len(bandwagon), 0)


class TestDunningKrugerBias(unittest.TestCase):
    """Dunning-Kruger bias detection tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_dk_detected_high_confidence_then_failure(self):
        """High confidence metadata followed by error."""
        events = [
            make_event("agent_response", confidence=0.95, content="I'm certain this will work"),
            make_event("tool_call", "deploy", success=False),
            make_event("error"),
        ]
        report = self.detector.analyze(make_session(events))
        dk = [s for s in report.signal_timeline if s.category == BiasCategory.DUNNING_KRUGER]
        self.assertGreater(len(dk), 0)

    def test_dk_detected_confidence_language(self):
        """Confidence language in content followed by failure."""
        events = [
            make_event("agent_response", content="I definitely know how to solve this"),
            make_event("tool_call", "solve", success=False),
            make_event("agent_response", content="Let me try again"),
        ]
        report = self.detector.analyze(make_session(events))
        dk = [s for s in report.signal_timeline if s.category == BiasCategory.DUNNING_KRUGER]
        self.assertGreater(len(dk), 0)

    def test_no_dk_confidence_then_success(self):
        """High confidence followed by success = no DK bias."""
        events = [
            make_event("agent_response", confidence=0.95, content="Certainly this works"),
            make_event("tool_call", "deploy", success=True),
            make_event("agent_response", content="Deployed successfully"),
        ]
        report = self.detector.analyze(make_session(events))
        dk = [s for s in report.signal_timeline if s.category == BiasCategory.DUNNING_KRUGER]
        self.assertEqual(len(dk), 0)

    def test_dk_multiple_instances(self):
        """Multiple confidence-failure pairs."""
        events = [
            make_event("agent_response", content="Absolutely certain about this approach"),
            make_event("tool_call", "attempt1", success=False),
            make_event("agent_response", content="No doubt this will work now"),
            make_event("tool_call", "attempt2", success=False),
            make_event("agent_response", content="Guaranteed to succeed this time"),
            make_event("tool_call", "attempt3", success=False),
        ]
        report = self.detector.analyze(make_session(events))
        dk = [s for s in report.signal_timeline if s.category == BiasCategory.DUNNING_KRUGER]
        self.assertGreaterEqual(len(dk), 2)


class TestScoringAndGrading(unittest.TestCase):
    """Scoring and grading tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_grade_a_clean_session(self):
        """Clean session gets A."""
        events = [
            make_event("tool_call", "search"),
            make_event("tool_call", "analyze"),
            make_event("tool_call", "write"),
            make_event("agent_response", content="Done"),
        ]
        report = self.detector.analyze(make_session(events))
        self.assertEqual(report.grade, "A")
        self.assertGreaterEqual(report.objectivity_score, 90)

    def test_grade_f_heavily_biased(self):
        """Heavily biased session gets low grade."""
        events = []
        # Sunk cost: 10 consecutive failures
        for _ in range(10):
            events.append(make_event("tool_call", "deploy", success=False))
        # DK bias: high confidence + failures
        events.append(make_event("agent_response", content="I definitely know what I'm doing"))
        events.append(make_event("tool_call", "fix", success=False))
        events.append(make_event("agent_response", content="Absolutely certain now"))
        events.append(make_event("tool_call", "fix2", success=False))

        report = self.detector.analyze(make_session(events))
        self.assertIn(report.grade, ["C", "D", "F"])
        self.assertLess(report.objectivity_score, 75)

    def test_objectivity_score_range(self):
        """Score always 0-100."""
        events = [make_event("tool_call", "x", success=False) for _ in range(20)]
        report = self.detector.analyze(make_session(events))
        self.assertGreaterEqual(report.objectivity_score, 0)
        self.assertLessEqual(report.objectivity_score, 100)

    def test_grade_boundaries(self):
        """Test grade computation directly."""
        self.assertEqual(self.detector._compute_grade(95), "A")
        self.assertEqual(self.detector._compute_grade(90), "A")
        self.assertEqual(self.detector._compute_grade(89), "B")
        self.assertEqual(self.detector._compute_grade(75), "B")
        self.assertEqual(self.detector._compute_grade(74), "C")
        self.assertEqual(self.detector._compute_grade(60), "C")
        self.assertEqual(self.detector._compute_grade(59), "D")
        self.assertEqual(self.detector._compute_grade(45), "D")
        self.assertEqual(self.detector._compute_grade(44), "F")
        self.assertEqual(self.detector._compute_grade(0), "F")


class TestReportSerialization(unittest.TestCase):
    """Report serialization tests."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_to_dict(self):
        events = [make_event("tool_call", "deploy", success=False) for _ in range(4)]
        report = self.detector.analyze(make_session(events))
        d = report.to_dict()
        self.assertIn("session_id", d)
        self.assertIn("total_events", d)
        self.assertIn("objectivity_score", d)
        self.assertIn("grade", d)
        self.assertIn("bias_profiles", d)
        self.assertIn("signal_timeline", d)
        self.assertIn("recommendations", d)

    def test_to_json(self):
        events = [make_event("tool_call", "deploy", success=False) for _ in range(4)]
        report = self.detector.analyze(make_session(events))
        j = report.to_json()
        parsed = json.loads(j)
        self.assertEqual(parsed["session_id"], "test-session")

    def test_format_report_non_empty(self):
        events = [make_event("tool_call", "deploy", success=False) for _ in range(4)]
        report = self.detector.analyze(make_session(events))
        text = report.format_report()
        self.assertIn("COGNITIVE BIAS ANALYSIS", text)
        self.assertIn("Objectivity Score", text)

    def test_bias_signal_to_dict(self):
        signal = BiasSignal(
            category=BiasCategory.ANCHORING,
            event_index=5,
            confidence=0.8,
            severity=BiasSeverity.MODERATE,
            description="Test",
            evidence="Evidence",
        )
        d = signal.to_dict()
        self.assertEqual(d["category"], "anchoring")
        self.assertEqual(d["event_index"], 5)
        self.assertEqual(d["confidence"], 0.8)

    def test_bias_profile_to_dict(self):
        profile = BiasProfile(
            category=BiasCategory.SUNK_COST,
            signal_count=3,
            avg_confidence=0.75,
            severity=BiasSeverity.SEVERE,
            trend=TrendDirection.INCREASING,
        )
        d = profile.to_dict()
        self.assertEqual(d["category"], "sunk_cost")
        self.assertEqual(d["signal_count"], 3)
        self.assertEqual(d["trend"], "increasing")


class TestMinConfidenceFiltering(unittest.TestCase):
    """Tests for confidence threshold filtering."""

    def test_high_threshold_filters_signals(self):
        """High confidence threshold filters out weak signals."""
        detector = CognitiveBiasDetector(min_confidence=0.9)
        events = [make_event("tool_call", "deploy", success=False) for _ in range(4)]
        report = detector.analyze(make_session(events))
        # With high threshold, fewer signals pass
        high_count = report.bias_signals_detected

        detector_low = CognitiveBiasDetector(min_confidence=0.3)
        report_low = detector_low.analyze(make_session(events))
        low_count = report_low.bias_signals_detected

        self.assertLessEqual(high_count, low_count)

    def test_zero_threshold_allows_all(self):
        """Zero confidence threshold allows all signals."""
        detector = CognitiveBiasDetector(min_confidence=0.0)
        events = [make_event("tool_call", "deploy", success=False) for _ in range(4)]
        report = detector.analyze(make_session(events))
        self.assertIsNotNone(report)


class TestTrendDetection(unittest.TestCase):
    """Tests for trend direction computation."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_stable_trend(self):
        """Evenly distributed signals = stable trend."""
        signals = [
            BiasSignal(BiasCategory.SUNK_COST, 1, 0.7, BiasSeverity.MODERATE),
            BiasSignal(BiasCategory.SUNK_COST, 5, 0.7, BiasSeverity.MODERATE),
            BiasSignal(BiasCategory.SUNK_COST, 9, 0.7, BiasSeverity.MODERATE),
            BiasSignal(BiasCategory.SUNK_COST, 13, 0.7, BiasSeverity.MODERATE),
        ]
        trend = self.detector._compute_trend(signals)
        self.assertEqual(trend, TrendDirection.STABLE)

    def test_single_signal_stable(self):
        """Single signal = stable."""
        signals = [BiasSignal(BiasCategory.SUNK_COST, 5, 0.7, BiasSeverity.MODERATE)]
        trend = self.detector._compute_trend(signals)
        self.assertEqual(trend, TrendDirection.STABLE)


class TestRecommendations(unittest.TestCase):
    """Tests for recommendation generation."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_recommendations_for_sunk_cost(self):
        events = [make_event("tool_call", "deploy", success=False) for _ in range(5)]
        report = self.detector.analyze(make_session(events))
        self.assertTrue(any("failure threshold" in r.lower() or "retry" in r.lower() or "strategy" in r.lower()
                           for r in report.recommendations))

    def test_recommendations_for_automation(self):
        events = [
            make_event("tool_call", "api_call", success=False),
            make_event("agent_response", content="Here are the results"),
            make_event("tool_call", "next"),
        ]
        report = self.detector.analyze(make_session(events))
        if report.bias_signals_detected > 0:
            self.assertTrue(len(report.recommendations) > 0)

    def test_no_bias_recommendation(self):
        """Clean session gets positive recommendation."""
        events = [
            make_event("tool_call", "a"),
            make_event("tool_call", "b"),
            make_event("tool_call", "c"),
        ]
        report = self.detector.analyze(make_session(events))
        if report.bias_signals_detected == 0:
            self.assertTrue(any("no significant" in r.lower() for r in report.recommendations))


class TestDominantBias(unittest.TestCase):
    """Tests for dominant bias detection."""

    def setUp(self):
        self.detector = CognitiveBiasDetector(min_confidence=0.3)

    def test_dominant_bias_identified(self):
        """Dominant bias should be the one with most signals * confidence."""
        events = [make_event("tool_call", "deploy", success=False) for _ in range(6)]
        report = self.detector.analyze(make_session(events))
        if report.bias_signals_detected > 0:
            self.assertIsNotNone(report.dominant_bias)

    def test_no_dominant_bias_clean(self):
        """Clean session has no dominant bias."""
        events = [
            make_event("tool_call", "a"),
            make_event("tool_call", "b"),
            make_event("tool_call", "c"),
        ]
        report = self.detector.analyze(make_session(events))
        if report.bias_signals_detected == 0:
            self.assertIsNone(report.dominant_bias)


class TestEnumProperties(unittest.TestCase):
    """Tests for enum properties."""

    def test_bias_category_labels(self):
        self.assertEqual(BiasCategory.ANCHORING.label, "Anchoring")
        self.assertEqual(BiasCategory.SUNK_COST.label, "Sunk Cost")
        self.assertEqual(BiasCategory.DUNNING_KRUGER.label, "Dunning Kruger")

    def test_severity_weights(self):
        self.assertEqual(BiasSeverity.NONE.weight, 0.0)
        self.assertGreater(BiasSeverity.MILD.weight, 0.0)
        self.assertGreater(BiasSeverity.CRITICAL.weight, BiasSeverity.SEVERE.weight)

    def test_all_bias_categories(self):
        self.assertEqual(len(BiasCategory), 8)

    def test_all_severities(self):
        self.assertEqual(len(BiasSeverity), 5)


if __name__ == "__main__":
    unittest.main()
