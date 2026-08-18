"""Local, deterministic loader for the bundled per-loan-type policy text and
the deterministic-check tool names each policy declares.

Split out of the former single `policy_rules.md` now that `loan_type` is a
structured field on the application, known before any agent runs — there is
nothing to search for, so this is a plain file read keyed by an enum value,
not a knowledge-base lookup.
"""

from __future__ import annotations

import re
from pathlib import Path

from schemas import LoanType

POLICIES_DIR = Path(__file__).parent / "policies"

# A policy.md/general.md may declare which of check_tools.CHECK_TOOLS_POOL
# apply to it with a single comment line, e.g.:
#   <!-- checks: compute_invoice_factoring_advance -->
# Kept as a plain regex-parsed comment rather than YAML frontmatter — this
# project has no YAML dependency, and a one-line list doesn't need one.
_CHECKS_COMMENT = re.compile(r"<!--\s*checks:\s*(?P<names>[^>]*?)\s*-->")


def _read(loan_type: LoanType, filename: str) -> str:
    return (POLICIES_DIR / loan_type.value / filename).read_text()


def load_policy_text(loan_type: LoanType) -> str:
    """General rules (apply to every loan type) plus the matching loan
    type's own section, concatenated — with the `checks:` declaration
    comment stripped from each. That comment is plumbing for
    `load_check_tool_names`, not policy content, so the agent never sees it."""
    general = (POLICIES_DIR / "general.md").read_text()
    specific = _read(loan_type, "policy.md")
    return _CHECKS_COMMENT.sub("", f"{general}\n\n{specific}").strip()


def load_check_tool_names(loan_type: LoanType) -> list[str]:
    """Tool names declared via `<!-- checks: ... -->` in general.md (applies
    to every loan type) plus this type's own policy.md. Returns `[]` if
    neither declares any — most loan types have no deterministic checks yet.

    This is what `check_against_policy` subsets `check_tools.CHECK_TOOLS_POOL`
    against (via `tools_for`) to scope which calculation tools the agent
    sees for this application. Adding a check to a loan type is a one-line
    edit to that type's policy.md — no graph.py or policy_loader.py changes.
    """
    general = (POLICIES_DIR / "general.md").read_text()
    specific = _read(loan_type, "policy.md")
    names: list[str] = []
    for text in (general, specific):
        for match in _CHECKS_COMMENT.finditer(text):
            names.extend(n.strip() for n in match.group("names").split(",") if n.strip())
    return names
