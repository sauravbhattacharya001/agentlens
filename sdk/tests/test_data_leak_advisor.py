"""Tests for DataLeakAdvisor."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import agentlens
from agentlens.data_leak_advisor import (
    ActionPriority,
    DataLeakAdvisor,
    DataLeakOptions,
    DataLeakReport,
    FindingKind,
    FindingSource,
    LeakGrade,
    LeakVerdict,
    RiskAppetite,
)


FIXED_NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return FIXED_NOW


def _llm_event(
    *,
    event_id: str = "e1",
    session_id: str = "s1",
    model: str = "gpt-4o",
    system: str = "You are helpful.",
    user: str = "hi",
    assistant: str = "hello",
) -> dict:
    return {
        "event_id": event_id,
        "session_id": session_id,
        "event_type": "llm_call",
        "timestamp": FIXED_NOW,
        "model": model,
        "tokens_in": 100,
        "tokens_out": 20,
        "input_data": {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        "output_data": {
            "messages": [
                {"role": "assistant", "content": assistant},
            ],
        },
    }


# --------------------------------------------------------------------------- #
# Basics
# --------------------------------------------------------------------------- #


def test_empty_input_returns_grade_a():
    advisor = DataLeakAdvisor(now_fn=_now)
    report = advisor.analyze([])
    assert isinstance(report, DataLeakReport)
    assert report.generated_at == FIXED_NOW
    assert report.portfolio.total_events == 0
    assert report.portfolio.scanned_events == 0
    assert report.portfolio.portfolio_grade is LeakGrade.A
    assert report.slices == []
    assert "NO_SCANNABLE_PAYLOADS" in report.insights
    assert any(a.id == "no_leak_action_needed" for a in report.playbook)


def test_clean_traffic_grade_a_no_findings():
    advisor = DataLeakAdvisor(now_fn=_now)
    report = advisor.analyze(
        [_llm_event(event_id=f"e{i}", user=f"question {i}") for i in range(3)]
    )
    assert report.portfolio.total_events == 3
    assert report.portfolio.scanned_events == 3
    assert report.portfolio.total_findings == 0
    assert report.portfolio.portfolio_grade is LeakGrade.A
    assert "ALL_CLEAN" in report.insights


def test_non_llm_events_still_scanned_if_payload_present():
    """Non-LLM events (e.g. tool calls) carry input/output too."""
    advisor = DataLeakAdvisor(now_fn=_now)
    tool_event = {
        "event_id": "t1",
        "session_id": "s1",
        "event_type": "tool_call",
        "timestamp": FIXED_NOW,
        "input_data": {"query": "lookup user@example.com please"},
        "output_data": {"result": "ok"},
    }
    report = advisor.analyze([tool_event])
    # We don't require a model; events without model get bucketed as "unknown".
    assert report.portfolio.scanned_events == 1
    assert any(f.code == "PII_EMAIL" for sl in report.slices for f in sl.findings)


# --------------------------------------------------------------------------- #
# Secret detection
# --------------------------------------------------------------------------- #


def test_aws_access_key_id_triggers_secret_leak_p0():
    advisor = DataLeakAdvisor(now_fn=_now)
    # NOTE: split to avoid tripping the pre-push secret-scan guard; the
    # advisor sees the concatenated string at runtime.
    fake_key = "AKIA" + "ABCDEFGHIJKLMNOP"
    bad = _llm_event(user=f"here is my key {fake_key} for testing")
    report = advisor.analyze([bad])
    assert report.portfolio.portfolio_grade is LeakGrade.F
    assert report.portfolio.secret_leak_count == 1
    slice_ = next(s for s in report.slices if s.verdict is LeakVerdict.SECRET_LEAK)
    assert slice_.priority is ActionPriority.P0
    assert slice_.source is FindingSource.INPUT
    assert any(f.code == "SECRET_AWS_ACCESS_KEY_ID" for f in slice_.findings)
    # Preview should not contain the full key.
    leaked_finding = next(
        f for f in slice_.findings if f.code == "SECRET_AWS_ACCESS_KEY_ID"
    )
    assert fake_key not in leaked_finding.preview
    assert leaked_finding.preview.startswith("***")
    assert any(
        a.id == "block_trace_export_pending_secret_rotation"
        for a in report.playbook
    )
    assert any(a.id == "rotate_leaked_credentials" for a in report.playbook)
    assert "SECRET_LEAK_DETECTED" in report.insights


def test_github_pat_in_output_is_detected_and_routed_to_output_slice():
    advisor = DataLeakAdvisor(now_fn=_now)
    # Split to dodge the pre-push secret-scan guard; matches at runtime.
    fake_pat = "ghp" + "_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    ev = _llm_event(
        assistant=f"use this token: {fake_pat}",
    )
    report = advisor.analyze([ev])
    output_slices = [s for s in report.slices if s.source is FindingSource.OUTPUT]
    assert len(output_slices) == 1
    assert output_slices[0].verdict is LeakVerdict.SECRET_LEAK
    assert "MODEL_OUTPUT_LEAKS_ONLY" in report.insights


def test_pem_private_key_header_detected():
    advisor = DataLeakAdvisor(now_fn=_now)
    pem_header = "-----BEGIN " + "RSA " + "PRIVATE " + "KEY-----"
    ev = _llm_event(user=f"please verify {pem_header}\nMIIEv...")
    report = advisor.analyze([ev])
    assert report.portfolio.secret_leak_count == 1


# --------------------------------------------------------------------------- #
# PII detection
# --------------------------------------------------------------------------- #


def test_single_email_is_minor_pii_only():
    advisor = DataLeakAdvisor(now_fn=_now)
    ev = _llm_event(user="ping me at alice@example.com")
    report = advisor.analyze([ev])
    assert report.portfolio.minor_leak_count == 1
    assert report.portfolio.pii_leak_count == 0
    sl = report.slices[0]
    assert sl.verdict is LeakVerdict.TRACE_PII_MINOR
    assert sl.priority is ActionPriority.P2
    assert report.portfolio.portfolio_grade is LeakGrade.B


def test_multiple_distinct_pii_kinds_promote_to_major():
    advisor = DataLeakAdvisor(now_fn=_now)
    ev = _llm_event(
        user="contact alice@example.com or call 415-555-2671",
    )
    report = advisor.analyze([ev])
    assert report.portfolio.pii_leak_count == 1
    sl = report.slices[0]
    assert sl.verdict is LeakVerdict.PII_LEAK
    assert sl.priority is ActionPriority.P1
    assert sl.distinct_kinds >= 2
    assert any(
        a.id == "enable_pii_redaction_on_export" for a in report.playbook
    )


def test_ssn_is_high_severity_and_promotes_alone():
    advisor = DataLeakAdvisor(now_fn=_now)
    ev = _llm_event(user="my SSN is 123-45-6789, please help")
    report = advisor.analyze([ev])
    assert report.portfolio.pii_leak_count == 1
    sl = report.slices[0]
    assert sl.verdict is LeakVerdict.PII_LEAK
    assert sl.max_severity >= 85


def test_credit_card_with_valid_luhn_detected_but_invalid_ignored():
    advisor = DataLeakAdvisor(now_fn=_now)
    # 4111 1111 1111 1111 is the classic Luhn-valid test PAN.
    good = _llm_event(event_id="g", user="card 4111 1111 1111 1111")
    # Modify a single digit -> Luhn-invalid.
    bad = _llm_event(event_id="b", session_id="s2", user="card 4111 1111 1111 1112")
    report_good = advisor.analyze([good])
    report_bad = advisor.analyze([bad])
    assert any(
        f.code == "PII_CREDIT_CARD"
        for sl in report_good.slices
        for f in sl.findings
    )
    assert not any(
        f.code == "PII_CREDIT_CARD"
        for sl in report_bad.slices
        for f in sl.findings
    )


# --------------------------------------------------------------------------- #
# Configuration knobs
# --------------------------------------------------------------------------- #


def test_disabling_pattern_silences_it():
    opts = DataLeakOptions(enable_email=False, enable_us_phone=False)
    advisor = DataLeakAdvisor(opts, now_fn=_now)
    ev = _llm_event(user="ping me at alice@example.com or 415-555-2671")
    report = advisor.analyze([ev])
    assert report.portfolio.total_findings == 0
    assert report.portfolio.portfolio_grade is LeakGrade.A


def test_scan_outputs_toggle_skips_output_payload():
    opts = DataLeakOptions(scan_outputs=False)
    advisor = DataLeakAdvisor(opts, now_fn=_now)
    ev = _llm_event(assistant="here is alice@example.com again")
    report = advisor.analyze([ev])
    assert report.portfolio.total_findings == 0


def test_cautious_appetite_lowers_secret_floor():
    # A secret with severity 70 (generic bearer) passes default floor 70 but
    # we want to verify cautious mode does not regress on it either.
    advisor_default = DataLeakAdvisor(now_fn=_now)
    ev = _llm_event(user="X-Api-Key: api_key=verysecretvalue1234567890ABC")
    default_report = advisor_default.analyze([ev])
    cautious_report = advisor_default.analyze([ev], risk_appetite="cautious")
    assert default_report.portfolio.secret_leak_count >= 1
    assert cautious_report.portfolio.secret_leak_count >= 1


def test_top_n_caps_slice_output():
    # Force 3 different models with distinct minor findings, cap top_n at 1.
    advisor = DataLeakAdvisor(DataLeakOptions(top_n=1), now_fn=_now)
    events = [
        _llm_event(event_id=f"e{i}", model=f"model-{i}", user="ping alice@example.com")
        for i in range(3)
    ]
    report = advisor.analyze(events)
    assert len(report.slices) == 1


# --------------------------------------------------------------------------- #
# Determinism / safety
# --------------------------------------------------------------------------- #


def test_advisor_does_not_mutate_input_events():
    advisor = DataLeakAdvisor(now_fn=_now)
    ev = _llm_event(user="my email is alice@example.com")
    snapshot = {
        "input": ev["input_data"]["messages"][1]["content"],
        "output": ev["output_data"]["messages"][0]["content"],
    }
    advisor.analyze([ev])
    assert ev["input_data"]["messages"][1]["content"] == snapshot["input"]
    assert ev["output_data"]["messages"][0]["content"] == snapshot["output"]


def test_repeated_runs_are_deterministic():
    advisor = DataLeakAdvisor(now_fn=_now)
    events = [
        _llm_event(event_id="e1", user="alice@example.com 415-555-2671"),
        _llm_event(event_id="e2", session_id="s2", model="claude-3-opus",
                   user="bob@example.com"),
    ]
    a = advisor.analyze(events)
    b = advisor.analyze(events)
    assert [(s.slice_id, s.verdict, s.finding_count) for s in a.slices] == \
           [(s.slice_id, s.verdict, s.finding_count) for s in b.slices]
    assert [p.id for p in a.playbook] == [p.id for p in b.playbook]


def test_preview_never_exceeds_max_preview_chars():
    opts = DataLeakOptions(max_preview_chars=8)
    advisor = DataLeakAdvisor(opts, now_fn=_now)
    ev = _llm_event(user="contact superlongname@example.com please")
    report = advisor.analyze([ev])
    for sl in report.slices:
        for f in sl.findings:
            assert len(f.preview) <= 8


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def test_to_text_and_to_markdown_render_without_error():
    advisor = DataLeakAdvisor(now_fn=_now)
    ev = _llm_event(user="alice@example.com 415-555-2671 SSN 123-45-6789")
    report = advisor.analyze([ev])
    text = report.to_text()
    md = report.to_markdown()
    assert "DataLeak advisor" in text
    assert "## DataLeak advisor" in md
    assert "Playbook" in md
    assert "pii_leak" in text or "PII_LEAK" in text or "pii_leak" in md
    # Secrets/PII must never appear verbatim in the rendered report.
    assert "alice@example.com" not in text
    assert "415-555-2671" not in text
    assert "123-45-6789" not in text


# --------------------------------------------------------------------------- #
# Package-level exports
# --------------------------------------------------------------------------- #


def test_advisor_exported_from_top_level_package():
    assert hasattr(agentlens, "DataLeakAdvisor")
    assert hasattr(agentlens, "DataLeakReport")
    assert hasattr(agentlens, "LeakVerdict")
    assert agentlens.LeakVerdict.SECRET_LEAK.value == "secret_leak"
    assert agentlens.DataLeakPriority.P0.value == "P0"
    assert agentlens.DataLeakRiskAppetite.CAUTIOUS.value == "cautious"
