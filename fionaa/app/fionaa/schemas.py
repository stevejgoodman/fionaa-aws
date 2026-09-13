"""Compatibility imports for business models; prefer domain modules in new code.

Workflow state and context live in workflow.state.
"""
from domain.applications import LoanType, ApplicationFormSchema
from domain.documents import BankStatementSchema, AnnualAccountsSchema, DocumentType, DocType
from domain.assessments import CompaniesHouseResult, PolicyCheckResult, FinancialCrossCheckSummary, FinancialAssessmentResult, FinalDecisionResult

__all__ = ['LoanType', 'ApplicationFormSchema', 'BankStatementSchema', 'AnnualAccountsSchema', 'DocumentType', 'DocType', 'CompaniesHouseResult', 'PolicyCheckResult', 'FinancialCrossCheckSummary', 'FinancialAssessmentResult', 'FinalDecisionResult']
