#!/usr/bin/env python3
"""Plain-English Apigee X policy & protection advisor (stdlib only).

Helps users who do NOT know Apigee terminology choose the right security, traffic,
caching, and AI/agent guardrails in simple everyday English — and outputs a ready-to-run
scaffold command.

Examples:
  # Interactive plain-English Q&A wizard:
  python3 recommend_policies.py wizard

  # Recommend from a plain-English description:
  python3 recommend_policies.py --describe "React web app calling our Cloud Run orders service with an OpenAPI spec"
  python3 recommend_policies.py --describe "Engineering team using Claude Code and Copilot with token budgets and safety checks"
  python3 recommend_policies.py --describe "Expose our Cloud Run MCP tool server to support and analytics agents with refund limits"

  # Explicit flags (human-readable report or --json):
  python3 recommend_policies.py --kind api --callers partner-apps --backend cloud-run --browser --oas ./openapi.json
"""
import argparse
import json
import os
import shlex
import sys

SKILLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_SCAFFOLD = os.path.join(SKILLS_DIR, "apigee-proxy-builder", "scripts", "scaffold_api_proxy.py")
SF_SCAFFOLD = os.path.join(SKILLS_DIR, "apigee-proxy-builder", "scripts", "scaffold_shared_flow.py")
AI_SCAFFOLD = os.path.join(SKILLS_DIR, "apigee-ai-gateway", "scripts", "scaffold_ai_gateway.py")
MCP_SCAFFOLD = os.path.join(SKILLS_DIR, "apigee-mcp-gateway", "scripts", "scaffold_mcp_proxy.py")


def _ask(prompt: str, default: str = "", choices=None) -> str:
    hint = f" ({'/'.join(choices)})" if choices else ""
    dflt = f" [{default}]" if default else ""
    while True:
        try:
            ans = input(f"{prompt}{hint}{dflt}: ").strip()
        except EOFError:
            ans = ""
        val = ans or default
        if not choices:
            return val
        for c in choices:
            if val.lower() == c.lower():
                return c
        print(f"  Please choose one of: {', '.join(choices)}")


def _ask_yn(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    ans = _ask(f"{prompt} ({d})", "y" if default else "n", ["y", "yes", "n", "no"]).lower()
    return ans.startswith("y")


def _item(capability: str, why: str, apigee_terms: str, how_enabled: str) -> dict:
    return {
        "capability": capability,
        "why": why,
        "apigee_terms": apigee_terms,
        "how_enabled": how_enabled,
    }


def _infer_from_description(desc: str, opts: dict) -> dict:
    d = (desc or "").lower()
    if not d:
        return opts

    if not opts.get("kind_explicit"):
        if any(k in d for k in ("mcp", "tool server", "json-rpc", "tools/call", "agent tool")):
            opts["kind"] = "mcp"
        elif any(k in d for k in (
            "llm", "gemini", "claude", "openai", "gpt", "copilot", "antigravity",
            "claude code", "vertex ai", "ai gateway", "prompt", "token quota", "model armor",
        )):
            opts["kind"] = "ai"
        elif any(k in d for k in ("shared flow", "shared rule", "across all api", "every proxy", "org-wide", "flow hook")):
            opts["kind"] = "shared-flow"
        else:
            opts["kind"] = "api"

    kind = opts["kind"]
    if kind == "api":
        if not opts.get("callers_explicit"):
            if any(k in d for k in ("jwt", "okta", "auth0", "entra", "oidc", "logged-in", "end user", "id token")):
                opts["callers"] = "logged-in-users"
            elif any(k in d for k in ("oauth", "client_credentials", "service-to-service", "app-to-app", "m2m")):
                opts["callers"] = "service-to-service"
            elif any(k in d for k in ("unauthenticated", "public api", "open access", "no auth", "without auth")):
                opts["callers"] = "public"
        if not opts.get("backend_explicit"):
            if any(k in d for k in ("cloud run", "cloud-run", "run.app", "cloud function")):
                opts["backend"] = "cloud-run"
            elif any(k in d for k in ("google api", "googleapis")):
                opts["backend"] = "google-api"
            elif any(k in d for k in ("multi-env", "staging and prod", "target server", "per-environment")):
                opts["backend"] = "multi-env"
        if any(k in d for k in ("browser", "react", "angular", "vue", "nextjs", "spa", "frontend", "web app", "website", "cors")):
            opts["browser"] = True
        if any(k in d for k in ("cache", "caching", "speed up get", "repeated read")) and not opts.get("cache_seconds"):
            opts["cache_seconds"] = 60
        if any(k in d for k in ("xml", "soap")):
            opts["xml"] = True
        if any(k in d for k in ("mtls", "mutual tls", "client cert")):
            opts["mtls"] = True
        if any(k in d for k in ("audit", "cloud logging", "compliance", "siem", "hipaa", "pci")):
            opts["audit_log"] = True

    elif kind == "ai":
        if not opts.get("models_explicit"):
            has_oai = any(k in d for k in ("openai", "copilot", "gpt", "chat completion"))
            has_claude = any(k in d for k in ("claude", "anthropic"))
            has_gemini = "gemini" in d
            if has_oai:
                opts["models"] = "all"
            elif has_claude:
                opts["models"] = "gemini+claude"
            elif has_gemini:
                opts["models"] = "gemini"
        if any(k in d for k in ("safety", "guardrail", "armor", "pii", "jailbreak", "injection", "sanitize", "screen")):
            opts["safety"] = True
        if any(k in d for k in ("auto-route", "auto route", "cheapest", "smart rout", "classify")):
            opts["smart_routing"] = True
        if any(k in d for k in ("semantic cache", "similar question", "cache")):
            opts["semantic_cache"] = True

    elif kind == "mcp":
        if not opts.get("backend_explicit"):
            opts["backend"] = "https" if ("external" in d or "non-gcp" in d) else "cloud-run"
        if any(k in d for k in ("limit", "refund", "cap", "approval", "amount", "threshold", "max")):
            opts["arg_limits"] = True

    return opts


def _format_cmd(cmd: list) -> str:
    parts = [f"{shlex.quote(cmd[0])} {shlex.quote(cmd[1])}"]
    i = 2
    while i < len(cmd):
        tok = cmd[i]
        if tok.startswith("--") and i + 1 < len(cmd) and not cmd[i + 1].startswith("--"):
            parts.append(f"{tok} {shlex.quote(cmd[i + 1])}")
            i += 2
        else:
            parts.append(shlex.quote(tok))
            i += 1
    return " \\\n  ".join(parts)


def build_recommendation(opts: dict) -> dict:
    kind = opts["kind"]
    rec, opt, glossary = [], [], []

    if kind == "api":
        name = opts.get("name") or "orders-v1"
        basepath = opts.get("basepath") or f"/{name.replace('-', '/')}"
        if not basepath.startswith("/"):
            basepath = "/" + basepath
        out = opts.get("out") or f"./{name}"
        callers = opts.get("callers") or "partner-apps"
        backend = opts.get("backend") or "https"
        cmd = ["python3", API_SCAFFOLD, "--out", out, "--name", name, "--basepath", basepath]

        if backend == "multi-env":
            cmd += ["--target-server", "backend-srv"]
            rec.append(_item(
                "Separate backend URLs per environment (dev / staging / prod)",
                "Lets you promote the exact same proxy bundle across environments without editing URLs in code.",
                "TargetServer + LoadBalancer",
                "--target-server backend-srv",
            ))
        else:
            default_url = "https://orders-xyz.a.run.app" if backend == "cloud-run" else (
                "https://storage.googleapis.com" if backend == "google-api" else "https://mocktarget.apigee.net"
            )
            cmd += ["--target", opts.get("target") or default_url]
            if backend == "cloud-run":
                cmd += ["--target-auth", "idtoken"]
                rec.append(_item(
                    "Keep your Cloud Run backend private with automatic Google sign-in",
                    "Apigee attaches a short-lived Google identity token to every backend call so your Cloud Run service does not need to be public.",
                    "GoogleIDToken on TargetEndpoint",
                    "--target-auth idtoken",
                ))
            elif backend == "google-api":
                cmd += ["--target-auth", "accesstoken"]
                rec.append(_item(
                    "Authenticate to Google Cloud APIs automatically",
                    "Apigee mints a short-lived OAuth access token from its runtime service account — no service-account keys stored anywhere.",
                    "GoogleAccessToken on TargetEndpoint",
                    "--target-auth accesstoken",
                ))

        if callers in ("partner-apps", "apikey"):
            cmd += ["--auth", "apikey", "--quota"]
            rec.append(_item(
                "Identify callers with an API key",
                "Blocks unknown callers at the front door; each approved app gets its own revocable key.",
                "VerifyAPIKey (VA-VerifyAPIKey)",
                "--auth apikey",
            ))
            rec.append(_item(
                "Enforce per-plan call limits (e.g. Free vs Partner tiers)",
                "Caps how many calls each app can make per minute/day/month based on its access plan — changeable without redeploying.",
                "Quota + API Product",
                "--quota",
            ))
        elif callers in ("logged-in-users", "jwt"):
            cmd += [
                "--auth", "jwt",
                "--jwks-url", opts.get("jwks_url") or "https://idp.example.com/.well-known/jwks.json",
                "--issuer", opts.get("issuer") or "https://idp.example.com",
                "--jwt-quota", "60",
            ]
            rec.append(_item(
                "Verify user login tokens from your identity provider (Google, Okta, Auth0, Entra ID)",
                "Checks the cryptographic signature and expiry of Bearer tokens before any request reaches your backend.",
                "VerifyJWT via JWKS (JWT-VerifyToken)",
                "--auth jwt --jwks-url ... --issuer ...",
            ))
            rec.append(_item(
                "Per-user fair-usage limit",
                "Prevents a single logged-in user from exhausting capacity for everyone else.",
                "Quota keyed on JWT subject claim",
                "--jwt-quota 60",
            ))
        elif callers in ("service-to-service", "oauth"):
            cmd += ["--auth", "oauth", "--token-endpoint", "--quota"]
            rec.append(_item(
                "Require short-lived OAuth 2.0 access tokens for app-to-app calls",
                "Callers exchange client credentials at POST /token for a temporary Bearer token instead of sending long-lived keys on every call.",
                "OAuthV2 (VerifyAccessToken + GenerateAccessToken)",
                "--auth oauth --token-endpoint",
            ))
            rec.append(_item(
                "Enforce per-app call allowances",
                "Applies the call limits defined on the caller's access plan.",
                "Quota + API Product",
                "--quota",
            ))
        else:
            cmd += ["--auth", "none"]
            opt.append(_item(
                "Add caller authentication (strongly recommended before production)",
                "Right now anyone on the internet can call this endpoint; add API keys or login tokens if it is not strictly public data.",
                "VerifyAPIKey / VerifyJWT / OAuthV2",
                "--auth apikey (or jwt / oauth)",
            ))

        cmd += ["--spike-arrest", "50ps"]
        rec.append(_item(
            "Smooth out sudden traffic spikes",
            "Protects your backend server from crashing during sudden bursts, retry loops, or runaway scripts.",
            "SpikeArrest (SA-SpikeArrest)",
            "--spike-arrest 50ps",
        ))
        rec.append(_item(
            "Block oversized or deeply nested JSON payloads",
            "Stops malformed or malicious JSON bodies from consuming backend memory or CPU.",
            "JSONThreatProtection (JTP-JSONThreatProtection)",
            "Built-in default",
        ))
        rec.append(_item(
            "Strip caller credentials & hide raw backend crashes",
            "Removes caller API keys/tokens before forwarding to your backend and replaces ugly 5xx stack traces with a clean JSON error + request ID.",
            "AssignMessage (AM-RemoveCredentials + DefaultFaultRule)",
            "Built-in default",
        ))

        if opts.get("browser"):
            cmd += ["--cors", "*"]
            rec.append(_item(
                "Allow web browsers to call your API safely",
                "Answers browser OPTIONS preflight checks at the gateway and attaches allowed-origin headers.",
                "CORS (CORS-Preflight)",
                '--cors "*" (lock down to your domain in prod)',
            ))
        else:
            opt.append(_item(
                "Browser access support (if a React/Vue/Angular frontend calls this directly)",
                "Needed whenever browser JavaScript calls the API across domains.",
                "CORS",
                '--cors "https://app.example.com"',
            ))

        if opts.get("oas"):
            cmd += ["--oas", opts["oas"]]
            rec.append(_item(
                "Validate every request against your OpenAPI / Swagger contract",
                "Automatically rejects unknown URLs (404), disallowed HTTP methods, and invalid JSON request fields before hitting your backend.",
                "OASValidation + per-operation Conditional Flows",
                f"--oas {opts['oas']}",
            ))
        else:
            opt.append(_item(
                "Automatic OpenAPI / Swagger request validation",
                "If you have an OpenAPI spec, Apigee can automatically block bad fields and unknown routes before they reach your code.",
                "OASValidation",
                "--oas ./openapi.yaml",
            ))

        if opts.get("cache_seconds", 0) > 0:
            sec = int(opts["cache_seconds"])
            cmd += ["--cache-ttl", str(sec)]
            rec.append(_item(
                f"Cache repeated GET reads for {sec} seconds",
                "Serves identical read requests straight from memory to cut latency and backend database load.",
                "ResponseCache (RC-GET)",
                f"--cache-ttl {sec}",
            ))
        else:
            opt.append(_item(
                "Speed up repeated GET endpoints with response caching",
                "Great for catalogs, lookups, or public reference data that doesn't change every millisecond.",
                "ResponseCache",
                "--cache-ttl 60",
            ))

        if opts.get("xml"):
            cmd += ["--xml-threat"]
            rec.append(_item(
                "Block malicious XML / XXE payloads",
                "Protects XML/SOAP endpoints against entity-expansion bombs and oversized XML structures.",
                "XMLThreatProtection",
                "--xml-threat",
            ))
        if opts.get("mtls"):
            opt.append(_item(
                "Mutual TLS (two-way certificate verification) to your backend",
                "Proves Apigee's identity to regulated/on-prem backends using a client certificate stored in an Apigee keystore.",
                "SSLInfo ClientAuthEnabled + Keystore/Truststore References",
                "--target-mtls ref://my-keystore:my-alias --target-truststore ref://my-truststore",
            ))
        if opts.get("audit_log"):
            cmd += ["--cloud-logging"]
            rec.append(_item(
                "Write asynchronous audit logs after every response",
                "Streams request metadata to Google Cloud Logging after the client already has their answer (zero added latency).",
                "MessageLogging in PostClientFlow",
                "--cloud-logging",
            ))
        else:
            opt.append(_item(
                "Central audit logging in Google Cloud Logging",
                "Records every call asynchronously after the response is sent, with zero impact on API latency.",
                "MessageLogging in PostClientFlow",
                "--cloud-logging",
            ))

        cmd += ["--health"]
        rec.append(_item(
            "Built-in gateway health check (/ping)",
            "Lets uptime monitors verify the gateway is live without waking up your backend.",
            "Loopback Conditional Flow + AssignMessage",
            "--health",
        ))

        glossary = [
            ("API Proxy", "The gateway doorway Apigee hosts for your backend URL — runs protections before and after calling your service."),
            ("Access Plan (API Product)", "A JSON config listing which URL paths and rate limits a tier (e.g. Free, Partner, Internal) gets."),
            ("Developer App", "Represents one caller application and holds its revocable API key / credentials."),
            ("TargetServer", "Named backend host setting per environment (dev/stage/prod) so you never hardcode URLs."),
        ]
        summary = "REST / HTTP API Gateway with caller authentication, burst protection, payload safety, and clean error handling."

    elif kind == "ai":
        name = opts.get("name") or "ai-gateway-v1"
        out = opts.get("out") or f"./{name}"
        project = opts.get("project") or "my-gcp-project"
        region = opts.get("region") or "us-central1"
        models = opts.get("models") or "gemini+claude"
        cmd = ["python3", AI_SCAFFOLD, "--out", out, "--name", name, "--project", project]

        rec.append(_item(
            "One gateway key for developers, apps, and coding IDEs",
            "Accepts keys from standard headers (Authorization Bearer, x-apikey, x-api-key, api-key, x-goog-api-key) and signs in to Vertex AI automatically so nobody holds GCP credentials.",
            "ExtractVariables + VerifyAPIKey + GoogleAccessToken",
            "Built-in default",
        ))
        if not opts.get("no_streaming"):
            rec.append(_item(
                "Live token-by-token streaming (Server-Sent Events) without 30-second drops",
                "Streams AI responses smoothly to chat UIs and coding assistants while still enforcing safety checks and counting tokens on the live stream.",
                "Dedicated *-stream-target TargetEndpoints + <EventFlow>",
                "Built-in default (--sse)",
            ))
        else:
            cmd += ["--no-sse"]

        if models in ("gemini+claude", "all"):
            cmd += ["--claude"]
            rec.append(_item(
                "Anthropic Claude + Claude Code support alongside Gemini",
                "Translates request/response formats automatically, supports native /v1/messages, and strips unsupported beta headers that would otherwise cause retry storms.",
                "claude-vertex-target + ClaudeRequestPrep.js + FormatClaudeStream.js",
                "--claude",
            ))
        if models == "all":
            cmd += ["--openai"]
            rec.append(_item(
                "OpenAI-compatible & GitHub/Microsoft Copilot support",
                "Exposes /v1/chat/completions and GET /v1/models so OpenAI SDKs and BYOK IDE Copilots plug straight in.",
                "openai-vertex-target + NormalizeOpenAIRequest.js",
                "--openai",
            ))
        elif models == "gemini":
            opt.append(_item(
                "Add Anthropic Claude or OpenAI / Copilot compatibility",
                "Let teams call Claude or OpenAI-compatible SDKs/IDEs through the exact same gateway URL and key.",
                "Multi-provider Targets & JS Mediation",
                "--claude and/or --openai",
            ))

        if opts.get("token_limits", True):
            cmd += ["--token-quota", "--prompt-token-limit", "5000pm"]
            rec.append(_item(
                "Cap AI token usage per app/user and per model (stop runaway AI bills)",
                "Tracks actual prompt + completion tokens across both normal and streaming calls, returning HTTP 429 when a caller hits their allowance.",
                "LLMTokenQuota (EnforceOnly + buffered/SSE CountOnly) + API Product llmOperationGroup",
                "--token-quota",
            ))
            rec.append(_item(
                "Block sudden mega-prompt surges",
                "Stops a buggy loop or giant pasted file from burning thousands of input tokens in a single second.",
                "PromptTokenLimit (PTL-PromptTokenLimit)",
                "--prompt-token-limit 5000pm",
            ))

        if opts.get("safety"):
            cmd += ["--armor-region", region]
            rec.append(_item(
                "Screen prompts and AI responses for jailbreaks, prompt injection, toxicity & PII",
                "Uses Google Cloud Model Armor to block unsafe prompts before paying for model inference — and scans streaming answers chunk-by-chunk.",
                "SanitizeUserPrompt + SanitizeModelResponse (ContinuousScan on SSE)",
                f"--armor-region {region}",
            ))
        else:
            opt.append(_item(
                "AI safety guardrails (block prompt injection, jailbreaks & sensitive data leaks)",
                "Screens user prompts and AI answers (including live SSE streams) via Google Cloud Model Armor.",
                "SanitizeUserPrompt + SanitizeModelResponse",
                f"--armor-region {region} (create Model Armor templates first)",
            ))

        if opts.get("smart_routing"):
            cmd += ["--auto-route"]
            rec.append(_item(
                "Smart model router (/models/auto) to cut AI costs",
                "Classifies each prompt (simple, coding, deep reasoning) and routes simple questions to fast/cheap models and hard ones to frontier models.",
                "Classifier ServiceCallout + AutoRouting.js + API Product attributes",
                "--auto-route",
            ))
        else:
            opt.append(_item(
                "Automatic model routing (/models/auto) for cost optimization",
                "Sends everyday questions to Flash/Haiku and complex coding/reasoning tasks to Pro/Opus based on the caller's plan.",
                "ServiceCallout + AutoRouting.js",
                "--auto-route",
            ))

        cmd += ["--analytics"]
        rec.append(_item(
            "Track token consumption, model usage, and errors per caller",
            "Captures input/output tokens, model name, cache hits, and error codes into Apigee Analytics dashboards.",
            "DataCapture (DC-ModelAnalytics + DC-FaultAnalytics)",
            "--analytics",
        ))

        if opts.get("semantic_cache"):
            opt.append(_item(
                "AI smart cache for repeated or similar questions (requires Vertex Vector Search endpoint)",
                "Returns cached answers when callers ask semantically identical questions (x-use-cache: true), saving 100% of model tokens and latency.",
                "SemanticCacheLookup + SemanticCachePopulate",
                f"--cache-region {region} --index-endpoint-host HOST --index-endpoint-id ID --index-id ID",
            ))
        else:
            opt.append(_item(
                "AI smart cache (reuse answers to similar questions)",
                "Skips the LLM completely when a semantically matching question was recently answered.",
                "SemanticCacheLookup + SemanticCachePopulate",
                "--cache-region REGION --index-endpoint-host HOST --index-endpoint-id ID --index-id ID",
            ))

        glossary = [
            ("Token Quota (LLMTokenQuota)", "Like a mobile data plan, but measured in AI words/tokens per minute, day, or month instead of raw HTTP calls."),
            ("Model Armor", "Google Cloud's AI safety filter that catches prompt injections, jailbreaks, toxic content, and PII leaks."),
            ("EventFlow (SSE Streaming)", "Apigee's real-time stream inspector that checks safety and counts tokens as words stream back live."),
            ("LLM Access Plan (llmOperationGroup)", "Part of the API Product JSON that decides which AI models a key can call and its token budget."),
        ]
        summary = "Model-agnostic AI & Coding-Agent Gateway with live streaming, token spend controls, and AI safety options."

    elif kind == "mcp":
        name = opts.get("name") or "orders-mcp"
        basepath = opts.get("basepath") or "/orders/mcp"
        out = opts.get("out") or f"./{name}"
        target = opts.get("target") or "https://orders-mcp-xyz.a.run.app/mcp"
        cmd = [
            "python3", MCP_SCAFFOLD, "--out", out, "--name", name, "--basepath", basepath,
            "--target", target, "--tools", "searchOrders,getOrder,issueRefund",
            "--persona", "Support:searchOrders,getOrder,issueRefund",
            "--persona", "Insights:searchOrders,getOrder",
        ]
        rec.append(_item(
            "Control exactly which tools each AI agent is allowed to call",
            "Inspects inside every MCP JSON-RPC request and blocks an agent from calling tools outside its assigned role (e.g. Insights can read orders, Support can issue refunds).",
            "ParsePayload (PP-MCP) + VerifyAPIKey + Persona API Products (payloadOperationGroup)",
            '--tools ... --persona "Role:tool1,tool2"',
        ))
        rec.append(_item(
            "Allow smooth MCP client connection while protecting tool calls",
            "Lets agents complete the initial MCP handshake (initialize/ping) cleanly, then enforces API keys and rate limits on every tool lookup or execution.",
            "JSON-RPC Method Allowlist + Conditional VerifyAPIKey on tools/*",
            "Built-in default",
        ))
        rec.append(_item(
            "Cap how many tool calls per minute an agent can make",
            "Stops a looping or hallucinating AI agent from hammering your backend systems.",
            "Per-tool Quota (Q-Limit)",
            "--quota 20 (default)",
        ))
        if opts.get("backend") != "https":
            rec.append(_item(
                "Keep your Cloud Run MCP server private",
                "Strips agent API keys and signs in to Cloud Run using Apigee's Google identity token so agents can never bypass the gateway.",
                "AssignMessage + GoogleIDToken",
                "Built-in default for *.run.app targets",
            ))
        else:
            cmd += ["--no-id-token"]

        if opts.get("arg_limits", True):
            cmd += ["--limit", "issueRefund:amount:50:Support:REFUND_LIMIT:Refunds over 50 need supervisor approval."]
            rec.append(_item(
                "Enforce dollar / quantity guardrails on tool arguments",
                "Checks numeric inputs inside tool calls (e.g. blocking refunds above $50) and returns a structured MCP tool error so the agent knows to ask a human instead of retrying.",
                "ExtractVariables + Conditional RaiseFault (JSON-RPC result.isError=true)",
                '--limit "tool:arg:max:Persona:CODE:message"',
            ))

        glossary = [
            ("MCP (Model Context Protocol)", "Standard protocol AI agents (Gemini CLI, Claude, ADK) use to discover and call external tools."),
            ("Persona Access Plan", "An API Product tailored to one agent role (e.g. Read-Only vs Support) listing only the exact tools it may run."),
            ("Business-Rule Tool Limit", "A gateway check on tool arguments (like refund amount <= 50) that tells the AI agent why an action requires human approval."),
        ]
        summary = "Enterprise MCP Agent Tools Gateway with per-agent tool permissions, rate limits, and input value guardrails."

    else:  # shared-flow
        name = opts.get("name") or "sf-security"
        out = opts.get("out") or f"./{name}"
        cmd = ["python3", SF_SCAFFOLD, "--out", out, "--name", name, "--features", "apikey,spikearrest,threat,strip-auth"]
        rec.append(_item(
            "Enforce one standard security baseline across every API in an environment",
            "Packages API key verification, burst smoothing, bad-JSON blocking, and credential stripping once so individual API teams cannot forget them.",
            "Shared Flow + Environment Flow Hook (PreProxyFlowHook)",
            "--features apikey,spikearrest,threat,strip-auth",
        ))
        opt.append(_item(
            "Organization-wide Cloud Logging shared rule",
            "Attach a second shared flow to PostProxyFlowHook so every API logs traffic consistently.",
            "Shared Flow (--features cloud-logging) + PostProxyFlowHook",
            "--features cloud-logging",
        ))
        glossary = [
            ("Shared Flow", "A reusable bundle of protections written once and shared by many API proxies."),
            ("Flow Hook", "An environment-level switch that automatically runs a Shared Flow on every proxy in dev, staging, or prod."),
        ]
        summary = "Reusable organization-wide security baseline for multiple Apigee APIs."

    return {
        "kind": kind,
        "summary": summary,
        "recommended": rec,
        "optional": opt,
        "glossary": glossary,
        "scaffold_cmd": cmd,
        "scaffold_command": _format_cmd(cmd),
    }


def run_wizard() -> dict:
    print("\n=== Apigee Plain-English Protection Advisor ===")
    print("No Apigee jargon needed — answer a few quick questions (press Enter for defaults).\n")
    k_map = {"1": "api", "2": "ai", "3": "mcp", "4": "shared-flow", "api": "api", "ai": "ai", "mcp": "mcp", "shared-flow": "shared-flow"}
    print("What do you want to put behind the gateway?")
    print("  1) A normal REST / HTTP backend service or microservice [default]")
    print("  2) AI models or coding assistants (Gemini, Claude, OpenAI, Copilot, Claude Code, Antigravity)")
    print("  3) An MCP tool server called by AI agents")
    print("  4) Reusable security rules shared across all APIs in an environment")
    raw_k = _ask("Choose 1-4", "1", list(k_map.keys()))
    kind = k_map[raw_k.lower()]
    opts = {"kind": kind, "kind_explicit": True}

    if kind == "api":
        opts["name"] = _ask("Short name for your API", "orders-v1")
        opts["callers"] = _ask(
            "Who calls this API?",
            "partner-apps",
            ["partner-apps", "logged-in-users", "service-to-service", "public"],
        )
        opts["backend"] = _ask(
            "Where does your backend service run?",
            "cloud-run",
            ["cloud-run", "https", "google-api", "multi-env"],
        )
        opts["browser"] = _ask_yn("Will a web browser (React/Vue/Angular) call this API directly?", False)
        oas = _ask("Path to an OpenAPI / Swagger file (leave blank if none)", "")
        if oas:
            opts["oas"] = oas
        if _ask_yn("Can GET responses be cached for 60s to speed up repeated reads?", False):
            opts["cache_seconds"] = 60
        opts["audit_log"] = _ask_yn("Stream audit logs of every call to Google Cloud Logging?", False)
    elif kind == "ai":
        opts["name"] = _ask("Short name for your AI gateway", "ai-gateway-v1")
        opts["project"] = _ask("GCP project ID for Vertex AI", "my-gcp-project")
        opts["models"] = _ask("Which AI models/clients need access?", "gemini+claude", ["gemini", "gemini+claude", "all"])
        opts["token_limits"] = _ask_yn("Set token budgets per app/user so nobody runs up a huge AI bill?", True)
        opts["safety"] = _ask_yn("Screen prompts & responses for prompt injection, jailbreaks, and sensitive data?", True)
        if opts["safety"]:
            opts["region"] = _ask("Region for safety screening (Model Armor)", "us-central1")
        opts["smart_routing"] = _ask_yn("Enable /models/auto to send simple prompts to cheaper models automatically?", False)
    elif kind == "mcp":
        opts["name"] = _ask("Short name for your MCP tool gateway", "orders-mcp")
        opts["backend"] = "cloud-run" if _ask_yn("Is your MCP server hosted on private Cloud Run?", True) else "https"
        opts["arg_limits"] = _ask_yn("Add numeric safety caps on sensitive tool inputs (e.g. max refund amount)?", True)
    else:
        opts["name"] = _ask("Short name for your shared security rule", "sf-security")

    return opts


def format_report(plan: dict) -> str:
    lines = [
        f"# Recommended Gateway Setup: {plan['summary']}",
        "",
        "## 1. Recommended Protections (Included)",
    ]
    for i, r in enumerate(plan["recommended"], 1):
        lines.append(f"{i}. **{r['capability']}**")
        lines.append(f"   - *What it does:* {r['why']}")
        lines.append(f"   - *Behind the scenes in Apigee:* `{r['apigee_terms']}` ({r['how_enabled']})")

    if plan["optional"]:
        lines += ["", "## 2. Optional Add-Ons You Can Enable Anytime"]
        for r in plan["optional"]:
            lines.append(f"- **{r['capability']}** — {r['why']} *(Apigee: `{r['apigee_terms']}`; enable with `{r['how_enabled']}`)*")

    lines += ["", "## 3. Plain-English Key Terms"]
    for term, meaning in plan["glossary"]:
        lines.append(f"- **{term}**: {meaning}")

    lines += [
        "",
        "## 4. Ready-to-Run Scaffold Command",
        "```bash",
        plan["scaffold_command"],
        "```",
    ]
    return "\n".join(lines)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "wizard":
        json_out = "--json" in sys.argv[2:]
        opts = run_wizard()
        plan = build_recommendation(opts)
        print(json.dumps(plan, indent=2) if json_out else "\n" + format_report(plan))
        return

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--describe", help="Plain-English sentence describing your app, callers, backend, and goals")
    p.add_argument("--kind", choices=["api", "ai", "mcp", "shared-flow"], help="Gateway type (auto-detected from --describe if omitted)")
    p.add_argument("--callers", choices=["partner-apps", "apikey", "logged-in-users", "jwt", "service-to-service", "oauth", "public", "none"])
    p.add_argument("--backend", choices=["https", "cloud-run", "google-api", "multi-env"])
    p.add_argument("--browser", action="store_true", help="API is called directly from a web browser (adds CORS)")
    p.add_argument("--oas", help="Path to OpenAPI / Swagger spec for automatic request validation")
    p.add_argument("--cache-seconds", type=int, default=0, help="Cache GET responses for N seconds")
    p.add_argument("--xml", action="store_true", help="Protect XML / SOAP request bodies")
    p.add_argument("--mtls", action="store_true", help="Include mutual TLS guidance for backend")
    p.add_argument("--audit-log", action="store_true", help="Stream async audit logs to Google Cloud Logging")
    p.add_argument("--models", choices=["gemini", "gemini+claude", "all"], help="AI model families to expose")
    p.add_argument("--safety", action="store_true", help="Screen AI prompts & responses for jailbreaks/PII")
    p.add_argument("--no-token-limits", action="store_true", help="Skip AI token budget policies")
    p.add_argument("--smart-routing", action="store_true", help="Enable /models/auto prompt classifier routing")
    p.add_argument("--semantic-cache", action="store_true", help="Include AI semantic cache guidance")
    p.add_argument("--no-streaming", action="store_true", help="Disable SSE live streaming on AI gateway")
    p.add_argument("--arg-limits", action="store_true", default=True, help="Include MCP tool argument limit example")
    p.add_argument("--no-arg-limits", dest="arg_limits", action="store_false")
    p.add_argument("--name", help="Proxy / shared-flow name")
    p.add_argument("--basepath", help="Gateway URL base path (e.g. /orders/v1)")
    p.add_argument("--target", help="Backend URL")
    p.add_argument("--project", help="GCP project ID (for AI gateway)")
    p.add_argument("--region", default="us-central1", help="GCP region for Model Armor / cache")
    p.add_argument("--out", help="Output directory for scaffold command")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    a = p.parse_args()

    opts = {
        "kind": a.kind or "api",
        "kind_explicit": a.kind is not None,
        "callers": a.callers or "partner-apps",
        "callers_explicit": a.callers is not None,
        "backend": a.backend or "https",
        "backend_explicit": a.backend is not None,
        "browser": a.browser,
        "oas": a.oas,
        "cache_seconds": a.cache_seconds,
        "xml": a.xml,
        "mtls": a.mtls,
        "audit_log": a.audit_log,
        "models": a.models or "gemini+claude",
        "models_explicit": a.models is not None,
        "safety": a.safety,
        "token_limits": not a.no_token_limits,
        "smart_routing": a.smart_routing,
        "semantic_cache": a.semantic_cache,
        "no_streaming": a.no_streaming,
        "arg_limits": a.arg_limits,
        "name": a.name,
        "basepath": a.basepath,
        "target": a.target,
        "project": a.project,
        "region": a.region,
        "out": a.out,
    }
    opts = _infer_from_description(a.describe, opts)
    plan = build_recommendation(opts)
    print(json.dumps(plan, indent=2) if a.json else format_report(plan))


if __name__ == "__main__":
    main()
