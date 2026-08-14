# geo-area-match Lambda — Gateway Target attach (manual step)

## Status: done (2026-08-14)

Live on `claimsagent-claimsgateway-glrrsnaalt`:
- Lambda: `arn:aws:lambda:us-east-1:492646066653:function:fionaa-geo-area-match`
- Gateway Target: `geo-target` (target id `OI7N6S2RTT`), status `READY`
- Invoke permission: a standalone inline policy `FionaaGeoAreaMatchInvoke` on
  the Gateway's role (`AgentCore-ClaimsAgent-dev-McpGatewayClaimsGatewayRo-i5L1x8ePo9L9`),
  added separately from that stack's own CDK-managed
  `McpGatewayClaimsGatewayRoleDefaultPolicy...` — kept as its own named
  policy so a redeploy of the ClaimsAgent stack doesn't silently drop it
  (that stack's CDK only manages the policy names it created).
- Verified: `pytest --run-live tests/test_live_companies_house.py -k london-vs-ruislip` passes.

**The matching rule actually shipped is distance-based, not hierarchy**, despite the
plan below — live testing against `geo-places` showed Ruislip's `SubRegion`
comes back as "Middlesex" (a historic/postal county), never "London" or
"Greater London", so hierarchy containment never matched the case this tool
exists for. See the docstring in `handler.py` for the full reasoning and the
35km threshold used.

## Why this is manual

This Lambda deploys from fionaa's own CDK stack (`GeoAreaMatchFunction` in
`fionaa/agentcore/cdk/lib/cdk-stack.ts`). The AgentCore Gateway it plugs into
(`claimsagent-claimsgateway`, see `AGENTCORE_GATEWAY_URL` in that same stack)
belongs to a different stack that is not in this repo. There is no CDK
construct here that can add a Target to a Gateway we don't own the source
for, so attaching this Lambda as an MCP Target is a one-time, out-of-band
step, not part of `cdk deploy`.

Do not run these commands from memory. Confirm the exact API shape (service
name, parameter names, `toolSchema` format) against current AWS docs first —
run `aws___read_documentation` / `aws___search_documentation` for
`bedrock-agentcore CreateGatewayTarget` before executing, per the AWS
guidance in `app/fionaa/CLAUDE.md`. The steps below are the intended shape,
not a verified final command.

## Steps

1. **Deploy the Lambda** (from `fionaa/agentcore/cdk`):
   ```
   npx cdk deploy --outputs-file outputs.json
   ```
   Read `GeoAreaMatchFunctionArn` from the stack output.

2. **Grant the Gateway's execution role permission to invoke the Lambda.**
   Find `claimsagent-claimsgateway`'s execution role ARN (from its own
   stack, or `GetGateway` on the gateway ID), then:
   ```
   aws lambda add-permission \
     --function-name fionaa-geo-area-match \
     --statement-id AllowClaimsAgentGatewayInvoke \
     --action lambda:InvokeFunction \
     --principal <claimsagent-claimsgateway execution role ARN>
   ```

3. **Create the Gateway Target**, pointing at the Lambda ARN, with the tool
   schema below (adjust parameter names to whatever `CreateGatewayTarget`
   expects them to be called at the time you run this):
   ```json
   {
     "name": "geo-target",
     "description": "Resolves whether two place names describe the same administrative area (e.g. Ruislip vs Greater London)",
     "targetConfiguration": {
       "mcp": {
         "lambda": {
           "lambdaArn": "<GeoAreaMatchFunctionArn>",
           "toolSchema": {
             "inlinePayload": [
               {
                 "name": "CheckSameArea",
                 "description": "Given two place names, returns whether they describe the same administrative area (e.g. a town within a larger city/region). Use this before treating an address mismatch as a real discrepancy — a looser or more specific place name for the same real place is not a mismatch.",
                 "inputSchema": {
                   "type": "object",
                   "properties": {
                     "place_a": { "type": "string", "description": "First place name, e.g. an applicant-supplied address town/city." },
                     "place_b": { "type": "string", "description": "Second place name, e.g. the Companies House address town/city." }
                   },
                   "required": ["place_a", "place_b"]
                 }
               }
             ]
           }
         }
       }
     }
   }
   ```
   This makes the tool available to the agent as `geo-target___CheckSameArea`
   (same `<target>___<tool>` naming the existing `CompaniesHouse___*` and
   `websearch-target___WebSearch` tools already use — see
   `app/fionaa/prompts.py`).

4. **Verify**: re-run
   `pytest --run-live tests/test_live_companies_house.py -k london-vs-ruislip`
   from `fionaa/app/fionaa`. It should now pass.

## Local testing without the Gateway

```
python fionaa/agentcore/lambda/geo_area_match/handler.py "Manor Road, London" "Manor Road, Ruislip"
```
prints the match verdict directly — useful for testing the geocoding/matching
logic before wiring up the Gateway Target.
