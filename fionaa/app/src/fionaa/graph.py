"""Graph topology and compatibility exports for existing runners."""
from fionaa.domain.applications import LoanType
from fionaa.domain.documents import AnnualAccountsSchema
from fionaa.domain.assessments import CompaniesHouseResult, FinancialAssessmentResult, PolicyCheckResult
from fionaa.policy_loader import load_policy_text
from fionaa.prompts import COMPANIES_HOUSE_PROMPT, WEB_SEARCH_PROMPT
from typing import Optional
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from fionaa.workflow.state import AgentContext, ApplicationState
from fionaa.integrations.checkpointing import build_checkpointer, checkpoint_config
from fionaa.workflow.common import tools_for
from fionaa.workflow.loading import DocumentSpec, DOCUMENT_SPECS, load_application
from fionaa.workflow.policy import check_against_policy
from fionaa.workflow.companies_house import check_companies_house
from fionaa.workflow.financial import check_financial_assessment
from fionaa.workflow.web_search import search_web
from fionaa.workflow.decision import reject_no_company, synthesize_decision
from fionaa.workflow.validation import validate_final_decision
from fionaa.workflow.triage import (
    forward_to_underwriter,
    return_to_applicant,
    route_by_triage,
    triage_application,
)

def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """Compiled per invocation, not once at module load. The checkpointer (if
    any) has to be built from that invocation's customer-scoped session — see
    `build_checkpointer` — so it can't be baked into a module-level singleton
    the way an uncheckpointed graph could be.

    `context_schema=AgentContext` registers `store`/`policy_docs`/`tools` as
    run-scoped runtime context rather than checkpointed state — see
    `AgentContext`'s docstring for why that split is what makes checkpointing
    actually work here.
    """
    g = StateGraph(ApplicationState, context_schema=AgentContext)
    g.add_node("load_application", load_application)
    g.add_node("companies_house", check_companies_house)
    g.add_node("reject_no_company", reject_no_company)
    g.add_node("policy_check", check_against_policy)
    g.add_node("financial_assessment", check_financial_assessment)
    g.add_node("web_search", search_web)
    g.add_node("synthesize_decision", synthesize_decision)
    g.add_node("validate_final_decision", validate_final_decision)
    g.add_node("triage", triage_application)
    g.add_node("to_underwriter", forward_to_underwriter)
    g.add_node("return_to_applicant", return_to_applicant)

    g.add_edge(START, "load_application")
    # companies_house runs first, not policy_check: it only needs `application`
    # (same as policy_check), and identity verification gates everything else
    # in the graph -- a company that can't be found/identified makes the rest
    # of the assessment moot, so there's no reason to run policy_check first
    # only to discard it on the reject_no_company branch. companies_house
    # routes dynamically via the Command it returns -- "policy_check" if the
    # company was confirmed, "reject_no_company" otherwise -- so no static
    # edge to either is declared here.
    g.add_edge("load_application", "companies_house")
    # Complete the evidence before annotating policy claims for the reviewer.
    g.add_edge("policy_check", "financial_assessment")
    g.add_edge("financial_assessment", "web_search")
    # Synthesis supplies an advisory recommendation; validation adds claim checks.
    g.add_edge("web_search", "synthesize_decision")
    g.add_edge("synthesize_decision", "validate_final_decision")

    # Both endings converge on triage, which asks a question neither of them
    # answers: does this submission have enough usable documentation for a
    # human underwriter to work on? reject_no_company arrives here too because
    # a company Companies House couldn't find is a lookup a human investigates,
    # not something the applicant can fix by uploading another file -- with
    # complete paperwork it still goes to the underwriter.
    g.add_edge("reject_no_company", "triage")
    g.add_edge("validate_final_decision", "triage")
    # The graph's only conditional edge. companies_house branches via the
    # Command it returns instead, because there the routing fact falls out of
    # the node's own LLM work; here it's a pure function of state
    # (route_by_triage is a one-line lookup), so a conditional edge keeps the
    # branch visible in the compiled graph rather than buried in a node.
    g.add_conditional_edges("triage", route_by_triage, {
        "underwriter": "to_underwriter",
        "return_to_applicant": "return_to_applicant",
    })
    # Two terminal nodes, not one node with an if: each writes its own handoff
    # artifact (decision/to_underwriter.json, decision/to_applicant.json) so a
    # downstream app watches one key and never has to read a route field out of
    # a shared document to learn whether it should act.
    g.add_edge("to_underwriter", END)
    g.add_edge("return_to_applicant", END)
    return g.compile(checkpointer=checkpointer)
