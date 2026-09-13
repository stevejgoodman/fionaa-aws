<!-- checks: check_revolving_credit_facility_amount_in_range -->

# Revolving Credit Facility

Loan Amount: £10,000 – £1,000,000
Loan Term: Ongoing/revolving — no fixed end date; funds drawn, repaid, and redrawn within an agreed limit, subject to periodic lender review

Documents Required:

- Director ID/address
- Recent bank statements
- Accounts/management information
- VAT returns (if registered)
- Details of existing borrowing
- Security/asset details (for secured facilities)

Other key eligibility/rules:

- 12+ months trading history

Note: whether the requested amount falls within the £10,000–£1,000,000
range above is checked deterministically by the
`check_revolving_credit_facility_amount_in_range` tool (see
`check_tools.py`) — the policy-check agent calls it with `loan_amount`
rather than comparing the figures itself.
