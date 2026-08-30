"""Audit factual statements and citations against indexed evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from paperwise.evidence.models import EvidencePack


@dataclass
class GroundingReport:
    total_claims: int = 0
    grounded_claims: int = 0
    citation_coverage: float = 0.0
    evidence_coverage: float = 0.0
    invalid_citations: list[str] = field(default_factory=list)

    @property
    def grounding_score(self) -> float:
        if self.total_claims == 0:
            return 1.0
        return (self.citation_coverage + self.evidence_coverage) / 2

    def to_dict(self) -> dict:
        return {
            "total_claims": self.total_claims,
            "grounded_claims": self.grounded_claims,
            "citation_coverage": round(self.citation_coverage, 3),
            "evidence_coverage": round(self.evidence_coverage, 3),
            "grounding_score": round(self.grounding_score, 3),
            "invalid_citations": self.invalid_citations,
        }


class CitationGroundingAuditor:
    """Check that claims carry citations pointing at real source locations."""

    citation_pattern = re.compile(
        r"\[source:\s*(?:[^\]#]+?)(?:#L)?L?(\d+)(?:[-–]L?(\d+))?\s*\]",
        re.IGNORECASE,
    )

    def audit(
        self,
        report: str,
        evidence_packs: Iterable[EvidencePack],
        paper_dir: Path | None = None,
    ) -> GroundingReport:
        claim_lines = [
            line.strip()
            for line in report.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        cited = sum(1 for line in claim_lines if self.citation_pattern.search(line))
        snippets = [s for pack in evidence_packs for s in pack.snippets]
        grounded = 0
        invalid: list[str] = []

        for claim in claim_lines:
            match = self.citation_pattern.search(claim)
            if not match:
                continue
            start = int(match.group(1))
            end = int(match.group(2) or match.group(1))
            in_pack = any(
                s.start_line and start <= s.end_line and end >= s.start_line
                for s in snippets
            )
            on_disk = self._line_exists(paper_dir, start, end)
            if in_pack or on_disk:
                grounded += 1
            elif paper_dir is not None:
                invalid.append(match.group(0))

        return GroundingReport(
            total_claims=len(claim_lines),
            grounded_claims=grounded,
            citation_coverage=cited / len(claim_lines) if claim_lines else 1.0,
            evidence_coverage=grounded / cited if cited else (1.0 if not claim_lines else 0.0),
            invalid_citations=invalid,
        )

    @staticmethod
    def _line_exists(paper_dir: Path | None, start: int, end: int) -> bool:
        if paper_dir is None:
            return False
        text_path = paper_dir / "text.md"
        if not text_path.exists() or start < 1 or end < start:
            return False
        line_count = len(text_path.read_text(encoding="utf-8", errors="replace").splitlines())
        return start <= line_count and end <= line_count
