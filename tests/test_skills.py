#!/usr/bin/env python3
"""Offline test suite for the Apigee skills repo (stdlib only; `node` optional for JS tests).

  python3 tests/test_skills.py            # or: make test
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SK = os.path.join(ROOT, "skills")
VALIDATE = os.path.join(SK, "apigee-proxy-builder", "scripts", "validate_bundle.py")
API = os.path.join(SK, "apigee-proxy-builder", "scripts", "scaffold_api_proxy.py")
SF = os.path.join(SK, "apigee-proxy-builder", "scripts", "scaffold_shared_flow.py")
AI = os.path.join(SK, "apigee-ai-gateway", "scripts", "scaffold_ai_gateway.py")
MCP = os.path.join(SK, "apigee-mcp-gateway", "scripts", "scaffold_mcp_proxy.py")
CTL = os.path.join(SK, "apigee-proxy-lifecycle", "scripts", "apigee_ctl.py")
EDIT = os.path.join(SK, "apigee-proxy-lifecycle", "scripts", "bundle_edit.py")
DEPLOY = os.path.join(SK, "apigee-proxy-builder", "scripts", "deploy_bundle.sh")
PROVISION = os.path.join(SK, "apigee-x-provisioning", "scripts", "provision.py")
TF_MODULE = os.path.join(SK, "apigee-x-provisioning", "terraform")
SPEC = os.path.join(ROOT, "examples", "specs", "orders-v1.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run(*args, ok=True, env=None, stdin=None):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, env=env, input=stdin)
    if ok and p.returncode != 0:
        raise AssertionError(f"{' '.join(map(str, args))} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    if ok is False and p.returncode == 0:
        raise AssertionError(f"{' '.join(map(str, args))} unexpectedly succeeded:\n{p.stdout}")
    return p


def py(*args, **kw):
    return run(sys.executable, *args, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="apigee-skills-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    def validate(self, d, strict=True):
        args = [VALIDATE, d] + (["--strict"] if strict else [])
        return py(*args)


class SkillLayout(unittest.TestCase):
    def test_every_skill_has_frontmatter_and_valid_links(self):
        import re
        for name in os.listdir(SK):
            md = os.path.join(SK, name, "SKILL.md")
            self.assertTrue(os.path.isfile(md), f"{name} has no SKILL.md")
            txt = open(md).read()
            self.assertTrue(txt.startswith("---\n"), f"{name}: missing frontmatter")
            fm = txt.split("---", 2)[1]
            self.assertIn(f"name: {name}", fm, f"{name}: frontmatter name must match folder")
            self.assertIn("description:", fm)
            for link in re.findall(r"\]\(((?:\.\./|references/|scripts/)[^)#\s]+)", txt):
                self.assertTrue(os.path.exists(os.path.normpath(os.path.join(SK, name, link))),
                                f"{name}/SKILL.md links to missing {link}")
        link_re = re.compile(r"\[[^\]]+\]\(([^)#\s]+)(?:#[^)]+)?\)")
        for dp, _, fns in os.walk(ROOT):
            if "/.git" in dp or "/.terraform" in dp or "/build" in dp:
                continue
            for fn in fns:
                if not fn.endswith(".md"):
                    continue
                p = os.path.join(dp, fn)
                txt = open(p).read()
                for target in link_re.findall(txt):
                    if target.startswith(("http://", "https://", "mailto:", "file://")):
                        self.assertNotIn("docs.apigee.com", target, f"legacy Edge doc URL in {p}: {target}")
                        continue
                    resolved = os.path.normpath(os.path.join(dp, target))
                    self.assertTrue(os.path.exists(resolved), f"{p} links to missing {target}")
        for mf in ("plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json"):
            data = json.load(open(os.path.join(ROOT, mf)))
            self.assertEqual(data["name"], "apigee-skills")
            self.assertIn("description", data)
            self.assertIn("version", data)

    def test_no_demo_coupling(self):
        bad = ["maloosatyam.demo", "altostrat", "apigee-ai-gateway-demo", "ui/server/"]  # repo URL itself is fine
        for d, _, files in os.walk(ROOT):
            if "/.git" in d or "/tests" in d:
                continue
            for f in files:
                if f.endswith((".md", ".py", ".sh", ".json")):
                    txt = open(os.path.join(d, f), errors="ignore").read()
                    for b in bad:
                        self.assertNotIn(b, txt, f"{os.path.join(d, f)} references demo-specific '{b}'")


class Scaffolds(Base):
    def test_api_proxy_full(self):
        out = self.p("orders")
        py(API, "--out", out, "--name", "orders-v1", "--basepath", "/orders/v1", "--target",
           "https://mocktarget.apigee.net", "--auth", "apikey", "--quota", "--cors", "*", "--oas", SPEC,
           "--cache-ttl", "60", "--health", "--cloud-logging")
        self.validate(out)
        prod = json.load(open(os.path.join(out, "api_product.json")))
        resources = {o["resource"] for o in prod["operationGroup"]["operationConfigs"][0]["operations"]}
        self.assertIn("/orders/*", resources)
        xml = open(os.path.join(out, "apiproxy/proxies/default.xml")).read()
        self.assertLess(xml.index('name="Health"'), xml.index('name="NotFound"'))

    def test_api_proxy_variants(self):
        py(API, "--out", self.p("a"), "--name", "a", "--basepath", "/a", "--target-server", "ts1,ts2",
           "--auth", "oauth", "--token-endpoint", "--quota")
        self.validate(self.p("a"))
        py(API, "--out", self.p("b"), "--name", "b", "--basepath", "/b", "--target", "https://b-x.a.run.app",
           "--target-auth", "idtoken", "--auth", "jwt", "--jwks-url", "https://example.com/jwks",
           "--issuer", "https://issuer", "--jwt-quota", "60")
        self.validate(self.p("b"))
        py(API, "--out", self.p("c"), "--name", "c", "--basepath", "/c", "--target", "https://c", "--auth", "none")
        r = py(VALIDATE, self.p("c"))
        self.assertIn("no authentication policy", r.stdout)

    def test_shared_flow(self):
        py(SF, "--out", self.p("sf"), "--name", "sf-sec", "--features", "apikey,spikearrest,threat,strip-auth")
        self.validate(self.p("sf"))

    def test_ai_gateway_all_features(self):
        out = self.p("ai")
        py(AI, "--out", out, "--project", "p", "--token-quota", "--prompt-token-limit", "5000pm",
           "--armor-region", "us-central1", "--cache-region", "us-central1", "--index-endpoint-host", "h",
           "--index-endpoint-id", "e", "--index-id", "i", "--claude", "--openai", "--analytics", "--auto-route")
        self.validate(out)
        xml = open(os.path.join(out, "apiproxy/proxies/default.xml")).read()
        self.assertLess(xml.index('name="HealthProbe"'), xml.index('name="AutoRoutingFlow"'))
        self.assertLess(xml.index('name="ListModels"'), xml.index('name="AutoRoutingFlow"'))
        self.assertLess(xml.index('name="AutoRoutingFlow"'), xml.index('name="LLMTokenQuotaFlow"'))
        prod = json.load(open(os.path.join(out, "api_product.json")))
        models = [c["llmOperations"][0]["model"] for c in prod["llmOperationGroup"]["operationConfigs"]]
        self.assertIn("auto", models)
        self.assertTrue(all(len(c["llmOperations"]) == 1 for c in prod["llmOperationGroup"]["operationConfigs"]))
        self.assertEqual(prod["operationGroup"]["operationConfigs"][0]["apiSource"], "ai-gateway-v1")
        self.assertIn("GET", prod["operationGroup"]["operationConfigs"][0]["operations"][0]["methods"])
        self.assertTrue(any(a["name"] == "routing.model.coding" for a in prod["attributes"]))
        # Verify SSE targets, RouteRule ordering, EventFlow policies and streaming guards
        self.assertLess(xml.index('name="loopback"'), xml.index('name="openai-stream-target"'))
        self.assertLess(xml.index('name="openai-stream-target"'), xml.index('name="openai-target"'))
        self.assertLess(xml.index('name="openai-target"'), xml.index('name="claude-stream-target"'))
        self.assertLess(xml.index('name="claude-stream-target"'), xml.index('name="claude-target"'))
        self.assertLess(xml.index('name="claude-target"'), xml.index('name="gemini-stream-target"'))
        self.assertLess(xml.index('name="gemini-stream-target"'), xml.index('name="gemini-target"'))
        self.assertIn('flow.is_streaming != "true"', xml)
        self.assertIn("DC-FaultAnalytics", xml)
        ev_key = open(os.path.join(out, "apiproxy/policies/EV-ExtractAPIKey.xml")).read()
        self.assertLess(ev_key.index('name="Authorization"'), ev_key.index('name="x-apikey"'))
        gem_st = open(os.path.join(out, "apiproxy/targets/gemini-vertex-stream-target.xml")).read()
        self.assertIn('<EventFlow name="EventFlow" content-type="text/event-stream">', gem_st)
        self.assertIn('<Property name="response.streaming.enabled">true</Property>', gem_st)
        self.assertLess(gem_st.index("JS-StreamMetrics"), gem_st.index("SMR-SanitizeModelResponseSSE"))
        self.assertLess(gem_st.index("SMR-SanitizeModelResponseSSE"), gem_st.index("LTQ-TokenCountSSE"))
        self.assertLess(gem_st.index("LTQ-TokenCountSSE"), gem_st.index("DC-ModelAnalytics"))
        claude_st = open(os.path.join(out, "apiproxy/targets/claude-vertex-stream-target.xml")).read()
        self.assertIn("JS-FormatClaudeStream", claude_st)
        self.assertLess(claude_st.index("SMR-SanitizeModelResponseSSE"), claude_st.index("LTQ-TokenCountSSE"))
        oai_st = open(os.path.join(out, "apiproxy/targets/openai-vertex-stream-target.xml")).read()
        self.assertIn("JS-NormalizeOpenAIRequest", oai_st)
        self.assertIn("DC-ModelAnalytics", oai_st)

    def test_ai_gateway_minimal(self):
        py(AI, "--out", self.p("ai"), "--project", "p")
        self.validate(self.p("ai"))
        self.assertTrue(os.path.isfile(self.p("ai", "apiproxy/targets/gemini-vertex-stream-target.xml")))

    def test_ai_gateway_no_sse_and_regional(self):
        out = self.p("ai_nosse")
        py(AI, "--out", out, "--project", "p", "--model-location", "us-central1", "--claude", "--token-quota", "--no-sse")
        self.validate(out)
        self.assertFalse(os.path.exists(os.path.join(out, "apiproxy/targets/gemini-vertex-stream-target.xml")))
        self.assertFalse(os.path.exists(os.path.join(out, "apiproxy/targets/claude-vertex-stream-target.xml")))
        self.assertTrue(os.path.isfile(os.path.join(out, "apiproxy/policies/RF-StreamingNotSupported.xml")))
        route = open(os.path.join(out, "apiproxy/policies/AM-RouteGeminiTarget.xml")).read()
        self.assertIn("https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/", route)

    def test_mcp(self):
        out = self.p("mcp")
        py(MCP, "--out", out, "--name", "orders-mcp", "--basepath", "/orders/mcp", "--target",
           "https://orders-mcp-x.a.run.app/mcp", "--tools", "searchOrders,getOrder,issueRefund",
           "--persona", "Support:searchOrders,getOrder,issueRefund", "--persona", "Insights:searchOrders",
           "--limit", "issueRefund:amount:50:Support:REFUND_LIMIT:Refunds over $50 need approval.")
        self.validate(out)
        self.assertEqual(len(os.listdir(os.path.join(out, "products"))), 2)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class AiJavaScript(Base):
    HARNESS = r"""
const fs=require('fs'), assert=require('assert');
function run(f, vars, props){ const c={v:Object.assign({},vars),getVariable(k){return this.v[k]},setVariable(k,x){this.v[k]=x}};
  new Function('context','properties',fs.readFileSync(f,'utf8'))(c,props||{}); return c.v; }
const J=process.argv[2];
let v=run(J+'/ClaudeRequestPrep.js',{"request.content":JSON.stringify({systemInstruction:{parts:[{text:"S"}]},
  contents:[{role:"user",parts:[{text:"hi"}]},{role:"model",parts:[{functionCall:{name:"w",args:{c:1}}}]},
  {role:"user",parts:[{functionResponse:{name:"w",response:{t:3}}}]}],generationConfig:{maxOutputTokens:300}})});
let r=JSON.parse(v["request.content"]); assert.strictEqual(r.system,"S"); assert.strictEqual(r.messages.length,3);
assert.strictEqual(r.messages[2].content[0].tool_use_id,"toolu_1"); assert.strictEqual(r.anthropic_version,"vertex-2023-10-16");
v=run(J+'/FormatClaudeResponse.js',{"response.content":JSON.stringify({content:[{type:"text",text:"A"}],stop_reason:"end_turn",usage:{input_tokens:4,output_tokens:6}})});
r=JSON.parse(v["response.content"]); assert.strictEqual(r.usageMetadata.totalTokenCount,10); assert.strictEqual(r.candidates[0].content.parts[0].text,"A");
v=run(J+'/AutoRouting.js',{"routerResponse.content":JSON.stringify({candidates:[{content:{parts:[{text:'{"category":"coding"}'}]}}]}),
  "verifyapikey.VA-VerifyAPIKey.apiproduct.routing.model.coding":"claude-opus-5-5"},{defaultModel:"g"});
assert.strictEqual(v["flow.model"],"claude-opus-5-5"); assert.strictEqual(v["flow.target_provider"],"anthropic");
v=run(J+'/AutoRouting.js',{},{defaultModel:"g"}); assert.strictEqual(v["flow.model"],"g"); assert.strictEqual(v["flow.router_category"],"general");
v=run(J+'/ExtractPrompt.js',{"flow.model":"auto","request.content":JSON.stringify({contents:[{parts:[{text:"q"}]}]})});
assert.strictEqual(v["flow.quota_model"],"auto"); assert.strictEqual(v["flow.userPrompt"],"q"); assert.strictEqual(v["flow.is_streaming"],"false");
v=run(J+'/ExtractPrompt.js',{"flow.model":"gemini-3.6-flash","proxy.pathsuffix":"/models/gemini-3.6-flash:streamGenerateContent","request.content":JSON.stringify({contents:[{parts:[{text:"s"}]}]})});
assert.strictEqual(v["flow.is_streaming"],"true"); assert.strictEqual(v["flow.userPrompt"],"s");
v=run(J+'/ExtractPrompt.js',{"request.content":JSON.stringify({messages:[{role:"user",content:"m"}],stream:true})});
assert.strictEqual(v["flow.is_streaming"],"true"); assert.strictEqual(v["flow.userPrompt"],"m");
v=run(J+'/ClaudeRequestPrep.js',{"flow.is_streaming":"true","request.content":JSON.stringify({contents:[{role:"user",parts:[{text:"hi"}]}]})});
assert.strictEqual(JSON.parse(v["request.content"]).stream,true);
v=run(J+'/StreamMetrics.js',{"response.event.current.data":JSON.stringify({modelVersion:"gemini-3.6-flash",usageMetadata:{promptTokenCount:5,candidatesTokenCount:7,totalTokenCount:12}})});
assert.strictEqual(v["flow.totalTokenCount"],"12"); assert.strictEqual(v["flow.promptTokenCount"],"5");
// Full Claude SSE -> Gemini SSE stream lifecycle (text + tool_use + final usage)
let ctx={"flow.convert_claude_to_gemini_resp":"true","flow.model":"claude-haiku-5-5"};
ctx=run(J+'/FormatClaudeStream.js',Object.assign(ctx,{"response.event.current.data":JSON.stringify({type:"message_start",message:{model:"claude-haiku-5-5",usage:{input_tokens:12}}})}));
assert.strictEqual(ctx["response.event.current.content"],""); assert.strictEqual(ctx["flow.claude_input_tokens"],"12");
ctx=run(J+'/FormatClaudeStream.js',Object.assign(ctx,{"response.event.current.data":JSON.stringify({type:"content_block_delta",delta:{type:"text_delta",text:"Hello"}})}));
assert.strictEqual(JSON.parse(ctx["response.event.current.data"]).candidates[0].content.parts[0].text,"Hello");
assert.ok(ctx["response.event.current.content"].indexOf("data: ")===0);
ctx=run(J+'/FormatClaudeStream.js',Object.assign(ctx,{"response.event.current.data":JSON.stringify({type:"content_block_start",content_block:{type:"tool_use",id:"toolu_9",name:"lookup"}})}));
ctx=run(J+'/FormatClaudeStream.js',Object.assign(ctx,{"response.event.current.data":JSON.stringify({type:"content_block_delta",delta:{type:"input_json_delta",partial_json:'{"id":'}})}));
ctx=run(J+'/FormatClaudeStream.js',Object.assign(ctx,{"response.event.current.data":JSON.stringify({type:"content_block_delta",delta:{type:"input_json_delta",partial_json:'42}'}})}));
ctx=run(J+'/FormatClaudeStream.js',Object.assign(ctx,{"response.event.current.data":JSON.stringify({type:"content_block_stop"})}));
assert.strictEqual(JSON.parse(ctx["response.event.current.data"]).candidates[0].content.parts[0].functionCall.args.id,42);
ctx=run(J+'/FormatClaudeStream.js',Object.assign(ctx,{"response.event.current.data":JSON.stringify({type:"message_delta",delta:{stop_reason:"end_turn"},usage:{output_tokens:8}})}));
let fin=JSON.parse(ctx["response.event.current.data"]);
assert.strictEqual(fin.candidates[0].finishReason,"STOP"); assert.strictEqual(fin.usageMetadata.totalTokenCount,20); assert.strictEqual(ctx["flow.totalTokenCount"],"20");
// Native Anthropic SSE pass-through mode preserves raw event data while capturing tokens and resets stream_done on message_stop
let nat=run(J+'/FormatClaudeStream.js',{"flow.claude_input_tokens":"3","response.event.current.data":'{"type":"message_delta","usage":{"output_tokens":4}}'});
assert.strictEqual(nat["response.event.current.data"],'{"type":"message_delta","usage":{"output_tokens":4}}'); assert.strictEqual(nat["flow.totalTokenCount"],"7"); assert.strictEqual(nat["flow.stream_done"],"true");
nat=run(J+'/FormatClaudeStream.js',Object.assign(nat,{"response.event.current.data":'{"type":"message_stop"}'}));
assert.strictEqual(nat["flow.stream_done"],"false");
// Antigravity /v1internal envelope unwrapping + @YYYYMMDD stripping on quota_model
let agy=run(J+'/ExtractPrompt.js',{"request.content":JSON.stringify({model:"gemini-3.6-flash@20260501",request:{contents:[{parts:[{thought:true,text:"think"},{text:"ask"}]}],session_id:"sess-1"}})});
assert.strictEqual(agy["flow.userPrompt"],"ask"); assert.strictEqual(agy["flow.quota_model"],"gemini-3.6-flash"); assert.strictEqual(JSON.parse(agy["request.content"]).session_id,undefined);
// Claude Code 1P anthropic-beta header filtering, -YYYYMMDD -> @YYYYMMDD mapping & count_tokens handling
let cc=run(J+'/ClaudeRequestPrep.js',{"flow.model":"claude-sonnet-4-5-20250929","request.header.anthropic-beta":"claude-code-20250219, advisor-tool-2026-03-01, thinking-token-count-2026-05-13","request.content":JSON.stringify({messages:[{role:"user",content:"hi"}]})});
assert.strictEqual(cc["flow.target_model"],"claude-sonnet-4-5@20250929"); assert.strictEqual(cc["request.header.anthropic-beta"],"claude-code-20250219,thinking-token-count-2026-05-13");
let hk=run(J+'/ClaudeRequestPrep.js',{"flow.model":"claude-haiku-5-5","proxy.pathsuffix":"/v1/messages/count_tokens","request.header.anthropic-beta":"thinking-token-count-2026-05-13,inline-tools-2026-09-15","request.content":JSON.stringify({model:"claude-haiku-5-5",messages:[{role:"user",content:"hi"}]})});
assert.strictEqual(hk["flow.claude_action"],"countTokens"); assert.strictEqual(hk["request.header.anthropic-beta"],""); assert.strictEqual(JSON.parse(hk["request.content"]).anthropic_version,undefined);
// OpenAI normalization + SSE finishReason / [DONE] non-duplication
let oai=run(J+'/NormalizeOpenAIRequest.js',{"flow.is_streaming":"true","request.content":JSON.stringify({model:"gemini-3.6-flash",messages:[{role:"user",content:"hi"}]})},{defaultModel:"gemini-3.6-flash"});
let oaiBody=JSON.parse(oai["request.content"]); assert.strictEqual(oaiBody.model,"google/gemini-3.6-flash"); assert.strictEqual(oaiBody.stream_options.include_usage,true);
let sm=run(J+'/StreamMetrics.js',{"response.event.current.data":JSON.stringify({candidates:[{content:{parts:[{text:"Hi"}]}}],usageMetadata:{promptTokenCount:5,candidatesTokenCount:2,totalTokenCount:7}})});
assert.strictEqual(sm["flow.stream_done"],"false"); assert.strictEqual(sm["flow.responseText"],"Hi");
sm=run(J+'/StreamMetrics.js',Object.assign(sm,{"response.event.current.data":JSON.stringify({candidates:[{content:{parts:[{text:"!"}]},finishReason:"STOP"}],usageMetadata:{promptTokenCount:5,candidatesTokenCount:3,thoughtsTokenCount:4,totalTokenCount:12}})}));
assert.strictEqual(sm["flow.stream_done"],"true"); assert.strictEqual(sm["flow.thoughtsTokenCount"],"4"); assert.strictEqual(sm["flow.responseText"],"Hi!");
sm=run(J+'/StreamMetrics.js',Object.assign(sm,{"response.event.current.data":"data: [DONE]"}));
assert.strictEqual(sm["flow.stream_done"],"false");
JSON.parse(run(J+'/PrepRouterRequest.js',{"flow.userPrompt":"x"})["flow.routerBody"]);
console.log("ok");
"""

    def test_generated_js(self):
        out = self.p("ai")
        py(AI, "--out", out, "--project", "p", "--claude", "--openai", "--auto-route", "--token-quota")
        h = self.p("h.js")
        open(h, "w").write(self.HARNESS)
        run("node", h, os.path.join(out, "apiproxy/resources/jsc"))


class ValidatorCatchesBugs(Base):
    def _ai(self):
        out = self.p("ai")
        py(AI, "--out", out, "--project", "p", "--token-quota")
        return out

    def _pol(self, out, name):
        return os.path.join(out, "apiproxy", "policies", name + ".xml")

    @staticmethod
    def _sub(path, old, new):
        txt = open(path).read()
        assert old in txt, f"{old!r} not in {path}"
        with open(path, "w") as f:
            f.write(txt.replace(old, new))

    def test_ltq_pair_mismatch(self):
        out = self._ai()
        f = self._pol(out, "LTQ-TokenCount")
        self._sub(f, "verifyapikey.VA-VerifyAPIKey.client_id", "flow.emailId")
        r = py(VALIDATE, out, ok=False)
        self.assertIn("members disagree", r.stdout)

    def test_missing_policy_and_resource(self):
        out = self._ai()
        os.remove(self._pol(out, "EV-ModelResponse"))
        os.remove(os.path.join(out, "apiproxy/resources/jsc/ExtractPrompt.js"))
        r = py(VALIDATE, out, ok=False)
        self.assertIn("missing policy 'EV-ModelResponse'", r.stdout)
        self.assertIn("ExtractPrompt.js", r.stdout)

    def test_swallowed_enforcer(self):
        out = self._ai()
        f = self._pol(out, "LTQ-TokenEnforce")
        self._sub(f, 'continueOnError="false"', 'continueOnError="true"')
        r = py(VALIDATE, out, ok=False)
        self.assertIn("never enforced", r.stdout)

    def test_hardcoded_secret(self):
        out = self._ai()
        f = self._pol(out, "AM-RemoveAuthorization")
        self._sub(f, "<Remove>", '<Set><Headers><Header name="x-key">AIza' + "A" * 35 + "</Header></Headers></Set><Remove>")
        r = py(VALIDATE, out, ok=False)
        self.assertIn("hardcoded Google API key", r.stdout)

    def test_semantic_cache_distance_measure(self):
        out = self.p("ai_sc")
        py(AI, "--out", out, "--project", "p", "--cache-region", "us-central1",
           "--index-endpoint-host", "idx.vdb.vertexai.goog", "--index-endpoint-id", "1",
           "--deployed-index-id", "d", "--index-id", "2")
        f = self._pol(out, "SCL-Semantic-Cache-Lookup")
        self._sub(f, "<Threshold>0.95</Threshold>",
                  "<Threshold>0.15</Threshold><DistanceMeasureType>COSINE_DISTANCE</DistanceMeasureType>")
        self.validate(out)
        self._sub(f, "COSINE_DISTANCE", "MANHATTAN_DISTANCE")
        r = py(VALIDATE, out, ok=False)
        self.assertIn("invalid <DistanceMeasureType>", r.stdout)

    def test_sse_eventflow_violations(self):
        out = self.p("ai_sse")
        py(AI, "--out", out, "--project", "p", "--token-quota", "--armor-region", "us-central1")
        st = os.path.join(out, "apiproxy/targets/gemini-vertex-stream-target.xml")
        # 1. Wrong content-type, missing response.streaming.enabled, and disallowed AssignMessage in EventFlow
        self._sub(st, 'content-type="text/event-stream"', 'content-type="application/json"')
        self._sub(st, '<Property name="response.streaming.enabled">true</Property>', "")
        self._sub(st, "<Step>\n        <Name>JS-StreamMetrics</Name>",
                  "<Step><Name>AM-RemoveAuthorization</Name></Step>\n      <Step>\n        <Name>JS-StreamMetrics</Name>")
        # 2. LTQ in EventFlow with want-array=true + SMR reading response.content + SUP with ContinuousScan
        self._sub(self._pol(out, "LTQ-TokenCountSSE"), "response.event.current.data,false", "response.event.current.data,true")
        self._sub(self._pol(out, "SMR-SanitizeModelResponseSSE"), "response.event.current.data", "response.content")
        self._sub(self._pol(out, "SUP-UserPrompt"), "</SanitizeUserPrompt>",
                  "  <ContinuousScan>true</ContinuousScan>\n</SanitizeUserPrompt>")
        # 3. Policy outside EventFlow referencing response.event.current.data
        self._sub(self._pol(out, "LTQ-TokenCount"), "response.content", "response.event.current.data")
        r = py(VALIDATE, out, "--strict", ok=False)
        self.assertIn('content-type="text/event-stream"', r.stdout)
        self.assertIn("response.streaming.enabled", r.stdout)
        self.assertIn("not allowed in <EventFlow>", r.stdout)
        self.assertIn("pass false for want-array", r.stdout)
        self.assertIn("reading response.event.current.data", r.stdout)
        self.assertIn("supported only on SanitizeModelResponse", r.stdout)
        self.assertIn("outside <EventFlow>", r.stdout)

    def test_ide_gateway_and_product_bugs(self):
        out = self.p("ai_ide")
        py(AI, "--out", out, "--project", "p", "--token-quota", "--analytics")
        # 1. Escaped double quotes inside <Condition> JavaRegex (causes expressions.parser.RegexRequired on Apigee X)
        st = os.path.join(out, "apiproxy/targets/gemini-vertex-stream-target.xml")
        self._sub(st, 'flow.stream_done = "true"',
                  'response.event.current.data JavaRegex "(?s).*(\\"finishReason\\").*"')
        # 2. EventFlow JS setting flow.stream_done="true" without resetting flow.stream_done="false"
        js = os.path.join(out, "apiproxy/resources/jsc/StreamMetrics.js")
        self._sub(js, 'context.setVariable("flow.stream_done", "false");\n', "")
        # 3. Unguarded DataCapture in ProxyEndpoint PostFlow/Response when EventFlow is used
        px = os.path.join(out, "apiproxy/proxies/default.xml")
        self._sub(px, '<Name>DC-ModelAnalytics</Name>\n        <Condition>request.verb = "POST" and response.status.code = 200 and flow.is_streaming != "true"</Condition>',
                  '<Name>DC-ModelAnalytics</Name>\n        <Condition>request.verb = "POST" and response.status.code = 200</Condition>')
        # 4. API product with >1 entity in llmOperations AND mismatched operationGroup vs llmOperationGroup apiSource
        prod_path = os.path.join(out, "api_product.json")
        prod = json.load(open(prod_path))
        prod["llmOperationGroup"]["operationConfigs"][0]["llmOperations"].append(
            {"resource": "/**", "methods": ["POST"], "model": "gemini-3.5-pro"}
        )
        prod["operationGroup"]["operationConfigs"].append(
            {"apiSource": "other-proxy-without-llm", "operations": [{"resource": "/**", "methods": ["POST"]}]}
        )
        json.dump(prod, open(prod_path, "w"))
        r = py(VALIDATE, out, "--strict", ok=False)
        self.assertIn("expressions.parser.RegexRequired", r.stdout)
        self.assertIn("without resetting flow.stream_done='false'", r.stdout)
        self.assertIn("DataCapture 'DC-ModelAnalytics' in PostFlow/Response lacks a streaming guard", r.stdout)
        self.assertIn("Operations must contain exactly one entity", r.stdout)
        self.assertIn("do not match llmOperationGroup apiSources", r.stdout)


class ValidatorBestPractices(Base):
    """Checks derived from the official Apigee proxy design best-practices page."""

    def _api(self, **extra):
        out = self.p("api")
        py(API, "--out", out, "--name", "o", "--basepath", "/o", "--target", "https://t", "--cache-ttl", "30")
        return out

    def test_scaffold_follows_cache_guidance(self):
        self.validate(self._api())

    def test_default_route_rule_must_be_last(self):
        out = self._api()
        f = os.path.join(out, "apiproxy/proxies/default.xml")
        txt = open(f).read()
        default = '  <RouteRule name="default">\n    <TargetEndpoint>default</TargetEndpoint>\n  </RouteRule>\n'
        extra = '  <RouteRule name="other">\n    <Condition>request.verb = "HEAD"</Condition>\n  </RouteRule>\n'
        with open(f, "w") as fh:
            fh.write(txt.replace(default, default + extra))
        r = py(VALIDATE, out, ok=False)
        self.assertIn("must be last", r.stdout)

    def test_cache_population_and_python_and_httpclient_and_callout_url(self):
        out = self._api()
        pol = os.path.join(out, "apiproxy/policies")
        f = os.path.join(pol, "RC-ResponseCache.xml")
        txt = open(f).read().replace(' or request.verb != "GET"', "")
        with open(f, "w") as fh:
            fh.write(txt)
        with open(os.path.join(pol, "SC-Bad.xml"), "w") as fh:
            fh.write('<ServiceCallout name="SC-Bad"><Response>r</Response><HTTPTargetConnection>'
                     '<URL>{flow.url}</URL></HTTPTargetConnection></ServiceCallout>')
        os.makedirs(os.path.join(out, "apiproxy/resources/jsc"))
        with open(os.path.join(out, "apiproxy/resources/jsc/unused.js"), "w") as fh:
            fh.write("httpClient.get('https://x');")
        r = py(VALIDATE, out, ok=False)
        self.assertIn("SkipCachePopulation", r.stdout)
        self.assertIn("URL protocol cannot come from a variable", r.stdout)
        self.assertIn("not referenced by any policy", r.stdout)
        self.assertIn("httpClient", r.stdout)


class SecurityHardening(Base):
    """Checks/options derived from 'Best practices for securing applications and APIs using Apigee'."""

    def test_backend_mtls_and_xml_threat(self):
        out = self.p("s")
        py(API, "--out", out, "--name", "s", "--basepath", "/s", "--target", "https://b.example.com",
           "--xml-threat", "--target-truststore", "ref://backend-ts", "--target-mtls", "ref://client-ks:client")
        self.validate(out)
        t = open(os.path.join(out, "apiproxy/targets/default.xml")).read()
        self.assertIn("<KeyStore>ref://client-ks</KeyStore>", t)
        self.assertIn("<KeyAlias>client</KeyAlias>", t)
        self.assertTrue(os.path.isfile(os.path.join(out, "apiproxy/policies/XTP-XMLThreatProtection.xml")))

    def test_plaintext_and_insecure_tls_flagged(self):
        out = self.p("p")
        py(API, "--out", out, "--name", "p", "--basepath", "/p", "--target", "http://public.example.com")
        f = os.path.join(out, "apiproxy/targets/default.xml")
        txt = open(f).read().replace("</URL>", "</URL>\n    <SSLInfo><Enabled>true</Enabled><ClientAuthEnabled>true</ClientAuthEnabled>"
                                      "<IgnoreValidationErrors>true</IgnoreValidationErrors></SSLInfo>")
        with open(f, "w") as fh:
            fh.write(txt)
        r = py(VALIDATE, out, ok=False)
        self.assertIn("plaintext HTTP", r.stdout)
        self.assertIn("IgnoreValidationErrors", r.stdout)
        self.assertIn("needs <KeyStore> and <KeyAlias>", r.stdout)

    def test_internal_http_backend_allowed(self):
        out = self.p("i")
        py(API, "--out", out, "--name", "i", "--basepath", "/i", "--target", "http://10.0.0.5:8080")
        self.validate(out)


class BundleEdit(Base):
    def test_update_existing_proxy_locally(self):
        out = self.p("orders")
        py(API, "--out", out, "--name", "orders-v1", "--basepath", "/orders/v1", "--target", "https://t")
        pol = self.p("SA-Burst.xml")
        open(pol, "w").write('<SpikeArrest name="SA-Burst"><Rate>5ps</Rate></SpikeArrest>')
        py(EDIT, "add-policy", out, pol)
        py(EDIT, "attach", out, "SA-Burst", "--flow", "PreFlow", "--phase", "Request",
           "--after", "VA-VerifyAPIKey", "--condition", 'request.verb = "POST"')
        py(EDIT, "add-flow", out, "Admin", "--condition", 'proxy.pathsuffix MatchesPath "/admin/**"', "--first")
        py(EDIT, "set-basepath", out, "/orders/v2")
        py(EDIT, "set-target-url", out, "https://new-backend")
        self.validate(out)
        o = py(EDIT, "outline", out).stdout
        self.assertIn("SA-Burst", o)
        self.assertIn("basepath=/orders/v2", o)
        self.assertLess(o.index("VA-VerifyAPIKey"), o.index("SA-Burst"))
        py(EDIT, "remove-policy", out, "SA-Burst")
        self.validate(out)
        self.assertNotIn("SA-Burst", open(os.path.join(out, "apiproxy/orders-v1.xml")).read())

    def test_eventflow_attach_outline_detach(self):
        out = self.p("ai_edit")
        py(AI, "--out", out, "--project", "p")
        pol = self.p("ML-EventAudit.xml")
        open(pol, "w").write('<MessageLogging name="ML-EventAudit"><CloudLogging>'
                             '<LogName>projects/{organization.name}/logs/sse</LogName>'
                             '<Message contentType="text/plain">{response.event.current.count}</Message>'
                             '<ResourceType>api</ResourceType></CloudLogging></MessageLogging>')
        py(EDIT, "add-policy", out, pol)
        py(EDIT, "attach", out, "ML-EventAudit", "--endpoint", "targets/gemini-vertex-stream-target.xml",
           "--flow", "EventFlow", "--phase", "Response", "--after", "JS-StreamMetrics")
        self.validate(out)
        o = py(EDIT, "outline", out).stdout
        self.assertIn("EventFlow (content-type=text/event-stream).Response", o)
        self.assertIn("ML-EventAudit", o)
        py(EDIT, "remove-policy", out, "ML-EventAudit")
        self.validate(out)


class Lifecycle(Base):
    def setUp(self):
        super().setUp()
        import mock_apigee
        self.srv, self.state = mock_apigee.start()
        self.env = dict(os.environ, APIGEE_TOKEN="test", APIGEE_ORG="org1",
                        APIGEE_API_BASE=f"http://127.0.0.1:{self.srv.server_port}")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def ctl(self, *a, **kw):
        return py(CTL, *a, env=self.env, **kw)

    def test_create_update_diff_rollback(self):
        out = self.p("orders")
        py(API, "--out", out, "--name", "orders-v1", "--basepath", "/orders/v1", "--target", "https://t")
        self.ctl("update", out, "--env", "dev")
        self.assertEqual(self.state.deployments[("apis", "dev", "orders-v1")], "1")

        # export what is deployed, change it, diff, redeploy
        ex = self.p("export")
        self.ctl("export", "orders-v1", "--env", "dev", "--out", ex)
        py(EDIT, "set-target-url", ex, "https://v2")
        d = self.ctl("diff", ex, "--env", "dev").stdout
        self.assertIn("+    <URL>https://v2</URL>", d)
        self.ctl("update", ex, "--env", "dev")
        self.assertEqual(self.state.deployments[("apis", "dev", "orders-v1")], "2")

        self.ctl("rollback", "orders-v1", "--env", "dev")
        self.assertEqual(self.state.deployments[("apis", "dev", "orders-v1")], "1")
        self.assertIn("orders-v1", self.ctl("list").stdout)

    def test_invalid_bundle_is_not_imported(self):
        out = self.p("bad")
        py(API, "--out", out, "--name", "bad", "--basepath", "/bad", "--target", "https://t")
        os.remove(os.path.join(out, "apiproxy/policies/SA-SpikeArrest.xml"))
        self.ctl("import", out, ok=False)
        self.assertEqual(self.state.bundles["apis"], {})

    def test_shared_flow_and_config(self):
        sf = self.p("sf")
        py(SF, "--out", sf, "--name", "sf-sec", "--features", "apikey,spikearrest")
        self.ctl("update", sf, "--env", "dev")
        self.assertEqual(self.state.deployments[("sharedflows", "dev", "sf-sec")], "1")
        self.ctl("flowhook-attach", "PreProxyFlowHook", "sf-sec", "--env", "dev")

        out = self.p("api")
        py(API, "--out", out, "--name", "o", "--basepath", "/o", "--target", "https://t")
        self.ctl("product-apply", os.path.join(out, "api_product.json"), "--env", "dev")
        self.ctl("product-apply", os.path.join(out, "api_product.json"), "--env", "dev")  # idempotent update
        self.assertEqual(self.state.products["o-standard"]["environments"], ["dev"])
        self.ctl("kvm-put", "creds", "router-key", "-", "--env", "dev", stdin="s3cr3t\n")
        self.assertEqual(self.state.kvms[("dev", "creds")]["router-key"], "s3cr3t")
        mask = self.p("mask.json")
        with open(mask, "w") as fh:
            json.dump({"requestJSONPaths": ["$.password"], "variables": ["request.header.authorization"]}, fh)
        self.ctl("debugmask-apply", mask, "--env", "dev")
        self.assertEqual(self.state.debugmasks["dev"]["requestJSONPaths"], ["$.password"])
        with open(mask, "w") as fh:
            json.dump({"bogus": []}, fh)
        self.ctl("debugmask-apply", mask, "--env", "dev", ok=False)


class Provisioning(Base):
    """apigee-x-provisioning wrapper (fake terraform) and Terraform module tests (real terraform, mocked providers)."""

    def _run(self, *args, ok=True, stdin=None):
        env = dict(os.environ, TERRAFORM=os.path.join(ROOT, "tests", "fake_terraform.py"), FAKE_TF_LOG=self.p("tf.log"))
        return py(PROVISION, *args, "--workdir", self.p("infra"), env=env, ok=ok, stdin=stdin)

    def test_defaults_payg_intermediate_analytics(self):
        self._run("apply", "--project", "my-proj", "--region", "us-central1", "--auto-approve")
        v = json.load(open(self.p("infra", "terraform.tfvars.json")))
        self.assertEqual(v["billing_type"], "PAYG")
        self.assertEqual(v["environments"], {"dev": {}})          # module defaults: INTERMEDIATE + analytics
        self.assertEqual(v["routing"], "external")
        self.assertNotIn("use_cmek", v)                           # GMEK default
        self.assertNotIn("network_mode", v)                       # PSC default
        self.assertNotIn("control_plane_location", v)             # without Data Residency default
        self.assertNotIn("envgroup_hostnames", v)                 # nip.io default
        log = open(self.p("tf.log")).read()
        self.assertIn("init -input=false", log)
        self.assertIn("plan -input=false -out=tfplan", log)
        self.assertIn("apply -input=false tfplan", log)
        self.assertIn("APIGEE_ORG=my-proj", open(self.p("infra", "apigee-x.env")).read())
        self.assertTrue(os.path.isfile(self.p("infra", "main.tf")))
        self.assertTrue(os.path.isfile(self.p("infra", "enterprise.tf")))

    def test_interactive_wizard_defaults_and_export(self):
        # Press Enter on every optional prompt -> accepts PAYG, INTERMEDIATE+Analytics, PSC, GMEK, no DR, external, nip.io, Google cert
        answers = "\n".join([
            "wiz-proj",      # 1. project
            "asia-south1",   # 2. primary region
            "",              #    additional regions (none)
            "",              # 3. billing (default PAYG)
            "",              # 4. environments (default dev:INTERMEDIATE)
            "",              # 5. network_mode (default PSC)
            "",              #    existing VPC (auto-create)
            "",              # 6. encryption (default GMEK)
            "",              # 7. data residency (default n)
            "",              # 8. routing (default external)
            "",              # 9. domain (default nip.io)
            "",              # 10. cert_mode (default google_managed)
            "",              # 11. enterprise options (default n)
            "export",        # action -> export standalone HCL package
        ]) + "\n"
        self._run("wizard", stdin=answers)
        tfvars_hcl = open(self.p("infra", "terraform.tfvars")).read()
        self.assertIn('project_id = "wiz-proj"', tfvars_hcl)
        self.assertIn('region = "asia-south1"', tfvars_hcl)
        self.assertIn('billing_type = "PAYG"', tfvars_hcl)
        self.assertIn('type = "INTERMEDIATE"', tfvars_hcl)
        self.assertTrue(os.path.isfile(self.p("infra", "routing.tf")))

    def test_env_specs_hostnames_and_enterprise_options(self):
        self._run(
            "plan", "--project", "p", "--region", "europe-west1",
            "--additional-region", "europe-west4",
            "--network-mode", "VPC_PEERING",
            "--env", "dev",
            "--env", "test:BASE:noanalytics@nonprod",
            "--env", "prod:COMPREHENSIVE",
            "--hostname", "prod-group=api.example.com",
            "--dns-zone", "example-zone",
            "--cloud-armor", "--http-redirect", "--nat-ips", "--cmek",
            "--addon", "api_security",
            "--southbound-psc", "orders=projects/bp/regions/europe-west1/serviceAttachments/sa",
            "--peered-dns", "corp=corp.internal",
            "--state-bucket", "tf-state",
        )
        v = json.load(open(self.p("infra", "terraform.tfvars.json")))
        self.assertEqual(v["network_mode"], "VPC_PEERING")
        self.assertEqual(v["additional_regions"], {"europe-west4": {}})
        self.assertEqual(v["environments"]["test"], {"type": "BASE", "analytics": False, "envgroup": "nonprod"})
        self.assertEqual(v["environments"]["prod"], {"type": "COMPREHENSIVE"})
        self.assertEqual(v["envgroup_hostnames"], {"prod-group": ["api.example.com"]})
        self.assertEqual(v["dns_managed_zone"], "example-zone")
        self.assertTrue(v["enable_cloud_armor"])
        self.assertTrue(v["enable_http_redirect"])
        self.assertTrue(v["enable_nat_ips"])
        self.assertTrue(v["use_cmek"])
        self.assertEqual(v["addons"], {"api_security": True})
        self.assertEqual(v["peered_dns_zones"], {"corp": {"domain": "corp.internal."}})
        self.assertIn("orders", v["southbound_psc_endpoints"])
        self.assertIn('bucket = "tf-state"', open(self.p("infra", "backend.tf")).read())
        self.assertNotIn("apply", open(self.p("tf.log")).read())

    def test_rejects_bad_input_evaluation_and_destroy(self):
        self._run("plan", "--project", "p", "--region", "r", "--billing", "EVALUATION", ok=False)
        self._run("plan", "--project", "p", "--region", "r", "--env", "dev:HUGE", ok=False)
        self._run("plan", "--project", "p", "--region", "r", "--routing", "internal", ok=False)
        self._run("plan", "--project", "p", "--region", "r", "--routing", "external-regional", ok=False)
        self._run("plan", "--project", "p", "--region", "r", "--control-plane-location", "us", ok=False)
        self._run("plan", "--project", "p", "--region", "r", "--peered-dns", "z=corp.internal", ok=False)
        self._run("destroy", ok=False)

    @unittest.skipUnless(shutil.which(os.environ.get("TERRAFORM", "terraform")), "terraform not installed")
    def test_terraform_module(self):
        tf = shutil.which(os.environ.get("TERRAFORM", "terraform"))
        work = self.p("mod")
        shutil.copytree(TF_MODULE, work)
        run(tf, f"-chdir={work}", "init", "-backend=false", "-input=false")
        run(tf, f"-chdir={work}", "validate")
        run(tf, f"-chdir={work}", "test")


class ConsumerAndConfigManagement(Base):
    """Developers, apps, keys, products, KVMs (all scopes) and target servers via apigee_ctl.py (mock API)."""

    def setUp(self):
        super().setUp()
        import mock_apigee
        self.srv, self.state = mock_apigee.start()
        self.env = dict(os.environ, APIGEE_TOKEN="test", APIGEE_ORG="org1", APIGEE_ENV="dev",
                        APIGEE_API_BASE=f"http://127.0.0.1:{self.srv.server_port}")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def ctl(self, *a, **kw):
        return py(CTL, *a, env=self.env, **kw)

    def test_developer_app_key_lifecycle(self):
        out = self.p("api")
        py(API, "--out", out, "--name", "o", "--basepath", "/o", "--target", "https://t")
        self.ctl("product-apply", os.path.join(out, "api_product.json"))
        self.assertIn("o-standard", self.ctl("product-list").stdout)

        r = self.ctl("app-create", "jane@example.com", "jane-app", "--product", "o-standard",
                     "--create-developer", "--credentials-out", self.p("creds.json"))
        self.assertIn("Created developer jane@example.com", r.stdout)
        self.assertIn("key=KEY0001abcdefgh", r.stdout)
        self.assertNotIn("SECRET0001xyz", r.stdout)                       # secret masked
        self.assertEqual(oct(os.stat(self.p("creds.json")).st_mode & 0o777), "0o600")
        self.assertEqual(json.load(open(self.p("creds.json")))["consumerSecret"], "SECRET0001xyz")

        # idempotent re-run adds a missing product to the existing key instead of creating a second app
        self.state.products["o-premium"] = {"name": "o-premium"}
        r = self.ctl("app-create", "jane@example.com", "jane-app", "--product", "o-standard", "--product", "o-premium")
        self.assertIn("Added o-premium", r.stdout)
        self.assertEqual(len(self.state.apps), 1)
        self.assertIn("o-premium", self.ctl("app-list", "jane@example.com").stdout)
        self.assertIn("SECRET0001xyz", self.ctl("app-get", "jane@example.com", "jane-app", "--show-secret").stdout)

        self.ctl("key-revoke", "jane@example.com", "jane-app", "KEY0001abcdefgh", "--product", "o-premium")
        cred = self.state.apps[("jane@example.com", "jane-app")]["credentials"][0]
        self.assertEqual({p["apiproduct"]: p["status"] for p in cred["apiProducts"]}["o-premium"], "revoked")
        self.ctl("key-revoke", "jane@example.com", "jane-app", "KEY0001abcdefgh")
        self.assertEqual(self.state.apps[("jane@example.com", "jane-app")]["credentials"][0]["status"], "revoked")

        self.assertIn("jane@example.com", self.ctl("developer-list").stdout)
        self.ctl("app-delete", "jane@example.com", "jane-app", ok=False)    # needs --yes
        self.ctl("app-delete", "jane@example.com", "jane-app", "--yes")
        self.ctl("developer-delete", "jane@example.com", "--yes")
        self.ctl("product-delete", "o-standard", "--yes")
        self.assertEqual(self.state.developers, {})
        self.assertNotIn("o-standard", self.state.products)

    def test_kvm_scopes_and_masking(self):
        self.ctl("kvm-put", "cfg", "api-key", "-", stdin="supersecretvalue\n")          # env scope (APIGEE_ENV)
        self.ctl("kvm-put", "global", "flag", "on", "--org-scope")
        self.ctl("kvm-put", "local", "limit", "5", "--proxy", "orders-v1")
        self.assertEqual(self.state.kvms[("dev", "cfg")]["api-key"], "supersecretvalue")
        self.assertIn(("_org", "global"), self.state.kvms)
        self.assertIn(("proxy:orders-v1", "local"), self.state.kvms)
        listing = self.ctl("kvm-entries", "cfg").stdout
        self.assertIn("api-key", listing)
        self.assertNotIn("supersecretvalue", listing)
        self.assertIn("supersecretvalue", self.ctl("kvm-get", "cfg", "api-key", "--show").stdout)
        self.assertIn("cfg", self.ctl("kvm-list").stdout)
        self.ctl("kvm-delete-entry", "cfg", "api-key", ok=False)
        self.ctl("kvm-delete-entry", "cfg", "api-key", "--yes")
        self.ctl("kvm-delete", "global", "--org-scope", "--yes")
        self.assertNotIn(("_org", "global"), self.state.kvms)

    def test_target_servers(self):
        ts = self.p("ts.json")
        with open(ts, "w") as f:
            json.dump({"name": "orders-backend", "host": "orders.internal", "port": 443, "isEnabled": True,
                       "sSLInfo": {"enabled": True}}, f)
        self.ctl("targetserver-apply", ts)
        self.assertIn("orders-backend", self.ctl("targetserver-list").stdout)
        self.assertIn("orders.internal", self.ctl("targetserver-get", "orders-backend").stdout)
        self.ctl("targetserver-delete", "orders-backend", ok=False)
        self.ctl("targetserver-delete", "orders-backend", "--yes")
        self.assertEqual(self.state.targetservers, {})

    def test_product_apply_validates_llm_operations(self):
        bad1 = self.p("bad_multi_ops.json")
        json.dump({
            "name": "bad-multi",
            "environments": ["dev"],
            "llmOperationGroup": {"operationConfigs": [{
                "apiSource": "ai-gw",
                "llmOperations": [
                    {"resource": "/**", "methods": ["POST"], "model": "gemini-3.6-flash"},
                    {"resource": "/**", "methods": ["POST"], "model": "claude-haiku-5-5"},
                ],
            }]},
        }, open(bad1, "w"))
        r1 = self.ctl("product-apply", bad1, ok=False)
        self.assertIn("exactly 1 entity", r1.stderr)

        bad2 = self.p("bad_mismatch.json")
        json.dump({
            "name": "bad-mismatch",
            "environments": ["dev"],
            "operationGroup": {"operationConfigs": [
                {"apiSource": "ai-gw", "operations": [{"resource": "/**", "methods": ["POST"]}]},
                {"apiSource": "extra-gw", "operations": [{"resource": "/**", "methods": ["POST"]}]},
            ]},
            "llmOperationGroup": {"operationConfigs": [{
                "apiSource": "ai-gw",
                "llmOperations": [{"resource": "/**", "methods": ["POST"], "model": "gemini-3.6-flash"}],
            }]},
        }, open(bad2, "w"))
        r2 = self.ctl("product-apply", bad2, ok=False)
        self.assertIn("do not match llmOperationGroup", r2.stderr)


class DeployScript(Base):
    def test_package_only(self):
        out = self.p("o")
        py(API, "--out", out, "--name", "o", "--basepath", "/o", "--target", "https://t")
        z = self.p("o.zip")
        run("bash", DEPLOY, out, "--package-only", "--out", z)
        import zipfile
        self.assertIn("apiproxy/o.xml", zipfile.ZipFile(z).namelist())


class InstallScript(Base):
    def test_install_modes(self):
        inst = os.path.join(ROOT, "install.sh")
        home = self.p("home")
        os.makedirs(home, exist_ok=True)
        run("bash", inst, env={"HOME": home})
        self.assertTrue(os.path.islink(os.path.join(home, ".gemini/config/plugins/apigee-skills")))
        run("bash", inst, ok=False, env={"HOME": home})
        run("bash", inst, "--copy", "--force", env={"HOME": home})
        self.assertTrue(os.path.isfile(os.path.join(home, ".gemini/config/plugins/apigee-skills/plugin.json")))

        proj = self.p("proj")
        run("bash", inst, "--project", proj)
        self.assertTrue(os.path.isfile(os.path.join(proj, ".agents/skills/apigee-proxy-builder/SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(proj, ".agents/rules/apigee_proxy_standards.md")))
        self.assertTrue(os.path.isfile(os.path.join(proj, ".agents/agents/apigee-architect.md")))

        claude = self.p("claude")
        run("bash", inst, "--claude", claude)
        self.assertTrue(os.path.isfile(os.path.join(claude, ".claude/skills/apigee-ai-gateway/SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(claude, ".claude/rules/security_and_governance.md")))
        ca = open(os.path.join(claude, ".claude/agents/apigee-architect.md")).read()
        self.assertIn("name: apigee-architect", ca)
        self.assertNotIn("mainAgent:", ca)

        copilot = self.p("copilot")
        run("bash", inst, "--copilot", copilot)
        self.assertTrue(os.path.isfile(os.path.join(copilot, ".github/skills/apigee-mcp-gateway/SKILL.md")))
        self.assertTrue(os.path.isfile(
            os.path.join(copilot, ".github/instructions/apigee_proxy_standards.instructions.md")))
        cpa = open(os.path.join(copilot, ".github/agents/apigee-architect.agent.md")).read()
        self.assertIn("name: apigee-architect", cpa)
        self.assertNotIn("tools:", cpa)

        run("bash", inst, "--copilot", home, env={"HOME": home})
        self.assertTrue(os.path.isfile(os.path.join(home, ".copilot/skills/apigee-proxy-builder/SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(home, ".copilot/agents/apigee-gateway-tester.agent.md")))

        gem = self.p("gemini")
        run("bash", inst, "--gemini", gem)
        self.assertTrue(os.path.isfile(os.path.join(gem, ".gemini/skills/apigee-proxy-lifecycle/SKILL.md")))

        codex = self.p("codex")
        run("bash", inst, "--codex", codex)
        self.assertTrue(os.path.isfile(os.path.join(codex, ".agents/skills/apigee-x-provisioning/SKILL.md")))

        raw = self.p("raw")
        run("bash", inst, "--target", raw)
        self.assertTrue(os.path.isfile(os.path.join(raw, "apigee-proxy-builder/SKILL.md")))
        self.assertEqual(len([d for d in os.listdir(raw) if os.path.isdir(os.path.join(raw, d))]), 5)


class ReleaseNotesSync(Base):
    def test_sync_release_notes_and_scheduler_setup(self):
        sync = os.path.join(ROOT, "scripts/sync_release_notes.py")
        sched = os.path.join(ROOT, "scripts/setup_cloud_scheduler.sh")
        feed = self.p("feed.xml")
        with open(feed, "w") as f:
            f.write("""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>November 01, 2026</title>
    <id>tag:google.com,2016:apigee-release-notes#November_01_2026</id>
    <updated>2026-11-01T00:00:00-07:00</updated>
    <content type="html"><![CDATA[
      <h2 class="release-note-product-title">Apigee X</h2>
      <p>Released 1-18-0-apigee-8 with LLMTokenQuota and SemanticCacheLookup enhancements.</p>
      <h2 class="release-note-product-title">Apigee Edge for Private Cloud</h2>
      <p>Legacy Edge patch.</p>
    ]]></content>
  </entry>
</feed>""")
        state_file = self.p("state.json")
        with open(state_file, "w") as f:
            json.dump({"last_synced_date": "2026-10-22"}, f)
        r = py(sync, "--state-file", state_file, "--feed-url", feed, "--json", "--mark-synced")
        rep = json.loads(r.stdout)
        self.assertEqual(rep["new_entries_count"], 1)
        self.assertEqual(rep["relevant_entries_count"], 1)
        self.assertIn("apigee-ai-gateway", rep["entries"][0]["affected_skills"])
        self.assertEqual(rep["entries"][0]["versions"], ["1-18-0-apigee-8"])
        self.assertEqual(json.load(open(state_file))["last_synced_date"], "2026-11-01")

        # Second check after mark-synced sees 0 new entries
        r2 = py(sync, "--state-file", state_file, "--feed-url", feed, "--json")
        self.assertEqual(json.loads(r2.stdout)["new_entries_count"], 0)

        # Verify setup_cloud_scheduler.sh --dry-run
        dr = run("bash", sched, "--project", "my-gcp-project", "--region", "us-central1", "--dry-run")
        self.assertIn("gcloud scheduler jobs create http apigee-release-notes-sync", dr.stdout)
        self.assertIn("Asia/Kolkata", dr.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2, warnings="ignore")
