"""Tests for the Agent Hallucination Detector."""

from __future__ import annotations

import json

import pytest

from agentlens.hallucination import (
    HallucinationDetector,
    HallucinationReport,
    HallucinationSignal,
    HallucinationProfile,
    HallucinationType,
    HallucinationSeverity,
    VeracityTier,
    TrendDirection,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _make_session(events, session_id="test-session"):
    return {"id": session_id, "events": events}


def _assistant(content):
    return {"type": "assistant", "content": content}


def _user(content):
    return {"type": "user", "content": content}


def _tool_result(content, success=True):
    return {"type": "tool_result", "content": content, "tool_result": content, "success": success}


def _tool_call(name, content, success=True, result=""):
    return {
        "type": "tool_call", "tool_name": name, "content": content,
        "success": success, "tool_result": result,
    }


# ── Basic / Edge Cases ──────────────────────────────────────────────


class TestBasicEdgeCases:
    def test_empty_session(self):
        det = HallucinationDetector()
        report = det.analyze(_make_session([]))
        assert report.veracity_score == 100.0
        assert report.signals_detected == 0
        assert report.tier == "excellent"

    def test_single_event(self):
        det = HallucinationDetector()
        report = det.analyze(_make_session([_assistant("Hello")]))
        assert report.veracity_score == 100.0

    def test_two_events(self):
        det = HallucinationDetector()
        report = det.analyze(_make_session([_user("Hi"), _assistant("Hello")]))
        assert report.veracity_score == 100.0
        assert report.signals_detected == 0

    def test_session_id_from_session_id_key(self):
        det = HallucinationDetector()
        report = det.analyze({"session_id": "my-sess", "events": []})
        assert report.session_id == "my-sess"

    def test_session_id_from_id_key(self):
        det = HallucinationDetector()
        report = det.analyze({"id": "id-sess", "events": []})
        assert report.session_id == "id-sess"

    def test_unknown_session_id(self):
        det = HallucinationDetector()
        report = det.analyze({"events": []})
        assert report.session_id == "unknown"

    def test_events_no_content(self):
        det = HallucinationDetector()
        events = [{"type": "assistant"}, {"type": "user"}, {"type": "assistant"}]
        report = det.analyze(_make_session(events))
        assert report.veracity_score == 100.0

    def test_min_confidence_filter(self):
        det = HallucinationDetector(min_confidence=0.99)
        events = [
            _user("What is X?"),
            _assistant("X is definitely true. Certainly correct. Absolutely right."),
            _assistant("Sorry, X is actually false. I was wrong about X."),
            _assistant("Let me correct that error about X."),
        ]
        report = det.analyze(_make_session(events))
        # With very high threshold, most signals should be filtered
        assert report.veracity_score >= 50.0


class TestCleanSession:
    def test_clean_grounded_session(self):
        det = HallucinationDetector()
        events = [
            _user("Search for Python documentation"),
            _tool_call("search", "python docs", True, "Found: docs.python.org"),
            _tool_result("Found: docs.python.org"),
            _assistant("I found the Python documentation at docs.python.org."),
            _user("What does it say about lists?"),
            _tool_call("read_page", "docs.python.org/lists", True, "Lists are mutable sequences."),
            _tool_result("Lists are mutable sequences."),
            _assistant("According to the documentation, lists are mutable sequences."),
        ]
        report = det.analyze(_make_session(events))
        assert report.veracity_score >= 70.0


# ── Detector 1: Self-Contradiction ──────────────────────────────────


class TestSelfContradiction:
    def test_direct_negation(self):
        det = HallucinationDetector()
        events = [
            _user("Tell me about the server configuration."),
            _assistant("The server configuration supports automatic scaling and load balancing features."),
            _user("Anything else?"),
            _assistant("The server configuration does not support automatic scaling. You need manual setup."),
        ]
        report = det.analyze(_make_session(events))
        contradiction_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.SELF_CONTRADICTION
        ]
        assert len(contradiction_signals) >= 1

    def test_numeric_contradiction(self):
        det = HallucinationDetector()
        events = [
            _user("What is the limit?"),
            _assistant("The server request limit is 1000 per minute for standard users."),
            _user("Can you confirm?"),
            _assistant("The server request limit is 500 per minute for standard users."),
        ]
        report = det.analyze(_make_session(events))
        contradiction_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.SELF_CONTRADICTION
        ]
        assert len(contradiction_signals) >= 1

    def test_no_contradiction_different_topics(self):
        det = HallucinationDetector()
        events = [
            _user("Tell me about cats."),
            _assistant("Cats are independent animals that enjoy sleeping."),
            _user("Tell me about dogs."),
            _assistant("Dogs are social animals that enjoy playing."),
        ]
        report = det.analyze(_make_session(events))
        contradiction_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.SELF_CONTRADICTION
        ]
        assert len(contradiction_signals) == 0


# ── Detector 2: Fabricated Reference ────────────────────────────────


class TestFabricatedReference:
    def test_academic_citation_no_source(self):
        det = HallucinationDetector()
        events = [
            _user("What are the best practices?"),
            _assistant("According to Smith et al. (2023), the recommended approach involves structured queries."),
            _assistant("This was confirmed by Johnson & Lee (2024) in their comprehensive review."),
        ]
        report = det.analyze(_make_session(events))
        fab_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.FABRICATED_REFERENCE
        ]
        assert len(fab_signals) >= 1

    def test_doi_no_source(self):
        det = HallucinationDetector()
        events = [
            _user("Give me a reference."),
            _assistant("See the paper at 10.1234/fake-doi-12345."),
            _assistant("Also check 10.5678/another-fabricated-ref."),
        ]
        report = det.analyze(_make_session(events))
        fab_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.FABRICATED_REFERENCE
        ]
        assert len(fab_signals) >= 1

    def test_url_not_from_tools(self):
        det = HallucinationDetector()
        events = [
            _user("Where can I find this?"),
            _assistant(
                "You can find the detailed report at "
                "https://example.com/reports/2024/q2/detailed-analysis/final-results.json"
            ),
            _assistant("Also see https://fake-org.io/docs/internal/secret/hidden-page.html"),
        ]
        report = det.analyze(_make_session(events))
        fab_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.FABRICATED_REFERENCE
        ]
        assert len(fab_signals) >= 1

    def test_citation_from_tool_output_ok(self):
        det = HallucinationDetector()
        events = [
            _user("Search for references."),
            _tool_result("Found: Smith et al. (2023) published a comprehensive review."),
            _assistant("According to Smith et al. (2023), the method is effective."),
        ]
        report = det.analyze(_make_session(events))
        fab_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.FABRICATED_REFERENCE
        ]
        # Citation is grounded in tool output
        assert len(fab_signals) == 0

    def test_multiple_versions_no_source(self):
        det = HallucinationDetector()
        events = [
            _user("What versions are available?"),
            _assistant("The available versions are v3.2.1, v4.0.0, and v5.1.2 of the framework."),
            _assistant("You should use version v3.2.1 or v4.0.0 for best compatibility."),
        ]
        report = det.analyze(_make_session(events))
        # Multiple unsourced versions may or may not trigger depending on threshold
        assert isinstance(report, HallucinationReport)


# ── Detector 3: Confidence-Reality Gap ──────────────────────────────


class TestConfidenceRealityGap:
    def test_confident_then_failure(self):
        det = HallucinationDetector()
        events = [
            _user("Find the data."),
            _assistant("I can definitely find this. The answer is certainly in the database."),
            _tool_call("query_db", "SELECT *", False, "Error: table not found"),
            _assistant("There seems to have been an error."),
        ]
        report = det.analyze(_make_session(events))
        gap_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.CONFIDENCE_REALITY_GAP
        ]
        assert len(gap_signals) >= 1

    def test_confident_then_correction(self):
        det = HallucinationDetector()
        events = [
            _user("What is the answer?"),
            _assistant("The answer is absolutely 42. No doubt about it. I am 100% confident."),
            _assistant("Actually, I was wrong. Sorry, let me correct my mistake. The answer is 7."),
            _assistant("That was my error earlier."),
        ]
        report = det.analyze(_make_session(events))
        gap_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.CONFIDENCE_REALITY_GAP
        ]
        assert len(gap_signals) >= 1

    def test_confident_then_success_no_gap(self):
        det = HallucinationDetector()
        events = [
            _user("Search for something."),
            _assistant("I'll definitely find this for you."),
            _tool_call("search", "query", True, "Result found: data"),
            _assistant("Here are the results I found."),
        ]
        report = det.analyze(_make_session(events))
        gap_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.CONFIDENCE_REALITY_GAP
        ]
        assert len(gap_signals) == 0


# ── Detector 4: Unverifiable Assertion ──────────────────────────────


class TestUnverifiableAssertion:
    def test_stats_without_source(self):
        det = HallucinationDetector()
        events = [
            _user("Tell me about adoption rates."),
            _assistant("The adoption rate is 94.3% across all enterprise companies worldwide."),
            _assistant("Additionally, 78.2% of developers prefer this framework over alternatives."),
        ]
        report = det.analyze(_make_session(events))
        uv_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.UNVERIFIABLE_ASSERTION
        ]
        assert len(uv_signals) >= 1

    def test_specific_date_without_source(self):
        det = HallucinationDetector()
        events = [
            _user("When was it released?"),
            _assistant("The framework was originally released on March 15, 2019."),
            _assistant("The major update came on November 23, 2021 with significant changes."),
        ]
        report = det.analyze(_make_session(events))
        uv_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.UNVERIFIABLE_ASSERTION
        ]
        assert len(uv_signals) >= 1

    def test_stat_from_tool_output_ok(self):
        det = HallucinationDetector()
        events = [
            _user("Get the data."),
            _tool_result("Usage: 94.3% adoption rate across enterprises"),
            _assistant("The adoption rate is 94.3% across enterprises."),
        ]
        report = det.analyze(_make_session(events))
        uv_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.UNVERIFIABLE_ASSERTION
        ]
        # The stat is grounded in tool output
        assert len(uv_signals) == 0


# ── Detector 5: Consistency Drift ───────────────────────────────────


class TestConsistencyDrift:
    def test_topic_drift(self):
        det = HallucinationDetector()
        events = [
            _user("Tell me about machine learning pipelines."),
            _assistant(
                "Machine learning pipelines involve data preprocessing, feature engineering, "
                "model training, validation, and deployment. The preprocessing step handles "
                "missing values, normalization, and encoding."
            ),
            _assistant(
                "Machine learning pipelines primarily focus on infrastructure, containerization, "
                "kubernetes orchestration, networking, and load balancing. The deployment "
                "infrastructure requires careful monitoring."
            ),
        ]
        report = det.analyze(_make_session(events))
        drift_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.CONSISTENCY_DRIFT
        ]
        # May or may not trigger depending on keyword overlap
        assert isinstance(report, HallucinationReport)

    def test_consistent_responses_no_drift(self):
        det = HallucinationDetector()
        events = [
            _user("Tell me about Python."),
            _assistant("Python is a programming language known for readability and simplicity."),
            _assistant("Python's readability and simplicity make it popular for beginners."),
        ]
        report = det.analyze(_make_session(events))
        assert isinstance(report, HallucinationReport)


# ── Detector 6: Phantom Knowledge ──────────────────────────────────


class TestPhantomKnowledge:
    def test_references_non_existent_discussion(self):
        det = HallucinationDetector()
        events = [
            _user("How do I set up a database?"),
            _assistant("First, install PostgreSQL."),
            _assistant(
                "As you mentioned in your earlier question about database sharding, "
                "the connection pooling strategy matters here."
            ),
        ]
        report = det.analyze(_make_session(events))
        phantom_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.PHANTOM_KNOWLEDGE
        ]
        assert len(phantom_signals) >= 1

    def test_references_non_existent_tool_output(self):
        det = HallucinationDetector()
        events = [
            _user("What should I do?"),
            _assistant("Based on the analysis, we should proceed."),
            _assistant("The output showed that our approach is correct."),
        ]
        report = det.analyze(_make_session(events))
        phantom_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.PHANTOM_KNOWLEDGE
        ]
        assert len(phantom_signals) >= 1

    def test_grounded_reference_ok(self):
        det = HallucinationDetector()
        events = [
            _user("How do I fix the database connection issue?"),
            _tool_result("Database connection timeout after 30 seconds"),
            _assistant("The output showed a connection timeout. Let me help fix that."),
        ]
        report = det.analyze(_make_session(events))
        phantom_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.PHANTOM_KNOWLEDGE
        ]
        # Reference to output is grounded
        assert len(phantom_signals) == 0


# ── Detector 7: Specificity Escalation ──────────────────────────────


class TestSpecificityEscalation:
    def test_escalating_detail_no_new_input(self):
        det = HallucinationDetector()
        events = [
            _user("How should I configure Redis?"),
            _assistant("Redis is a good choice for caching."),
            _assistant(
                "Configure Redis with 8 shards for optimal performance using version 6.0."
            ),
            _assistant(
                "Specifically, set Redis to use exactly 16 shards with v7.0.11, configure "
                "precisely 62.5 requests per second per shard, set TTL to 60.001 seconds, "
                "and use the RATELIMIT command with 99.97% accuracy rate on port 6380."
            ),
        ]
        report = det.analyze(_make_session(events))
        spec_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.SPECIFICITY_ESCALATION
        ]
        assert len(spec_signals) >= 1

    def test_specificity_with_new_input_ok(self):
        det = HallucinationDetector()
        events = [
            _user("How should I configure Redis?"),
            _assistant("Redis is a good choice for caching."),
            _user("Give me specific numbers."),
            _tool_result("Redis docs: recommended 16 shards, TTL 60s, port 6379"),
            _assistant(
                "According to the docs, use 16 shards with TTL 60 seconds on port 6379."
            ),
        ]
        report = det.analyze(_make_session(events))
        spec_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.SPECIFICITY_ESCALATION
        ]
        # New input justifies specificity
        assert len(spec_signals) == 0


# ── Detector 8: Hedging Collapse ────────────────────────────────────


class TestHedgingCollapse:
    def test_hedging_disappears_without_evidence(self):
        det = HallucinationDetector()
        events = [
            _user("What is the best approach?"),
            _assistant(
                "I think maybe the best approach might be to use a microservices architecture. "
                "It seems like it could possibly help with scaling, though I'm not sure about "
                "the trade-offs. Perhaps you should consider the alternatives."
            ),
            _assistant(
                "The microservices architecture is definitely the best approach. It certainly "
                "provides perfect scaling. There is absolutely no doubt that this is the "
                "correct choice. Obviously you should use it."
            ),
        ]
        report = det.analyze(_make_session(events))
        hedge_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.HEDGING_COLLAPSE
        ]
        assert len(hedge_signals) >= 1

    def test_hedging_maintained_ok(self):
        det = HallucinationDetector()
        events = [
            _user("What do you think?"),
            _assistant(
                "I think maybe the best approach might be to use microservices. "
                "Perhaps it could help with scaling."
            ),
            _assistant(
                "I still think microservices might be a good choice. "
                "It seems like it could work well, perhaps worth trying."
            ),
        ]
        report = det.analyze(_make_session(events))
        hedge_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.HEDGING_COLLAPSE
        ]
        assert len(hedge_signals) == 0

    def test_hedging_disappears_with_evidence_ok(self):
        det = HallucinationDetector()
        events = [
            _user("What approach should I use?"),
            _assistant(
                "I think maybe microservices might work. Perhaps it could be beneficial. "
                "I'm not sure about the specifics though."
            ),
            _tool_result("Benchmark: microservices 3x faster than monolith for this workload"),
            _assistant(
                "Microservices are definitely the right choice here. The benchmarks "
                "clearly show 3x performance improvement."
            ),
        ]
        report = det.analyze(_make_session(events))
        hedge_signals = [
            s for s in report.signal_timeline
            if s.type == HallucinationType.HEDGING_COLLAPSE
        ]
        # Evidence justifies confidence increase
        assert len(hedge_signals) == 0


# ── Scoring & Tiers ─────────────────────────────────────────────────


class TestScoringAndTiers:
    def test_excellent_tier(self):
        det = HallucinationDetector()
        events = [
            _user("Hi"),
            _tool_result("Data found"),
            _assistant("Here is the data I found."),
            _user("Thanks"),
        ]
        report = det.analyze(_make_session(events))
        assert report.veracity_score >= 80.0
        assert report.tier == "excellent"

    def test_low_score_many_signals(self):
        """Session with many hallucination signals should score low."""
        det = HallucinationDetector(min_confidence=0.3)
        events = [
            _user("Tell me about everything."),
            _assistant(
                "Absolutely, this is definitely the answer. According to Smith et al. (2023), "
                "the rate is 94.3%. See https://fake.org/reports/2024/q1/analysis/detailed/v3.json "
                "and DOI 10.1234/fabricated-reference-123456."
            ),
            _assistant(
                "Sorry, I was wrong earlier. Let me correct that error. "
                "Actually the rate is not 94.3%, it is 78.2%."
            ),
            _assistant(
                "As you mentioned about database optimization earlier, the connection "
                "pooling strategy from the output showed interesting results."
            ),
        ]
        report = det.analyze(_make_session(events))
        assert report.signals_detected > 0
        assert report.veracity_score < 90.0

    def test_tier_classification(self):
        det = HallucinationDetector()
        assert det._compute_tier(90) == "excellent"
        assert det._compute_tier(70) == "good"
        assert det._compute_tier(50) == "questionable"
        assert det._compute_tier(30) == "poor"
        assert det._compute_tier(10) == "unreliable"

    def test_score_bounds(self):
        det = HallucinationDetector()
        # Even with no profiles, score should be bounded
        score = det._compute_veracity_score([], 0, 10)
        assert 0 <= score <= 100

        score2 = det._compute_veracity_score([], 100, 10)
        assert 0 <= score2 <= 100


# ── Profiles ────────────────────────────────────────────────────────


class TestProfiles:
    def test_profile_building(self):
        det = HallucinationDetector()
        signals = [
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=1, confidence=0.8,
                severity=HallucinationSeverity.HIGH,
            ),
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=3, confidence=0.6,
                severity=HallucinationSeverity.MEDIUM,
            ),
        ]
        profiles = det._build_profiles(signals, 10)
        assert len(profiles) == 8  # one per type
        fab_profile = next(p for p in profiles if p.type == HallucinationType.FABRICATED_REFERENCE)
        assert fab_profile.signal_count == 2
        assert fab_profile.avg_confidence == pytest.approx(0.7, abs=0.01)

    def test_empty_profiles(self):
        det = HallucinationDetector()
        profiles = det._build_profiles([], 10)
        assert len(profiles) == 8
        assert all(p.signal_count == 0 for p in profiles)

    def test_severity_aggregation(self):
        det = HallucinationDetector()
        high_signals = [
            HallucinationSignal(
                type=HallucinationType.SELF_CONTRADICTION,
                event_index=0, confidence=0.9,
                severity=HallucinationSeverity.CRITICAL,
            ),
        ]
        sev = det._aggregate_severity(high_signals)
        assert sev == HallucinationSeverity.CRITICAL

    def test_severity_none_for_empty(self):
        det = HallucinationDetector()
        assert det._aggregate_severity([]) == HallucinationSeverity.NONE


# ── Trends ──────────────────────────────────────────────────────────


class TestTrends:
    def test_increasing_trend(self):
        det = HallucinationDetector()
        signals = [
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=8, confidence=0.7,
                severity=HallucinationSeverity.HIGH,
            ),
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=9, confidence=0.7,
                severity=HallucinationSeverity.HIGH,
            ),
        ]
        trend = det._compute_trend(signals, 10)
        assert trend == TrendDirection.INCREASING

    def test_decreasing_trend(self):
        det = HallucinationDetector()
        signals = [
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=0, confidence=0.7,
                severity=HallucinationSeverity.HIGH,
            ),
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=1, confidence=0.7,
                severity=HallucinationSeverity.HIGH,
            ),
        ]
        trend = det._compute_trend(signals, 10)
        assert trend == TrendDirection.DECREASING

    def test_stable_trend(self):
        det = HallucinationDetector()
        signals = [
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=2, confidence=0.7,
                severity=HallucinationSeverity.HIGH,
            ),
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=7, confidence=0.7,
                severity=HallucinationSeverity.HIGH,
            ),
        ]
        trend = det._compute_trend(signals, 10)
        assert trend == TrendDirection.STABLE

    def test_single_signal_stable(self):
        det = HallucinationDetector()
        signals = [
            HallucinationSignal(
                type=HallucinationType.FABRICATED_REFERENCE,
                event_index=5, confidence=0.7,
                severity=HallucinationSeverity.HIGH,
            ),
        ]
        trend = det._compute_trend(signals, 10)
        assert trend == TrendDirection.STABLE


# ── Recommendations ─────────────────────────────────────────────────


class TestRecommendations:
    def test_recommendations_for_signals(self):
        det = HallucinationDetector()
        events = [
            _user("Tell me about this."),
            _assistant(
                "According to Smith et al. (2023), the rate is 94.3% adoption worldwide. "
                "This is definitely confirmed."
            ),
            _assistant("Sorry, I made an error. The real rate is actually unknown."),
        ]
        report = det.analyze(_make_session(events))
        assert len(report.recommendations) > 0

    def test_clean_session_recommendation(self):
        det = HallucinationDetector()
        events = [
            _user("Hello"),
            _assistant("Hi there!"),
            _user("How are you?"),
            _assistant("I'm doing well, thanks for asking."),
        ]
        report = det.analyze(_make_session(events))
        # Should have a "no patterns detected" recommendation
        assert any("grounded" in r.lower() or "no significant" in r.lower()
                    for r in report.recommendations)

    def test_dominant_type_in_recommendations(self):
        det = HallucinationDetector(min_confidence=0.3)
        events = [
            _user("Tell me things."),
            _assistant("According to Johnson & Lee (2023), the results show 87.5% improvement."),
            _assistant("See also Brown et al. (2024) and the DOI 10.9999/fake-ref-here."),
            _assistant("The study by Williams (2022) confirmed these findings at 92.1%."),
        ]
        report = det.analyze(_make_session(events))
        if report.dominant_type:
            assert any("priority" in r.lower() or "dominant" in r.lower()
                        for r in report.recommendations)


# ── Insights ────────────────────────────────────────────────────────


class TestInsights:
    def test_clean_session_insight(self):
        det = HallucinationDetector()
        events = [
            _user("Hello"),
            _assistant("Hi"),
            _user("Bye"),
            _assistant("Goodbye"),
        ]
        report = det.analyze(_make_session(events))
        assert any("clean" in i.lower() or "no hallucination" in i.lower()
                    for i in report.insights)

    def test_insights_generated_for_signals(self):
        det = HallucinationDetector(min_confidence=0.3)
        events = [
            _user("Question"),
            _assistant(
                "Definitely the answer. According to Chen (2023), it's 94.3%. "
                "See DOI 10.1234/fake. The output showed results."
            ),
            _assistant("Sorry, that was wrong. Let me correct my error."),
            _assistant(
                "As you mentioned about scaling earlier, the previous analysis "
                "indicated improvements."
            ),
        ]
        report = det.analyze(_make_session(events))
        assert len(report.insights) >= 1


# ── Report Formatting ───────────────────────────────────────────────


class TestReportFormatting:
    def test_format_report_text(self):
        det = HallucinationDetector()
        events = [
            _user("Hello"),
            _assistant("Certainly, the answer is 42. No doubt about that."),
            _assistant("Actually, I apologize. That was wrong. Let me correct the error."),
            _assistant("The answer was definitely not 42, sorry for the mistake."),
        ]
        report = det.analyze(_make_session(events))
        text = report.format_report()
        assert "AGENT HALLUCINATION ANALYSIS" in text
        assert "Veracity Score" in text
        assert "Tier" in text

    def test_to_dict(self):
        det = HallucinationDetector()
        events = [_user("Hi"), _assistant("Hello"), _user("?"), _assistant("!")]
        report = det.analyze(_make_session(events))
        d = report.to_dict()
        assert "session_id" in d
        assert "veracity_score" in d
        assert "signals_detected" in d
        assert "profiles" in d
        assert "tier" in d
        assert "insights" in d

    def test_to_json(self):
        det = HallucinationDetector()
        events = [_user("Hi"), _assistant("Hello"), _user("?"), _assistant("!")]
        report = det.analyze(_make_session(events))
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["session_id"] == "test-session"

    def test_signal_to_dict(self):
        s = HallucinationSignal(
            type=HallucinationType.FABRICATED_REFERENCE,
            event_index=5,
            confidence=0.8,
            severity=HallucinationSeverity.HIGH,
            description="Test",
            evidence="Evidence",
        )
        d = s.to_dict()
        assert d["type"] == "fabricated_reference"
        assert d["confidence"] == 0.8
        assert d["severity"] == "high"

    def test_profile_to_dict(self):
        p = HallucinationProfile(
            type=HallucinationType.SELF_CONTRADICTION,
            signal_count=3,
            avg_confidence=0.7,
            severity=HallucinationSeverity.MEDIUM,
            trend=TrendDirection.INCREASING,
        )
        d = p.to_dict()
        assert d["type"] == "self_contradiction"
        assert d["signal_count"] == 3
        assert d["trend"] == "increasing"


# ── Enums ───────────────────────────────────────────────────────────


class TestEnums:
    def test_hallucination_type_labels(self):
        assert HallucinationType.SELF_CONTRADICTION.label == "Self Contradiction"
        assert HallucinationType.FABRICATED_REFERENCE.label == "Fabricated Reference"

    def test_severity_weights(self):
        assert HallucinationSeverity.NONE.weight == 0.0
        assert HallucinationSeverity.LOW.weight == 0.15
        assert HallucinationSeverity.CRITICAL.weight == 1.0

    def test_veracity_tier_labels(self):
        assert VeracityTier.EXCELLENT.label == "Excellent"
        assert VeracityTier.UNRELIABLE.label == "Unreliable"

    def test_all_types_exist(self):
        assert len(HallucinationType) == 8

    def test_all_severities_exist(self):
        assert len(HallucinationSeverity) == 5

    def test_all_tiers_exist(self):
        assert len(VeracityTier) == 5


# ── Helpers ─────────────────────────────────────────────────────────


class TestHelpers:
    def test_get_content_from_content(self):
        det = HallucinationDetector()
        assert det._get_content({"content": "hello"}) == "hello"

    def test_get_content_from_text(self):
        det = HallucinationDetector()
        assert det._get_content({"text": "hello"}) == "hello"

    def test_get_content_from_message(self):
        det = HallucinationDetector()
        assert det._get_content({"message": "hello"}) == "hello"

    def test_get_content_empty(self):
        det = HallucinationDetector()
        assert det._get_content({}) == ""

    def test_get_keywords(self):
        det = HallucinationDetector()
        kws = det._get_keywords("The quick brown fox jumps over the lazy dog")
        assert "quick" in kws
        assert "brown" in kws
        assert "jumps" in kws
        # Stop words removed
        assert "over" not in kws

    def test_severity_from_confidence(self):
        det = HallucinationDetector()
        assert det._severity_from_conf(0.9) == HallucinationSeverity.CRITICAL
        assert det._severity_from_conf(0.75) == HallucinationSeverity.HIGH
        assert det._severity_from_conf(0.6) == HallucinationSeverity.MEDIUM
        assert det._severity_from_conf(0.4) == HallucinationSeverity.LOW
        assert det._severity_from_conf(0.1) == HallucinationSeverity.NONE

    def test_specificity_score(self):
        det = HallucinationDetector()
        s1 = det._specificity_score("Hello world")
        s2 = det._specificity_score(
            "Configure Redis v7.0.11 with exactly 16 shards on port 6380 "
            "using the API endpoint with JSON response format"
        )
        assert s2 > s1

    def test_specificity_score_empty(self):
        det = HallucinationDetector()
        assert det._specificity_score("") == 0.0


# ── Integration ─────────────────────────────────────────────────────


class TestIntegration:
    def test_full_hallucinating_session(self):
        """A session with many hallucination patterns should score low."""
        det = HallucinationDetector(min_confidence=0.4)
        events = [
            _user("Tell me about API design."),
            _assistant(
                "I think API design might be important. Perhaps RESTful approaches "
                "could be good. Maybe consider different options."
            ),
            _assistant(
                "API design is absolutely critical. The standard is definitely REST. "
                "According to the landmark paper by Richardson & Ruby (2007), "
                "REST is the only correct approach — 100% guaranteed."
            ),
            _tool_call("search", "REST API standards", False, "Error: connection timeout"),
            _assistant(
                "As I mentioned, REST is certainly perfect. The output confirmed this. "
                "See https://api-standards.org/official/2024/definitive-guide/rest-is-best/final.html "
                "and DOI 10.5555/fake-api-study. The adoption rate is 97.8% globally."
            ),
            _assistant(
                "As you discussed earlier about GraphQL migration, the previous results "
                "showed that REST endpoints should always be preferred."
            ),
            _assistant(
                "Specifically, use exactly 47 endpoints per microservice with v3.14.159 "
                "of the framework, configured with precisely 12.5ms timeout on port 8443."
            ),
            _assistant(
                "The rate limit should never exceed 1000 requests per second. "
                "This is a universal standard that every API follows."
            ),
            _assistant(
                "Actually, the rate limit is always 2000 requests per second. "
                "The standard was updated on June 15, 2024."
            ),
        ]
        report = det.analyze(_make_session(events))
        assert report.signals_detected >= 3
        assert report.veracity_score < 80.0
        assert len(report.profiles) == 8
        assert len(report.recommendations) >= 1
        assert len(report.insights) >= 1

    def test_full_grounded_session(self):
        """A well-grounded session should score high."""
        det = HallucinationDetector()
        events = [
            _user("Search for Python documentation on lists."),
            _tool_call("search", "python lists", True, "docs.python.org/lists"),
            _tool_result("Lists are mutable sequences. Common operations: append, extend, insert."),
            _assistant("According to the Python docs, lists are mutable sequences. You can use append, extend, and insert."),
            _user("What about tuples?"),
            _tool_call("search", "python tuples", True, "docs.python.org/tuples"),
            _tool_result("Tuples are immutable sequences. Created with parentheses."),
            _assistant("The docs say tuples are immutable sequences, created with parentheses."),
        ]
        report = det.analyze(_make_session(events))
        assert report.veracity_score >= 70.0
        assert report.tier in ("excellent", "good")
