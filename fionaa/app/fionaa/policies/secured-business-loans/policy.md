<!-- checks: check_secured_business_loan_amount_in_range -->

# Secured Business Loans

Loan Amount: £25,000 – £20,000,000
Loan Term: 12 – 72 months

Documents Required:

- Recent bank statements and annual accounts
- Proof of ownership/valuation of the collateral asset

Other key eligibility/rules:

- Requires an asset as security (property, equipment, vehicles, invoices, or intangible assets)
- Lower interest rates than unsecured; PG may still apply
- Typically requires 12–36 months trading history
- Approval slower due to valuation/legal checks

Note: whether the requested amount falls within the £25,000–£20,000,000
range above is checked deterministically by the
`check_secured_business_loan_amount_in_range` tool (see `check_tools.py`) —
the policy-check agent calls it with `loan_amount` rather than
comparing the figures itself.
