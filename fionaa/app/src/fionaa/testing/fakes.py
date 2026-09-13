"""Shared test doubles used across the split test modules.

None of these touch AWS or Bedrock — they stand in for the storage layer
(`ApplicationStore`/`PolicyDocStore`), LangGraph's `Runtime`, and
`langchain.agents.create_agent` so node/graph tests can run without a real
model or S3.
"""

import typing

from langchain.messages import ToolMessage


def _generic_structured_fields(response_format, response_content) -> dict:
    """Build a valid kwargs dict for an arbitrary pydantic `response_format`
    from a single canned `response_content`, without hardcoding field names
    for any one schema -- covers CompaniesHouseResult, FinalDecisionResult,
    PolicyCheckResult, FinancialAssessmentResult, and any future
    response_format= node without editing this fake again. bool fields get
    True, Literal fields get their first allowed value, list fields get an
    empty list, a type that allows None (e.g. `float | None`) gets None,
    everything else gets str(response_content)."""
    fields = {}
    for name, info in response_format.model_fields.items():
        annotation = info.annotation
        origin = typing.get_origin(annotation)
        if annotation is bool:
            fields[name] = True
        elif origin is typing.Literal:
            fields[name] = typing.get_args(annotation)[0]
        elif origin in (list, typing.List):
            fields[name] = []
        elif type(None) in typing.get_args(annotation):
            fields[name] = None
        else:
            fields[name] = str(response_content)
    return fields


class FakeStore:
    """Stands in for ApplicationStore: same get_json/put_json surface, backed
    by a plain dict instead of S3."""

    def __init__(self, initial: dict | None = None) -> None:
        self.data: dict[str, dict] = dict(initial or {})
        self.puts: list[tuple[str, dict]] = []

    def get_json(self, relative_key):
        return self.data.get(relative_key)

    def put_json(self, relative_key, payload):
        self.data[relative_key] = payload
        self.puts.append((relative_key, payload))
        return f"fake://{relative_key}"

    def list_keys(self, key_prefix):
        return sorted(k for k in self.data if k.startswith(key_prefix))


class FakePolicyDocs:
    def __init__(self, docs: dict[str, bytes] | None = None) -> None:
        self.docs = docs or {}

    def load(self, doc_key):
        return self.docs[doc_key]


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeTool:
    """Stands in for a live MCP `StructuredTool`: `graph.tools_for` only
    ever reads `.name` off tools to filter by prefix."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other):
        return isinstance(other, FakeTool) and self.name == other.name

    def __repr__(self):
        return f"FakeTool({self.name!r})"


def make_fake_create_agent(response_content, calls: list):
    """Stands in for langchain.agents.create_agent: records the
    (tools, system_prompt) it was built with and the message content it was
    invoked with, and returns `response_content` as the final AI message —
    no real model or tool call involved.

    If built with `response_format=`, the "structured_response" is
    constructed from `response_content` via that schema instead — matching
    how `check_companies_house`/`synthesize_decision` read
    `response["structured_response"]`. Pass a dict of field values for that
    case; a plain string is wrapped into a generic passing result via
    `_generic_structured_fields` (bool fields -> True, Literal fields -> their
    first allowed value, everything else -> str(response_content)) so the
    same `response_content` can drive a multi-node fake spanning more than
    one response_format shape (see `test_build_graph_runs_all_nodes_in_order`
    — companies_house and synthesize_decision both force structured output,
    with different schemas).

    If any scoped `tools` entry is a CompaniesHouse___* tool, also emits a
    harmless ToolMessage from it -- check_companies_house's runtime
    groundedness check (groundedness.py) requires at least one such tool
    call when the structured response claims found=True, which this generic
    fake would otherwise never produce (it never actually calls a tool)."""

    def fake_create_agent(*, model, tools, system_prompt, response_format=None):
        calls.append({"tools": tools, "system_prompt": system_prompt})

        class FakeAgent:
            async def ainvoke(self, input):
                calls.append({"message_content": input["messages"][0].content})
                messages = [FakeMessage(response_content)]
                companies_house_tool = next(
                    (t for t in tools if getattr(t, "name", "").startswith("CompaniesHouse___")), None
                )
                if companies_house_tool is not None:
                    messages.append(
                        ToolMessage(content="{}", name=companies_house_tool.name, tool_call_id="fake-call")
                    )
                result = {"messages": messages}
                if response_format is not None:
                    fields = (
                        response_content
                        if isinstance(response_content, dict)
                        else _generic_structured_fields(response_format, response_content)
                    )
                    result["structured_response"] = response_format(**fields)
                return result

        return FakeAgent()

    return fake_create_agent


class FakeRuntime:
    """Stands in for langgraph.runtime.Runtime[AgentContext]: node functions
    only ever read `.context` off it, so a plain attribute holder is enough."""

    def __init__(self, context) -> None:
        self.context = context


class FakeRequestContext:
    def __init__(self, headers):
        self.request_headers = headers


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False
