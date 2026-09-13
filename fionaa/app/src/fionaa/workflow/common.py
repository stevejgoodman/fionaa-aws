"""Common workflow stage."""
from __future__ import annotations



def tools_for(all_tools, *names_or_prefixes):
    """Helper function to subset the tools by name so that each agent only recieves
    a subset of the gateways tools that are relevant"""
    return [t for t in all_tools if any(t.name.startswith(p) for p in names_or_prefixes)]
