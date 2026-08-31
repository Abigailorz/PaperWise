from paperwise.memory.question_registry import ResearchQuestionRegistry
from paperwise.memory.research_state import ResearchState
from paperwise.opportunity.models import OpportunityType, ResearchOpportunity
from paperwise.research_graph.builder import ResearchGraphBuilder
from paperwise.research_graph.models import EntityType, RelationType


def test_graph_merges_questions_and_opportunities():
    state = ResearchState(state_id="state", user_id="default")
    opportunity = ResearchOpportunity(
        type=OpportunityType.KNOWLEDGE_GAP,
        title="Unclear supervision signal",
        description="The paper does not compare semantic supervision alternatives.",
        confidence=0.8,
        importance=0.8,
    )
    state.opportunities.append(opportunity)
    question = ResearchQuestionRegistry.derive([opportunity])[0]
    state.questions.append(question)

    graph = ResearchGraphBuilder().build(state)
    question_nodes = [node for node in graph.nodes if node.entity_type == EntityType.RESEARCH_QUESTION]
    opportunity_nodes = [node for node in graph.nodes if node.entity_type == EntityType.OPPORTUNITY]
    assert any(node.node_id == question.question_id for node in question_nodes)
    assert opportunity_nodes
    assert any(
        edge.source_id == question.question_id
        and edge.target_id == opportunity_nodes[0].node_id
        and edge.relation == RelationType.HAS_GAP
        for edge in graph.edges
    )
