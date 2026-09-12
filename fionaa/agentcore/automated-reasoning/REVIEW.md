# Secured policy extraction review

Source: general.md plus secured-business-loans/policy.md, as captured in
secured-business-loans.txt. The original AWS build output is preserved in
secured-generated-definition.json. The reviewed policy is in
secured-reviewed-definition.json.

Corrections applied to the generated definition:

- Loan term bounds are conditions of eligibility, not unconditional axioms.
  Otherwise an 84-month application makes the premises impossible rather
  than allowing a correct finding that it is ineligible.
- Removed the tautological accounts-recency rule and included accounts
  recency in required-document completeness.
- Removed director ID from substantive eligibility. General policy defines
  valid ID types but the secured policy does not explicitly require it.
- Required documents remain separate from substantive eligibility.
- The approval variable describes whether an approval meets the covered
  policy. It does not require the lender to approve every eligible applicant;
  financial assessment and other evidence may still warrant referral/rejection.
- Bank-statement age must be nonnegative as well as strictly less than 90 days.
- Retained the 12-month trading minimum and inclusive amount/term boundaries.

Remaining evaluation limitations after runtime activation:

- Translation of the real JSON assessment fields into the formal variables.
- Coverage of the approval/eligibility claim, rather than only incidental facts.
- Unknown collateral/age/registration/document evidence must not be invented.
- The current application does not consistently supply an application date,
  collateral verification, or exact applicant birth date. These are evidence
  gaps, not permission to assume that an eligibility condition holds.
- This first formal policy covers secured loans only; the four additional
  product policies below now have their own versioned guardrail bindings.

## Additional loan types

These definitions reuse the reviewed general predicates, with explicit
product-specific eligibility and document conditions. They were authored
directly from the revised Markdown rather than another automatic extraction.

- Unsecured: GBP1,000–500,000 inclusive; 3–60 months inclusive; minimum
  6 months trading; personal guarantee required strictly above GBP25,000.
  Director ID/address, financial information and recent bank statements are
  required. VAT returns and borrowing details are conditional on applicability.
- Revolving: GBP10,000–1,000,000 inclusive; minimum 12 months trading;
  no fixed loan-term bound. VAT returns depend on VAT registration, and
  security/asset documents depend on the facility being secured.
- Invoice discounting: minimum GBP100,000 turnover; 70–90% advance inclusive;
  minimum 6 months contract, confirmed by the user; agreed exit notice period.
  Registration, recent financial statements, aged debt reports, bank statements,
  customer-base details and credit-control procedures are required.
- Invoice factoring: minimum GBP50,000 turnover; the same advance band and
  confirmed 6-month minimum contract. It does not inherit discounting's
  higher turnover minimum or credit-control-procedure documentation requirement.

Documentation completeness remains separate from substantive eligibility.
All products retain the shared 18+ applicant rule, UK registration/address,
eligible business types and strict bank-statement recency. Financial-document
recency is part of the `hasRecentFinancialStatements` or
`hasAccountsOrManagementInformation` evidence predicate; upstream verification
must establish it, rather than treating document existence as sufficient.
An approval meeting these covered rules is permitted, not mandatory: the
other assessments can still cause referral or rejection.
