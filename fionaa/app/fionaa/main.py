from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from fionaa_scoped_agent import (
    APPLICATIONS_BUCKET,
    ApplicationStore,
    PolicyDocStore,
    build_checkpointer,
    build_graph,
    checkpoint_config,
    identity_from_request_context,
    load_gateway_tools,
    scoped_boto_session,
)

LangchainInstrumentor().instrument()

app = BedrockAgentCoreApp()
log = app.logger


@app.entrypoint
async def invoke(payload, context):
    """Credentials are minted once per invocation and injected into state, so no
    node ever constructs its own S3 client from the ambient execution role.

    The graph is compiled here too, not at module load — its checkpointer has
    to be built from this invocation's scoped session (see
    fionaa_scoped_agent.build_checkpointer), so it can't be a module-level
    singleton anymore.
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
    # invocation (same reasoning as the checkpointer above) rather than
    # cached at module load.
    tools = await load_gateway_tools()

    # actor_id/thread_id are derived from the verified identity, never
    # accepted from the payload — same rule as customer_id, and it matters
    # more here since a paused HITL thread sits in AgentCore Memory waiting
    # for input.
    config = checkpoint_config(identity)

    final_state = await graph.ainvoke({
        "identity": identity,
        "store": ApplicationStore(identity, session),
        "policy_docs": PolicyDocStore(session),
        "tools": tools,
    }, config)

    result = {
        "application_id": identity.application_id,
        "decision": final_state["assessment"]["decision"],
        "result_uri": f"s3://{APPLICATIONS_BUCKET}/{identity.customer_id}/"
                      f"{identity.application_id}/assessment/final_result.json",
    }
    log.info(f"Agent output: {result}")
    return result


if __name__ == "__main__":
    app.run()
