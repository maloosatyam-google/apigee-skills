#!/usr/bin/env python3
"""Scaffold a deployable Apigee X AI Gateway proxy bundle (stdlib only, no repo dependencies).

  python3 scaffold_ai_gateway.py --out ./ai-gateway-v1 --project my-proj [options]

Options:
  --name ai-gateway-v1          proxy name (also API product apiSource)
  --basepath /ai/v1
  --project PROJECT             GCP project hosting Vertex AI / Model Armor
  --model-location global       Vertex location for model calls
  --default-model gemini-3.6-flash
  --armor-region REGION         enable Model Armor (SUP + buffered SMR + SSE ContinuousScan SMR)
  --prompt-template NAME        Model Armor template for prompts   (default apigee-sanitize-user-prompt)
  --response-template NAME      Model Armor template for responses (default apigee-sanitize-model-response)
  --cache-region REGION         enable semantic cache (opt-in header x-use-cache: true; bypassed on SSE)
  --index-endpoint-host HOST    e.g. 123.asia-southeast1-456.vdb.vertexai.goog
  --index-endpoint-id ID  --deployed-index-id ID  --index-id ID
  --token-quota                 add LLMTokenQuota enforce + buffered/SSE count policies (product llmOperationGroup)
  --prompt-token-limit RATE     add PromptTokenLimit, e.g. 5000pm
  --claude                      add Anthropic-on-Vertex buffered + SSE targets (/models/claude-* and /v1/messages)
                                with Gemini<->Claude conversion, native Anthropic pass-through, and beta-header filtering
  --claude-model NAME           Claude model id put in the API product (default claude-haiku-5-5)
  --openai                      add OpenAI / Chat Completions buffered + SSE targets (/v1/chat/completions,
                                /openai/deployments/{model}/chat/completions, and GET /v1/models discovery)
  --analytics                   add 8-collector DataCapture policies (DC-ModelAnalytics + DC-FaultAnalytics)
  --auto-route                  add POST {basepath}/models/auto:{generateContent|streamGenerateContent}:
                                classifier ServiceCallout picks routing.model.<category> from API product
  --router-model NAME           classifier model (default gemini-3.5-flash-lite)
  --sse / --no-sse              enable (default) or disable Server-Sent Events streaming (:streamGenerateContent);
                                --no-sse rejects streaming requests explicitly with HTTP 501

Generated paths:
  POST {basepath}/models/{model}:generateContent         (buffered JSON; also /v1beta/models/... & /v1/models/...)
  POST {basepath}/models/{model}:streamGenerateContent   (SSE text/event-stream; ?alt=sse enforced upstream)
  POST {basepath}/v1/messages                            (with --claude: Anthropic Messages & SSE :streamRawPredict)
  POST {basepath}/v1/chat/completions                    (with --openai: OpenAI Chat Completions & SSE)
  GET  {basepath}/v1/models                              (with --openai: OpenAI/IDE model discovery)
  HEAD|GET {basepath}/api/hello                          (unauthenticated health probe for IDE/CLI clients)
Every response carries x-gateway-model / -provider / -streaming / -prompt-tokens / -completion-tokens /
-total-tokens / -cached headers. Also writes api_product.json with matching operationGroup + llmOperationGroup.
"""
import argparse
import json
import os
import textwrap

H = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--out", required=True)
    a.add_argument("--name", default="ai-gateway-v1")
    a.add_argument("--basepath", default="/ai/v1")
    a.add_argument("--project", required=True)
    a.add_argument("--model-location", default="global")
    a.add_argument("--default-model", default="gemini-3.6-flash")
    a.add_argument("--armor-region")
    a.add_argument("--prompt-template", default="apigee-sanitize-user-prompt")
    a.add_argument("--response-template", default="apigee-sanitize-model-response")
    a.add_argument("--cache-region")
    a.add_argument("--index-endpoint-host")
    a.add_argument("--index-endpoint-id")
    a.add_argument("--deployed-index-id", default="semantic_cache")
    a.add_argument("--index-id")
    a.add_argument("--token-quota", action="store_true")
    a.add_argument("--prompt-token-limit")
    a.add_argument("--claude", action="store_true")
    a.add_argument("--claude-model", default="claude-haiku-5-5")
    a.add_argument("--openai", action="store_true")
    a.add_argument("--analytics", action="store_true")
    a.add_argument("--auto-route", action="store_true")
    a.add_argument("--router-model", default="gemini-3.5-flash-lite")
    sse_group = a.add_mutually_exclusive_group()
    sse_group.add_argument("--sse", action="store_true", help="enable SSE streaming (default)")
    sse_group.add_argument("--no-sse", action="store_true", help="disable SSE streaming (return HTTP 501)")
    o = a.parse_args()
    if o.cache_region and not (o.index_endpoint_host and o.index_endpoint_id and o.index_id):
        a.error("--cache-region needs --index-endpoint-host, --index-endpoint-id and --index-id")
    sse = not o.no_sse
    vertex_host = "aiplatform.googleapis.com" if o.model_location == "global" else f"{o.model_location}-aiplatform.googleapis.com"

    P = {}  # policy name -> xml
    pre, flows_extra, post, tgt_pre = [], [], [], []
    NOT_OPT = 'request.verb != "OPTIONS"'
    IS_HELLO = '(proxy.pathsuffix = "/api/hello") and (request.verb = "HEAD" or request.verb = "GET")'
    NOT_PUBLIC = f'({NOT_OPT}) and !({IS_HELLO})'
    NOT_STREAM = 'flow.is_streaming != "true"'
    CACHE_ON = '(request.header.x-use-cache = "true")'
    CACHE_ELIGIBLE = f'{CACHE_ON} and ({NOT_STREAM})' if sse else CACHE_ON
    DONE_SSE_EVENT = 'flow.stream_done = "true"'

    P["CORS-Headers"] = """<CORS continueOnError="false" enabled="true" name="CORS-Headers">
  <AllowOrigins>{request.header.origin}</AllowOrigins>
  <AllowMethods>GET, POST, HEAD, OPTIONS</AllowMethods>
  <AllowHeaders>origin, accept, content-type, authorization, api-key, x-api-key, x-apikey, x-goog-api-key, anthropic-version, anthropic-beta, x-use-cache</AllowHeaders>
  <ExposeHeaders>*</ExposeHeaders>
  <MaxAge>3628800</MaxAge>
  <GeneratePreflightResponse>true</GeneratePreflightResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</CORS>"""
    P["AM-HealthOk"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-HealthOk">
  <Set>
    <Payload contentType="application/json">{"status":"ok"}</Payload>
    <StatusCode>200</StatusCode>
  </Set>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="response"/>
</AssignMessage>"""
    P["EV-ExtractAPIKey"] = """<ExtractVariables continueOnError="true" enabled="true" name="EV-ExtractAPIKey">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <!-- Evaluated top-to-bottom: explicit API key headers override Authorization so callers can pass
       Authorization: Bearer <GCP identity JWT> alongside x-apikey / x-api-key / x-goog-api-key -->
  <Header name="Authorization">
    <Pattern ignoreCase="true">Bearer {apiKey}</Pattern>
  </Header>
  <Header name="api-key">
    <Pattern>{apiKey}</Pattern>
  </Header>
  <Header name="x-api-key">
    <Pattern>{apiKey}</Pattern>
  </Header>
  <Header name="x-apikey">
    <Pattern>{apiKey}</Pattern>
  </Header>
  <Header name="x-goog-api-key">
    <Pattern>{apiKey}</Pattern>
  </Header>
  <QueryParam name="api-key">
    <Pattern>{apiKey}</Pattern>
  </QueryParam>
  <Source clearPayload="false">request</Source>
  <VariablePrefix>flow</VariablePrefix>
</ExtractVariables>"""
    P["EV-RequestDetails"] = """<ExtractVariables continueOnError="true" enabled="true" name="EV-RequestDetails">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <URIPath>
    <Pattern ignoreCase="true">/v1beta/models/{model}:streamGenerateContent</Pattern>
    <Pattern ignoreCase="true">/v1beta/models/{model}:generateContent</Pattern>
    <Pattern ignoreCase="true">/v1/models/{model}:streamGenerateContent</Pattern>
    <Pattern ignoreCase="true">/v1/models/{model}:generateContent</Pattern>
    <Pattern ignoreCase="true">/models/{model}:streamGenerateContent</Pattern>
    <Pattern ignoreCase="true">/models/{model}:generateContent</Pattern>
    <Pattern ignoreCase="true">/v1/models/{model}:streamRawPredict</Pattern>
    <Pattern ignoreCase="true">/v1/models/{model}:rawPredict</Pattern>
    <Pattern ignoreCase="true">/models/{model}:streamRawPredict</Pattern>
    <Pattern ignoreCase="true">/models/{model}:rawPredict</Pattern>
    <Pattern ignoreCase="true">/openai/deployments/{model}/chat/completions</Pattern>
  </URIPath>
  <Source clearPayload="false">request</Source>
  <VariablePrefix>flow</VariablePrefix>
</ExtractVariables>"""
    P["JS-ExtractPrompt"] = """<Javascript continueOnError="true" enabled="true" timeLimit="200" name="JS-ExtractPrompt">
  <ResourceURL>jsc://ExtractPrompt.js</ResourceURL>
</Javascript>"""
    P["VA-VerifyAPIKey"] = """<VerifyAPIKey continueOnError="false" enabled="true" name="VA-VerifyAPIKey">
  <APIKey ref="flow.apiKey"/>
</VerifyAPIKey>"""
    P["AM-RemoveAuthorization"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-RemoveAuthorization">
  <Remove>
    <Headers>
      <Header name="Authorization"/>
      <Header name="api-key"/>
      <Header name="x-api-key"/>
      <Header name="x-apikey"/>
      <Header name="x-goog-api-key"/>
    </Headers>
  </Remove>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="request"/>
</AssignMessage>"""
    P["AM-InitCacheStatus"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-InitCacheStatus">
  <AssignVariable><Name>flow.cached</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>flow.cacheStatus</Name><Value>DISABLED</Value></AssignVariable>
  <AssignVariable><Name>flow.target_provider</Name><Value>google</Value></AssignVariable>
  <AssignVariable>
    <Name>flow.userEmail</Name>
    <Ref>verifyapikey.VA-VerifyAPIKey.developer.email</Ref>
    <Value>anonymous</Value>
  </AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
    pre += [("CORS-Headers", NOT_OPT), ("EV-ExtractAPIKey", NOT_PUBLIC), ("EV-RequestDetails", NOT_PUBLIC),
            ("JS-ExtractPrompt", NOT_PUBLIC), ("VA-VerifyAPIKey", NOT_PUBLIC)]

    if not sse:
        P["RF-StreamingNotSupported"] = """<RaiseFault continueOnError="false" enabled="true" name="RF-StreamingNotSupported">
  <FaultResponse>
    <Set>
      <Payload contentType="application/json">{"error":{"code":501,"status":"UNIMPLEMENTED","message":"SSE streaming is disabled on this gateway. Call :generateContent instead."}}</Payload>
      <StatusCode>501</StatusCode>
      <ReasonPhrase>Not Implemented</ReasonPhrase>
    </Set>
  </FaultResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</RaiseFault>"""
        pre.append(("RF-StreamingNotSupported", f'{NOT_PUBLIC} and flow.is_streaming = "true"'))

    if o.armor_region:
        P["SUP-UserPrompt"] = f"""<SanitizeUserPrompt async="false" continueOnError="false" enabled="true" name="SUP-UserPrompt">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <ModelArmor>
    <TemplateName>projects/{o.project}/locations/{o.armor_region}/templates/{o.prompt_template}</TemplateName>
  </ModelArmor>
  <UserPromptSource>{{flow.userPrompt}}</UserPromptSource>
</SanitizeUserPrompt>"""
        pre.append(("SUP-UserPrompt", f'{NOT_PUBLIC} and flow.userPrompt != null and flow.userPrompt != ""'))
    if o.prompt_token_limit:
        P["PTL-PromptTokenLimit"] = f"""<PromptTokenLimit continueOnError="false" enabled="true" name="PTL-PromptTokenLimit">
  <Properties/>
  <UserPromptSource>{{flow.userPrompt}}</UserPromptSource>
  <Identifier ref="verifyapikey.VA-VerifyAPIKey.client_id"/>
  <Rate>{o.prompt_token_limit}</Rate>
  <UseEffectiveCount>true</UseEffectiveCount>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</PromptTokenLimit>"""
        pre.append(("PTL-PromptTokenLimit", f'{NOT_PUBLIC} and flow.userPrompt != null and flow.userPrompt != ""'))
    pre += [("AM-RemoveAuthorization", NOT_PUBLIC), ("AM-InitCacheStatus", NOT_PUBLIC)]

    if o.openai:
        P["AM-PrepOpenAI"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-PrepOpenAI">
  <AssignVariable><Name>flow.target_provider</Name><Value>openai</Value></AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
        pre.append((
            "AM-PrepOpenAI",
            f'{NOT_PUBLIC} and ((proxy.pathsuffix JavaRegex "^(/v1)?/chat/completions$") '
            f'or (proxy.pathsuffix JavaRegex "^/openai/deployments/[^/]+/chat/completions$"))',
        ))

    if o.claude:
        P["AM-PrepClaude"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-PrepClaude">
  <AssignVariable><Name>flow.target_provider</Name><Value>anthropic</Value></AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
        pre.append((
            "AM-PrepClaude",
            f'{NOT_PUBLIC} and ((proxy.pathsuffix JavaRegex "^(/v1beta|/v1)?/models/claude.*") '
            f'or (proxy.pathsuffix JavaRegex "^(/v1)?/messages(/count_tokens)?$") '
            f'or (flow.model JavaRegex "^claude.*"))',
        ))

    auto_steps = []
    if o.auto_route:
        P["JS-PrepRouterRequest"] = """<Javascript continueOnError="true" enabled="true" timeLimit="200" name="JS-PrepRouterRequest">
  <ResourceURL>jsc://PrepRouterRequest.js</ResourceURL>
</Javascript>"""
        P["SC-ModelRouter"] = f"""<ServiceCallout continueOnError="true" enabled="true" name="SC-ModelRouter">
  <!-- Classifier call. continueOnError: on failure JS-AutoRouting falls back to routing.model.general -->
  <Request clearPayload="true" variable="routerRequest">
    <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
    <Set>
      <Headers>
        <Header name="Content-Type">application/json</Header>
      </Headers>
      <Verb>POST</Verb>
      <Payload contentType="application/json">{{flow.routerBody}}</Payload>
    </Set>
  </Request>
  <Response>routerResponse</Response>
  <HTTPTargetConnection>
    <Properties>
      <Property name="io.timeout.millis">3000</Property>
    </Properties>
    <Authentication>
      <GoogleAccessToken>
        <Scopes>
          <Scope>https://www.googleapis.com/auth/cloud-platform</Scope>
        </Scopes>
      </GoogleAccessToken>
    </Authentication>
    <URL>https://aiplatform.googleapis.com/v1/projects/{o.project}/locations/global/publishers/google/models/{o.router_model}:generateContent</URL>
  </HTTPTargetConnection>
</ServiceCallout>"""
        P["JS-AutoRouting"] = f"""<Javascript continueOnError="false" enabled="true" timeLimit="200" name="JS-AutoRouting">
  <Properties>
    <Property name="defaultModel">{o.default_model}</Property>
  </Properties>
  <ResourceURL>jsc://AutoRouting.js</ResourceURL>
</Javascript>"""
        auto_steps = ["JS-PrepRouterRequest", "SC-ModelRouter", "JS-AutoRouting"]

    if o.cache_region:
        P["AM-SetCacheHitExpected"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-SetCacheHitExpected">
  <AssignVariable><Name>flow.cached</Name><Value>true</Value></AssignVariable>
  <AssignVariable><Name>flow.cacheStatus</Name><Value>HIT</Value></AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
        P["AM-SetCacheMiss"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-SetCacheMiss">
  <!-- Runs in the TargetEndpoint PreFlow, which a semantic-cache hit never reaches -->
  <AssignVariable><Name>flow.cached</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>flow.cacheStatus</Name><Value>MISS</Value></AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
        r = o.cache_region
        P["SCL-Semantic-Cache-Lookup"] = f"""<SemanticCacheLookup async="false" continueOnError="true" enabled="true" name="SCL-Semantic-Cache-Lookup">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <UserPromptSource>{{flow.userPrompt}}</UserPromptSource>
  <Embeddings>
    <VertexAI>
      <URL>https://{r}-aiplatform.googleapis.com/v1/projects/{o.project}/locations/{r}/publishers/google/models/text-embedding-005:predict</URL>
    </VertexAI>
  </Embeddings>
  <SimilaritySearch>
    <VertexAI>
      <URL>https://{o.index_endpoint_host}/v1/projects/{o.project}/locations/{r}/indexEndpoints/{o.index_endpoint_id}:findNeighbors</URL>
      <DeployedIndexID>{o.deployed_index_id}</DeployedIndexID>
      <Threshold>0.95</Threshold>
    </VertexAI>
  </SimilaritySearch>
</SemanticCacheLookup>"""
        P["SCP-Semantic-Cache-Populate"] = f"""<SemanticCachePopulate async="false" continueOnError="true" enabled="true" name="SCP-Semantic-Cache-Populate">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <SimilaritySearch>
    <VertexAI>
      <URL>https://{r}-aiplatform.googleapis.com/v1/projects/{o.project}/locations/{r}/indexes/{o.index_id}:upsertDatapoints</URL>
    </VertexAI>
  </SimilaritySearch>
  <TTLInSeconds>180</TTLInSeconds>
</SemanticCachePopulate>"""
        pre += [("AM-SetCacheHitExpected", f"{NOT_PUBLIC} and {CACHE_ELIGIBLE}"),
                ("SCL-Semantic-Cache-Lookup", f"{NOT_PUBLIC} and {CACHE_ELIGIBLE}")]
        tgt_pre.append(("AM-SetCacheMiss", CACHE_ON))

    if o.token_quota:
        common = """  <Allow count="1000" countRef="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.limit"/>
  <Interval ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.interval">1</Interval>
  <TimeUnit ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.timeunit">minute</TimeUnit>
  <Distributed>true</Distributed>
  <Synchronous>true</Synchronous>
  <!-- Identifier, LLMModelSource and SharedName MUST be identical in all LTQ policies -->
  <Identifier ref="verifyapikey.VA-VerifyAPIKey.client_id"/>"""
        P["LTQ-TokenEnforce"] = f"""<LLMTokenQuota continueOnError="false" enabled="true" name="LTQ-TokenEnforce" type="rollingwindow">
{common}
  <LLMModelSource>{{flow.quota_model}}</LLMModelSource>
  <EnforceOnly>true</EnforceOnly>
  <SharedName>common-counter</SharedName>
</LLMTokenQuota>"""
        P["LTQ-TokenCount"] = f"""<LLMTokenQuota continueOnError="true" enabled="true" name="LTQ-TokenCount" type="rollingwindow">
{common}
  <LLMTokenUsageSource>{{firstnonnull(flow.totalTokenCount,jsonPath('$.usageMetadata.totalTokenCount',response.content,true))}}</LLMTokenUsageSource>
  <LLMModelSource>{{flow.quota_model}}</LLMModelSource>
  <CountOnly>true</CountOnly>
  <SharedName>common-counter</SharedName>
</LLMTokenQuota>"""
        if sse:
            P["LTQ-TokenCountSSE"] = f"""<LLMTokenQuota continueOnError="true" enabled="true" name="LTQ-TokenCountSSE" type="rollingwindow">
{common}
  <LLMTokenUsageSource>{{firstnonnull(flow.totalTokenCount,jsonPath('$.usageMetadata.totalTokenCount',response.event.current.data,false))}}</LLMTokenUsageSource>
  <LLMModelSource>{{flow.quota_model}}</LLMModelSource>
  <CountOnly>true</CountOnly>
  <SharedName>common-counter</SharedName>
</LLMTokenQuota>"""

    models = [o.default_model] + ([o.claude_model] if o.claude else []) + (["auto"] if o.auto_route else [])
    IS_LIST_MODELS = 'request.verb = "GET" and (proxy.pathsuffix = "/v1/models" or proxy.pathsuffix = "/models")'
    flows_extra.append(("HealthProbe", ["AM-HealthOk"], IS_HELLO))
    if o.openai:
        model_cards = json.dumps({
            "object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "vertex-ai"} for m in models],
        })
        P["AM-ListModels"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-ListModels">
  <Set>
    <Payload contentType="application/json">{model_cards}</Payload>
    <StatusCode>200</StatusCode>
  </Set>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="response"/>
</AssignMessage>"""
        flows_extra.append(("ListModels", ["AM-ListModels"], IS_LIST_MODELS))

    # Conditional flows run only on a semantic-cache miss (a lookup hit short-circuits after the PreFlow).
    # Only the FIRST matching conditional flow runs, so /models/auto must come before the generic inference flow,
    # and each flow must carry its own LLMTokenQuota enforce step.
    ltq = ["LTQ-TokenEnforce"] if o.token_quota else []
    if o.auto_route:
        flows_extra.append(("AutoRoutingFlow", ltq + auto_steps,
                            f'{NOT_PUBLIC} and proxy.pathsuffix JavaRegex "^(/v1beta|/v1)?/models/auto:.*"'))
    if ltq:
        flows_extra.append((
            "LLMTokenQuotaFlow",
            ltq,
            f'{NOT_PUBLIC} and (request.verb = "POST") and !(proxy.pathsuffix JavaRegex "^.*(:countTokens|/count_tokens)$")',
        ))

    buffered_200 = f"response.status.code = 200 and {NOT_STREAM}" if sse else "response.status.code = 200"
    P["EV-ModelResponse"] = """<ExtractVariables continueOnError="true" enabled="true" name="EV-ModelResponse">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <JSONPayload>
    <!-- never name a variable "model" here: it would overwrite flow.model mid-flow -->
    <Variable name="responseModelVersion"><JSONPath>$.modelVersion</JSONPath></Variable>
    <Variable name="promptTokenCount"><JSONPath>$.usageMetadata.promptTokenCount</JSONPath></Variable>
    <Variable name="candidatesTokenCount"><JSONPath>$.usageMetadata.candidatesTokenCount</JSONPath></Variable>
    <Variable name="totalTokenCount"><JSONPath>$.usageMetadata.totalTokenCount</JSONPath></Variable>
    <Variable name="responseText"><JSONPath>$.candidates[0].content.parts[0].text</JSONPath></Variable>
  </JSONPayload>
  <Source clearPayload="false">response</Source>
  <VariablePrefix>flow</VariablePrefix>
</ExtractVariables>"""
    post.append(("EV-ModelResponse", buffered_200))
    if o.openai:
        P["EV-OpenAIResponse"] = """<ExtractVariables continueOnError="true" enabled="true" name="EV-OpenAIResponse">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <JSONPayload>
    <Variable name="responseModelVersion"><JSONPath>$.model</JSONPath></Variable>
    <Variable name="promptTokenCount"><JSONPath>$.usage.prompt_tokens</JSONPath></Variable>
    <Variable name="candidatesTokenCount"><JSONPath>$.usage.completion_tokens</JSONPath></Variable>
    <Variable name="totalTokenCount"><JSONPath>$.usage.total_tokens</JSONPath></Variable>
    <Variable name="responseText"><JSONPath>$.choices[0].message.content</JSONPath></Variable>
  </JSONPayload>
  <Source clearPayload="false">response</Source>
  <VariablePrefix>flow</VariablePrefix>
</ExtractVariables>"""
        post.append(("EV-OpenAIResponse", f'{buffered_200} and flow.target_provider = "openai"'))
    if o.token_quota:
        post.append(("LTQ-TokenCount",
                     f'{buffered_200} and flow.cached != "true" and !(proxy.pathsuffix JavaRegex "^.*(:countTokens|/count_tokens)$")'))
    if o.cache_region:
        post.append(("SCP-Semantic-Cache-Populate",
                     f'response.status.code = 200 and {CACHE_ELIGIBLE} and flow.cached != "true"'))
    if o.armor_region:
        P["SMR-SanitizeModelResponse"] = f"""<SanitizeModelResponse async="false" continueOnError="true" enabled="true" name="SMR-SanitizeModelResponse">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <ModelArmor>
    <TemplateName>projects/{o.project}/locations/{o.armor_region}/templates/{o.response_template}</TemplateName>
  </ModelArmor>
  <LLMResponseSource>{{firstnonnull(flow.responseText,jsonPath('$.candidates[-1].content.parts',response.content,true))}}</LLMResponseSource>
</SanitizeModelResponse>"""
        post.append(("SMR-SanitizeModelResponse", buffered_200))
        if sse:
            P["SMR-SanitizeModelResponseSSE"] = f"""<SanitizeModelResponse async="false" continueOnError="false" enabled="true" name="SMR-SanitizeModelResponseSSE">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <ModelArmor>
    <TemplateName>projects/{o.project}/locations/{o.armor_region}/templates/{o.response_template}</TemplateName>
  </ModelArmor>
  <LLMResponseSource>{{firstnonnull(flow.currentChunkText,jsonPath('$.candidates[-1].content.parts[-1].text',response.event.current.data,true))}}</LLMResponseSource>
  <ContinuousScan>true</ContinuousScan>
</SanitizeModelResponse>"""
    if o.analytics:
        P["DC-ModelAnalytics"] = """<DataCapture continueOnError="true" enabled="true" name="DC-ModelAnalytics">
  <DisplayName>DC-ModelAnalytics</DisplayName>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <Capture>
    <DataCollector>dc_user_email</DataCollector>
    <Collect ref="flow.userEmail" default="anonymous"/>
  </Capture>
  <Capture>
    <DataCollector>dc_model_name</DataCollector>
    <Collect ref="flow.model" default="unknown"/>
  </Capture>
  <Capture>
    <DataCollector>dc_prompt_token_count</DataCollector>
    <Collect ref="flow.promptTokenCount" default="0"/>
  </Capture>
  <Capture>
    <DataCollector>dc_candidates_token_count</DataCollector>
    <Collect ref="flow.candidatesTokenCount" default="0"/>
  </Capture>
  <Capture>
    <DataCollector>dc_total_token_count</DataCollector>
    <Collect ref="flow.totalTokenCount" default="0"/>
  </Capture>
  <Capture>
    <DataCollector>dc_llm_input_token</DataCollector>
    <Collect ref="flow.promptTokenCount" default="0"/>
  </Capture>
  <Capture>
    <DataCollector>dc_llm_output_token</DataCollector>
    <Collect ref="flow.candidatesTokenCount" default="0"/>
  </Capture>
  <Capture>
    <DataCollector>dc_cache_status</DataCollector>
    <Collect ref="flow.cacheStatus" default="DISABLED"/>
  </Capture>
</DataCapture>"""
        P["DC-FaultAnalytics"] = """<DataCapture continueOnError="true" enabled="true" name="DC-FaultAnalytics">
  <DisplayName>DC-FaultAnalytics</DisplayName>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <Capture>
    <DataCollector>dc_user_email</DataCollector>
    <Collect ref="flow.userEmail" default="anonymous"/>
  </Capture>
  <Capture>
    <DataCollector>dc_model_name</DataCollector>
    <Collect ref="flow.model" default="unknown"/>
  </Capture>
</DataCapture>"""
        post.append(("DC-ModelAnalytics", f'request.verb = "POST" and {buffered_200}'))
    P["AM-SetResponseHeaders"] = """<AssignMessage continueOnError="true" enabled="true" name="AM-SetResponseHeaders">
  <Set>
    <Headers>
      <Header name="x-gateway-model">{flow.model}</Header>
      <Header name="x-gateway-provider">{flow.target_provider}</Header>
      <Header name="x-gateway-streaming">{flow.is_streaming}</Header>
      <Header name="x-gateway-cached">{flow.cached}</Header>
      <Header name="x-gateway-cache-status">{flow.cacheStatus}</Header>
      <Header name="x-gateway-prompt-tokens">{flow.promptTokenCount}</Header>
      <Header name="x-gateway-completion-tokens">{flow.candidatesTokenCount}</Header>
      <Header name="x-gateway-total-tokens">{flow.totalTokenCount}</Header>
      <Header name="x-gateway-router-category">{flow.router_category}</Header>
    </Headers>
  </Set>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="response"/>
</AssignMessage>"""
    post.append(("AM-SetResponseHeaders", f"({NOT_STREAM})" if sse else None))
    P["ML-CloudLogging"] = """<MessageLogging continueOnError="true" enabled="true" name="ML-CloudLogging">
  <CloudLogging>
    <LogName>projects/{organization.name}/logs/apigee</LogName>
    <Message contentType="application/json">{"proxy":"{apiproxy.name}","user":"{flow.userEmail}","model":"{flow.model}","provider":"{flow.target_provider}","streaming":"{flow.is_streaming}","app":"{developer.app.name}","cached":"{flow.cached}","promptTokens":"{flow.promptTokenCount}","completionTokens":"{flow.candidatesTokenCount}","totalTokens":"{flow.totalTokenCount}","status":"{response.status.code}","fault":"{fault.name}","messageId":"{messageid}"}</Message>
    <ResourceType>api</ResourceType>
  </CloudLogging>
</MessageLogging>"""

    def build_ef_steps(first_js):
        ef = [(first_js, None)]
        if o.armor_region:
            ef.append((
                "SMR-SanitizeModelResponseSSE",
                'response.event.current.data != null and response.event.current.data != "" and response.event.current.data != "[DONE]"',
            ))
        if o.token_quota:
            ef.append(("LTQ-TokenCountSSE", DONE_SSE_EVENT))
        if o.analytics:
            ef.append(("DC-ModelAnalytics", DONE_SSE_EVENT))
        return ef

    # Targets: ALWAYS separate SSE and non-SSE TargetEndpoints (mixing causes empty response.content).
    # Each entry: target_name -> (pre_req_steps, pre_resp_steps, event_flow_steps_or_None, is_streaming)
    targets = {}
    P["AM-RouteGeminiTarget"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RouteGeminiTarget">
  <AssignVariable><Name>target.copy.pathsuffix</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>target.copy.queryparams</Name><Value>false</Value></AssignVariable>
  <AssignVariable>
    <Name>target.url</Name>
    <Template>https://{vertex_host}/v1/projects/{o.project}/locations/{o.model_location}/publishers/google/models/{{flow.model}}:generateContent</Template>
  </AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
    targets["gemini-vertex-target"] = (tgt_pre + [("AM-RouteGeminiTarget", None)], [], None, False)

    if sse:
        P["AM-RouteGeminiStreamTarget"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RouteGeminiStreamTarget">
  <AssignVariable><Name>target.copy.pathsuffix</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>target.copy.queryparams</Name><Value>false</Value></AssignVariable>
  <AssignVariable>
    <Name>target.url</Name>
    <Template>https://{vertex_host}/v1/projects/{o.project}/locations/{o.model_location}/publishers/google/models/{{flow.model}}:streamGenerateContent?alt=sse</Template>
  </AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
        P["JS-StreamMetrics"] = """<Javascript continueOnError="true" enabled="true" timeLimit="200" name="JS-StreamMetrics">
  <ResourceURL>jsc://StreamMetrics.js</ResourceURL>
</Javascript>"""
        targets["gemini-vertex-stream-target"] = (
            [("AM-RouteGeminiStreamTarget", None)],
            [],
            build_ef_steps("JS-StreamMetrics"),
            True,
        )

    if o.openai:
        P["JS-NormalizeOpenAIRequest"] = f"""<Javascript continueOnError="false" enabled="true" timeLimit="200" name="JS-NormalizeOpenAIRequest">
  <Properties>
    <Property name="defaultModel">{o.default_model}</Property>
  </Properties>
  <ResourceURL>jsc://NormalizeOpenAIRequest.js</ResourceURL>
</Javascript>"""
        P["AM-RouteOpenAITarget"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RouteOpenAITarget">
  <AssignVariable><Name>target.copy.pathsuffix</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>target.copy.queryparams</Name><Value>false</Value></AssignVariable>
  <AssignVariable>
    <Name>target.url</Name>
    <Template>https://{vertex_host}/v1beta1/projects/{o.project}/locations/{o.model_location}/endpoints/openapi/chat/completions</Template>
  </AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
        targets["openai-vertex-target"] = (
            tgt_pre + [("JS-NormalizeOpenAIRequest", None), ("AM-RouteOpenAITarget", None)],
            [],
            None,
            False,
        )
        if sse:
            P["AM-RouteOpenAIStreamTarget"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RouteOpenAIStreamTarget">
  <AssignVariable><Name>target.copy.pathsuffix</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>target.copy.queryparams</Name><Value>false</Value></AssignVariable>
  <AssignVariable>
    <Name>target.url</Name>
    <Template>https://{vertex_host}/v1beta1/projects/{o.project}/locations/{o.model_location}/endpoints/openapi/chat/completions</Template>
  </AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
            targets["openai-vertex-stream-target"] = (
                [("JS-NormalizeOpenAIRequest", None), ("AM-RouteOpenAIStreamTarget", None)],
                [],
                build_ef_steps("JS-StreamMetrics"),
                True,
            )

    if o.claude:
        P["JS-ClaudeRequestPrep"] = """<Javascript continueOnError="false" enabled="true" timeLimit="200" name="JS-ClaudeRequestPrep">
  <ResourceURL>jsc://ClaudeRequestPrep.js</ResourceURL>
</Javascript>"""
        P["AM-RouteClaudeTarget"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RouteClaudeTarget">
  <!-- Anthropic on Vertex: body is Anthropic Messages shape; queryparams (?beta=true) are stripped -->
  <AssignVariable><Name>target.copy.pathsuffix</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>target.copy.queryparams</Name><Value>false</Value></AssignVariable>
  <AssignVariable>
    <Name>target.url</Name>
    <Template>https://aiplatform.googleapis.com/v1/projects/{o.project}/locations/global/publishers/anthropic/models/{{flow.target_model}}:{{flow.claude_action}}</Template>
  </AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
        P["JS-FormatClaudeResponse"] = """<Javascript continueOnError="true" enabled="true" timeLimit="200" name="JS-FormatClaudeResponse">
  <ResourceURL>jsc://FormatClaudeResponse.js</ResourceURL>
</Javascript>"""
        targets["claude-vertex-target"] = (
            tgt_pre + [("JS-ClaudeRequestPrep", None), ("AM-RouteClaudeTarget", None)],
            [("JS-FormatClaudeResponse", None)],
            None,
            False,
        )
        if sse:
            P["AM-RouteClaudeStreamTarget"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RouteClaudeStreamTarget">
  <!-- Anthropic on Vertex SSE: :streamRawPredict with "stream": true in the Anthropic Messages payload -->
  <AssignVariable><Name>target.copy.pathsuffix</Name><Value>false</Value></AssignVariable>
  <AssignVariable><Name>target.copy.queryparams</Name><Value>false</Value></AssignVariable>
  <AssignVariable>
    <Name>target.url</Name>
    <Template>https://aiplatform.googleapis.com/v1/projects/{o.project}/locations/global/publishers/anthropic/models/{{flow.target_model}}:streamRawPredict</Template>
  </AssignVariable>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""
            P["JS-FormatClaudeStream"] = """<Javascript continueOnError="true" enabled="true" timeLimit="200" name="JS-FormatClaudeStream">
  <ResourceURL>jsc://FormatClaudeStream.js</ResourceURL>
</Javascript>"""
            targets["claude-vertex-stream-target"] = (
                [("JS-ClaudeRequestPrep", None), ("AM-RouteClaudeStreamTarget", None)],
                [],
                build_ef_steps("JS-FormatClaudeStream"),
                True,
            )

    # ---- write files
    root = os.path.join(o.out, "apiproxy")
    for d in ("policies", "proxies", "targets", "resources/jsc"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for n, x in P.items():
        open(os.path.join(root, "policies", n + ".xml"), "w").write(H + x + "\n")
    js_files = {"ExtractPrompt.js": JS}
    if sse:
        js_files["StreamMetrics.js"] = JS_STREAM_METRICS
    if o.openai:
        js_files["NormalizeOpenAIRequest.js"] = JS_OPENAI_REQ
    if o.claude:
        js_files["ClaudeRequestPrep.js"] = JS_CLAUDE_REQ
        js_files["FormatClaudeResponse.js"] = JS_CLAUDE_RESP
        if sse:
            js_files["FormatClaudeStream.js"] = JS_CLAUDE_STREAM
    if o.auto_route:
        js_files["PrepRouterRequest.js"] = JS_ROUTER_REQ
        js_files["AutoRouting.js"] = JS_AUTO_ROUTING
    for fn, src in js_files.items():
        open(os.path.join(root, "resources/jsc", fn), "w").write(src)

    def steps(lst, ind="      "):
        out = []
        for n, c in lst:
            out.append(f"{ind}<Step>\n{ind}  <Name>{n}</Name>")
            if c:
                out.append(f"{ind}  <Condition>{c.replace('>', '&gt;')}</Condition>")
            out.append(f"{ind}</Step>")
        return "\n".join(out)

    flows = [f"""    <Flow name="OptionsPreFlight">
      <Request>
{steps([("CORS-Headers", None)], "        ")}
      </Request>
      <Response/>
      <Condition>request.verb == "OPTIONS" AND request.header.origin != null</Condition>
    </Flow>"""]
    for fname, pols, cond in flows_extra:
        if fname in ("HealthProbe", "ListModels"):
            flows.append(f"""    <Flow name="{fname}">
      <Request/>
      <Response>
{steps([(x, None) for x in pols], "        ")}
      </Response>
      <Condition>{cond}</Condition>
    </Flow>""")
        else:
            flows.append(f"""    <Flow name="{fname}">
      <Request>
{steps([(x, None) for x in pols], "        ")}
      </Request>
      <Response/>
      <Condition>{cond}</Condition>
    </Flow>""")

    loopback_cond = f"({IS_HELLO}) or ({IS_LIST_MODELS})" if o.openai else IS_HELLO
    rr_lines = [f"""  <RouteRule name="loopback">
    <Condition>{loopback_cond}</Condition>
  </RouteRule>"""]
    if o.openai and sse:
        rr_lines.append("""  <RouteRule name="openai-stream-target">
    <Condition>(flow.target_provider == "openai") and (flow.is_streaming == "true")</Condition>
    <TargetEndpoint>openai-vertex-stream-target</TargetEndpoint>
  </RouteRule>""")
    if o.openai:
        rr_lines.append("""  <RouteRule name="openai-target">
    <Condition>flow.target_provider == "openai"</Condition>
    <TargetEndpoint>openai-vertex-target</TargetEndpoint>
  </RouteRule>""")
    if o.claude and sse:
        rr_lines.append("""  <RouteRule name="claude-stream-target">
    <Condition>(flow.target_provider == "anthropic") and (flow.is_streaming == "true")</Condition>
    <TargetEndpoint>claude-vertex-stream-target</TargetEndpoint>
  </RouteRule>""")
    if o.claude:
        rr_lines.append("""  <RouteRule name="claude-target">
    <Condition>flow.target_provider == "anthropic"</Condition>
    <TargetEndpoint>claude-vertex-target</TargetEndpoint>
  </RouteRule>""")
    if sse:
        rr_lines.append("""  <RouteRule name="gemini-stream-target">
    <Condition>flow.is_streaming == "true"</Condition>
    <TargetEndpoint>gemini-vertex-stream-target</TargetEndpoint>
  </RouteRule>""")
    rr_lines.append("""  <RouteRule name="gemini-target">
    <TargetEndpoint>gemini-vertex-target</TargetEndpoint>
  </RouteRule>""")
    route_rules = "\n".join(rr_lines)

    default_fault_xml = ""
    if o.analytics:
        default_fault_xml = f"""  <DefaultFaultRule name="attribute-fault">
    <AlwaysEnforce>true</AlwaysEnforce>
{steps([("DC-FaultAnalytics", None)], "    ")}
  </DefaultFaultRule>
"""

    proxy = f"""{H}<ProxyEndpoint name="default">
  <PreFlow name="PreFlow">
    <Request>
{steps(pre)}
    </Request>
    <Response/>
  </PreFlow>
  <Flows>
{chr(10).join(flows)}
  </Flows>
  <PostFlow name="PostFlow">
    <Request/>
    <Response>
{steps(post)}
    </Response>
  </PostFlow>
  <PostClientFlow>
    <Response>
{steps([("ML-CloudLogging", None)])}
    </Response>
  </PostClientFlow>
{default_fault_xml}  <HTTPProxyConnection>
    <BasePath>{o.basepath}</BasePath>
  </HTTPProxyConnection>
{route_rules}
</ProxyEndpoint>
"""
    open(os.path.join(root, "proxies/default.xml"), "w").write(proxy)
    for tname, (treq, tresp, tef, is_stream) in targets.items():
        resp_xml = f"    <Response>\n{steps(tresp)}\n    </Response>" if tresp else "    <Response/>"
        ef_xml = ""
        if tef is not None:
            ef_xml = f"""  <EventFlow name="EventFlow" content-type="text/event-stream">
    <Response>
{steps(tef)}
    </Response>
  </EventFlow>
"""
        props_xml = ""
        if is_stream:
            props_xml = """    <Properties>
      <Property name="response.streaming.enabled">true</Property>
      <Property name="keepalive.timeout.millis">300000</Property>
      <Property name="io.timeout.millis">300000</Property>
    </Properties>
"""
        t_host = vertex_host if tname.startswith(("gemini-", "openai-")) else "aiplatform.googleapis.com"
        open(os.path.join(root, "targets", tname + ".xml"), "w").write(f"""{H}<TargetEndpoint name="{tname}">
  <PreFlow name="PreFlow">
    <Request>
{steps(treq)}
    </Request>
{resp_xml}
  </PreFlow>
{ef_xml}  <HTTPTargetConnection>
{props_xml}    <URL>https://{t_host}</URL>
    <Authentication>
      <GoogleAccessToken>
        <Scopes><Scope>https://www.googleapis.com/auth/cloud-platform</Scope></Scopes>
      </GoogleAccessToken>
    </Authentication>
  </HTTPTargetConnection>
</TargetEndpoint>
""")
    pol_list = "\n".join(f"    <Policy>{n}</Policy>" for n in sorted(P))
    tgt_list = "\n".join(f"    <TargetEndpoint>{t}</TargetEndpoint>" for t in targets)
    res_list = "\n".join(f"    <Resource>jsc://{fn}</Resource>" for fn in sorted(js_files))
    open(os.path.join(root, f"{o.name}.xml"), "w").write(f"""{H}<APIProxy revision="1" name="{o.name}">
  <DisplayName>{o.name}</DisplayName>
  <Description>AI Gateway scaffolded by the apigee-ai-gateway skill</Description>
  <BasePaths>{o.basepath}</BasePaths>
  <Policies>
{pol_list}
  </Policies>
  <Resources>
{res_list}
  </Resources>
  <ProxyEndpoints>
    <ProxyEndpoint>default</ProxyEndpoint>
  </ProxyEndpoints>
  <TargetEndpoints>
{tgt_list}
  </TargetEndpoints>
</APIProxy>
""")

    product = {
        "name": f"{o.name} - Standard",
        "displayName": f"{o.name} - Standard",
        "approvalType": "auto",
        "attributes": [{"name": "access", "value": "private"}] + ([
            # /models/auto: classifier category -> model, per product (persona). Edit per product, not in the proxy.
            {"name": "routing.model.coding", "value": o.claude_model if o.claude else o.default_model},
            {"name": "routing.model.deep_reasoning", "value": o.default_model},
            {"name": "routing.model.simple", "value": o.router_model},
            {"name": "routing.model.general", "value": o.default_model},
        ] if o.auto_route else []),
        "environments": ["<ENV>"],
        # Keep operationGroup and llmOperationGroup apiSource sets identical so VerifyAPIKey + LLMTokenQuota agree.
        "operationGroup": {
            "operationConfigType": "proxy",
            "operationConfigs": [{
                "apiSource": o.name,
                "operations": [{"resource": "/**", "methods": ["GET", "POST", "OPTIONS"]}],
            }],
        },
        # Apigee X requires exactly ONE entity per llmOperations[] list; add one operationConfig per model.
        "llmOperationGroup": {"operationConfigs": [
            {"apiSource": o.name,
             "llmOperations": [{"resource": "/**", "methods": ["POST"], "model": m}],
             "llmTokenQuota": {"limit": "10000", "interval": "1", "timeUnit": "minute"}} for m in models]},
    }
    open(os.path.join(o.out, "api_product.json"), "w").write(json.dumps(product, indent=2) + "\n")
    print(f"Scaffolded {o.out} ({len(P)} policies, targets: {', '.join(targets)})")
    print("Next: validate_bundle.py, then deploy_bundle.sh --sa <runtime SA with aiplatform.user"
          + (", modelarmor.user" if o.armor_region else "") + ">, then create the API product + app.")


JS = textwrap.dedent("""\
    // Extract the user prompt (Gemini / Antigravity v1internal / Anthropic / OpenAI / flat) into flow.userPrompt
    // so SanitizeUserPrompt, PromptTokenLimit and SemanticCacheLookup share one provider-agnostic source.
    // Also detect SSE streaming (:streamGenerateContent / :streamRawPredict or body.stream === true).
    // flow.quota_model is the model id LLMTokenQuota keys on (without @YYYYMMDD suffix); never mutate mid-flow.
    var model = context.getVariable("flow.model") || "";
    var suffix = String(context.getVariable("proxy.pathsuffix") || "");
    var isStreaming = /:(streamGenerateContent|streamRawPredict)$/i.test(suffix);
    var body = context.getVariable("request.content") || "";
    try {
      var p = JSON.parse(body), text = "";
      if (p && typeof p.request === "object" && p.request && p.request.contents) {
        // Unwrap Antigravity /v1internal {request: {contents, session_id, ...}} envelope for Vertex GenAI.
        if (!model && p.model) { model = String(p.model); }
        p = p.request;
        delete p.session_id;
        context.setVariable("request.content", JSON.stringify(p));
      }
      if (!model && p && typeof p.model === "string") {
        model = p.model;
      }
      if (p && p.stream === true) { isStreaming = true; }
      if (p && p.contents && p.contents.length) {
        var parts = p.contents[p.contents.length - 1].parts || [];
        text = parts.filter(function (x) { return x && x.text && !x.thought; }).map(function (x) { return x.text; }).join(" ");
      } else if (p && p.messages && p.messages.length) {
        var c = p.messages[p.messages.length - 1].content;
        text = typeof c === "string" ? c : (c || []).filter(function (x) { return x && x.text; }).map(function (x) { return x.text; }).join(" ");
      } else if (p && p.prompt) {
        text = typeof p.prompt === "string" ? p.prompt : JSON.stringify(p.prompt);
      }
      if (text) { context.setVariable("flow.userPrompt", text); }
    } catch (e) { /* non-JSON body: leave unset */ }
    if (model) {
      var cleanModel = String(model).replace(/^(google|anthropic)\\//, "").replace(/^models\\//, "");
      context.setVariable("flow.model", cleanModel);
      context.setVariable("flow.quota_model", cleanModel.split("@")[0]);
    }
    context.setVariable("flow.is_streaming", isStreaming ? "true" : "false");
    """)

JS_STREAM_METRICS = textwrap.dedent("""\
    // Process each SSE chunk inside <EventFlow> for Gemini (:streamGenerateContent?alt=sse) and OpenAI SSE.
    // IMPORTANT: Always reset flow.stream_done to "false" on line 1 so trailing events like "data: [DONE]"
    // never re-trigger LTQ-TokenCountSSE or DC-ModelAnalytics a second time.
    context.setVariable("flow.stream_done", "false");
    context.setVariable("flow.currentChunkText", "");
    try {
      var raw = context.getVariable("response.event.current.data") || "";
      if (raw) {
        var lines = String(raw).split("\\n");
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line) { continue; }
          if (line.indexOf("data:") === 0) { line = line.substring(5).trim(); }
          if (!line || line === "[DONE]" || line.charAt(0) !== "{") { continue; }
          var ev = JSON.parse(line), deltaText = "";
          if (ev.modelVersion) { context.setVariable("flow.responseModelVersion", String(ev.modelVersion)); }
          else if (ev.model) { context.setVariable("flow.responseModelVersion", String(ev.model)); }

          if (ev.candidates && ev.candidates.length && ev.candidates[0].content && ev.candidates[0].content.parts) {
            var parts = ev.candidates[0].content.parts;
            for (var p = 0; p < parts.length; p++) {
              if (parts[p] && typeof parts[p].text === "string" && !parts[p].thought) {
                deltaText += parts[p].text;
              }
            }
          } else if (ev.choices && ev.choices.length && ev.choices[0].delta && typeof ev.choices[0].delta.content === "string") {
            deltaText = ev.choices[0].delta.content;
          }
          if (deltaText) {
            context.setVariable("flow.currentChunkText", deltaText);
            var prev = context.getVariable("flow.responseText") || "";
            if (prev.length < 4096) {
              context.setVariable("flow.responseText", (prev + deltaText).substring(0, 4096));
            }
          }

          var u = ev.usageMetadata;
          if (u) {
            if (u.promptTokenCount !== undefined) { context.setVariable("flow.promptTokenCount", String(u.promptTokenCount)); }
            if (u.candidatesTokenCount !== undefined) { context.setVariable("flow.candidatesTokenCount", String(u.candidatesTokenCount)); }
            if (u.thoughtsTokenCount !== undefined) { context.setVariable("flow.thoughtsTokenCount", String(u.thoughtsTokenCount)); }
            if (u.totalTokenCount !== undefined) { context.setVariable("flow.totalTokenCount", String(u.totalTokenCount)); }
            // Intermediate Gemini chunks can carry candidatesTokenCount; only finishReason marks the single final chunk.
            if (ev.candidates && ev.candidates[0] && ev.candidates[0].finishReason) {
              context.setVariable("flow.stream_done", "true");
            }
          } else if (ev.usage && (ev.usage.total_tokens !== undefined || ev.usage.prompt_tokens !== undefined)) {
            var oaiP = ev.usage.prompt_tokens || 0, oaiC = ev.usage.completion_tokens || 0;
            var oaiT = ev.usage.total_tokens !== undefined ? ev.usage.total_tokens : (oaiP + oaiC);
            context.setVariable("flow.promptTokenCount", String(oaiP));
            context.setVariable("flow.candidatesTokenCount", String(oaiC));
            context.setVariable("flow.totalTokenCount", String(oaiT));
            context.setVariable("flow.stream_done", "true");
          }
        }
      }
    } catch (e) { /* non-JSON SSE event: ignore */ }
    """)

JS_OPENAI_REQ = textwrap.dedent("""\
    // Normalize OpenAI / Azure OpenAI Chat Completions payload for Vertex AI's OpenAI-compatible endpoint:
    //   1. Prefix model with "google/" (required by /endpoints/openapi/chat/completions).
    //   2. Inject stream_options: {include_usage: true} on SSE streams so Vertex emits final token usage before [DONE].
    try {
      var b = JSON.parse(context.getVariable("request.content") || "{}");
      var m = b.model || context.getVariable("flow.model") || properties.defaultModel;
      m = String(m).replace(/^google\\//, "");
      context.setVariable("flow.model", m);
      if (!context.getVariable("flow.quota_model")) {
        context.setVariable("flow.quota_model", m.split("@")[0]);
      }
      b.model = "google/" + m;
      if (context.getVariable("flow.is_streaming") === "true" || b.stream === true) {
        b.stream = true;
        b.stream_options = b.stream_options || {};
        b.stream_options.include_usage = true;
      }
      context.setVariable("request.content", JSON.stringify(b));
    } catch (e) {
      context.setVariable("flow.openai_prep_error", String(e));
    }
    """)

JS_CLAUDE_REQ = textwrap.dedent("""\
    // Prepare Anthropic-on-Vertex request (:rawPredict / :streamRawPredict / :countTokens).
    // Supports BOTH Gemini generateContent callers (one client contract) AND native Anthropic Messages callers
    // (Claude Code / Claude Desktop / Anthropic SDK). Filters 1P-only anthropic-beta flags that Vertex AI rejects
    // with HTTP 400 and normalizes -YYYYMMDD model suffixes to @YYYYMMDD without mutating flow.quota_model.
    try {
      var rawModel = String(context.getVariable("flow.model") || "");
      var suffix = String(context.getVariable("proxy.pathsuffix") || "");
      var isCountTokens = /(:countTokens|\/count_tokens)$/i.test(suffix);
      var isStream = !isCountTokens && (context.getVariable("flow.is_streaming") === "true");
      var body = JSON.parse(context.getVariable("request.content") || "{}");
      if (!rawModel && body.model) { rawModel = String(body.model); }
      // Map Anthropic 1P date suffix (-20250514) to Vertex version separator (@20250514) for target.url.
      var targetModel = rawModel.replace(/-(\\d{8})$/, "@$1");
      context.setVariable("flow.target_model", targetModel);
      context.setVariable("flow.claude_action", isCountTokens ? "countTokens" : "rawPredict");

      // Filter request.header.anthropic-beta to flags supported by Vertex AI (prevents 1P flag HTTP 400 storms).
      var betaHdr = context.getVariable("request.header.anthropic-beta");
      if (betaHdr) {
        var isHaiku = /haiku/i.test(targetModel);
        var allowed = {
          "claude-code-20250219": true,
          "interleaved-thinking-2025-05-14": true,
          "context-management-2025-06-27": true,
          "thinking-token-count-2026-05-13": !isHaiku,
          "advanced-tool-use-2025-11-20": true,
          "effort-2025-11-24": true,
          "token-efficient-tools-2025-02-19": true,
          "computer-use-2025-01-24": true,
          "context-1m-2025-08-07": true
        };
        var flags = String(betaHdr).split(","), kept = [];
        for (var f = 0; f < flags.length; f++) {
          var fl = flags[f].trim();
          if (fl && allowed[fl]) { kept.push(fl); }
        }
        if (kept.length) {
          context.setVariable("request.header.anthropic-beta", kept.join(","));
        } else if (typeof context.removeVariable === "function") {
          context.removeVariable("request.header.anthropic-beta");
        } else {
          context.setVariable("request.header.anthropic-beta", "");
        }
      }

      var isArr = function (v) { return Object.prototype.toString.call(v) === "[object Array]"; };
      if (isArr(body.contents)) {
        var msgs = [], lastId = {}, seq = 0;
        for (var i = 0; i < body.contents.length; i++) {
          var item = body.contents[i] || {}, role = item.role === "model" ? "assistant" : "user";
          var blocks = [], results = [], parts = item.parts || [];
          for (var j = 0; j < parts.length; j++) {
            var pt = parts[j];
            if (!pt) { continue; }
            if (pt.text) { blocks.push({ type: "text", text: pt.text }); }
            else if (pt.functionCall) {
              var id = pt.functionCall.id || ("toolu_" + (++seq));
              lastId[pt.functionCall.name] = id;
              blocks.push({ type: "tool_use", id: id, name: pt.functionCall.name, input: pt.functionCall.args || {} });
            } else if (pt.functionResponse) {
              var fr = pt.functionResponse, r = fr.response;
              results.push({ type: "tool_result", tool_use_id: fr.id || lastId[fr.name] || ("toolu_" + fr.name),
                             content: typeof r === "string" ? r : JSON.stringify(r === undefined ? {} : r) });
            }
          }
          if (results.length) { msgs.push({ role: "user", content: results.concat(blocks) }); continue; }
          if (blocks.length) { msgs.push({ role: role, content: blocks }); }
        }
        // Anthropic requires alternating roles: merge neighbours with the same role.
        var merged = [];
        for (var k = 0; k < msgs.length; k++) {
          var prev = merged[merged.length - 1];
          if (prev && prev.role === msgs[k].role) { prev.content = prev.content.concat(msgs[k].content); }
          else { merged.push(msgs[k]); }
        }
        var out = { anthropic_version: "vertex-2023-10-16", messages: merged, max_tokens: 1024 };
        if (isStream) { out.stream = true; }
        if (body.systemInstruction && isArr(body.systemInstruction.parts)) {
          var sys = [];
          for (var s = 0; s < body.systemInstruction.parts.length; s++) {
            if (body.systemInstruction.parts[s] && body.systemInstruction.parts[s].text) { sys.push(body.systemInstruction.parts[s].text); }
          }
          if (sys.length) { out.system = sys.join("\\n"); }
        }
        var gc = body.generationConfig || {};
        if (gc.temperature !== undefined) { out.temperature = gc.temperature; }
        if (gc.maxOutputTokens !== undefined) { out.max_tokens = gc.maxOutputTokens; }
        if (gc.topP !== undefined) { out.top_p = gc.topP; }
        if (gc.stopSequences !== undefined) { out.stop_sequences = gc.stopSequences; }
        if (isArr(body.tools)) {
          var tools = [];
          for (var t = 0; t < body.tools.length; t++) {
            var decls = (body.tools[t] || {}).functionDeclarations || [];
            for (var d = 0; d < decls.length; d++) {
              tools.push({ name: decls[d].name, description: decls[d].description || "",
                           input_schema: decls[d].parameters || { type: "object", properties: {} } });
            }
          }
          if (tools.length) { out.tools = tools; if (out.max_tokens < 2048) { out.max_tokens = 2048; } }
        }
        context.setVariable("request.content", JSON.stringify(out));
        context.setVariable("flow.convert_claude_to_gemini_resp", "true");
      } else if (isArr(body.messages)) {
        // Already Anthropic-shaped: add Vertex fields (except countTokens where anthropic_version is omitted) and drop "model".
        if (isCountTokens) {
          delete body.anthropic_version;
          delete body.max_tokens;
          delete body.stream;
        } else {
          if (!body.anthropic_version) { body.anthropic_version = "vertex-2023-10-16"; }
          if (!body.max_tokens) { body.max_tokens = 1024; }
          if (isStream) { body.stream = true; }
        }
        delete body.model;
        context.setVariable("request.content", JSON.stringify(body));
      }
    } catch (e) {
      context.setVariable("flow.claude_prep_error", String(e));
    }
    """)

JS_CLAUDE_RESP = textwrap.dedent("""\
    // Extract Claude buffered usage into flow.* and (when flow.convert_claude_to_gemini_resp === "true")
    // convert Anthropic Messages response -> Gemini generateContent response. Rhino (ES5).
    try {
      var c = JSON.parse(context.getVariable("response.content") || "{}");
      var u = c.usage || {}, pin = u.input_tokens || 0, pout = u.output_tokens || 0;
      if (u.input_tokens !== undefined || u.output_tokens !== undefined) {
        context.setVariable("flow.promptTokenCount", String(pin));
        context.setVariable("flow.candidatesTokenCount", String(pout));
        context.setVariable("flow.totalTokenCount", String(pin + pout));
      }
      if (Object.prototype.toString.call(c.content) === "[object Array]") {
        var parts = [], text = "", fullText = "";
        for (var i = 0; i < c.content.length; i++) {
          var b = c.content[i] || {};
          if (b.type === "text" && b.text) { text += b.text; fullText += b.text; }
          else if (b.type === "tool_use") {
            if (text) { parts.push({ text: text }); text = ""; }
            parts.push({ functionCall: { id: b.id || "", name: b.name || "", args: b.input || {} } });
          }
        }
        if (fullText) { context.setVariable("flow.responseText", fullText.substring(0, 4096)); }
        if (context.getVariable("flow.convert_claude_to_gemini_resp") !== "false") {
          if (text) { parts.push({ text: text }); }
          if (!parts.length) { parts.push({ text: "" }); }
          var finish = c.stop_reason === "max_tokens" ? "MAX_TOKENS" : (c.stop_reason === "refusal" ? "SAFETY" : "STOP");
          context.setVariable("response.content", JSON.stringify({
            candidates: [{ content: { role: "model", parts: parts }, finishReason: finish }],
            usageMetadata: { promptTokenCount: pin, candidatesTokenCount: pout, totalTokenCount: pin + pout },
            modelVersion: c.model || context.getVariable("flow.model")
          }));
        }
      }
    } catch (e) {
      context.setVariable("flow.claude_format_error", String(e));
    }
    """)

JS_CLAUDE_STREAM = textwrap.dedent("""\
    // Anthropic :streamRawPredict SSE event processor inside <EventFlow>.
    // Resets flow.stream_done = "false" on every chunk so trailing message_stop / ping events never
    // trigger LTQ-TokenCountSSE or DC-ModelAnalytics twice. Converts to Gemini SSE when
    // flow.convert_claude_to_gemini_resp === "true", or preserves native Anthropic SSE otherwise.
    context.setVariable("flow.stream_done", "false");
    context.setVariable("flow.currentChunkText", "");
    try {
      var raw = context.getVariable("response.event.current.data") || "";
      var convert = context.getVariable("flow.convert_claude_to_gemini_resp") === "true";
      if (raw) {
        var ev = JSON.parse(raw), t = ev.type || "";
        var model = context.getVariable("flow.claude_stream_model") || context.getVariable("flow.model") || "";
        var emitGemini = function (obj) {
          var s = JSON.stringify(obj);
          context.setVariable("response.event.current.data", s);
          context.setVariable("response.event.current.content", "data: " + s + "\\n\\n");
        };
        var suppress = function () {
          context.setVariable("response.event.current.data", "");
          context.setVariable("response.event.current.content", "");
        };
        if (t === "message_start") {
          var m = ev.message || {}, mu = m.usage || {};
          if (m.model) {
            model = m.model;
            context.setVariable("flow.claude_stream_model", model);
            context.setVariable("flow.responseModelVersion", model);
          }
          context.setVariable("flow.claude_input_tokens", String(mu.input_tokens || 0));
          context.setVariable("flow.promptTokenCount", String(mu.input_tokens || 0));
          if (convert) { suppress(); }
        } else if (t === "content_block_start") {
          var cb = ev.content_block || {};
          if (cb.type === "tool_use") {
            context.setVariable("flow.claude_tool_id", cb.id || "");
            context.setVariable("flow.claude_tool_name", cb.name || "");
            context.setVariable("flow.claude_tool_json", "");
          }
          if (convert) { suppress(); }
        } else if (t === "content_block_delta") {
          var d = ev.delta || {};
          if (d.type === "text_delta" && d.text) {
            context.setVariable("flow.currentChunkText", d.text);
            var prevTxt = context.getVariable("flow.responseText") || "";
            if (prevTxt.length < 4096) {
              context.setVariable("flow.responseText", (prevTxt + d.text).substring(0, 4096));
            }
            if (convert) {
              emitGemini({ candidates: [{ content: { role: "model", parts: [{ text: d.text }] } }], modelVersion: model });
            }
          } else if (d.type === "input_json_delta") {
            var prev = context.getVariable("flow.claude_tool_json") || "";
            context.setVariable("flow.claude_tool_json", prev + (d.partial_json || ""));
            if (convert) { suppress(); }
          } else if (convert) { suppress(); }
        } else if (t === "content_block_stop") {
          var tname = context.getVariable("flow.claude_tool_name") || "";
          if (tname) {
            var tid = context.getVariable("flow.claude_tool_id") || "", args = {};
            try { args = JSON.parse(context.getVariable("flow.claude_tool_json") || "{}"); } catch (je) { args = {}; }
            context.setVariable("flow.claude_tool_name", "");
            context.setVariable("flow.claude_tool_id", "");
            context.setVariable("flow.claude_tool_json", "");
            if (convert) {
              emitGemini({ candidates: [{ content: { role: "model", parts: [{ functionCall: { id: tid, name: tname, args: args } }] } }], modelVersion: model });
            }
          } else if (convert) { suppress(); }
        } else if (t === "message_delta") {
          var pin = parseInt(context.getVariable("flow.claude_input_tokens") || context.getVariable("flow.promptTokenCount") || "0", 10) || 0;
          var pout = (ev.usage && ev.usage.output_tokens) || 0;
          var tot = pin + pout;
          context.setVariable("flow.promptTokenCount", String(pin));
          context.setVariable("flow.candidatesTokenCount", String(pout));
          context.setVariable("flow.totalTokenCount", String(tot));
          context.setVariable("flow.stream_done", "true");
          if (convert) {
            var sr = (ev.delta && ev.delta.stop_reason) || "end_turn";
            var fin = sr === "max_tokens" ? "MAX_TOKENS" : (sr === "refusal" ? "SAFETY" : "STOP");
            emitGemini({
              candidates: [{ content: { role: "model", parts: [{ text: "" }] }, finishReason: fin }],
              usageMetadata: { promptTokenCount: pin, candidatesTokenCount: pout, totalTokenCount: tot },
              modelVersion: model
            });
          }
        } else if (convert) {
          suppress();
        }
      }
    } catch (e) {
      context.setVariable("flow.claude_stream_error", String(e));
    }
    """)

JS_ROUTER_REQ = textwrap.dedent("""\
    // Builds the classifier request body for SC-ModelRouter (structured output pins the category).
    var prompt = String(context.getVariable("flow.userPrompt") || "").substring(0, 4000);
    context.setVariable("flow.routerBody", JSON.stringify({
      contents: [{ role: "user", parts: [{ text:
        "Classify the user request into exactly one category: coding (writing/debugging code), " +
        "deep_reasoning (multi-step analysis, math, planning), simple (short factual or chit-chat), " +
        "general (everything else).\\n\\nUser request:\\n" + prompt }] }],
      generationConfig: {
        temperature: 0, maxOutputTokens: 20,
        responseMimeType: "application/json",
        responseSchema: { type: "OBJECT", properties: { category: { type: "STRING",
          "enum": ["coding", "deep_reasoning", "simple", "general"] } }, required: ["category"] }
      }
    }));
    """)

JS_AUTO_ROUTING = textwrap.dedent("""\
    // category (from the classifier) -> model, read from the caller's API product attribute
    // routing.model.<category>. Falls back to routing.model.general, then the defaultModel property.
    // flow.quota_model stays "auto" (set by ExtractPrompt.js) so LLMTokenQuota keys on the /auto operation.
    var category = "general";
    try {
      var r = JSON.parse(context.getVariable("routerResponse.content") || "{}");
      var t = r.candidates[0].content.parts[0].text;
      var c = JSON.parse(t).category;
      if (/^(coding|deep_reasoning|simple|general)$/.test(c)) { category = c; }
    } catch (e) { category = "general"; }
    var prefix = "verifyapikey.VA-VerifyAPIKey.apiproduct.routing.model.";
    var model = context.getVariable(prefix + category) || context.getVariable(prefix + "general")
      || properties.defaultModel;
    context.setVariable("flow.router_category", category);
    context.setVariable("flow.model", model);
    context.setVariable("flow.target_provider", /^claude/.test(model) ? "anthropic" : "google");
    """)

if __name__ == "__main__":
    main()
