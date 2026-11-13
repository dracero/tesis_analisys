"""Orquestación del Grafo Multi-Agente con LangGraph para Análisis de Tesis."""

from langgraph.graph import StateGraph, START, END

from src.state import GraphState
from src.agents.pdf_parser import pdf_parser_node
from src.agents.verifier import reference_verifier_node
from src.agents.evaluator import tutor_evaluator_node
from src.agents.reporter import reporter_node


def build_graph():
    """Construye y compila el flujo secuencial de 4 agentes para auditar tesis en PDF."""
    workflow = StateGraph(GraphState)

    workflow.add_node("pdf_parser", pdf_parser_node)
    workflow.add_node("verifier", reference_verifier_node)
    workflow.add_node("evaluator", tutor_evaluator_node)
    workflow.add_node("reporter", reporter_node)

    workflow.add_edge(START, "pdf_parser")
    workflow.add_edge("pdf_parser", "verifier")
    workflow.add_edge("verifier", "evaluator")
    workflow.add_edge("evaluator", "reporter")
    workflow.add_edge("reporter", END)

    return workflow.compile()

