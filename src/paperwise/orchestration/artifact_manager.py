"""Artifact serialization manager for the dynamic orchestration layer.

Converts ad-hoc JSON outputs (facts.json, verified.json, reports, slides) into
typed Artifact dataclasses under workspace/{paper_id}/artifacts/.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from paperwise.orchestration.types import (
    Artifact,
    ClaimArtifact,
    MethodArtifact,
    PaperArtifact,
    ReportArtifact,
    SlideArtifact,
)


class ArtifactManager:
    """Save and load typed artifacts as JSON files."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.artifacts_dir = self.workspace / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.artifacts_dir / f"{name}.json"

    def save(self, name: str, artifact: Any) -> Path:
        """Serialize an artifact to JSON and return the saved path."""
        data = {"artifact_type": type(artifact).__name__, "payload": self._to_dict(artifact)}
        path = self._path(name)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> Any:
        path = self._path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("payload")

    def load_typed(self, name: str, cls: type) -> Any:
        raw = self.load(name)
        if raw is None:
            return None
        return cls(**raw)

    @staticmethod
    def _to_dict(obj: Any) -> Any:
        if is_dataclass(obj):
            return {k: ArtifactManager._to_dict(v) for k, v in asdict(obj).items()}
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, list):
            return [ArtifactManager._to_dict(i) for i in obj]
        if isinstance(obj, dict):
            return {k: ArtifactManager._to_dict(v) for k, v in obj.items()}
        return obj

    def from_facts_json(self, facts_path: Path) -> tuple[PaperArtifact, MethodArtifact]:
        """Build PaperArtifact and MethodArtifact from a reader facts.json file."""
        raw: dict = {}
        if facts_path.exists():
            try:
                raw = json.loads(facts_path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}

        paper = PaperArtifact(
            artifact_type="PaperArtifact",
            path=facts_path,
            title=raw.get("title", "") or raw.get("paper_title", ""),
            authors=raw.get("authors", []) if isinstance(raw.get("authors"), list) else [],
            abstract=raw.get("abstract", ""),
            metadata={"source": str(facts_path)},
        )

        claims = []
        for c in raw.get("claims", []):
            if isinstance(c, dict):
                claims.append(ClaimArtifact(
                    artifact_type="ClaimArtifact",
                    claim=str(c.get("claim", "")),
                    evidence=c.get("evidence", []),
                    confidence=float(c.get("confidence", 0.0)),
                ))

        method = MethodArtifact(
            artifact_type="MethodArtifact",
            path=facts_path,
            problem=raw.get("problem", ""),
            method=raw.get("method", "") or raw.get("approach", ""),
            key_idea=raw.get("key_idea", ""),
            pipeline=raw.get("pipeline", []) if isinstance(raw.get("pipeline"), list) else [],
            claims=claims,
        )
        return paper, method

    def from_verified_json(self, verified_path: Path) -> list[ClaimArtifact]:
        """Build a list of ClaimArtifacts from a verifier verified.json file."""
        raw: dict = {}
        if verified_path.exists():
            try:
                raw = json.loads(verified_path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}

        claims = []
        for item in raw.get("claims", []):
            if isinstance(item, dict):
                claims.append(ClaimArtifact(
                    artifact_type="ClaimArtifact",
                    claim=str(item.get("claim", "")),
                    evidence=[item.get("status", "")],
                    source_lines=[],
                    confidence=1.0 if item.get("status") == "verified" else 0.0,
                ))
        return claims

    def from_report(self, report_path: Path, sections: dict[str, Path] | None = None) -> ReportArtifact:
        """Build a ReportArtifact from the generated report file."""
        sections = sections or {}
        return ReportArtifact(
            artifact_type="ReportArtifact",
            path=report_path,
            outline={"title": report_path.stem},
            section_paths={k: str(v) for k, v in sections.items()},
            final_report_path=report_path,
            citations_verified=False,
        )

    def from_pptx(self, pptx_path: Path) -> SlideArtifact:
        """Build a SlideArtifact from the generated slides file."""
        return SlideArtifact(
            artifact_type="SlideArtifact",
            path=pptx_path,
            title=pptx_path.stem,
            bullets=[],
        )
