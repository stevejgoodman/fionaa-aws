from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from gateway import load_gateway_tools
from graph import AgentContext, build_checkpointer, build_graph, checkpoint_config
from security import identity_from_request_context, scoped_boto_session
from storage import APPLICATIONS_BUCKET, ApplicationStore, PolicyDocStore

LangchainInstrumentor().instrument()

app = BedrockAgentCoreApp()
log = app.logger


@app.entrypoint
async def invoke(payload, context):
    """Credentials are minted once per invocation and injected via runtime
    context, so no node ever constructs its own S3 client from the ambient
    execution role.

    Checkpointing is back on. The `store`/`policy_docs`/`tools` dependencies
    used to live in `ApplicationState` and broke every checkpoint write
    (`TypeError: Type is not msgpack serializable` — none of the three are
    msgpack-encodable). They now travel as LangGraph runtime context
    (`AgentContext`, passed via `context=` below) instead of graph state, so
    `AgentCoreMemorySaver` only ever has to serialize the plain-dict/string
    evidence fields in `ApplicationState` — see `AgentContext`'s docstring in
    graph.py.
    """
    log.info("Invoking Agent.....")

    identity = identity_from_request_context(
        context=context,
        application_id=payload["application_id"],
    )
    session = scoped_boto_session(identity)
    checkpointer = build_checkpointer(session)
    graph = build_graph(checkpointer=checkpointer)

    # Gateway OAuth token is short-lived, so tools are loaded fresh per
    # invocation rather than cached at module load.
    tools = await load_gateway_tools()

    # actor_id/thread_id are derived from the verified identity, never
    # accepted from the payload — same rule as customer_id.
    config = checkpoint_config(identity)

    agent_context = AgentContext(
        store=ApplicationStore(identity, session),
        policy_docs=PolicyDocStore(session),
        tools=tools,
    )

    await graph.ainvoke({}, config, context=agent_context)

    prefix = f"s3://{APPLICATIONS_BUCKET}/{identity.customer_id}/{identity.application_id}"
    result = {
        "application_id": identity.application_id,
        "policy_check_uri": f"{prefix}/policy_check/result.json",
        "companies_house_uri": f"{prefix}/companies_house/result.json",
        "web_search_uri": f"{prefix}/web_search/result.json",
    }
    log.info(f"Agent output: {result}")
    return result


if __name__ == "__main__":
    app.run()
