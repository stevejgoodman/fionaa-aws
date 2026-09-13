"""Graph topology and compatibility exports for existing runners."""
from domain.applications import LoanType
from domain.documents import AnnualAccountsSchema
from domain.assessments import CompaniesHouseResult, FinancialAssessmentResult, PolicyCheckResult
from policy_loader import load_policy_text
from prompts import COMPANIES_HOUSE_PROMPT, WEB_SEARCH_PROMPT
from typing import Optional
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from workflow.state import AgentContext, ApplicationState
from integrations.checkpointing import build_checkpointer, checkpoint_config
from workflow.nodes import (
    load_application,
    check_against_policy,
    validate_policy_assessment,
    validate_final_decision,
    check_companies_house,
    reject_no_company,
    check_financial_assessment,
    search_web,
    synthesize_decision,
    tools_for,
    DocumentSpec,
    DOCUMENT_SPECS
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
    g.add_node("policy_check", check_against_policy)
    g.add_node("validate_policy_assessment", validate_policy_assessment)
    g.add_node("validate_final_decision", validate_final_decision)
    g.add_node("companies_house", check_companies_house)
    g.add_node("reject_no_company", reject_no_company)
    g.add_node("financial_assessment", check_financial_assessment)
    g.add_node("web_search", search_web)
    g.add_node("synthesize_decision", synthesize_decision)

    g.add_edge(START, "load_application")
    g.add_edge("load_application", "policy_check")
    # policy_check deliberately does not short-circuit: a failed policy check
    # is a business outcome, not a dead end. Its result is already carried in
    # state (`policy_check`), so it flows into whichever artifact ends up
    # documenting the run's outcome instead of only living in
    # policy_check/result.json.
    g.add_edge("policy_check", "validate_policy_assessment")
    # companies_house routes dynamically via the Command it returns —
    # "financial_assessment" if the company was confirmed, "reject_no_company"
    # otherwise — so no static edge to either is declared here. Only the
    # "found" branch reaches financial_assessment: it cross-checks the
    # application against the companies_house findings, so it needs that
    # node to have already run.
    g.add_edge("financial_assessment", "web_search")
    # web_search used to go straight to END, leaving policy_check/
    # companies_house/financial_assessment/web_search as separate evidence
    # artifacts with no rolled-up outcome on the success path — only
    # reject_no_company ever wrote a final_decision. synthesize_decision
    # closes that gap.
    g.add_edge("web_search", "synthesize_decision")
    g.add_edge("reject_no_company", END)
    g.add_edge("synthesize_decision", "validate_final_decision")
    g.add_edge("validate_final_decision", END)
    return g.compile(checkpointer=checkpointer)
