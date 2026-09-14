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
    g.add_edge("reject_no_company", END)
    # policy_check deliberately does not short-circuit: a failed policy check
    # is a business outcome, not a dead end. Its result is already carried in
    # state (`policy_check`), so it flows into whichever artifact ends up
    # documenting the run's outcome instead of only living in
    # policy_check/result.json. No separate Automated Reasoning gate sits
    # between policy_check and financial_assessment any more -- see
    # validate_final_decision's docstring for why that check moved to run
    # once, at the very end, instead of being split across an early stage
    # that could never see companies_house/financial_assessment evidence.
    g.add_edge("policy_check", "financial_assessment")
    g.add_edge("financial_assessment", "web_search")
    # web_search used to go straight to END, leaving policy_check/
    # companies_house/financial_assessment/web_search as separate evidence
    # artifacts with no rolled-up outcome on the success path — only
    # reject_no_company ever wrote a final_decision. synthesize_decision
    # closes that gap.
    g.add_edge("web_search", "synthesize_decision")
    g.add_edge("synthesize_decision", "validate_final_decision")
    g.add_edge("validate_final_decision", END)
    return g.compile(checkpointer=checkpointer)
