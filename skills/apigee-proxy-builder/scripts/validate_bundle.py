#!/usr/bin/env python3
"""Standalone Apigee X bundle validator for API proxies AND shared flows (stdlib only).

Usage:
  python3 validate_bundle.py <proxy-or-sharedflow-dir | apiproxy/ | sharedflowbundle/ | bundle.zip> [--strict] [--json]

Checks:
  * every XML file is well-formed; policy `name` attribute matches its file name
  * root manifest exists; <Policies>/<Resources>/<ProxyEndpoints>/<TargetEndpoints>/<SharedFlows> match files
  * every <Step><Name> refers to an existing policy; every policy is attached somewhere
  * every RouteRule <TargetEndpoint> exists; duplicate BasePaths across ProxyEndpoints
  * jsc:// / oas:// / py:// / xsl:// / java:// / properties:// resources referenced by policies exist
  * security hygiene:
      - (API proxies) no authentication policy attached anywhere -> warning
      - literal secrets in policies / JS (Google API keys, bearer tokens, private keys, password values)
      - KVM <InitialEntries> with literal values -> warning (seed KVMs out of band)
  * AI/MCP-policy sanity:
      - LLMTokenQuota / Quota pairs that share <SharedName> agree on Identifier, LLMModelSource,
        Allow/Interval/TimeUnit, and include exactly one EnforceOnly and one CountOnly/Weight side
      - LLMTokenQuota CountOnly policies have <LLMTokenUsageSource>
      - SanitizeUserPrompt / SanitizeModelResponse have <ModelArmor><TemplateName>
      - SemanticCacheLookup has Embeddings + SimilaritySearch; Populate has SimilaritySearch
      - DataCapture `default` attributes are not message templates ("{...}" is recorded literally)
      - conditions reference parsepayload.<name>.* only for an existing ParsePayload policy
      - Quota/LLMTokenQuota with continueOnError="true" + EnforceOnly need a Step condition that checks
        ratelimit.<name>.failed / exceed.count (otherwise the limit is never enforced)
Exit code 0 = no errors (warnings allowed unless --strict).
"""
import json
import os
import re
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

ERRORS, WARNINGS = [], []
AUTH_TAGS = {"VerifyAPIKey", "OAuthV2", "VerifyJWT", "VerifyJWS", "VerifyIAM", "SAMLAssertion",
             "BasicAuthentication", "HMAC", "AccessControl"}
SECRET_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "Google API key"),
    (re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}"), "Google OAuth access token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |)PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.=]{24,}"), "literal bearer token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "API secret key"),
    (re.compile(r"<(Password|ClientSecret|Secret)>\s*[^{<\s][^<]*</\1>", re.I), "literal password/secret element"),
]


def err(msg):
    ERRORS.append(msg)


def warn(msg):
    WARNINGS.append(msg)


_PARSED = {}


def parse(path):
    """Parse once; report each malformed file a single time."""
    if path not in _PARSED:
        try:
            _PARSED[path] = ET.parse(path).getroot()
        except ET.ParseError as e:
            err(f"XML parse error in {path}: {e}")
            _PARSED[path] = None
    return _PARSED[path]


def text(el, tag):
    if el is None:
        return None
    found = el.find(tag)
    return found.text.strip() if found is not None and found.text else None


def locate_bundle(arg):
    """Return (root_dir, kind) where kind is 'apiproxy' or 'sharedflowbundle'."""
    if arg.endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="bundle-")
        with zipfile.ZipFile(arg) as z:
            z.extractall(tmp)
        arg = tmp
    base = os.path.basename(os.path.normpath(arg))
    if base in ("apiproxy", "sharedflowbundle"):
        return arg, base
    for kind in ("apiproxy", "sharedflowbundle"):
        cand = os.path.join(arg, kind)
        if os.path.isdir(cand):
            return cand, kind
    sys.exit(f"Cannot find apiproxy/ or sharedflowbundle/ under {arg}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    as_json = "--json" in sys.argv
    if not args:
        sys.exit(__doc__)
    root_dir, kind = locate_bundle(args[0])

    # ---- well-formedness of every XML
    for d, _, files in os.walk(root_dir):
        for f in files:
            if f.endswith(".xml"):
                parse(os.path.join(d, f))

    manifests = [f for f in os.listdir(root_dir) if f.endswith(".xml")]
    if len(manifests) != 1:
        err(f"expected exactly one root manifest XML in {kind}/, found {manifests}")
        return report(strict, as_json)
    manifest = parse(os.path.join(root_dir, manifests[0]))
    expected_root = "APIProxy" if kind == "apiproxy" else "SharedFlowBundle"
    if manifest is not None and manifest.tag != expected_root:
        err(f"root manifest {manifests[0]} has <{manifest.tag}>, expected <{expected_root}>")

    pol_dir = os.path.join(root_dir, "policies")
    policies = {}
    if os.path.isdir(pol_dir):
        for f in sorted(os.listdir(pol_dir)):
            if f.endswith(".xml"):
                el = parse(os.path.join(pol_dir, f))
                if el is not None:
                    name = el.get("name")
                    if name != f[:-4]:
                        err(f"policies/{f}: name attribute '{name}' does not match file name")
                    policies[name] = el

    # ---- manifest lists (optional in Apigee, but if present they must agree)
    res_dir = os.path.join(root_dir, "resources")
    if manifest is not None:
        listed = [p.text.strip() for p in manifest.findall("./Policies/Policy") if p.text]
        if listed:
            for p in set(listed) - set(policies):
                err(f"manifest lists policy '{p}' with no policies/{p}.xml")
            for p in set(policies) - set(listed):
                warn(f"policy '{p}' exists but is not listed in the manifest <Policies>")
        listed_res = [r.text.strip() for r in manifest.findall("./Resources/Resource") if r.text]
        for r in listed_res:
            m = re.match(r"(\w+)://(.+)", r)
            if m and not os.path.isfile(os.path.join(res_dir, m.group(1), m.group(2))):
                err(f"manifest lists resource '{r}' but resources/{m.group(1)}/{m.group(2)} is missing")
        if listed_res and os.path.isdir(res_dir):
            for sub in os.listdir(res_dir):
                sd = os.path.join(res_dir, sub)
                if os.path.isdir(sd):
                    for f in os.listdir(sd):
                        if not f.startswith(".") and f"{sub}://{f}" not in listed_res:
                            warn(f"resources/{sub}/{f} is not listed in the manifest <Resources>")

    # ---- endpoints / shared flows
    endpoints = []
    targets = set()
    subdirs = ("proxies", "targets") if kind == "apiproxy" else ("sharedflows",)
    for sub in subdirs:
        d = os.path.join(root_dir, sub)
        if not os.path.isdir(d):
            if sub in ("proxies", "sharedflows"):
                err(f"missing {sub}/ directory")
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".xml"):
                el = parse(os.path.join(d, f))
                if el is not None:
                    endpoints.append((f"{sub}/{f}", el))
                    if sub == "targets":
                        targets.add(el.get("name"))

    attached = set()
    all_conditions = []
    basepaths = {}
    for label, ep in endpoints:
        for step in ep.iter("Step"):
            n = text(step, "Name")
            if n:
                attached.add(n)
                if n not in policies:
                    err(f"{label}: Step references missing policy '{n}'")
            c = text(step, "Condition")
            if c:
                all_conditions.append((label, n, c))
        for c in ep.iter("Condition"):
            if c.text:
                all_conditions.append((label, None, c.text))
        for rr in ep.iter("RouteRule"):
            t = text(rr, "TargetEndpoint")
            if t and t not in targets:
                err(f"{label}: RouteRule '{rr.get('name')}' targets missing TargetEndpoint '{t}'")
        bp = text(ep.find("HTTPProxyConnection"), "BasePath") if ep.tag == "ProxyEndpoint" else None
        if bp:
            if bp in basepaths:
                err(f"{label}: BasePath '{bp}' duplicates {basepaths[bp]}")
            basepaths[bp] = label
        if ep.tag == "ProxyEndpoint" and ep.find("RouteRule") is None:
            warn(f"{label}: ProxyEndpoint has no RouteRule (requests will never reach a target)")
    for p in set(policies) - attached:
        warn(f"policy '{p}' is not attached to any Step")

    # ---- resources referenced by policies
    for name, el in policies.items():
        blob = ET.tostring(el, encoding="unicode")
        for scheme, ref in re.findall(r"\b(jsc|oas|py|xsl|java|properties)://([^<\s\"]+)", blob):
            if not os.path.isfile(os.path.join(res_dir, scheme, ref)):
                err(f"policy '{name}' references {scheme}://{ref} but resources/{scheme}/{ref} is missing")

    security_checks(root_dir, kind, policies, attached)
    ai_checks(policies, all_conditions)
    return report(strict, as_json)


def security_checks(root_dir, kind, policies, attached):
    if kind == "apiproxy":
        auth = [n for n, el in policies.items() if el.tag in AUTH_TAGS and n in attached]
        if not auth:
            warn("no authentication policy (VerifyAPIKey/OAuthV2/VerifyJWT/VerifyIAM/...) is attached - "
                 "the proxy is open to anyone (fine only for intentionally public APIs)")
    for d, _, files in os.walk(root_dir):
        for f in files:
            if not f.endswith((".xml", ".js", ".py", ".properties", ".json")):
                continue
            path = os.path.join(d, f)
            try:
                content = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            rel = os.path.relpath(path, root_dir)
            for rx, label in SECRET_PATTERNS:
                if rx.search(content):
                    err(f"{rel}: looks like a hardcoded {label} - move it to an encrypted KVM or Secret Manager")
    for n, el in policies.items():
        if el.tag == "KeyValueMapOperations":
            ie = el.find("InitialEntries")
            if ie is not None and len(list(ie)):
                warn(f"KVM policy '{n}' seeds <InitialEntries> from the bundle - seed KVMs out of band instead")


def ai_checks(policies, conditions):
    by_type = {}
    for n, el in policies.items():
        by_type.setdefault(el.tag, []).append((n, el))

    # Shared-counter pairs (Quota + LLMTokenQuota)
    groups = {}
    for tag in ("Quota", "LLMTokenQuota"):
        for n, el in by_type.get(tag, []):
            sn = text(el, "SharedName")
            if sn:
                groups.setdefault((tag, sn), []).append((n, el))
    for (tag, sn), members in groups.items():
        if len(members) < 2:
            continue

        def sig(el):
            allow = el.find("Allow")
            return (
                (el.find("Identifier").get("ref") if el.find("Identifier") is not None else None),
                text(el, "LLMModelSource"),
                (allow.get("countRef"), allow.get("count")) if allow is not None else None,
                (el.find("Interval").get("ref") if el.find("Interval") is not None else None),
                (el.find("TimeUnit").get("ref") if el.find("TimeUnit") is not None else None),
                el.get("type"),
            )
        sigs = {n: sig(el) for n, el in members}
        if len(set(sigs.values())) > 1:
            err(f"{tag} SharedName '{sn}': members disagree on Identifier/LLMModelSource/Allow/Interval/TimeUnit/type {sigs} "
                f"- the enforcer would read a counter nobody writes")
        enforce = [n for n, el in members if (text(el, "EnforceOnly") or "").lower() == "true"]
        count = [n for n, el in members if (text(el, "CountOnly") or "").lower() == "true" or el.find("Weight") is not None]
        if not enforce:
            warn(f"{tag} SharedName '{sn}': no EnforceOnly member")
        if not count:
            warn(f"{tag} SharedName '{sn}': no CountOnly/Weight member")
        for n, el in members:
            if (text(el, "EnforceOnly") or "").lower() != "true" and el.find("Weight") is None \
                    and (text(el, "CountOnly") or "").lower() != "true" and tag == "Quota":
                warn(f"Quota '{n}' shares '{sn}' without EnforceOnly/Weight - it silently adds weight 1 per call")

    for n, el in by_type.get("LLMTokenQuota", []):
        if (text(el, "CountOnly") or "").lower() == "true" and text(el, "LLMTokenUsageSource") is None:
            warn(f"LLMTokenQuota '{n}' is CountOnly without <LLMTokenUsageSource> (default is Gemini usageMetadata)")

    # Swallowed enforcers need an explicit RaiseFault
    cond_blob = " ".join(c for _, _, c in conditions)
    for tag in ("Quota", "LLMTokenQuota"):
        for n, el in by_type.get(tag, []):
            if el.get("continueOnError") == "true" and (text(el, "EnforceOnly") or "").lower() == "true":
                if f"ratelimit.{n}." not in cond_blob:
                    err(f"{tag} '{n}' is EnforceOnly with continueOnError=true but no Step condition checks "
                        f"ratelimit.{n}.failed/exceed.count - the limit is never enforced")

    for tag in ("SanitizeUserPrompt", "SanitizeModelResponse"):
        for n, el in by_type.get(tag, []):
            if el.find("./ModelArmor/TemplateName") is None:
                err(f"{tag} '{n}' missing <ModelArmor><TemplateName>")
            if el.find("Source") is not None:
                err(f"{tag} '{n}' has <Source> - not a valid element (use UserPromptSource / LLMResponseSource)")

    for n, el in by_type.get("SemanticCacheLookup", []):
        if el.find("./Embeddings/VertexAI/URL") is None or el.find("./SimilaritySearch/VertexAI") is None:
            err(f"SemanticCacheLookup '{n}' needs <Embeddings><VertexAI><URL> and <SimilaritySearch><VertexAI>")
        if el.find("CacheConfig") is not None:
            err(f"SemanticCacheLookup '{n}' uses <CacheConfig> - not valid Apigee X syntax")
    for n, el in by_type.get("SemanticCachePopulate", []):
        if el.find("./SimilaritySearch/VertexAI/URL") is None:
            err(f"SemanticCachePopulate '{n}' needs <SimilaritySearch><VertexAI><URL> (…:upsertDatapoints)")

    for n, el in by_type.get("DataCapture", []):
        for c in el.iter("Collect"):
            d = c.get("default") or ""
            if "{" in d:
                err(f"DataCapture '{n}': default=\"{d}\" is recorded literally (not a message template)")

    pp_names = {n for n, _ in by_type.get("ParsePayload", [])}
    for label, step, c in conditions:
        for ref in set(re.findall(r"parsepayload\.([A-Za-z0-9_-]+)\.", c)):
            if ref not in pp_names:
                err(f"{label}: condition references parsepayload.{ref}.* but no ParsePayload policy '{ref}' exists")


def report(strict=False, as_json=False):
    ok = not ERRORS and not (strict and WARNINGS)
    if as_json:
        print(json.dumps({"ok": ok, "errors": ERRORS, "warnings": WARNINGS}, indent=2))
    else:
        for w in WARNINGS:
            print(f"WARN  {w}")
        for e in ERRORS:
            print(f"ERROR {e}")
        print(f"\n{'PASS' if ok else 'FAIL'}: {len(ERRORS)} error(s), {len(WARNINGS)} warning(s)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
