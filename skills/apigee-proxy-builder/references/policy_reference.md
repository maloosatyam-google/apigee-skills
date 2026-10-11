# Apigee Policy Reference Guide (cheat sheet)

For details see the topic references. The newer AI and MCP policies are covered in sections 5-12 below and in [policies_ai_gateway.md](policies_ai_gateway.md) / [policies_mcp_tools.md](policies_mcp_tools.md).

## 1. VerifyAPIKey (`VAK-`)
```xml
<VerifyAPIKey async="false" continueOnError="false" enabled="true" name="VAK-VerifyApiKey">
    <DisplayName>VAK-VerifyApiKey</DisplayName>
    <APIKey ref="request.header.x-apikey"/>
</VerifyAPIKey>
```

## 2. SpikeArrest (`SA-`)
```xml
<SpikeArrest async="false" continueOnError="false" enabled="true" name="SA-SpikeArrest">
    <DisplayName>SA-SpikeArrest</DisplayName>
    <Rate>100ps</Rate>
    <UseEffectiveParamValues>true</UseEffectiveParamValues>
</SpikeArrest>
```

## 3. Quota with Dynamic Token Weighting (`Q-`)
```xml
<Quota async="false" continueOnError="false" enabled="true" name="Q-TokenQuota" type="calendar">
    <DisplayName>Q-TokenQuota</DisplayName>
    <Identifier ref="verifyapikey.VAK-VerifyApiKey.client_id"/>
    <Allow count="1000000"/>
    <Interval>1</Interval>
    <TimeUnit>month</TimeUnit>
    <Distributed>true</Distributed>
    <Synchronous>true</Synchronous>
    <Weight ref="totalTokens"/>
</Quota>
```

## 4. ExtractVariables (`EV-`)
```xml
<ExtractVariables async="false" continueOnError="true" enabled="true" name="EV-ExtractTokenUsage">
    <Source>response</Source>
    <JSONPayload>
        <Variable name="totalTokens">
            <JSONPath>$.usageMetadata.totalTokenCount</JSONPath>
        </Variable>
    </JSONPayload>
</ExtractVariables>
```

> `Quota` with `<Weight>` still works for request-count or cost budgets. For **LLM tokens**, use
> `LLMTokenQuota` (section 6). It reads usage from the response and resolves per-model limits from the API product.

## 5. SanitizeUserPrompt (`SUP-`), Model Armor on the prompt
```xml
<SanitizeUserPrompt continueOnError="false" enabled="true" name="SUP-UserPrompt">
    <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
    <ModelArmor><TemplateName>projects/{PROJECT}/locations/{REGION}/templates/{TEMPLATE}</TemplateName></ModelArmor>
    <UserPromptSource>{jsonPath('$.contents[-1].parts[-1].text',request.content,true)}</UserPromptSource>
</SanitizeUserPrompt>
```

## 6. LLMTokenQuota (`LTQ-`): EnforceOnly in the request + CountOnly in the response, same SharedName/Identifier/model
```xml
<LLMTokenQuota continueOnError="false" enabled="true" name="LTQ-TokenEnforce" type="rollingwindow">
    <Allow count="1000" countRef="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.limit"/>
    <Interval ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.interval">1</Interval>
    <TimeUnit ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.timeunit">minute</TimeUnit>
    <Identifier ref="verifyapikey.VA-VerifyAPIKey.client_id"/>
    <LLMModelSource>{flow.model}</LLMModelSource>
    <EnforceOnly>true</EnforceOnly>
    <SharedName>common-counter</SharedName>
</LLMTokenQuota>
<!-- LTQ-TokenCount: same, but <CountOnly>true</CountOnly> and
     <LLMTokenUsageSource>{jsonPath('$.usageMetadata.totalTokenCount',response.content,true)}</LLMTokenUsageSource> -->
```

## 7. PromptTokenLimit (`PTL-`), spike arrest on prompt tokens
```xml
<PromptTokenLimit continueOnError="false" enabled="true" name="PTL-PromptTokenLimit">
    <UserPromptSource>{jsonPath('$.contents[-1].parts[-1].text',request.content,true)}</UserPromptSource>
    <Identifier ref="verifyapikey.VA-VerifyAPIKey.client_id"/>
    <Rate>5000pm</Rate>
    <UseEffectiveCount>true</UseEffectiveCount>
</PromptTokenLimit>
```

## 8. SemanticCacheLookup (`SCL-`) / SemanticCachePopulate (`SCP-`)
```xml
<SemanticCacheLookup continueOnError="true" enabled="true" name="SCL-Lookup">
    <UserPromptSource>{jsonPath('$.contents[-1].parts[-1].text',request.content,true)}</UserPromptSource>
    <Embeddings><VertexAI><URL>https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/publishers/google/models/text-embedding-005:predict</URL></VertexAI></Embeddings>
    <SimilaritySearch><VertexAI>
        <URL>https://{PUBLIC_DOMAIN}/v1/projects/{PROJECT}/locations/{REGION}/indexEndpoints/{ENDPOINT_ID}:findNeighbors</URL>
        <DeployedIndexID>{DEPLOYED_INDEX_ID}</DeployedIndexID><Threshold>0.95</Threshold>
    </VertexAI></SimilaritySearch>
</SemanticCacheLookup>
<SemanticCachePopulate continueOnError="true" enabled="true" name="SCP-Populate">
    <SimilaritySearch><VertexAI><URL>https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/indexes/{INDEX_ID}:upsertDatapoints</URL></VertexAI></SimilaritySearch>
    <TTLInSeconds>180</TTLInSeconds>
</SemanticCachePopulate>
```

## 9. SanitizeModelResponse (`SMR-`)
```xml
<SanitizeModelResponse continueOnError="true" enabled="true" name="SMR-SanitizeModelResponse">
    <ModelArmor><TemplateName>projects/{PROJECT}/locations/{REGION}/templates/{TEMPLATE}</TemplateName></ModelArmor>
    <LLMResponseSource>{jsonPath('$.candidates[-1].content.parts',response.content,true)}</LLMResponseSource>
</SanitizeModelResponse>
```

## 10. MonetizationLimitsCheck (`MLC-`), after VerifyAPIKey
```xml
<MonetizationLimitsCheck continueOnError="false" enabled="true" name="MLC-EnforceMonetizationLimits">
    <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
    <FaultResponse><Set>
        <Payload contentType="application/json">{"error":{"code":403,"message":"{mint.limitscheck.status_message}"}}</Payload>
        <StatusCode>403</StatusCode>
    </Set></FaultResponse>
</MonetizationLimitsCheck>
```

## 11. ParsePayload (`PP-`), MCP / JSON-RPC 2.0
```xml
<ParsePayload continueOnError="false" enabled="true" name="PP-MCP">
    <Source>request</Source>
    <PayloadType>JSON-RPC-2.0</PayloadType>
    <Protocol>MCP</Protocol>
</ParsePayload>
<!-- parsepayload.PP-MCP.operation = tools/call/<tool> | tools/list ; .json-rpc.request.method / .id / .params.name / .params.arguments.<arg> -->
```

## 12. DataCapture (`DC-`), analytics + monetization charge
```xml
<DataCapture continueOnError="true" enabled="true" name="DC-ModelAnalytics">
    <Capture><Collect ref="flow.target_model" default=""/><DataCollector>dc_model_name</DataCollector></Capture>
    <Capture><Collect ref="perUnitPriceMultiplier" default="1.0"/><DataCollector scope="monetization">perUnitPriceMultiplier</DataCollector></Capture>
</DataCapture>
<!-- default is a LITERAL string: never default="{var}" -->
```
