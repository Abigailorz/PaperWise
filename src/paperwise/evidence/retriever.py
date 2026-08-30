"""Section-aware chunking and hybrid retrieval into an EvidencePack."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional

from paperwise.evidence.models import (
    EvidencePack,
    EvidenceSnippet,
    StructureType,
)
from paperwise.memory.knowledge_base import Chunk


Reranker = Callable[[str, list[EvidenceSnippet]], list[EvidenceSnippet]]


def section_chunks(text: str, chunk_size: int = 900) -> list[dict]:
    """Split paper text by headings while retaining stable 1-based line numbers."""
    lines = text.splitlines()
    chunks: list[dict] = []
    current_section = "Front Matter"
    current_lines: list[str] = []
    start_line = 1 if lines else 0

    def flush() -> None:
        nonlocal current_lines, start_line
        if not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if content:
            chunks.append(
                {
                    "section": current_section,
                    "start_line": start_line,
                    "end_line": start_line + len(current_lines) - 1,
                    "content": content,
                }
            )
        current_lines = []

    for line_number, line in enumerate(lines, start=1):
        heading = re.match(r"^\s{0,3}(#{1,4})\s+(.+?)\s*$", line)
        numbered = re.match(r"^\s*(\d+(?:\.\d+)*)\.?\s+([A-Z][^:]{2,100})\s*$", line)
        common = re.match(
            r"^\s*(Abstract|Introduction|Related Work|Method|Methods|Experiments|Results|"
            r"Discussion|Conclusion|References|Limitations)\s*$",
            line,
            re.IGNORECASE,
        )
        is_heading = bool(heading or common)
        if is_heading or (numbered and len(line) < 100):
            flush()
            current_section = (heading.group(2) if heading else line).strip()
            start_line = line_number + 1
            continue
        if not current_lines:
            start_line = line_number
        current_lines.append(line)
        if sum(len(item) for item in current_lines) >= chunk_size:
            flush()
    flush()
    return chunks


class EvidenceRetriever:
    """Build and query the local/cross-paper evidence index."""

    def __init__(self, knowledge_base, reranker: Optional[Reranker] = None):
        self.kb = knowledge_base
        self.reranker = reranker

    def index_paper(self, paper_dir: Path, paper_id: Optional[str] = None) -> dict[str, int]:
        """Index text sections plus figures, tables, equations, and references."""
        paper_dir = Path(paper_dir)
        paper_id = paper_id or paper_dir.name
        counts = {"section": 0, "figure": 0, "table": 0, "equation": 0, "reference": 0}
        text_path = paper_dir / "text.md"
        if text_path.exists():
            for chunk in section_chunks(text_path.read_text(encoding="utf-8", errors="replace")):
                metadata = {
                    "type": "paper_section",
                    "structure_type": StructureType.SECTION.value,
                    "paper_id": paper_id,
                    "section": chunk["section"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                }
                self._add(
                    chunk["content"],
                    metadata,
                    f"section:{paper_id}:{chunk['start_line']}",
                )
                counts["section"] += 1

        self._index_json_items(paper_dir / "figures" / "index.json", StructureType.FIGURE, paper_id, counts)
        self._index_tables(paper_dir, paper_id, counts)
        self._index_formulas(paper_dir, paper_id, counts)
        self._index_references(paper_dir, paper_id, counts)
        self.kb.save()
        self.kb._indexed = False
        return counts

    def retrieve(
        self,
        query: str,
        paper_dir: Optional[Path] = None,
        scope: str = "current_paper",
        top_k: int = 6,
        min_score: float = 0.01,
        structure_types: Optional[Iterable[StructureType]] = None,
    ) -> EvidencePack:
        """Retrieve snippets for the current paper or the user's paper library."""
        if not query.strip():
            return EvidencePack(query=query, scope=scope, low_recall=True)
        paper_id = Path(paper_dir).name if paper_dir else None
        queries = self._expanded_queries(query)
        seen: dict[str, EvidenceSnippet] = {}

        for retrieval_query in queries:
            if scope == "current_paper" and not paper_id:
                break
            filters: dict = {"paper_id": paper_id} if paper_id and scope == "current_paper" else {}
            candidates = self.kb.search_chunks(retrieval_query, top_k=top_k * 2, filters=filters)
            used_fallback = bool(candidates and filters)
            if not candidates and scope == "current_paper" and filters:
                candidates = self.kb.search_chunks(retrieval_query, top_k=top_k * 2)
                used_fallback = bool(candidates)
            for chunk in candidates:
                metadata = dict(chunk.metadata or {})
                if metadata.get("structure_type") in {"paper_section", None}:
                    structure_type = StructureType.SECTION
                else:
                    try:
                        structure_type = StructureType(metadata["structure_type"])
                    except (KeyError, ValueError):
                        structure_type = StructureType.SECTION
                if structure_types and structure_type not in structure_types:
                    continue
                start_line = int(metadata.get("start_line", 0) or 0)
                end_line = int(metadata.get("end_line", 0) or 0)
                snippet = EvidenceSnippet(
                    evidence_id=str(metadata.get("doc_id") or chunk.id),
                    content=chunk.content,
                    structure_type=structure_type,
                    paper_id=str(metadata.get("paper_id", chunk.doc_id)),
                    section=str(metadata.get("section", "")),
                    start_line=start_line,
                    end_line=end_line,
                    page=int(metadata.get("page", 0) or 0),
                    location=str(metadata.get("source_path", metadata.get("location", ""))),
                    metadata={k: v for k, v in metadata.items() if k not in {"embedding"}},
                )
                previous = seen.get(snippet.evidence_id)
                if previous is None:
                    seen[snippet.evidence_id] = snippet

        snippets = list(seen.values())
        if self.reranker:
            snippets = self.reranker(query, snippets)
        else:
            search_chunks = [
                Chunk(
                    id=snippet.evidence_id,
                    content=snippet.content,
                    doc_id=snippet.paper_id,
                    chunk_index=index,
                    metadata=snippet.metadata,
                )
                for index, snippet in enumerate(snippets)
            ]
            ranked = self.kb.retriever.sparse.search(
                query,
                search_chunks,
                top_k=top_k,
            )
            for chunk, score in ranked:
                if chunk.id in seen:
                    seen[chunk.id].score = float(score)
            snippets = [seen[chunk.id] for chunk, _ in ranked if chunk.id in seen]

        low_recall = not snippets or used_fallback
        return EvidencePack(
            query=query,
            snippets=snippets[:top_k],
            scope=scope,
            retrieval_queries=queries,
            low_recall=low_recall,
        )

    def _expanded_queries(self, query: str) -> list[str]:
        base = query.strip()
        queries = [base]
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", base)]
        if len(words) > 3:
            queries.append(" ".join(words[:6]))
        if len(words) > 6:
            queries.append(" ".join(words[-6:]))
        return queries

    def _add(self, content: str, metadata: dict, doc_id: str) -> None:
        if doc_id in self.kb.docs or not content.strip():
            return
        metadata["doc_id"] = doc_id
        self.kb.add(content=content, metadata=metadata, doc_id=doc_id)

    def _index_json_items(self, path: Path, structure_type: StructureType, paper_id: str, counts: dict) -> None:
        if not path.exists():
            return
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        for item in items:
            if structure_type == StructureType.FIGURE:
                content = f"Figure {item.get('index', '')}: {item.get('caption', '')}"
                doc_id = f"figure:{paper_id}:{item.get('index', len(items))}"
                source_path = item.get("path", "")
            else:
                content = json.dumps(item, ensure_ascii=False)
                doc_id = f"{structure_type.value}:{paper_id}:{item.get('index', len(items))}"
                source_path = ""
            self._add(
                content,
                {
                    "type": structure_type.value,
                    "structure_type": structure_type.value,
                    "paper_id": paper_id,
                    "page": item.get("page", 0),
                    "source_path": source_path,
                    "caption": item.get("caption", ""),
                },
                doc_id,
            )
            counts[structure_type.value] += 1

    def _index_tables(self, paper_dir: Path, paper_id: str, counts: dict) -> None:
        tables_dir = paper_dir / "tables"
        if not tables_dir.exists():
            return
        for path in sorted(tables_dir.glob("*.json")):
            try:
                table = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows = [" | ".join(map(str, row)) for row in table.get("rows", [])[:20]]
            content = f"Table {table.get('index', '')}: {table.get('caption', '')}\n"
            content += " | ".join(map(str, table.get("headers", [])))
            if rows:
                content += "\n" + "\n".join(rows)
            self._add(
                content,
                {
                    "type": "table",
                    "structure_type": "table",
                    "paper_id": paper_id,
                    "page": table.get("page", 0),
                    "source_path": str(path.relative_to(paper_dir)),
                    "caption": table.get("caption", ""),
                },
                f"table:{paper_id}:{path.stem}",
            )
            counts["table"] += 1

    def _index_formulas(self, paper_dir: Path, paper_id: str, counts: dict) -> None:
        formulas_dir = paper_dir / "formulas"
        if not formulas_dir.exists():
            return
        for path in sorted(formulas_dir.glob("*.tex")):
            latex = path.read_text(encoding="utf-8", errors="replace")
            self._add(
                latex,
                {
                    "type": "equation",
                    "structure_type": "equation",
                    "paper_id": paper_id,
                    "source_path": str(path.relative_to(paper_dir)),
                },
                f"equation:{paper_id}:{path.stem}",
            )
            counts["equation"] += 1

    def _index_references(self, paper_dir: Path, paper_id: str, counts: dict) -> None:
        path = paper_dir / "references.json"
        if not path.exists():
            return
        try:
            references = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        for reference in references:
            text = reference.get("text", "")
            self._add(
                text,
                {
                    "type": "reference",
                    "structure_type": "reference",
                    "paper_id": paper_id,
                    "reference_id": reference.get("id", 0),
                },
                f"reference:{paper_id}:{reference.get('id', uuid.uuid4().hex[:8])}",
            )
            counts["reference"] += 1
