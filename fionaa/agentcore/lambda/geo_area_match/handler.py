"""AgentCore Gateway Lambda target: "is this the same area?" tool.

Why this exists
----------------
`check_companies_house` (see app/fionaa/graph.py) rejects applications where
the applicant-supplied address doesn't look like the Companies House address
on file. A plain string compare fails for correct-but-loose input — e.g. an
applicant writes "London" for an address that Companies House lists as
"Ruislip", which is true (Ruislip is part of Greater London) but not a
string match. See fionaa/app/fionaa/tests/test_live_companies_house.py,
case id="goodai-consulting-london-vs-ruislip".

What it does
------------
Geocodes both place names with Amazon Location Service Places (the
`geo-places` API — SearchText) and checks how far apart they are.

This started as an administrative-hierarchy comparison (does one place's
Region/SubRegion/District chain name the other?) rather than a distance
check, on the reasoning that "nearby" isn't the same claim as "the same
place". That turned out not to hold up against this data source: SearchText
categorises Ruislip's SubRegion as "Middlesex" (a historic/postal county),
not "London" or "Greater London" — so the hierarchy never actually overlaps
for the Ruislip/London case this tool exists to solve. Region alone
("England") overlaps for almost any two English places, which is worse: it
would have called London and Manchester the same area. Distance, with a
threshold sized to Greater London's own radius, is what the data actually
supports.

`_MAX_SAME_AREA_KM` is that threshold (35km, straight-line/great-circle).
Central London to Ruislip is ~22km; London to Reading (a different town, not
part of London) is ~58km; London to Manchester is ~262km. There's no exact
boundary that a distance threshold can express — this is a heuristic, not a
lookup of Greater London's real (non-circular) boundary — so it will have
edge cases near the threshold in either direction.

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
import math
from typing import Any

import boto3

_location = boto3.client("geo-places")

_MAX_SAME_AREA_KM = 35.0
_EARTH_RADIUS_KM = 6371.0

# SearchText requires exactly one of BiasPosition / Filter.BoundingBox /
# Filter.Circle. Fionaa only ever verifies UK (Companies House) addresses, so
# a fixed bias near the geographic centre of Great Britain is a reasonable
# default — it doesn't restrict results, just ranks closer ones higher.
# IncludeCountries is the actual restriction to the UK.
_UK_BIAS_POSITION = [-2.0, 54.0]  # [longitude, latitude]

# Levels shown in the response for a human/agent to sanity-check the verdict
# against — not used for matching (see module docstring for why).
_HIERARCHY_FIELDS = ("Locality", "District", "SubRegion", "Region")


def _hierarchy_names(address: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for field in _HIERARCHY_FIELDS:
        value = address.get(field)
        if isinstance(value, dict):
            value = value.get("Name")
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    return names


def _geocode(place_name: str) -> dict[str, Any] | None:
    """First SearchText result for a free-text place name, or None if no match."""
    response = _location.search_text(
        QueryText=place_name,
        MaxResults=1,
        BiasPosition=_UK_BIAS_POSITION,
        Filter={"IncludeCountries": ["GBR"]},
    )
    results = response.get("ResultItems") or []
    return results[0] if results else None


def _distance_km(position_a: list[float], position_b: list[float]) -> float:
    """Great-circle distance between two [longitude, latitude] positions."""
    lon1, lat1 = math.radians(position_a[0]), math.radians(position_a[1])
    lon2, lat2 = math.radians(position_b[0]), math.radians(position_b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


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

    address_a, address_b = result_a.get("Address", {}), result_b.get("Address", {})
    distance_km = round(_distance_km(result_a["Position"], result_b["Position"]), 1)
    match = distance_km <= _MAX_SAME_AREA_KM

    return {
        "match": match,
        "reason": (
            f"{distance_km}km apart — within the {_MAX_SAME_AREA_KM}km same-area threshold."
            if match
            else f"{distance_km}km apart — outside the {_MAX_SAME_AREA_KM}km same-area threshold."
        ),
        "distance_km": distance_km,
        "place_a": {"input": place_a, "label": address_a.get("Label"), "hierarchy": _hierarchy_names(address_a)},
        "place_b": {"input": place_b, "label": address_b.get("Label"), "hierarchy": _hierarchy_names(address_b)},
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
