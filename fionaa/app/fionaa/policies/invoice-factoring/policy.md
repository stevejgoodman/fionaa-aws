<!-- checks: compute_invoice_factoring_advance -->

# Invoice Factoring

Loan Amount: Advance of 70–90% of invoice value; available to businesses with annual turnover of £50,000+
Loan Term: Minimum contract period 6–12 months, plus a notice period to exit

Documents Required:

- Business registration documents
- Recent financial statements
- Aged debt reports
- Bank statements
- Details of customer base

Note: the advance amount is calculated deterministically by the
`compute_invoice_factoring_advance` tool (see `check_tools.py`) — the
policy-check agent calls it with `invoices_owed` rather than inferring an
advance from this text.
