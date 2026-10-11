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
API = os.path.join(SK, "apigee-api-proxy", "scripts", "scaffold_api_proxy.py")
SF = os.path.join(SK, "apigee-api-proxy", "scripts", "scaffold_shared_flow.py")
AI = os.path.join(SK, "apigee-ai-gateway", "scripts", "scaffold_ai_gateway.py")
MCP = os.path.join(SK, "apigee-mcp-gateway", "scripts", "scaffold_mcp_proxy.py")
CTL = os.path.join(SK, "apigee-proxy-lifecycle", "scripts", "apigee_ctl.py")
EDIT = os.path.join(SK, "apigee-proxy-lifecycle", "scripts", "bundle_edit.py")
DEPLOY = os.path.join(SK, "apigee-proxy-builder", "scripts", "deploy_bundle.sh")
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

    def test_no_demo_coupling(self):
        bad = ["maloosatyam", "altostrat", "apigee-ai-gateway-demo", "ui/server/"]
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
           "--index-endpoint-id", "e", "--index-id", "i", "--claude", "--auto-route")
        self.validate(out)
        xml = open(os.path.join(out, "apiproxy/proxies/default.xml")).read()
        self.assertLess(xml.index('name="AutoRoutingFlow"'), xml.index('name="LLMTokenQuotaFlow"'))
        prod = json.load(open(os.path.join(out, "api_product.json")))
        models = [c["llmOperations"][0]["model"] for c in prod["llmOperationGroup"]["operationConfigs"]]
        self.assertIn("auto", models)
        self.assertTrue(any(a["name"] == "routing.model.coding" for a in prod["attributes"]))

    def test_ai_gateway_minimal(self):
        py(AI, "--out", self.p("ai"), "--project", "p")
        self.validate(self.p("ai"))

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
assert.strictEqual(v["flow.quota_model"],"auto"); assert.strictEqual(v["flow.userPrompt"],"q");
JSON.parse(run(J+'/PrepRouterRequest.js',{"flow.userPrompt":"x"})["flow.routerBody"]);
console.log("ok");
"""

    def test_generated_js(self):
        out = self.p("ai")
        py(AI, "--out", out, "--project", "p", "--claude", "--auto-route", "--token-quota")
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


class DeployScript(Base):
    def test_package_only(self):
        out = self.p("o")
        py(API, "--out", out, "--name", "o", "--basepath", "/o", "--target", "https://t")
        z = self.p("o.zip")
        run("bash", DEPLOY, out, "--package-only", "--out", z)
        import zipfile
        self.assertIn("apiproxy/o.xml", zipfile.ZipFile(z).namelist())


if __name__ == "__main__":
    unittest.main(verbosity=2, warnings="ignore")
