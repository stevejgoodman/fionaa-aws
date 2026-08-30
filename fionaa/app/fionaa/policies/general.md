<!-- checks: check_bank_statements_recent_and_sufficient -->

# General rules (apply to all loan types)

- Recent bank statements means at least 3 months of statements, most recent statement must be less than 90 days from date of application
- UK-based means business must be registered in the UK and have a UK address.
  This is not inferred from this text — a confirmed Companies House match (see
  `check_companies_house` in `graph.py`) is itself proof of both halves:
  Companies House is a UK-only register, and every registered office it
  returns is a UK address by law. That same check also reconciles a loosely
  worded applicant-supplied address against the Companies House registered
  address (via `geo-target___CheckSameArea`) before treating any wording
  difference as a discrepancy — see `COMPANIES_HOUSE_PROMPT`.
- Businesses must be Limited Companies or partnerships, not sole-traders
- Filed accounts are accounting statements or annual reports - the last accounting date must be in the past 12 months from date of application
- Director ID means a Drivers licence or Passport
- Applicants must be aged 18+

Note: whether the supplied bank statements meet the "at least 3 months,
most recent within 90 days" rule above is checked deterministically by the
`check_bank_statements_recent_and_sufficient` tool (see `check_tools.py`)
— the policy-check agent calls it with each statement's end date and
today's date rather than counting or comparing the dates itself.
