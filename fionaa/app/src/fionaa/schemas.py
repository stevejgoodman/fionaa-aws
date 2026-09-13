"""Compatibility imports for business models; prefer domain modules in new code.

Workflow state and context live in workflow.state.
"""
from fionaa.domain.applications import LoanType, ApplicationFormSchema
from fionaa.domain.documents import BankStatementSchema, AnnualAccountsSchema, DocumentType, DocType
from fionaa.domain.assessments import CompaniesHouseResult, PolicyCheckResult, FinancialCrossCheckSummary, FinancialAssessmentResult, FinalDecisionResult

__all__ = ['LoanType', 'ApplicationFormSchema', 'BankStatementSchema', 'AnnualAccountsSchema', 'DocumentType', 'DocType', 'CompaniesHouseResult', 'PolicyCheckResult', 'FinancialCrossCheckSummary', 'FinancialAssessmentResult', 'FinalDecisionResult']
