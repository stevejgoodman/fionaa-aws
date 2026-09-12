from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from gateway import load_gateway_tools
from policy_consistency import PolicyConsistencyChecker
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
        policy_checker=PolicyConsistencyChecker.from_environment(),
    )
    # no msg as context contains the relevant info
    final_state = await graph.ainvoke({}, config, context=agent_context)

    prefix = f"s3://{APPLICATIONS_BUCKET}/{identity.customer_id}/{identity.application_id}"
    result = {
        "application_id": identity.application_id,
        "outcome": final_state["final_decision"]["outcome"],
        "decision_uri": f"{prefix}/decision/result.json",
    }
    for stage in ("policy_check", "companies_house", "financial_assessment", "web_search"):
        if stage in final_state:
            result[f"{stage}_uri"] = f"{prefix}/{stage}/result.json"
    log.info(f"Agent output: {result}")
    return result


if __name__ == "__main__":
    app.run()
