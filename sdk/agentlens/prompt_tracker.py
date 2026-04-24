"""Prompt Version Tracker — track prompt template evolution and correlate with performance.

Track prompt template versions over time, diff changes between versions,
and correlate prompt modifications with session quality metrics.

Usage::

    from agentlens import PromptVersionTracker

    tracker = PromptVersionTracker()

    # Register prompt versions
    v1 = tracker.register("summarizer", "Summarize the following text: {text}")
    v2 = tracker.register("summarizer", "You are a concise summarizer. Summarize: {text}", tags=["concise"])

    # Record performance for a version
    tracker.record_outcome(v2.version_id, tokens=450, latency_ms=1200, quality_score=0.92)
    tracker.record_outcome(v2.version_id, tokens=380, latency_ms=1050, quality_score=0.88)

    # Compare versions
    diff = tracker.diff("summarizer", v1.version_number, v2.version_number)

    # Get best performing version
    report = tracker.report("summarizer")
    print(report.best_version)  # version with highest avg quality

    # Export full history
    data = tracker.export_json()
"""

from __future__ import annotations

import hashlib
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff
from enum import Enum
from typing import Any


class DiffKind(Enum):
    """Kind of change between two prompt versions."""
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


@dataclass
class PromptVersion:
    """A single version of a prompt template."""
    version_id: str
    prompt_name: str
    version_number: int
    template: str
    content_hash: str
    created_at: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_version: str | None = None

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "prompt_name": self.prompt_name,
            "version_number": self.version_number,
            "template": self.template,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "tags": self.tags,
            "metadata": self.metadata,
            "parent_version": self.parent_version,
        }


@dataclass
class Outcome:
    """A recorded outcome for a prompt version execution."""
    outcome_id: str
    version_id: str
    timestamp: str
    tokens: int = 0
    latency_ms: float = 0.0
    quality_score: float | None = None
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "outcome_id": self.outcome_id,
            "version_id": self.version_id,
            "timestamp": self.timestamp,
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "quality_score": self.quality_score,
            "success": self.success,
            "metadata": self.metadata,
        }


@dataclass
class VersionStats:
    """Aggregated statistics for a prompt version."""
    version_id: str
    version_number: int
    runs: int
    avg_tokens: float
    avg_latency_ms: float
    avg_quality: float | None
    success_rate: float
    min_quality: float | None
    max_quality: float | None
    p50_latency_ms: float
    p95_latency_ms: float

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "version_number": self.version_number,
            "runs": self.runs,
            "avg_tokens": round(self.avg_tokens, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "avg_quality": round(self.avg_quality, 4) if self.avg_quality is not None else None,
            "success_rate": round(self.success_rate, 4),
            "min_quality": round(self.min_quality, 4) if self.min_quality is not None else None,
            "max_quality": round(self.max_quality, 4) if self.max_quality is not None else None,
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
        }


@dataclass
class PromptDiff:
    """Diff between two prompt versions."""
    prompt_name: str
    from_version: int
    to_version: int
    kind: DiffKind
    unified_diff: str
    char_delta: int
    line_delta: int
    from_hash: str
    to_hash: str

    def to_dict(self) -> dict:
        return {
            "prompt_name": self.prompt_name,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "kind": self.kind.value,
            "unified_diff": self.unified_diff,
            "char_delta": self.char_delta,
            "line_delta": self.line_delta,
            "from_hash": self.from_hash,
            "to_hash": self.to_hash,
        }


@dataclass
class PromptReport:
    """Performance report for a prompt across all versions."""
    prompt_name: str
    total_versions: int
    total_runs: int
    version_stats: list[VersionStats]
    best_version: VersionStats | None
    latest_version: VersionStats | None
    quality_trend: list[dict]  # [{version, avg_quality}]

    def to_dict(self) -> dict:
        return {
            "prompt_name": self.prompt_name,
            "total_versions": self.total_versions,
            "total_runs": self.total_runs,
            "version_stats": [s.to_dict() for s in self.version_stats],
            "best_version": self.best_version.to_dict() if self.best_version else None,
            "latest_version": self.latest_version.to_dict() if self.latest_version else None,
            "quality_trend": self.quality_trend,
        }


def _hash_template(template: str) -> str:
    """SHA-256 hash of a prompt template (first 12 hex chars)."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(data: list[float], pct: float) -> float:
    """Simple percentile (nearest-rank)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = max(0, min(int(len(sorted_data) * pct / 100), len(sorted_data) - 1))
    return sorted_data[k]


class PromptVersionTracker:
    """Track prompt template versions and correlate with performance.

    Maintains a registry of prompt templates by name, auto-incrementing
    version numbers. Records execution outcomes per version and produces
    comparison reports.

    Args:
        dedup: If True (default), skip registration when the template
            content hash matches the latest version.
    """

    def __init__(self, *, dedup: bool = True) -> None:
        self._dedup = dedup
        # prompt_name -> [PromptVersion] ordered by version_number
        self._versions: dict[str, list[PromptVersion]] = {}
        # version_id -> [Outcome]
        self._outcomes: dict[str, list[Outcome]] = {}
        # version_id -> PromptVersion (flat lookup)
        self._version_index: dict[str, PromptVersion] = {}
        # tag -> set of version_ids (inverted index for O(1) tag lookup)
        self._tag_index: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        prompt_name: str,
        template: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PromptVersion:
        """Register a new prompt version.

        Args:
            prompt_name: Logical name of the prompt (e.g. ``"summarizer"``).
            template: The full prompt template text.
            tags: Optional tags for categorization.
            metadata: Optional key-value metadata.

        Returns:
            The created ``PromptVersion``. If dedup is enabled and the
            template hasn't changed, returns the existing latest version.

        Raises:
            ValueError: If prompt_name or template is empty.
        """
        if not prompt_name or not prompt_name.strip():
            raise ValueError("prompt_name must be non-empty")
        if not template:
            raise ValueError("template must be non-empty")

        content_hash = _hash_template(template)
        existing = self._versions.get(prompt_name, [])

        # Dedup: if latest version has same hash, return it
        if self._dedup and existing and existing[-1].content_hash == content_hash:
            return existing[-1]

        version_number = len(existing) + 1
        parent = existing[-1].version_id if existing else None

        version = PromptVersion(
            version_id=uuid.uuid4().hex[:16],
            prompt_name=prompt_name,
            version_number=version_number,
            template=template,
            content_hash=content_hash,
            created_at=_now_iso(),
            tags=tags or [],
            metadata=metadata or {},
            parent_version=parent,
        )

        self._versions.setdefault(prompt_name, []).append(version)
        self._version_index[version.version_id] = version
        self._outcomes[version.version_id] = []
        # Update inverted tag index
        for tag in version.tags:
            self._tag_index.setdefault(tag, set()).add(version.version_id)
        return version

    # ------------------------------------------------------------------
    # Outcome recording
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        version_id: str,
        *,
        tokens: int = 0,
        latency_ms: float = 0.0,
        quality_score: float | None = None,
        success: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Outcome:
        """Record a performance outcome for a prompt version.

        Args:
            version_id: The version to record against.
            tokens: Total tokens used.
            latency_ms: Response latency in milliseconds.
            quality_score: Optional quality score (0.0-1.0).
            success: Whether the call succeeded.
            metadata: Optional key-value metadata.

        Returns:
            The created Outcome.

        Raises:
            KeyError: If version_id is not found.
            ValueError: If quality_score is out of range.
        """
        if version_id not in self._version_index:
            raise KeyError(f"Unknown version_id: {version_id}")
        if quality_score is not None and not (0.0 <= quality_score <= 1.0):
            raise ValueError("quality_score must be between 0.0 and 1.0")

        outcome = Outcome(
            outcome_id=uuid.uuid4().hex[:16],
            version_id=version_id,
            timestamp=_now_iso(),
            tokens=tokens,
            latency_ms=latency_ms,
            quality_score=quality_score,
            success=success,
            metadata=metadata or {},
        )
        self._outcomes[version_id].append(outcome)
        return outcome

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_versions(self, prompt_name: str) -> list[PromptVersion]:
        """Get all versions of a prompt, ordered by version number."""
        return list(self._versions.get(prompt_name, []))

    def get_version(self, version_id: str) -> PromptVersion:
        """Get a specific version by ID.

        Raises:
            KeyError: If not found.
        """
        if version_id not in self._version_index:
            raise KeyError(f"Unknown version_id: {version_id}")
        return self._version_index[version_id]

    def get_latest(self, prompt_name: str) -> PromptVersion | None:
        """Get the latest version of a prompt, or None."""
        versions = self._versions.get(prompt_name, [])
        return versions[-1] if versions else None

    def list_prompts(self) -> list[str]:
        """List all registered prompt names."""
        return sorted(self._versions.keys())

    def get_outcomes(self, version_id: str) -> list[Outcome]:
        """Get all outcomes for a version."""
        return list(self._outcomes.get(version_id, []))

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(self, prompt_name: str, from_version: int, to_version: int) -> PromptDiff:
        """Compute a diff between two versions of a prompt.

        Args:
            prompt_name: The prompt name.
            from_version: Source version number.
            to_version: Target version number.

        Returns:
            A PromptDiff with unified diff and change metrics.

        Raises:
            KeyError: If prompt or versions not found.
        """
        versions = self._versions.get(prompt_name)
        if not versions:
            raise KeyError(f"Unknown prompt: {prompt_name}")

        from_v = next((v for v in versions if v.version_number == from_version), None)
        to_v = next((v for v in versions if v.version_number == to_version), None)
        if from_v is None:
            raise KeyError(f"Version {from_version} not found for {prompt_name}")
        if to_v is None:
            raise KeyError(f"Version {to_version} not found for {prompt_name}")

        from_lines = from_v.template.splitlines(keepends=True)
        to_lines = to_v.template.splitlines(keepends=True)

        diff_lines = list(unified_diff(
            from_lines, to_lines,
            fromfile=f"{prompt_name} v{from_version}",
            tofile=f"{prompt_name} v{to_version}",
        ))
        diff_text = "".join(diff_lines)

        char_delta = len(to_v.template) - len(from_v.template)
        line_delta = len(to_lines) - len(from_lines)

        if from_v.content_hash == to_v.content_hash:
            kind = DiffKind.UNCHANGED
        else:
            kind = DiffKind.MODIFIED

        return PromptDiff(
            prompt_name=prompt_name,
            from_version=from_version,
            to_version=to_version,
            kind=kind,
            unified_diff=diff_text,
            char_delta=char_delta,
            line_delta=line_delta,
            from_hash=from_v.content_hash,
            to_hash=to_v.content_hash,
        )

    # ------------------------------------------------------------------
    # Stats & Reporting
    # ------------------------------------------------------------------

    def _compute_stats(self, version: PromptVersion) -> VersionStats:
        outcomes = self._outcomes.get(version.version_id, [])
        runs = len(outcomes)
        if runs == 0:
            return VersionStats(
                version_id=version.version_id,
                version_number=version.version_number,
                runs=0, avg_tokens=0, avg_latency_ms=0,
                avg_quality=None, success_rate=0,
                min_quality=None, max_quality=None,
                p50_latency_ms=0, p95_latency_ms=0,
            )

        # Single-pass collection replaces 4 separate list comprehensions
        # over outcomes (O(4·N) → O(N)), reducing allocation pressure
        # and iteration overhead for large outcome sets.
        total_tokens = 0
        latencies: list[float] = []
        qualities: list[float] = []
        successes = 0

        for o in outcomes:
            total_tokens += o.tokens
            latencies.append(o.latency_ms)
            if o.quality_score is not None:
                qualities.append(o.quality_score)
            if o.success:
                successes += 1

        total_latency = sum(latencies)

        return VersionStats(
            version_id=version.version_id,
            version_number=version.version_number,
            runs=runs,
            avg_tokens=total_tokens / runs,
            avg_latency_ms=total_latency / runs,
            avg_quality=sum(qualities) / len(qualities) if qualities else None,
            success_rate=successes / runs,
            min_quality=min(qualities) if qualities else None,
            max_quality=max(qualities) if qualities else None,
            p50_latency_ms=_percentile(latencies, 50),
            p95_latency_ms=_percentile(latencies, 95),
        )

    def report(self, prompt_name: str) -> PromptReport:
        """Generate a performance report for a prompt across all versions.

        Args:
            prompt_name: The prompt to report on.

        Returns:
            A PromptReport with per-version stats, best/latest version,
            and quality trend.

        Raises:
            KeyError: If prompt not found.
        """
        versions = self._versions.get(prompt_name)
        if not versions:
            raise KeyError(f"Unknown prompt: {prompt_name}")

        all_stats = [self._compute_stats(v) for v in versions]
        total_runs = sum(s.runs for s in all_stats)

        # Best version by quality (fallback: lowest latency)
        with_quality = [s for s in all_stats if s.avg_quality is not None and s.runs > 0]
        with_runs = [s for s in all_stats if s.runs > 0]
        best = None
        if with_quality:
            best = max(with_quality, key=lambda s: s.avg_quality)  # type: ignore
        elif with_runs:
            best = min(with_runs, key=lambda s: s.avg_latency_ms)

        latest_stats = all_stats[-1] if all_stats else None

        quality_trend = []
        for s in all_stats:
            if s.runs > 0:
                entry: dict[str, Any] = {"version": s.version_number}
                if s.avg_quality is not None:
                    entry["avg_quality"] = round(s.avg_quality, 4)
                entry["avg_latency_ms"] = round(s.avg_latency_ms, 2)
                entry["runs"] = s.runs
                quality_trend.append(entry)

        return PromptReport(
            prompt_name=prompt_name,
            total_versions=len(versions),
            total_runs=total_runs,
            version_stats=all_stats,
            best_version=best,
            latest_version=latest_stats,
            quality_trend=quality_trend,
        )

    # ------------------------------------------------------------------
    # Search by tag
    # ------------------------------------------------------------------

    def search_by_tag(self, tag: str) -> list[PromptVersion]:
        """Find all versions across all prompts that have a given tag.

        Uses an inverted tag index for O(matching) lookup instead of
        scanning all versions across all prompts (was O(total_versions)).
        """
        version_ids = self._tag_index.get(tag)
        if not version_ids:
            return []
        return [self._version_index[vid] for vid in version_ids
                if vid in self._version_index]

    # ------------------------------------------------------------------
    # Rollback helper
    # ------------------------------------------------------------------

    def rollback(self, prompt_name: str, to_version: int) -> PromptVersion:
        """Create a new version that reverts to a previous version's template.

        Args:
            prompt_name: The prompt name.
            to_version: The version number to roll back to.

        Returns:
            A new PromptVersion with the old template content.

        Raises:
            KeyError: If prompt or version not found.
        """
        versions = self._versions.get(prompt_name)
        if not versions:
            raise KeyError(f"Unknown prompt: {prompt_name}")
        target = next((v for v in versions if v.version_number == to_version), None)
        if target is None:
            raise KeyError(f"Version {to_version} not found for {prompt_name}")

        return self.register(
            prompt_name,
            target.template,
            tags=target.tags + ["rollback"],
            metadata={"rolled_back_from": versions[-1].version_number, "rolled_back_to": to_version},
        )

    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    def export_json(self) -> dict:
        """Export full tracker state as a JSON-serializable dict."""
        prompts = {}
        for name, versions in self._versions.items():
            prompts[name] = {
                "versions": [v.to_dict() for v in versions],
                "outcomes": {
                    v.version_id: [o.to_dict() for o in self._outcomes.get(v.version_id, [])]
                    for v in versions
                },
            }
        return {"prompts": prompts, "total_prompts": len(prompts)}

    def import_json(self, data: dict) -> int:
        """Import tracker state from a previously exported dict.

        Args:
            data: Dict from ``export_json()``.

        Returns:
            Number of versions imported.
        """
        count = 0
        for name, prompt_data in data.get("prompts", {}).items():
            for vd in prompt_data.get("versions", []):
                version = PromptVersion(
                    version_id=vd["version_id"],
                    prompt_name=vd["prompt_name"],
                    version_number=vd["version_number"],
                    template=vd["template"],
                    content_hash=vd["content_hash"],
                    created_at=vd["created_at"],
                    tags=vd.get("tags", []),
                    metadata=vd.get("metadata", {}),
                    parent_version=vd.get("parent_version"),
                )
                self._versions.setdefault(name, []).append(version)
                self._version_index[version.version_id] = version
                self._outcomes.setdefault(version.version_id, [])
                # Update inverted tag index
                for tag in version.tags:
                    self._tag_index.setdefault(tag, set()).add(version.version_id)
                count += 1

                # Import outcomes for this version
                outcomes_data = prompt_data.get("outcomes", {}).get(version.version_id, [])
                for od in outcomes_data:
                    outcome = Outcome(
                        outcome_id=od["outcome_id"],
                        version_id=od["version_id"],
                        timestamp=od["timestamp"],
                        tokens=od.get("tokens", 0),
                        latency_ms=od.get("latency_ms", 0.0),
                        quality_score=od.get("quality_score"),
                        success=od.get("success", True),
                        metadata=od.get("metadata", {}),
                    )
                    self._outcomes[version.version_id].append(outcome)
        return count
