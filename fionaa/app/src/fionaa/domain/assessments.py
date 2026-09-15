"""Business models for assessments."""
from typing import Literal

from pydantic import BaseModel, Field


class CompaniesHouseResult(BaseModel):
    """Forced structured output for `check_companies_house` —
    agent's final turn is constrained to this
    schema and `found` becomes a branch condition in the graph."""

    found: bool = Field(
        description="True if the applicant's company was confirmed as a "
        "genuine UK company with the named applicant as an officer "
        "or PSC. False if no such company could be confirmed."
    )
    confidence: str = Field(description="high, medium, or low")
    summary: str = Field(
        description="Explanation of the finding, including any partial matches considered. "
        "When a company was found, must include the director/PSC names and the registered "
        "office's town/city and postcode area — search_web uses these to disambiguate the "
        "company online, not just the summary verdict. Do not include the building/house "
        "number or street name (the first line of the address): state location at "
        "town/postcode level only, to keep street-level address PII out of this free-text "
        "field, which flows into evidence storage, traces, and the dashboard."
    )


class PolicyCheckResult(BaseModel):
    """Forced structured output for `check_against_policy` — the eligibility
    counterpart to `CompaniesHouseResult`/`FinalDecisionResult`, constraining
    the model to an actual verdict plus itemized clause findings rather than
    free-text prose downstream nodes can only re-read via another LLM call."""

    eligible: Literal["eligible", "ineligible", "inconclusive"] = Field(
        description="Overall verdict against the loan policy's substantive criteria, assessed "
        "separately from documentation completeness (see documentation_gaps)."
    )
    clause_findings: list[str] = Field(
        description="One entry per quantifiable or explicitly-stated policy requirement checked "
        "(loan amount range, advance-rate band, turnover/trading-history thresholds, term limits, "
        "security/collateral requirements, etc.) — each entry names the clause and states whether "
        "the application satisfies it."
    )
    documentation_gaps: list[str] = Field(
        default_factory=list,
        description="Missing supporting documents or documentation-only shortfalls (e.g. "
        "insufficient/stale bank statements per check_bank_statements_recent_and_sufficient) — "
        "kept separate from eligible so a documentation gap alone never forces "
        "eligible='inconclusive' when the substantive criteria could already be assessed.",
    )
    summary: str = Field(description="Brief overall summary of the assessment.")


class FinancialCrossCheckSummary(BaseModel):
    """JSON-serializable mirror of check_tools.FieldComparison — the LLM-
    facing/evidence-artifact shape of a single deterministic cross-check
    comparison. Field names/values are computed in code
    (check_tools.cross_check_financial_figures) and must be copied through
    verbatim, not recomputed."""

    field: str
    application_value: float | None
    reference_value: float | None
    source: str
    delta_pct: float | None
    material: bool


class FinancialAssessmentResult(BaseModel):
    """Forced structured output for `check_financial_assessment`. `cross_check`
    is the authoritative, code-computed turnover/profit comparison
    (check_tools.cross_check_financial_figures) — the model copies it through
    rather than re-deriving its own delta percentages; `discrepancies` is for
    narrative mismatches cross_check doesn't cover (company name, time
    trading, etc.)."""

    verdict: Literal["consistent", "inconsistent", "inconclusive"] = Field(
        description="Overall cross-source consistency verdict. inconsistent means a genuine "
        "cross-source contradiction was found (e.g. a material cross_check discrepancy, or a "
        "conflicting fact across sources) -- not merely that evidence is missing. inconclusive "
        "means there wasn't enough evidence to assess consistency at all (e.g. no annual_accounts "
        "and no bank_statements to compare against); never use inconsistent for that case, since "
        "synthesize_decision weighs inconsistent much more heavily toward rejection than a "
        "documentation gap warrants on its own."
    )
    discrepancies: list[str] = Field(
        default_factory=list,
        description="Non-numeric or otherwise not-already-covered-by-cross_check mismatches "
        "across sources (company name, time trading, etc.) — do not restate a cross_check entry "
        "here, its material flag already captures that.",
    )
    monthly_repayment: float | None = Field(
        default=None,
        description="compute_monthly_repayment's result, if the loan type/fields make one "
        "applicable. Never estimated by the model itself.",
    )
    affordability_basis: Literal["bank_statements", "application_self_reported", "not_applicable"] = Field(
        description="Which evidence the affordability_verdict is based on -- bank_statements is "
        "preferred over application_self_reported when both are available."
    )
    affordability_verdict: str = Field(description="The affordability judgement itself.")
    cross_check: list[FinancialCrossCheckSummary] = Field(
        description="The deterministic turnover/profit comparison result, copied through "
        "verbatim from the CROSS-CHECK RESULT provided in the human message."
    )
    summary: str = Field(description="Brief overall summary of the assessment.")


class FinalDecisionResult(BaseModel):
    """Advisory AI recommendation. A human always makes the final decision."""

    outcome: Literal["approved", "rejected", "referred"] = Field(
        description="Advisory recommendation only; never a final decision. approved: no material issues across the four assessments. rejected: a clear, "
        "material failure (ineligible on policy, insolvent/dissolved company, unaffordable "
        "repayment, serious unresolved discrepancy). referred: issues a human underwriter should "
        "review, but nothing warranting a recommendation to reject."
    )
    reason: str = Field(
        description="Which of the four assessments (policy_check/companies_house/"
        "financial_assessment/web_search) drove this outcome, and what in each."
    )
    rationale: str = Field(description="Brief overall rationale weighing the four assessments together.")
