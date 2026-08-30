"""Mergeable persistence for the user-level research graph."""

from __future__ import annotations

from pathlib import Path

from paperwise.memory.storage import create_storage
from paperwise.research_graph.models import ResearchGraph


class ResearchGraphStore:
    def __init__(self, workspace: Path, user_id: str = "default", backend: str = "sqlite"):
        self.user_id = user_id
        self.store = create_storage(backend, Path(workspace) / ".research_graph")
        self._graph: ResearchGraph | None = None

    def load(self) -> ResearchGraph:
        if self._graph is None:
            data = self.store.get("research_graph", self.user_id)
            if data and "graph" in data:
                try:
                    self._graph = ResearchGraph.from_dict(data["graph"])
                except Exception:
                    self._graph = None
            if self._graph is None:
                self._graph = ResearchGraph(
                    graph_id=f"rg_{self.user_id}",
                    user_id=self.user_id,
                )
        return self._graph

    def save(self, graph: ResearchGraph) -> ResearchGraph:
        graph.touch()
        self._graph = graph
        self.store.put("research_graph", self.user_id, {"graph": graph.to_dict()})
        return graph

    def merge(self, graph: ResearchGraph) -> ResearchGraph:
        current = self.load()
        current.merge(graph)
        return self.save(current)
