"""
models.py - Pydantic / dataclass data models for the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompanyRecord:
    """Raw data loaded from the input Excel."""

    sno: int
    cin: str
    company_name: str

    def __str__(self) -> str:
        return f"[{self.sno}] {self.company_name} ({self.cin})"


@dataclass
class ClassificationResult:
    """Structured classification output from the LLM."""

    # Core classification
    domain: str = "NEITHER"                 # ECE | CSE | BOTH | NEITHER
    confidence: str = "LOW"                 # HIGH | MEDIUM | LOW
    primary_domain_area: str = "UNKNOWN"    # e.g. "Semiconductor design"
    hardware_or_software: str = "Neither"   # Hardware | Software | Both | Neither

    # Hiring signals
    hiring_possible: str = "UNKNOWN"        # YES | NO | UNKNOWN
    fresher_friendly: str = "UNKNOWN"       # YES | NO | UNKNOWN
    likely_roles: str = "UNKNOWN"           # Comma-separated role names

    # Diagnostic
    reason: str = ""                        # Brief justification
    error: Optional[str] = None            # Non-None if classification failed

    @property
    def success(self) -> bool:
        """True when the result was produced without errors."""
        return self.error is None


@dataclass
class PipelineStats:
    """Running statistics collected during pipeline execution."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    retried: int = 0
    elapsed_seconds: float = 0.0
    partial_saves: int = 0
    domain_counts: dict[str, int] = field(
        default_factory=lambda: {"ECE": 0, "CSE": 0, "BOTH": 0, "NEITHER": 0}
    )

    def record(self, result: ClassificationResult) -> None:
        """Update counters from a finished result."""
        if result.success:
            self.succeeded += 1
            key = result.domain.upper()
            self.domain_counts[key] = self.domain_counts.get(key, 0) + 1
        else:
            self.failed += 1

    def summary_lines(self) -> list[str]:
        """Return a list of human-readable summary lines."""
        return [
            f"  Total companies          : {self.total}",
            f"  Classified successfully  : {self.succeeded}",
            f"  Failed / errored         : {self.failed}",
            f"  Skipped (empty input)    : {self.skipped}",
            f"  Retried API calls        : {self.retried}",
            f"  Partial saves            : {self.partial_saves}",
            f"  Elapsed time             : {_fmt_seconds(self.elapsed_seconds)}",
            "  Domain breakdown:",
            f"    ECE     : {self.domain_counts.get('ECE', 0)}",
            f"    CSE     : {self.domain_counts.get('CSE', 0)}",
            f"    BOTH    : {self.domain_counts.get('BOTH', 0)}",
            f"    NEITHER : {self.domain_counts.get('NEITHER', 0)}",
        ]


def _fmt_seconds(secs: float) -> str:
    mins, s = divmod(int(secs), 60)
    return f"{mins}m {s}s" if mins else f"{s}s"
