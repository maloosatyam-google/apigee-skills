#!/usr/bin/env python3
"""Check Google Cloud Apigee release notes and sync updates into apigee-skills.

Stdlib-only Python 3.9+. Designed to run locally, in Cloud Build / Cloud Run Jobs
triggered by Google Cloud Scheduler, or in CI.

Examples:
  # Check for any Apigee X release notes newer than scripts/release_notes_state.json:
  python3 scripts/sync_release_notes.py --check

  # Check since a specific date (e.g. 2026-08-01) and output JSON:
  python3 scripts/sync_release_notes.py --since 2026-08-01 --json

  # Apply updates automatically via Vertex AI Gemini and verify with `make test`:
  python3 scripts/sync_release_notes.py --apply --project my-gcp-project
"""
import argparse
import gzip
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_STATE_FILE = ROOT / "scripts" / "release_notes_state.json"
DEFAULT_FEED_URL = "https://cloud.google.com/feeds/apigee-release-notes.xml"
DEFAULT_HTML_URL = "https://docs.cloud.google.com/apigee/docs/release-notes"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

RELEVANT_PRODUCTS = {
    "apigee x",
    "apigee api hub",
    "apigee ui",
    "apigee advanced api security",
}

SKILL_KEYWORDS = {
    "apigee-ai-gateway": [
        "SanitizeUserPrompt", "SanitizeModelResponse", "Model Armor", "modelarmor",
        "LLMTokenQuota", "PromptTokenLimit", "SemanticCacheLookup", "SemanticCachePopulate",
        "llmOperationGroup", "llmTokenQuota", "DistanceMeasureType", "FunctionCallSource",
        "FunctionResponseSource", "SemanticGovernance", "Semantic Governance", "EventFlow",
        "ai_llm_", "Vertex AI", "Gemini", "Anthropic",
    ],
    "apigee-mcp-gateway": [
        "ParsePayload", "parsepayload", "payloadOperationGroup", "JSON-RPC", "json-rpc",
        "MCP", "Model Context Protocol", "ConfigureAndDeployServer", "Tool performance",
    ],
    "apigee-api-proxy": [
        "VerifyAPIKey", "OAuthV2", "VerifyJWT", "GenerateJWT", "DecodeJWT", "VerifyJWS",
        "GenerateJWS", "SpikeArrest", "Quota", "OASValidation", "JSONThreatProtection",
        "XMLThreatProtection", "RegularExpressionProtection", "CORS", "ResponseCache",
        "LookupCache", "PopulateCache", "InvalidateCache", "ServiceCallout", "AssignMessage",
        "ExtractVariables", "RaiseFault", "MessageLogging", "DataCapture", "AccessControl",
        "VerifyIAM", "BasicAuthentication", "HMAC", "JavaCallout", "PythonScript", "JavaScript",
    ],
    "apigee-proxy-lifecycle": [
        "KeyValueMapOperations", "KVM", "TargetServer", "SharedFlow", "FlowCallout",
        "FlowHook", "DebugMask", "trace", "API product", "apiproduct", "AppGroup",
    ],
    "apigee-x-provisioning": [
        "Private Service Connect", "PSC", "Service Networking", "peering", "CMEK",
        "PAYG", "Subscription", "INTERMEDIATE", "COMPREHENSIVE", "instance", "envgroup",
    ],
}

SKILL_FILES = {
    "apigee-ai-gateway": [
        "skills/apigee-proxy-builder/references/policies_ai_gateway.md",
        "skills/apigee-ai-gateway/SKILL.md",
        "skills/apigee-ai-gateway/references/model_routing_playbook.md",
        "skills/apigee-proxy-builder/scripts/validate_bundle.py",
    ],
    "apigee-mcp-gateway": [
        "skills/apigee-proxy-builder/references/policies_mcp_tools.md",
        "skills/apigee-mcp-gateway/SKILL.md",
        "skills/apigee-mcp-gateway/references/mcp_rest_bridge.md",
    ],
    "apigee-api-proxy": [
        "skills/apigee-proxy-builder/references/policies_security.md",
        "skills/apigee-proxy-builder/references/policies_caching.md",
        "skills/apigee-proxy-builder/references/policies_mediation.md",
        "skills/apigee-proxy-builder/references/policies_traffic_management.md",
        "skills/apigee-proxy-builder/scripts/validate_bundle.py",
    ],
    "apigee-proxy-lifecycle": [
        "skills/apigee-proxy-lifecycle/SKILL.md",
        "skills/apigee-proxy-lifecycle/references/update_playbook.md",
    ],
    "apigee-x-provisioning": [
        "skills/apigee-x-provisioning/SKILL.md",
    ],
}


def fetch_bytes(url_or_path: str) -> bytes:
    p = pathlib.Path(url_or_path)
    if p.exists():
        return p.read_bytes()
    req = urllib.request.Request(url_or_path, headers={"User-Agent": "apigee-skills-release-notes-sync/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw


def strip_html(fragment: str) -> str:
    txt = re.sub(r"<script.*?</script>", "", fragment, flags=re.S)
    txt = re.sub(r"<style.*?</style>", "", txt, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def match_skills(text: str) -> list:
    matched = []
    for skill, kws in SKILL_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(kw) + r"\b", text, re.I) for kw in kws):
            matched.append(skill)
    return matched


def parse_atom_feed(xml_bytes: bytes, since_date: str, include_hybrid: bool = False) -> list:
    root = ET.fromstring(xml_bytes)
    results = []
    prod_re = re.compile(
        r'<h2[^>]*class="release-note-product-title"[^>]*>(.*?)</h2>(.*?)(?=<h2[^>]*class="release-note-product-title"|\Z)',
        flags=re.S | re.I,
    )
    ver_re = re.compile(r"\b(1-\d+-\d+-apigee-\d+)\b")

    for entry in root.findall("atom:entry", ATOM_NS):
        updated = (entry.findtext("atom:updated", "", ATOM_NS) or "").strip()
        date_str = updated[:10]
        if not date_str or date_str <= since_date:
            continue
        entry_id = (entry.findtext("atom:id", "", ATOM_NS) or "").strip()
        title = (entry.findtext("atom:title", "", ATOM_NS) or "").strip()
        content_html = entry.findtext("atom:content", "", ATOM_NS) or ""

        sections = []
        matches = list(prod_re.finditer(content_html))
        if matches:
            for m in matches:
                product = strip_html(m.group(1))
                body_text = strip_html(m.group(2))
                prod_lower = product.lower()
                if not include_hybrid and prod_lower not in RELEVANT_PRODUCTS:
                    continue
                sections.append({"product": product, "text": body_text})
        else:
            body_text = strip_html(content_html)
            if body_text:
                sections.append({"product": "Apigee", "text": body_text})

        if not sections:
            continue

        combined_text = "\n".join(f"[{s['product']}] {s['text']}" for s in sections)
        versions = sorted(set(ver_re.findall(combined_text)))
        affected_skills = match_skills(combined_text)
        results.append({
            "date": date_str,
            "id": entry_id,
            "title": title,
            "versions": versions,
            "products": [s["product"] for s in sections],
            "affected_skills": affected_skills,
            "summary": combined_text,
        })

    results.sort(key=lambda r: r["date"])
    return results


def get_gcp_token() -> str:
    if os.environ.get("APIGEE_TOKEN"):
        return os.environ["APIGEE_TOKEN"].strip()
    meta_url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
    try:
        req = urllib.request.Request(meta_url, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))["access_token"]
    except Exception:
        pass
    out = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    if not out:
        raise RuntimeError("Failed to obtain GCP access token from APIGEE_TOKEN, metadata server, or gcloud.")
    return out


def call_vertex_gemini(project: str, location: str, model: str, notes: list, target_files: dict) -> dict:
    token = get_gcp_token()
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )
    prompt_parts = [
        "You are maintaining the Google Cloud Apigee X agent skills repository (`apigee-skills`).\n"
        "Review the following new Apigee X release notes and determine whether any skill reference docs, "
        "SKILL.md files, or validator checks need to be updated.\n"
        "Rules:\n"
        "1. Only propose edits if a release note introduces or changes an Apigee X policy element, attribute, "
        "flow variable, fault code, API product behavior, or runtime requirement relevant to the files provided.\n"
        "2. Keep edits concise and match the exact style of the existing markdown or Python file.\n"
        "3. Never add org-, project-, or demo-specific values. Use Apigee X terminology only.\n"
        "4. Each edit must provide an exact `target` substring currently present in `file` and its `replacement`.\n\n"
        "### New Release Notes\n"
        + json.dumps(notes, indent=2)
        + "\n\n### Current Repository Files\n"
    ]
    for rel_path, content in target_files.items():
        prompt_parts.append(f"--- FILE: {rel_path} ---\n{content}\n")

    payload = {
        "contents": [{"role": "user", "parts": [{"text": "\n".join(prompt_parts)}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "summary": {"type": "STRING"},
                    "edits": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "file": {"type": "STRING"},
                                "target": {"type": "STRING"},
                                "replacement": {"type": "STRING"},
                            },
                            "required": ["file", "target", "replacement"],
                        },
                    },
                },
                "required": ["summary", "edits"],
            },
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def apply_edits_and_verify(edits: list) -> list:
    backups = {}
    applied = []
    try:
        for edit in edits:
            rel = edit["file"].lstrip("/")
            if not (rel.startswith("skills/") or rel.startswith("rules/")):
                raise ValueError(f"Refusing to edit file outside skills/ or rules/: {rel}")
            full = (ROOT / rel).resolve()
            if not str(full).startswith(str(ROOT.resolve())) or not full.is_file():
                raise ValueError(f"Target file does not exist: {rel}")
            orig = full.read_text(encoding="utf-8")
            if edit["target"] not in orig:
                raise ValueError(f"Target snippet not found in {rel}")
            backups.setdefault(full, orig)
            full.write_text(orig.replace(edit["target"], edit["replacement"], 1), encoding="utf-8")
            applied.append(rel)

        subprocess.run([sys.executable, str(ROOT / "tests" / "test_skills.py")], cwd=ROOT, check=True)
        subprocess.run(["make", "lint"], cwd=ROOT, check=True)
        return applied
    except Exception:
        for full, orig in backups.items():
            full.write_text(orig, encoding="utf-8")
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="Check Apigee release notes and sync updates into apigee-skills.")
    ap.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="Path to release_notes_state.json")
    ap.add_argument("--feed-url", default="", help="Override Atom feed URL or local XML file path")
    ap.add_argument("--since", default="", help="Override last_synced_date (YYYY-MM-DD)")
    ap.add_argument("--include-hybrid", action="store_true", help="Include Apigee hybrid-only release notes")
    ap.add_argument("--check", action="store_true", help="Check for new release notes without modifying files")
    ap.add_argument("--fail-on-new", action="store_true", help="Exit with code 2 if new relevant notes are found")
    ap.add_argument("--mark-synced", action="store_true", help="Update state file to latest release date")
    ap.add_argument("--apply", action="store_true", help="Use Vertex AI Gemini to propose & test skill updates")
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", ""), help="GCP project for Vertex AI")
    ap.add_argument("--location", default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"), help="Vertex region")
    ap.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-pro"), help="Vertex Gemini model")
    ap.add_argument("--json", action="store_true", help="Output report as JSON")
    args = ap.parse_args()

    state_path = pathlib.Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    since_date = args.since or state.get("last_synced_date", "2026-01-01")
    feed_url = args.feed_url or state.get("feed_url", DEFAULT_FEED_URL)

    xml_bytes = fetch_bytes(feed_url)
    entries = parse_atom_feed(xml_bytes, since_date=since_date, include_hybrid=args.include_hybrid)
    relevant = [e for e in entries if e["affected_skills"]]

    report = {
        "since_date": since_date,
        "new_entries_count": len(entries),
        "relevant_entries_count": len(relevant),
        "entries": entries,
        "applied_files": [],
        "ai_summary": "",
    }

    if args.apply and relevant:
        if not args.project:
            ap.error("--apply requires --project (or $GOOGLE_CLOUD_PROJECT)")
        files_to_send = {}
        for e in relevant:
            for sk in e["affected_skills"]:
                for rel in SKILL_FILES.get(sk, []):
                    p = ROOT / rel
                    if p.is_file() and rel not in files_to_send:
                        files_to_send[rel] = p.read_text(encoding="utf-8")
        ai_res = call_vertex_gemini(args.project, args.location, args.model, relevant, files_to_send)
        report["ai_summary"] = ai_res.get("summary", "")
        edits = ai_res.get("edits") or []
        if edits:
            report["applied_files"] = apply_edits_and_verify(edits)

    if (args.mark_synced or args.apply) and entries:
        latest = entries[-1]
        state["last_synced_date"] = latest["date"]
        if latest.get("id"):
            state["last_synced_id"] = latest["id"]
        if latest.get("versions"):
            state["last_synced_version"] = latest["versions"][-1]
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Checked Apigee release notes since {since_date}: "
              f"{len(entries)} new entry/entries ({len(relevant)} affecting skills).")
        for e in entries:
            skills_str = ", ".join(e["affected_skills"]) if e["affected_skills"] else "none"
            vers_str = f" [{', '.join(e['versions'])}]" if e["versions"] else ""
            print(f"  - {e['date']} ({', '.join(e['products'])}){vers_str} -> skills: {skills_str}")
        if report["ai_summary"]:
            print(f"\nAI Sync Summary: {report['ai_summary']}")
        if report["applied_files"]:
            print(f"Updated & verified files: {', '.join(report['applied_files'])}")

    if args.fail_on_new and relevant:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
