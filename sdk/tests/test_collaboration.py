"""Tests for Agent Collaboration Analyzer."""

import json
import math
import unittest

from agentlens.collaboration import (
    BottleneckAgent,
    BottleneckSeverity,
    CollaborationAnalyzer,
    CollaborationConfig,
    CollaborationEvent,
    CollaborationPattern,
    CollaborationReport,
    DelegationNode,
    EngineResult,
    HandoffDetail,
    HandoffVerdict,
    TeamworkGrade,
    WorkloadEntry,
)


def _make_event(ts, agent, etype, target=None, **meta):
    return CollaborationEvent(
        timestamp=ts, agent_id=agent, event_type=etype,
        target_agent=target, metadata=meta,
    )


class TestCollaborationAnalyzerEmpty(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_empty_events(self):
        report = self.analyzer.analyze([], session_id="empty")
        self.assertEqual(report.event_count, 0)
        self.assertEqual(report.grade, TeamworkGrade.DYSFUNCTIONAL)
        self.assertEqual(report.collaboration_pattern, CollaborationPattern.SOLO)

    def test_single_agent(self):
        events = [
            _make_event(1.0, "a1", "tool_call", tool="search"),
            _make_event(2.0, "a1", "decision", topic="x", outcome="yes"),
        ]
        report = self.analyzer.analyze(events, session_id="solo")
        self.assertEqual(report.agent_count, 1)
        self.assertEqual(report.collaboration_pattern, CollaborationPattern.SOLO)

    def test_report_to_json(self):
        report = self.analyzer.analyze([], session_id="test")
        data = json.loads(report.to_json())
        self.assertEqual(data["session_id"], "test")
        self.assertIn("teamwork_score", data)

    def test_report_to_dict(self):
        report = self.analyzer.analyze([], session_id="test")
        d = report.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["session_id"], "test")

    def test_format_report_nocrash(self):
        report = self.analyzer.analyze([], session_id="test")
        text = report.format_report()
        self.assertIn("COLLABORATION ANALYSIS", text)


class TestHandoffQuality(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_clean_handoff(self):
        events = [
            _make_event(1.0, "a1", "tool_call", tool="search"),
            _make_event(2.0, "a1", "handoff", "a2",
                        latency_ms=100, context_size=100, received_context=98),
            _make_event(3.0, "a2", "tool_call", tool="write"),
            _make_event(4.0, "a2", "complete"),
        ]
        report = self.analyzer.analyze(events, session_id="clean")
        self.assertGreater(report.handoff_quality_score, 70)
        self.assertEqual(len(report.handoffs), 1)
        self.assertEqual(report.handoffs[0].verdict, HandoffVerdict.CLEAN)

    def test_lossy_handoff(self):
        events = [
            _make_event(1.0, "a1", "handoff", "a2",
                        latency_ms=6000, context_size=100, received_context=70),
        ]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.handoffs[0].verdict, HandoffVerdict.LOSSY)

    def test_failed_handoff(self):
        events = [
            _make_event(1.0, "a1", "handoff", "a2",
                        latency_ms=15000, context_size=100, received_context=30),
        ]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.handoffs[0].verdict, HandoffVerdict.FAILED)

    def test_no_handoffs_full_score(self):
        events = [
            _make_event(1.0, "a1", "tool_call"),
            _make_event(2.0, "a2", "tool_call"),
        ]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.handoff_quality_score, 100.0)

    def test_redundant_work_detection(self):
        events = [
            _make_event(1.0, "a1", "tool_call", tool="search"),
            _make_event(2.0, "a1", "handoff", "a2",
                        latency_ms=100, context_size=100, received_context=95),
            _make_event(3.0, "a2", "tool_call", tool="search"),  # same tool = redundant
        ]
        report = self.analyzer.analyze(events)
        self.assertTrue(report.handoffs[0].redundant_work)

    def test_handoff_detail_to_dict(self):
        hd = HandoffDetail("a1", "a2", 1.0, 100.0, 0.1, HandoffVerdict.CLEAN, False)
        d = hd.to_dict()
        self.assertEqual(d["source_agent"], "a1")
        self.assertEqual(d["verdict"], "clean")


class TestCommunicationBottleneck(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_no_bottleneck(self):
        events = [
            _make_event(1.0, "a1", "message", "a2"),
            _make_event(2.0, "a2", "message", "a1"),
        ]
        report = self.analyzer.analyze(events)
        self.assertEqual(len(report.bottlenecks), 0)
        self.assertEqual(report.bottleneck_score, 100.0)

    def test_bottleneck_detected(self):
        # Many agents sending to one
        events = [
            _make_event(1.0, "a1", "message", "central"),
            _make_event(2.0, "a2", "message", "central"),
            _make_event(3.0, "a3", "message", "central"),
            _make_event(4.0, "a4", "message", "central"),
        ]
        report = self.analyzer.analyze(events)
        self.assertGreater(len(report.bottlenecks), 0)
        self.assertTrue(any(b.agent_id == "central" for b in report.bottlenecks))

    def test_severe_bottleneck(self):
        events = []
        for i in range(6):
            events.append(_make_event(float(i), f"w{i}", "delegate", "hub"))
        report = self.analyzer.analyze(events)
        bn = [b for b in report.bottlenecks if b.agent_id == "hub"]
        self.assertTrue(len(bn) > 0)
        self.assertIn(bn[0].severity, (BottleneckSeverity.SEVERE, BottleneckSeverity.CRITICAL))

    def test_bottleneck_agent_to_dict(self):
        ba = BottleneckAgent("hub", 5, 1, BottleneckSeverity.SEVERE, ["a", "b"], "split it")
        d = ba.to_dict()
        self.assertEqual(d["agent_id"], "hub")
        self.assertEqual(d["severity"], "severe")


class TestDelegationChain(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_no_delegations(self):
        events = [_make_event(1.0, "a1", "tool_call")]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.delegation_score, 100.0)
        self.assertEqual(report.max_delegation_depth, 0)

    def test_simple_delegation(self):
        events = [
            _make_event(1.0, "boss", "delegate", "worker"),
            _make_event(2.0, "worker", "complete"),
        ]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.max_delegation_depth, 1)
        self.assertEqual(report.abandoned_delegations, 0)

    def test_abandoned_delegation(self):
        events = [
            _make_event(1.0, "boss", "delegate", "worker"),
            # worker never completes
        ]
        report = self.analyzer.analyze(events)
        self.assertGreater(report.abandoned_delegations, 0)
        self.assertLess(report.delegation_score, 100.0)

    def test_deep_delegation(self):
        events = [
            _make_event(1.0, "a", "delegate", "b"),
            _make_event(2.0, "b", "delegate", "c"),
            _make_event(3.0, "c", "delegate", "d"),
            _make_event(4.0, "d", "delegate", "e"),
            _make_event(5.0, "e", "delegate", "f"),
            _make_event(6.0, "f", "delegate", "g"),
            _make_event(7.0, "g", "complete"),
        ]
        report = self.analyzer.analyze(events)
        self.assertGreater(report.max_delegation_depth, 4)

    def test_circular_delegation(self):
        events = [
            _make_event(1.0, "a", "delegate", "b"),
            _make_event(2.0, "b", "delegate", "c"),
            _make_event(3.0, "c", "delegate", "a"),  # circular
        ]
        report = self.analyzer.analyze(events)
        # Should detect and score penalty
        self.assertLess(report.delegation_score, 100.0)

    def test_delegation_node_to_dict(self):
        dn = DelegationNode("a", 0, ["b", "c"], True, False)
        d = dn.to_dict()
        self.assertEqual(d["agent_id"], "a")
        self.assertEqual(d["depth"], 0)


class TestWorkloadBalance(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_balanced_workload(self):
        events = [
            _make_event(1.0, "a1", "tool_call"),
            _make_event(2.0, "a2", "tool_call"),
            _make_event(3.0, "a1", "tool_call"),
            _make_event(4.0, "a2", "tool_call"),
        ]
        report = self.analyzer.analyze(events)
        self.assertLess(report.gini_coefficient, 0.2)
        self.assertGreater(report.workload_balance_score, 80)

    def test_imbalanced_workload(self):
        events = [_make_event(float(i), "busy", "tool_call") for i in range(20)]
        events.append(_make_event(21.0, "idle", "tool_call"))
        report = self.analyzer.analyze(events)
        self.assertGreater(report.gini_coefficient, 0.3)
        # With 20 vs 1, avg=10.5, but 20 < 21 (2x avg), so overloaded check uses > not >=
        # Check that imbalance is reflected in score
        self.assertLess(report.workload_balance_score, 70)

    def test_workload_entry_to_dict(self):
        we = WorkloadEntry("a1", 10, 5, 1, 0.5, "balanced")
        d = we.to_dict()
        self.assertEqual(d["agent_id"], "a1")
        self.assertEqual(d["status"], "balanced")

    def test_idle_detection(self):
        events = [_make_event(float(i), "worker", "tool_call") for i in range(10)]
        events.append(_make_event(11.0, "slacker", "message"))
        config = CollaborationConfig(workload_idle_factor=0.25)
        analyzer = CollaborationAnalyzer(config=config)
        report = analyzer.analyze(events)
        idle = [w for w in report.workload_entries if w.status == "idle"]
        self.assertGreater(len(idle), 0)


class TestTeamworkRhythm(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_high_coordination_overhead(self):
        events = [
            _make_event(float(i), "a1" if i % 2 == 0 else "a2", "message", "a2" if i % 2 == 0 else "a1")
            for i in range(20)
        ]
        report = self.analyzer.analyze(events)
        self.assertGreater(report.coordination_overhead_pct, 50)

    def test_low_overhead_productive(self):
        events = []
        for i in range(15):
            events.append(_make_event(float(i), "a1" if i % 2 == 0 else "a2", "tool_call"))
        events.append(_make_event(16.0, "a1", "message", "a2"))
        report = self.analyzer.analyze(events)
        self.assertLess(report.coordination_overhead_pct, 20)

    def test_single_event_rhythm(self):
        events = [_make_event(1.0, "a1", "tool_call")]
        report = self.analyzer.analyze(events)
        # Should not crash
        self.assertIsNotNone(report.rhythm_score)


class TestCollectiveIntelligence(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_error_correction(self):
        events = [
            _make_event(1.0, "a1", "error"),
            _make_event(2.0, "a2", "complete"),  # a2 fixes a1's error
        ]
        report = self.analyzer.analyze(events)
        # Should have some synergy score for error correction
        self.assertIsNotNone(report.synergy_score)

    def test_knowledge_complementarity(self):
        events = [
            _make_event(1.0, "a1", "tool_call", tool="search"),
            _make_event(2.0, "a1", "tool_call", tool="analyze"),
            _make_event(3.0, "a2", "tool_call", tool="write"),
            _make_event(4.0, "a2", "tool_call", tool="deploy"),
        ]
        report = self.analyzer.analyze(events)
        # Different tools = high complementarity
        self.assertGreater(report.synergy_score, 30)

    def test_consensus_quality(self):
        events = [
            _make_event(1.0, "a1", "decision", topic="deploy", outcome="yes"),
            _make_event(2.0, "a2", "decision", topic="deploy", outcome="yes"),
        ]
        report = self.analyzer.analyze(events)
        self.assertIsNotNone(report.synergy_score)

    def test_single_agent_synergy_neutral(self):
        events = [_make_event(1.0, "a1", "tool_call")]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.synergy_score, 50.0)


class TestPatternDetection(unittest.TestCase):
    def setUp(self):
        self.analyzer = CollaborationAnalyzer()

    def test_orchestrated_pattern(self):
        events = [
            _make_event(1.0, "orchestrator", "delegate", "w1"),
            _make_event(2.0, "orchestrator", "delegate", "w2"),
            _make_event(3.0, "orchestrator", "delegate", "w3"),
            _make_event(4.0, "w1", "complete"),
            _make_event(5.0, "w2", "complete"),
            _make_event(6.0, "w3", "complete"),
        ]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.collaboration_pattern, CollaborationPattern.ORCHESTRATED)

    def test_pipeline_pattern(self):
        events = [
            _make_event(1.0, "a", "handoff", "b"),
            _make_event(2.0, "b", "handoff", "c"),
            _make_event(3.0, "c", "complete"),
        ]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.collaboration_pattern, CollaborationPattern.PIPELINE)

    def test_solo_pattern(self):
        events = [_make_event(1.0, "alone", "tool_call")]
        report = self.analyzer.analyze(events)
        self.assertEqual(report.collaboration_pattern, CollaborationPattern.SOLO)

    def test_hierarchical_pattern(self):
        events = [
            _make_event(1.0, "ceo", "delegate", "mgr1"),
            _make_event(2.0, "ceo", "delegate", "mgr2"),
            _make_event(3.0, "mgr1", "delegate", "worker1"),
            _make_event(4.0, "mgr2", "delegate", "worker2"),
            _make_event(5.0, "worker1", "complete"),
            _make_event(6.0, "worker2", "complete"),
        ]
        report = self.analyzer.analyze(events)
        self.assertIn(report.collaboration_pattern,
                       (CollaborationPattern.HIERARCHICAL, CollaborationPattern.ORCHESTRATED))


class TestGrading(unittest.TestCase):
    def test_elite_grade(self):
        self.assertEqual(CollaborationAnalyzer._classify_grade(95), TeamworkGrade.ELITE)

    def test_strong_grade(self):
        self.assertEqual(CollaborationAnalyzer._classify_grade(80), TeamworkGrade.STRONG)

    def test_functional_grade(self):
        self.assertEqual(CollaborationAnalyzer._classify_grade(65), TeamworkGrade.FUNCTIONAL)

    def test_struggling_grade(self):
        self.assertEqual(CollaborationAnalyzer._classify_grade(45), TeamworkGrade.STRUGGLING)

    def test_dysfunctional_grade(self):
        self.assertEqual(CollaborationAnalyzer._classify_grade(30), TeamworkGrade.DYSFUNCTIONAL)


class TestConfiguration(unittest.TestCase):
    def test_custom_config(self):
        config = CollaborationConfig(
            handoff_latency_threshold_ms=1000.0,
            bottleneck_fan_in_threshold=5,
        )
        analyzer = CollaborationAnalyzer(config=config)
        self.assertEqual(analyzer.config.handoff_latency_threshold_ms, 1000.0)

    def test_engine_weights_sum(self):
        config = CollaborationConfig()
        total = (config.weight_handoff + config.weight_bottleneck +
                 config.weight_delegation + config.weight_workload +
                 config.weight_rhythm + config.weight_synergy)
        self.assertAlmostEqual(total, 1.0, places=5)


class TestEnumLabels(unittest.TestCase):
    def test_teamwork_grade_labels(self):
        self.assertEqual(TeamworkGrade.ELITE.label, "Elite")
        self.assertEqual(TeamworkGrade.DYSFUNCTIONAL.label, "Dysfunctional")

    def test_collaboration_pattern_labels(self):
        self.assertEqual(CollaborationPattern.PEER_TO_PEER.label, "Peer To Peer")
        self.assertEqual(CollaborationPattern.ORCHESTRATED.label, "Orchestrated")


class TestEngineResult(unittest.TestCase):
    def test_to_dict(self):
        er = EngineResult("test", 85.3, ["finding1"], {"key": "val"})
        d = er.to_dict()
        self.assertEqual(d["engine"], "test")
        self.assertEqual(d["score"], 85.3)
        self.assertEqual(d["findings"], ["finding1"])


class TestFullCollaborationScenario(unittest.TestCase):
    """Integration test with a realistic multi-agent scenario."""

    def test_full_orchestrated_session(self):
        events = [
            # Orchestrator delegates to workers
            _make_event(0.0, "orch", "delegate", "researcher"),
            _make_event(0.1, "orch", "delegate", "coder"),
            _make_event(0.2, "orch", "delegate", "reviewer"),
            # Researcher works
            _make_event(1.0, "researcher", "tool_call", tool="search"),
            _make_event(2.0, "researcher", "tool_call", tool="analyze"),
            _make_event(3.0, "researcher", "handoff", "coder",
                        latency_ms=200, context_size=100, received_context=95),
            # Coder works
            _make_event(4.0, "coder", "tool_call", tool="write"),
            _make_event(5.0, "coder", "tool_call", tool="compile"),
            _make_event(6.0, "coder", "handoff", "reviewer",
                        latency_ms=150, context_size=80, received_context=78),
            # Reviewer works
            _make_event(7.0, "reviewer", "tool_call", tool="review"),
            _make_event(7.5, "reviewer", "decision", topic="approve", outcome="yes"),
            _make_event(8.0, "reviewer", "complete"),
            _make_event(8.5, "coder", "complete"),
            _make_event(9.0, "researcher", "complete"),
        ]
        analyzer = CollaborationAnalyzer()
        report = analyzer.analyze(events, session_id="full-test")

        self.assertEqual(report.agent_count, 4)
        self.assertGreater(report.teamwork_score, 0)
        self.assertIn(report.grade, list(TeamworkGrade))
        self.assertIn(report.collaboration_pattern, list(CollaborationPattern))

        # Check report renders
        text = report.format_report()
        self.assertIn("full-test", text)
        self.assertIn("ENGINE SCORES", text)

        # Check JSON round-trip
        d = json.loads(report.to_json())
        self.assertEqual(d["session_id"], "full-test")
        self.assertEqual(d["agent_count"], 4)

    def test_dysfunctional_session(self):
        """Session with many problems: abandoned delegations, bottleneck, errors."""
        events = [
            # Everyone sends to one agent
            _make_event(1.0, "a1", "delegate", "hub"),
            _make_event(2.0, "a2", "delegate", "hub"),
            _make_event(3.0, "a3", "delegate", "hub"),
            _make_event(4.0, "a4", "delegate", "hub"),
            # Hub errors
            _make_event(5.0, "hub", "error"),
            _make_event(6.0, "hub", "error"),
            # Nothing completes
            _make_event(7.0, "a1", "handoff", "hub",
                        latency_ms=20000, context_size=100, received_context=10),
        ]
        analyzer = CollaborationAnalyzer()
        report = analyzer.analyze(events, session_id="bad")
        # Should have low scores
        self.assertLess(report.teamwork_score, 80)
        self.assertGreater(len(report.bottlenecks), 0)


if __name__ == "__main__":
    unittest.main()
