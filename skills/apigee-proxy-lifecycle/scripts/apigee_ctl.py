#!/usr/bin/env python3
"""apigee_ctl.py - standalone Apigee X management CLI for the proxy lifecycle (stdlib only).

Works on API proxies (default) and shared flows (--sharedflow). Auth: $APIGEE_TOKEN, else
`gcloud auth print-access-token`. Org/env: --org/--env or $APIGEE_ORG/$APIGEE_ENV.
$APIGEE_API_BASE overrides https://apigee.googleapis.com (testing / data residency endpoints).

Read
  list                                   list proxies (or shared flows)
  info NAME                              revisions + where each is deployed
  deployments [--env ENV]                deployments in the org / one environment
  export NAME --out DIR [--rev N | --env ENV]
                                         download a revision (default: the one deployed in --env,
                                         else the latest) and unzip it into DIR/apiproxy|sharedflowbundle
  diff DIR NAME [--rev N | --env ENV]    unified diff: deployed/selected revision vs local DIR
Write
  import DIR                             validate + zip + import as a new revision (prints revision)
  deploy NAME --rev N --env ENV [--sa SA] [--no-wait]
  update DIR --env ENV [--sa SA] [--no-validate] [--no-wait]
                                         validate -> import new revision -> deploy (override) -> wait READY
  rollback NAME --env ENV [--to-rev N] [--sa SA]
                                         redeploy the previous (or given) revision
  undeploy NAME --env ENV [--rev N]
Config
  product-apply FILE.json [--env ENV]    create or update an API product (replaces "<ENV>" placeholders)
  product-get NAME
  kvm-put MAP KEY VALUE --env ENV        create encrypted env KVM if missing, then upsert the entry
                                         (VALUE "-" reads stdin, so secrets stay out of shell history)
  targetserver-apply FILE.json --env ENV create or update a target server
  flowhook-attach HOOK SHAREDFLOW --env ENV
                                         HOOK = PreProxyFlowHook|PostProxyFlowHook|PreTargetFlowHook|PostTargetFlowHook
Debug
  debug NAME --env ENV [--rev N] [--seconds 60] [--out DIR]
                                         start a debug (trace) session, wait, download transactions as JSON
"""
import argparse
import difflib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def find_validator():
    for cand in (os.path.join(HERE, "..", "..", "apigee-proxy-builder", "scripts", "validate_bundle.py"),
                 os.path.join(HERE, "validate_bundle.py")):
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


# --------------------------------------------------------------------------- HTTP
class Api:
    def __init__(self, org, kind):
        if not org:
            sys.exit("--org (or $APIGEE_ORG) is required")
        self.base = os.environ.get("APIGEE_API_BASE", "https://apigee.googleapis.com").rstrip("/")
        self.org = org
        self.coll = "sharedflows" if kind == "sharedflow" else "apis"
        self.bundle_dir = "sharedflowbundle" if kind == "sharedflow" else "apiproxy"
        self._token = None

    @property
    def token(self):
        if self._token is None:
            self._token = os.environ.get("APIGEE_TOKEN") or subprocess.check_output(
                ["gcloud", "auth", "print-access-token"], text=True).strip()
        return self._token

    def url(self, path, query=None):
        u = f"{self.base}/v1/organizations/{self.org}{path}"
        if query:
            u += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        return u

    def req(self, method, path, query=None, body=None, raw=None, ctype=None, ok404=False, binary=False):
        data, headers = None, {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif raw is not None:
            data, headers["Content-Type"] = raw, ctype
        r = urllib.request.Request(self.url(path, query), data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            if ok404 and e.code == 404:
                return None
            detail = e.read().decode(errors="ignore")
            try:
                detail = json.loads(detail)["error"]["message"]
            except Exception:
                pass
            sys.exit(f"HTTP {e.code} {method} {path}: {detail}")
        if binary:
            return payload
        return json.loads(payload) if payload.strip() else {}

    # -- helpers
    def revisions(self, name):
        d = self.req("GET", f"/{self.coll}/{name}")
        return sorted((d.get("revision") or []), key=int)

    def deployed_revs(self, name, env):
        d = self.req("GET", f"/environments/{env}/{self.coll}/{name}/deployments", ok404=True) or {}
        return sorted({x["revision"] for x in d.get("deployments", [])}, key=int)

    def resolve_rev(self, name, rev=None, env=None):
        if rev:
            return str(rev)
        if env:
            revs = self.deployed_revs(name, env)
            if not revs:
                sys.exit(f"{name} is not deployed in {env}; pass --rev")
            return revs[-1]
        return self.revisions(name)[-1]

    def export_zip(self, name, rev):
        return self.req("GET", f"/{self.coll}/{name}/revisions/{rev}", query={"format": "bundle"}, binary=True)

    def deploy(self, name, rev, env, sa=None, wait=True):
        self.req("POST", f"/environments/{env}/{self.coll}/{name}/revisions/{rev}/deployments",
                 query={"override": "true", "serviceAccount": sa})
        print(f"Deploying {self.coll}/{name} r{rev} to {env}...")
        if not wait:
            return
        state = ""
        for _ in range(90):
            d = self.req("GET", f"/environments/{env}/{self.coll}/{name}/revisions/{rev}/deployments")
            state = d.get("state", "")
            if state == "READY":
                print(f"READY: {name} r{rev} in {env}")
                return
            if state == "ERROR":
                msgs = "; ".join(e.get("message", "") for e in d.get("errors", []))
                sys.exit(f"Deployment ERROR: {msgs}")
            time.sleep(5)
        sys.exit(f"Timed out waiting for READY (last state: {state})")


# --------------------------------------------------------------------------- bundle helpers
def bundle_kind(path):
    for k in ("apiproxy", "sharedflowbundle"):
        if os.path.isdir(os.path.join(path, k)):
            return k
    if os.path.basename(os.path.normpath(path)) in ("apiproxy", "sharedflowbundle"):
        return os.path.basename(os.path.normpath(path))
    sys.exit(f"no apiproxy/ or sharedflowbundle/ under {path}")


def bundle_name(path):
    import xml.etree.ElementTree as ET
    kind = bundle_kind(path)
    d = os.path.join(path, kind)
    manifests = [f for f in os.listdir(d) if f.endswith(".xml")]
    if not manifests:
        sys.exit(f"no root manifest in {d}")
    root = ET.parse(os.path.join(d, manifests[0])).getroot()
    return root.get("name") or manifests[0][:-4], kind


def zip_bundle(path):
    kind = bundle_kind(path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d, _, files in os.walk(os.path.join(path, kind)):
            for f in files:
                if f in (".DS_Store",) or f.endswith(".swp"):
                    continue
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, path))
    return buf.getvalue()


def multipart(field, filename, content):
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def validate(path):
    v = find_validator()
    if not v:
        print("WARN validator not found (apigee-proxy-builder skill missing) - skipping validation")
        return
    rc = subprocess.call([sys.executable, v, path])
    if rc != 0:
        sys.exit("validation failed - fix the errors above or pass --no-validate")


def read_tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            if f == ".DS_Store":
                continue
            full = os.path.join(d, f)
            try:
                out[os.path.relpath(full, root)] = open(full, encoding="utf-8").read().replace("\r\n", "\n")
            except UnicodeDecodeError:
                out[os.path.relpath(full, root)] = "<binary>"
    return out


# --------------------------------------------------------------------------- commands
def cmd_list(api, o):
    d = api.req("GET", f"/{api.coll}")
    items = d.get("proxies") or d.get("sharedFlows") or []
    for p in items:
        print(p.get("name"))


def cmd_info(api, o):
    d = api.req("GET", f"/{api.coll}/{o.name}")
    print(json.dumps({"name": d.get("name"), "revisions": d.get("revision"),
                      "latestRevisionId": d.get("latestRevisionId")}, indent=2))
    deps = api.req("GET", f"/{api.coll}/{o.name}/deployments", ok404=True) or {}
    for x in deps.get("deployments", []):
        print(f"deployed: env={x.get('environment')} rev={x.get('revision')} state={x.get('state', '')}")


def cmd_deployments(api, o):
    path = f"/environments/{o.env}/deployments" if o.env else "/deployments"
    d = api.req("GET", path, query={"sharedFlows": "true" if api.coll == "sharedflows" else None})
    for x in d.get("deployments", []):
        print(f"{x.get('environment', o.env)}\t{x.get('apiProxy') or x.get('sharedFlow', '')}\tr{x.get('revision')}")


def cmd_export(api, o):
    rev = api.resolve_rev(o.name, o.rev, o.env)
    data = api.export_zip(o.name, rev)
    os.makedirs(o.out, exist_ok=True)
    target = os.path.join(o.out, api.bundle_dir)
    if os.path.exists(target) and not o.force:
        sys.exit(f"{target} exists - pass --force to overwrite")
    if os.path.exists(target):
        import shutil
        shutil.rmtree(target)
    zipfile.ZipFile(io.BytesIO(data)).extractall(o.out)
    print(f"Exported {api.coll}/{o.name} r{rev} -> {target}")


def cmd_diff(api, o):
    name = o.name or bundle_name(o.dir)[0]
    rev = api.resolve_rev(name, o.rev, o.env)
    tmp = tempfile.mkdtemp(prefix="apigee-diff-")
    zipfile.ZipFile(io.BytesIO(api.export_zip(name, rev))).extractall(tmp)
    kind = bundle_kind(o.dir)
    remote, local = read_tree(os.path.join(tmp, kind)), read_tree(os.path.join(o.dir, kind))
    changed = 0
    for f in sorted(set(remote) | set(local)):
        a, b = remote.get(f, ""), local.get(f, "")
        if a != b:
            changed += 1
            sys.stdout.writelines(difflib.unified_diff(
                a.splitlines(True), b.splitlines(True), f"r{rev}/{f}" if f in remote else "/dev/null",
                f"local/{f}" if f in local else "/dev/null"))
    print(f"\n{changed} file(s) differ between r{rev} and {o.dir}")


def do_import(api, path, skip_validate=False):
    name, kind = bundle_name(path)
    if (kind == "sharedflowbundle") != (api.coll == "sharedflows"):
        api.coll = "sharedflows" if kind == "sharedflowbundle" else "apis"
    if not skip_validate:
        validate(path)
    body, ctype = multipart("file", f"{name}.zip", zip_bundle(path))
    d = api.req("POST", f"/{api.coll}", query={"name": name, "action": "import"}, raw=body, ctype=ctype)
    rev = str(d.get("revision"))
    print(f"Imported {api.coll}/{name} revision {rev}")
    return name, rev


def cmd_import(api, o):
    do_import(api, o.dir, o.no_validate)


def cmd_deploy(api, o):
    api.deploy(o.name, o.rev, o.env, o.sa, not o.no_wait)


def cmd_update(api, o):
    name, rev = do_import(api, o.dir, o.no_validate)
    before = api.deployed_revs(name, o.env)
    api.deploy(name, rev, o.env, o.sa, not o.no_wait)
    if before:
        print(f"Previously deployed: r{before[-1]}  (rollback: apigee_ctl.py rollback {name} --env {o.env}"
              f"{' --sharedflow' if api.coll == 'sharedflows' else ''})")


def cmd_rollback(api, o):
    current = api.deployed_revs(o.name, o.env)
    if o.to_rev:
        target = str(o.to_rev)
    else:
        if not current:
            sys.exit(f"{o.name} is not deployed in {o.env}")
        older = [r for r in api.revisions(o.name) if int(r) < int(current[-1])]
        if not older:
            sys.exit(f"no revision older than r{current[-1]}")
        target = older[-1]
    print(f"Rolling back {o.name} in {o.env}: r{current[-1] if current else '-'} -> r{target}")
    api.deploy(o.name, target, o.env, o.sa, True)


def cmd_undeploy(api, o):
    revs = [str(o.rev)] if o.rev else api.deployed_revs(o.name, o.env)
    for r in revs:
        api.req("DELETE", f"/environments/{o.env}/{api.coll}/{o.name}/revisions/{r}/deployments")
        print(f"Undeployed {o.name} r{r} from {o.env}")


def load_json(path, env=None):
    txt = open(path).read()
    if env:
        txt = txt.replace("<ENV>", env)
    if "<ENV>" in txt:
        sys.exit(f"{path} still contains <ENV> - pass --env")
    return json.loads(txt)


def cmd_product_apply(api, o):
    p = load_json(o.file, o.env)
    exists = api.req("GET", f"/apiproducts/{urllib.parse.quote(p['name'])}", ok404=True)
    if exists:
        api.req("PUT", f"/apiproducts/{urllib.parse.quote(p['name'])}", body=p)
        print(f"Updated API product '{p['name']}'")
    else:
        api.req("POST", "/apiproducts", body=p)
        print(f"Created API product '{p['name']}'")


def cmd_product_get(api, o):
    print(json.dumps(api.req("GET", f"/apiproducts/{urllib.parse.quote(o.name)}"), indent=2))


def cmd_kvm_put(api, o):
    value = sys.stdin.read().rstrip("\n") if o.value == "-" else o.value
    base = f"/environments/{o.env}/keyvaluemaps"
    maps = api.req("GET", base) or []
    if o.map not in (maps if isinstance(maps, list) else []):
        api.req("POST", base, body={"name": o.map, "encrypted": True})
        print(f"Created encrypted KVM '{o.map}' in {o.env}")
    entry = api.req("GET", f"{base}/{o.map}/entries/{urllib.parse.quote(o.key)}", ok404=True)
    if entry is None:
        api.req("POST", f"{base}/{o.map}/entries", body={"name": o.key, "value": value})
    else:
        api.req("PUT", f"{base}/{o.map}/entries/{urllib.parse.quote(o.key)}", body={"name": o.key, "value": value})
    print(f"Set {o.map}/{o.key} in {o.env}")


def cmd_targetserver_apply(api, o):
    ts = load_json(o.file, o.env)
    base = f"/environments/{o.env}/targetservers"
    if api.req("GET", f"{base}/{ts['name']}", ok404=True):
        api.req("PUT", f"{base}/{ts['name']}", body=ts)
        print(f"Updated target server '{ts['name']}' in {o.env}")
    else:
        api.req("POST", base, body=ts)
        print(f"Created target server '{ts['name']}' in {o.env}")


def cmd_flowhook_attach(api, o):
    api.req("PUT", f"/environments/{o.env}/flowhooks/{o.hook}",
            body={"sharedFlow": o.sharedflow_name, "continueOnError": True})
    print(f"Attached shared flow '{o.sharedflow_name}' to {o.hook} in {o.env}")


def cmd_debug(api, o):
    rev = api.resolve_rev(o.name, o.rev, o.env)
    base = f"/environments/{o.env}/{api.coll}/{o.name}/revisions/{rev}/debugsessions"
    s = api.req("POST", base, body={"timeout": str(max(o.seconds + 30, 60))})
    sid = s.get("name")
    print(f"Debug session {sid} on {o.name} r{rev} - send traffic now ({o.seconds}s)...")
    time.sleep(o.seconds)
    txs = api.req("GET", f"{base}/{sid}/data") or []
    os.makedirs(o.out, exist_ok=True)
    for tx in txs:
        data = api.req("GET", f"{base}/{sid}/data/{tx}")
        with open(os.path.join(o.out, f"{tx}.json"), "w") as f:
            json.dump(data, f, indent=2)
        summarize_tx(tx, data)
    print(f"{len(txs)} transaction(s) saved to {o.out}")


def summarize_tx(tx, data):
    """Print executed policies and errors for one debug transaction."""
    steps, status = [], ""
    for point in data.get("point", []):
        for r in point.get("results", []):
            props = {p.get("name"): p.get("value") for p in
                     (r.get("properties", {}) or {}).get("property", [])}
            if point.get("id") == "Execution" and props.get("stepDefinition-name"):
                flag = " ERROR" if props.get("isError") == "true" or props.get("expression-result") == "false" else ""
                steps.append(props["stepDefinition-name"] + flag)
            if r.get("ActionResult") == "ResponseMessage" and r.get("statusCode"):
                status = r.get("statusCode")
    print(f"- {tx}: status={status or '?'} steps={', '.join(steps) or '-'}")


# --------------------------------------------------------------------------- main
def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--org", default=os.environ.get("APIGEE_ORG"))
    a.add_argument("--sharedflow", action="store_true", help="operate on shared flows instead of API proxies")
    sub = a.add_subparsers(dest="cmd", required=True)
    env_default = os.environ.get("APIGEE_ENV")

    def sp(name, fn, *args, **kw):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        return p

    sp("list", cmd_list)
    p = sp("info", cmd_info); p.add_argument("name")
    p = sp("deployments", cmd_deployments); p.add_argument("--env", default=env_default)
    p = sp("export", cmd_export); p.add_argument("name"); p.add_argument("--out", required=True)
    p.add_argument("--rev"); p.add_argument("--env", default=env_default); p.add_argument("--force", action="store_true")
    p = sp("diff", cmd_diff); p.add_argument("dir"); p.add_argument("name", nargs="?")
    p.add_argument("--rev"); p.add_argument("--env", default=env_default)
    p = sp("import", cmd_import); p.add_argument("dir"); p.add_argument("--no-validate", action="store_true")
    p = sp("deploy", cmd_deploy); p.add_argument("name"); p.add_argument("--rev", required=True)
    p.add_argument("--env", default=env_default, required=env_default is None)
    p.add_argument("--sa", default=os.environ.get("APIGEE_DEPLOY_SA")); p.add_argument("--no-wait", action="store_true")
    p = sp("update", cmd_update); p.add_argument("dir")
    p.add_argument("--env", default=env_default, required=env_default is None)
    p.add_argument("--sa", default=os.environ.get("APIGEE_DEPLOY_SA"))
    p.add_argument("--no-validate", action="store_true"); p.add_argument("--no-wait", action="store_true")
    p = sp("rollback", cmd_rollback); p.add_argument("name")
    p.add_argument("--env", default=env_default, required=env_default is None)
    p.add_argument("--to-rev"); p.add_argument("--sa", default=os.environ.get("APIGEE_DEPLOY_SA"))
    p = sp("undeploy", cmd_undeploy); p.add_argument("name")
    p.add_argument("--env", default=env_default, required=env_default is None); p.add_argument("--rev")
    p = sp("product-apply", cmd_product_apply); p.add_argument("file"); p.add_argument("--env", default=env_default)
    p = sp("product-get", cmd_product_get); p.add_argument("name")
    p = sp("kvm-put", cmd_kvm_put); p.add_argument("map"); p.add_argument("key"); p.add_argument("value")
    p.add_argument("--env", default=env_default, required=env_default is None)
    p = sp("targetserver-apply", cmd_targetserver_apply); p.add_argument("file")
    p.add_argument("--env", default=env_default, required=env_default is None)
    p = sp("flowhook-attach", cmd_flowhook_attach)
    p.add_argument("hook", choices=["PreProxyFlowHook", "PostProxyFlowHook", "PreTargetFlowHook", "PostTargetFlowHook"])
    p.add_argument("sharedflow_name"); p.add_argument("--env", default=env_default, required=env_default is None)
    p = sp("debug", cmd_debug); p.add_argument("name")
    p.add_argument("--env", default=env_default, required=env_default is None)
    p.add_argument("--rev"); p.add_argument("--seconds", type=int, default=60); p.add_argument("--out", default="./debug")

    o = a.parse_args()
    api = Api(o.org, "sharedflow" if o.sharedflow else "proxy")
    o.fn(api, o)


if __name__ == "__main__":
    main()
