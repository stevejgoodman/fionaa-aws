<!-- checks: compute_invoice_discounting_advance -->

# Invoice Discounting

Loan Amount: Advance of 70–90% of invoice value; requires annual turnover of £100,000–£250,000+
Loan Term: Minimum contract period 6–12 months, plus a notice period to exit

Documents Required:

- Business registration documents
- Recent financial statements
- Aged debt reports
- Bank statements
- Details of customer base and credit control procedures

Note: the advance amount is calculated deterministically by the
`compute_invoice_discounting_advance` tool (see `check_tools.py`) — the
policy-check agent calls it with `invoices_owed` rather than inferring an
advance from this text.
