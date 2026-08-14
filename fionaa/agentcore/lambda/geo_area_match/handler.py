"""AgentCore Gateway Lambda target: "is this the same area?" tool.

Why this exists
----------------
`check_companies_house` (see app/fionaa/graph.py) rejects applications where
the applicant-supplied address doesn't look like the Companies House address
on file. A plain string compare fails for correct-but-loose input — e.g. an
applicant writes "London" for an address that Companies House lists as
"Ruislip", which is true (Ruislip is a district of Greater London) but not a
string match. See fionaa/app/fionaa/tests/test_live_companies_house.py,
case id="goodai-consulting-london-vs-ruislip".

What it does
------------
Geocodes both place names with Amazon Location Service Places (the `geo-places`
API — SearchText) and compares each place's administrative hierarchy
(locality / district / sub-region / region). Two places match if any level of
one place's hierarchy names the other, so "Ruislip" (locality) whose
sub-region is "Greater London" matches a lookup for "London".

This does NOT do a distance/radius check — nearby-but-different areas (e.g.
Ruislip and Uxbridge, both in Hillingdon but distinct towns) should not
silently match just because they're close. Hierarchy containment is the
rule: one place must actually be *administratively part of* the other.

Deployment
----------
This Lambda is deployed from fionaa's own CDK stack (see
fionaa/agentcore/cdk/lib/cdk-stack.ts, `GeoAreaMatch*` resources) but the
AgentCore Gateway it's attached to (claimsagent-claimsgateway) is owned by a
different stack that isn't in this repo. Attaching this function as an MCP
Gateway Target — and granting that Gateway's execution role
lambda:InvokeFunction on it — is therefore a manual/out-of-band step. See
README.md in this directory for the exact CreateGatewayTarget call.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

_location = boto3.client("geo-places")

# Address hierarchy levels to compare, most to least specific. We deliberately
# skip Country — two UK places always "match" on country, which would make
# the comparison meaningless — and skip PostalCode/Street, which are too
# specific to ever produce a useful containment match here.
_HIERARCHY_FIELDS = ("Locality", "District", "SubRegion", "Region")


def _hierarchy_names(address: dict[str, Any]) -> set[str]:
    """Every place name in an Address's administrative chain, lower-cased.

    `Region`/`SubRegion` are `{"Name": ..., "Code": ...}` objects; the rest
    are plain strings. Missing fields are skipped rather than erroring —
    Places doesn't guarantee every level is populated for every place.
    """
    names: set[str] = set()
    for field in _HIERARCHY_FIELDS:
        value = address.get(field)
        if isinstance(value, dict):
            value = value.get("Name")
        if isinstance(value, str) and value.strip():
            names.add(value.strip().lower())
    return names


def _geocode(place_name: str) -> dict[str, Any] | None:
    """First SearchText result for a free-text place name, or None if no match."""
    response = _location.search_text(QueryText=place_name, MaxResults=1)
    results = response.get("ResultItems") or []
    return results[0] if results else None


def check_same_area(place_a: str, place_b: str) -> dict[str, Any]:
    """Core logic, callable directly from tests without going through Lambda framing."""
    result_a = _geocode(place_a)
    result_b = _geocode(place_b)

    if result_a is None or result_b is None:
        unresolved = place_a if result_a is None else place_b
        return {
            "match": False,
            "reason": f"Could not resolve {unresolved!r} to a known place.",
            "place_a": place_a,
            "place_b": place_b,
        }

    names_a = _hierarchy_names(result_a.get("Address", {}))
    names_b = _hierarchy_names(result_b.get("Address", {}))
    overlap = names_a & names_b

    return {
        "match": bool(overlap),
        "reason": (
            f"Shared administrative area: {sorted(overlap)[0]}"
            if overlap
            else "No shared administrative area between the two places."
        ),
        "place_a": {"input": place_a, "label": result_a.get("Address", {}).get("Label"), "hierarchy": sorted(names_a)},
        "place_b": {"input": place_b, "label": result_b.get("Address", {}).get("Label"), "hierarchy": sorted(names_b)},
    }


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """AgentCore Gateway Lambda target entrypoint.

    The Gateway invokes this Lambda synchronously and passes the tool call's
    arguments as the event body. Tool schema (see README.md) declares two
    required string params: `place_a`, `place_b`.
    """
    place_a = event.get("place_a")
    place_b = event.get("place_b")
    if not place_a or not place_b:
        return {"error": "Both place_a and place_b are required."}

    try:
        return check_same_area(place_a, place_b)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as a tool error, not a crash
        return {"error": f"Location lookup failed: {exc}"}


if __name__ == "__main__":
    import sys

    print(json.dumps(check_same_area(sys.argv[1], sys.argv[2]), indent=2))
