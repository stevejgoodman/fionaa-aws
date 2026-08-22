<!-- checks: check_unsecured_business_loan_amount_in_range -->

# Unsecured Business Loans

Loan Amount: £1,000 – £500,000
Loan Term: 3 – 60 months

Documents Required:

- Director ID and proof of address
- Recent business bank statements
- Accounts/management information
- VAT returns (if registered)
- Details of existing borrowing if any

Other key eligibility/rules:

- Applicant must be 18+, UK-based
- No collateral required; personal guarantee (PG) required for loans over £25,000
- Requires 6–12 months minimum trading history

Note: whether the requested amount falls within the £1,000–£500,000 range
above is checked deterministically by the
`check_unsecured_business_loan_amount_in_range` tool (see `check_tools.py`)
— the policy-check agent calls it with `loan_amount` rather than
comparing the figures itself.
