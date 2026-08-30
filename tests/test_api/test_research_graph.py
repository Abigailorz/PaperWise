import json

from paperwise.research_graph import EntityType, ResearchGraph, ResearchGraphStore, ResearchNode


def test_research_graph_endpoint_returns_user_graph(client):
    from paperwise.config.settings import get_settings

    graph = ResearchGraphStore(get_settings().workspace_dir).save(
        ResearchGraph(graph_id="rg_test", user_id="default")
    )
    response = client.get("/api/research-graph")
    assert response.status_code == 200
    assert response.json()["graph_id"] == graph.graph_id
    assert response.json()["stats"]["nodes"] == 0


def test_research_graph_endpoint_returns_paper_graph(client, tmp_path):
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    graph = ResearchGraph(graph_id="rg_paper", user_id="default")
    graph.add_node(ResearchNode(
        node_id="paper_test", entity_type=EntityType.PAPER,
        label="Test paper",
    ))
    graph.save(paper_dir / "research_graph.json")

    response = client.get("/api/research-graph", params={"paper_dir": str(paper_dir)})
    assert response.status_code == 200
    assert response.json()["graph_id"] == "rg_paper"


def test_research_graph_endpoint_missing_paper_graph(client):
    response = client.get(
        "/api/research-graph", params={"paper_dir": "does-not-exist"}
    )
    assert response.status_code == 404
