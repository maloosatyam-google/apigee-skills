#!/usr/bin/env python3
"""bundle_edit.py - structural, manifest-safe edits to an existing Apigee proxy / shared flow bundle (stdlib only).

Use it to modify an exported bundle without hand-editing endpoint XML (comments are preserved; files that are
modified are re-indented). Every command accepts a bundle dir (containing apiproxy/ or sharedflowbundle/).

  outline   DIR                                   print endpoints, flows, steps and conditions
  add-policy DIR POLICY.xml [--force]             copy a policy file into policies/ and register it in the manifest
  add-resource DIR FILE --type jsc|py|xsl|oas|properties|java
  attach    DIR POLICY --endpoint proxies/default.xml --flow PreFlow|PostFlow|PostClientFlow|<FlowName>|DefaultFaultRule
            --phase Request|Response [--condition EXPR] [--before STEP | --after STEP | --first]
  detach    DIR POLICY [--endpoint FILE] [--flow F] [--phase P]   remove matching Steps (all by default)
  remove-policy DIR POLICY                        detach everywhere, delete the file, unregister it
  add-flow  DIR NAME --endpoint FILE --condition EXPR [--before FLOW | --first]   add an empty conditional flow
  set-basepath DIR /new/path [--endpoint proxies/default.xml]
  set-target-url DIR URL [--endpoint targets/default.xml]
  sync-manifest DIR                                rebuild <Policies>/<Resources>/<ProxyEndpoints>/<TargetEndpoints>
  bump-revision DIR                                increment the manifest revision attribute (cosmetic)

Typical update loop: apigee_ctl.py export -> bundle_edit.py ... -> validate_bundle.py -> apigee_ctl.py diff -> update
"""
import argparse
import os
import shutil
import sys
import xml.etree.ElementTree as ET

DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


# --------------------------------------------------------------------------- io
def root_of(d):
    for k in ("apiproxy", "sharedflowbundle"):
        if os.path.isdir(os.path.join(d, k)):
            return os.path.join(d, k), k
    b = os.path.basename(os.path.normpath(d))
    if b in ("apiproxy", "sharedflowbundle"):
        return d, b
    sys.exit(f"no apiproxy/ or sharedflowbundle/ under {d}")


def load(path):
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(path, parser)


def save(tree, path):
    ET.indent(tree, space="  ")
    with open(path, "w", encoding="utf-8") as f:
        f.write(DECL)
        f.write(ET.tostring(tree.getroot(), encoding="unicode"))
        f.write("\n")


def manifest_path(root):
    m = [f for f in os.listdir(root) if f.endswith(".xml")]
    if len(m) != 1:
        sys.exit(f"expected one root manifest in {root}, found {m}")
    return os.path.join(root, m[0])


def endpoint_files(root, kind):
    subs = ("proxies", "targets") if kind == "apiproxy" else ("sharedflows",)
    out = []
    for s in subs:
        d = os.path.join(root, s)
        if os.path.isdir(d):
            out += [os.path.join(s, f) for f in sorted(os.listdir(d)) if f.endswith(".xml")]
    return out


def default_endpoint(root, kind):
    return "proxies/default.xml" if kind == "apiproxy" else "sharedflows/default.xml"


# --------------------------------------------------------------------------- flow lookup
def find_flow(ep, flow, phase, create=True):
    """Return the <Request>/<Response> container element for flow+phase."""
    if flow == "DefaultFaultRule":
        dfr = ep.find("DefaultFaultRule")
        if dfr is None:
            if not create:
                return None
            dfr = ET.SubElement(ep, "DefaultFaultRule", name="default-fault")
            ET.SubElement(dfr, "AlwaysEnforce").text = "true"
        return dfr  # Steps live directly under DefaultFaultRule
    if ep.tag == "SharedFlow":
        return ep  # Steps live directly under <SharedFlow>
    if flow in ("PreFlow", "PostFlow", "PostClientFlow"):
        f = ep.find(flow)
        if f is None:
            if not create:
                return None
            f = ET.Element(flow, name=flow) if flow != "PostClientFlow" else ET.Element(flow)
            ep.insert(0, f)
    else:
        f = None
        flows = ep.find("Flows")
        if flows is not None:
            f = next((x for x in flows.findall("Flow") if x.get("name") == flow), None)
        if f is None:
            sys.exit(f"conditional flow '{flow}' not found (create it with add-flow)")
    c = f.find(phase)
    if c is None:
        if not create:
            return None
        c = ET.SubElement(f, phase)
    return c


def make_step(name, condition=None):
    s = ET.Element("Step")
    ET.SubElement(s, "Name").text = name
    if condition:
        ET.SubElement(s, "Condition").text = condition
    return s


def step_name(s):
    n = s.find("Name")
    return n.text.strip() if n is not None and n.text else None


# --------------------------------------------------------------------------- commands
def cmd_outline(o):
    root, kind = root_of(o.dir)
    m = load(manifest_path(root)).getroot()
    print(f"{m.tag} name={m.get('name')} revision={m.get('revision')}")
    for rel in endpoint_files(root, kind):
        ep = load(os.path.join(root, rel)).getroot()
        extra = ""
        bp = ep.find("./HTTPProxyConnection/BasePath")
        url = ep.find("./HTTPTargetConnection/URL")
        if bp is not None:
            extra = f" basepath={bp.text}"
        if url is not None:
            extra = f" url={url.text}"
        print(f"\n[{rel}] <{ep.tag} name={ep.get('name')}>{extra}")

        def dump(label, container):
            if container is None:
                return
            steps = container.findall("Step")
            if steps:
                print(f"  {label}:")
                for s in steps:
                    c = s.find("Condition")
                    print(f"    - {step_name(s)}" + (f"   [if {c.text.strip()}]" if c is not None and c.text else ""))
        if ep.tag == "SharedFlow":
            dump("Steps", ep)
            continue
        for fl in ("PreFlow",):
            for ph in ("Request", "Response"):
                dump(f"{fl}.{ph}", find_flow(ep, fl, ph, create=False))
        for f in ep.findall("./Flows/Flow"):
            cond = f.find("Condition")
            print(f"  Flow '{f.get('name')}'" + (f" [if {cond.text.strip()}]" if cond is not None and cond.text else ""))
            for ph in ("Request", "Response"):
                dump(f"  {ph}", f.find(ph))
        for fl in ("PostFlow", "PostClientFlow"):
            for ph in ("Request", "Response"):
                dump(f"{fl}.{ph}", find_flow(ep, fl, ph, create=False))
        for fr in ep.findall("./FaultRules/FaultRule"):
            dump(f"FaultRule '{fr.get('name')}'", fr)
        dump("DefaultFaultRule", ep.find("DefaultFaultRule"))
        for rr in ep.findall("RouteRule"):
            c = rr.find("Condition")
            t = rr.find("TargetEndpoint")
            print(f"  RouteRule '{rr.get('name')}' -> {t.text if t is not None else '(none)'}"
                  + (f" [if {c.text.strip()}]" if c is not None and c.text else ""))


def cmd_add_policy(o):
    root, _ = root_of(o.dir)
    el = load(o.policy).getroot()
    name = el.get("name")
    if not name:
        sys.exit("policy XML has no name attribute")
    dest = os.path.join(root, "policies", f"{name}.xml")
    if os.path.exists(dest) and not o.force:
        sys.exit(f"{dest} exists - pass --force to replace")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(o.policy, dest)
    sync_manifest(root)
    print(f"Added policy {name}")


def cmd_add_resource(o):
    root, _ = root_of(o.dir)
    dest = os.path.join(root, "resources", o.type, os.path.basename(o.file))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(o.file, dest)
    sync_manifest(root)
    print(f"Added resource {o.type}://{os.path.basename(o.file)}")


def cmd_attach(o):
    root, kind = root_of(o.dir)
    if not os.path.isfile(os.path.join(root, "policies", f"{o.policy}.xml")):
        sys.exit(f"policies/{o.policy}.xml not found - add-policy first")
    rel = o.endpoint or default_endpoint(root, kind)
    path = os.path.join(root, rel)
    tree = load(path)
    container = find_flow(tree.getroot(), o.flow, o.phase)
    steps = container.findall("Step")
    new = make_step(o.policy, o.condition)
    children = list(container)
    if o.first:
        idx = children.index(steps[0]) if steps else len(children)
    elif o.before or o.after:
        ref = o.before or o.after
        match = next((s for s in steps if step_name(s) == ref), None)
        if match is None:
            sys.exit(f"step '{ref}' not found in {rel} {o.flow}.{o.phase}")
        idx = children.index(match) + (1 if o.after else 0)
    else:
        idx = (children.index(steps[-1]) + 1) if steps else len(children)
    container.insert(idx, new)
    save(tree, path)
    print(f"Attached {o.policy} to {rel} {o.flow}{'' if o.flow == 'DefaultFaultRule' else '.' + o.phase}")


def detach_everywhere(root, kind, policy, endpoint=None, flow=None, phase=None):
    removed = 0
    for rel in endpoint_files(root, kind):
        if endpoint and rel != endpoint:
            continue
        path = os.path.join(root, rel)
        tree = load(path)
        changed = False
        for parent in tree.getroot().iter():
            for s in list(parent.findall("Step")):
                if step_name(s) != policy:
                    continue
                if phase and parent.tag != phase:
                    continue
                if flow:
                    # parent is <Request>/<Response>; its parent is the flow - check by walking
                    owner = next((f for f in tree.getroot().iter() if parent in list(f)), None)
                    oname = owner.get("name") if owner is not None else None
                    if owner is None or (owner.tag != flow and oname != flow):
                        continue
                parent.remove(s)
                removed += 1
                changed = True
        if changed:
            save(tree, path)
    return removed


def cmd_detach(o):
    root, kind = root_of(o.dir)
    n = detach_everywhere(root, kind, o.policy, o.endpoint, o.flow, o.phase)
    print(f"Removed {n} step(s) for {o.policy}")


def cmd_remove_policy(o):
    root, kind = root_of(o.dir)
    n = detach_everywhere(root, kind, o.policy)
    p = os.path.join(root, "policies", f"{o.policy}.xml")
    if os.path.exists(p):
        os.remove(p)
    sync_manifest(root)
    print(f"Removed policy {o.policy} ({n} step(s) detached)")


def cmd_add_flow(o):
    root, kind = root_of(o.dir)
    rel = o.endpoint or default_endpoint(root, kind)
    path = os.path.join(root, rel)
    tree = load(path)
    ep = tree.getroot()
    flows = ep.find("Flows")
    if flows is None:
        flows = ET.Element("Flows")
        pre = ep.find("PreFlow")
        ep.insert(list(ep).index(pre) + 1 if pre is not None else 0, flows)
    if any(f.get("name") == o.name for f in flows.findall("Flow")):
        sys.exit(f"flow '{o.name}' already exists")
    f = ET.Element("Flow", name=o.name)
    ET.SubElement(f, "Request")
    ET.SubElement(f, "Response")
    ET.SubElement(f, "Condition").text = o.condition
    existing = flows.findall("Flow")
    if o.first and existing:
        flows.insert(list(flows).index(existing[0]), f)
    elif o.before:
        ref = next((x for x in existing if x.get("name") == o.before), None)
        if ref is None:
            sys.exit(f"flow '{o.before}' not found")
        flows.insert(list(flows).index(ref), f)
    else:
        flows.append(f)
    save(tree, path)
    print(f"Added flow '{o.name}' to {rel} (only the FIRST matching conditional flow runs - check ordering)")


def cmd_set_basepath(o):
    root, _ = root_of(o.dir)
    rel = o.endpoint or "proxies/default.xml"
    path = os.path.join(root, rel)
    tree = load(path)
    bp = tree.getroot().find("./HTTPProxyConnection/BasePath")
    if bp is None:
        sys.exit(f"no HTTPProxyConnection/BasePath in {rel}")
    old, bp.text = bp.text, o.basepath
    save(tree, path)
    mp = manifest_path(root)
    mt = load(mp)
    mb = mt.getroot().find("BasePaths")
    if mb is not None:
        mb.text = o.basepath
        save(mt, mp)
    print(f"BasePath {old} -> {o.basepath}")


def cmd_set_target_url(o):
    root, _ = root_of(o.dir)
    rel = o.endpoint or "targets/default.xml"
    path = os.path.join(root, rel)
    tree = load(path)
    u = tree.getroot().find("./HTTPTargetConnection/URL")
    if u is None:
        sys.exit(f"no HTTPTargetConnection/URL in {rel} (LoadBalancer targets: edit TargetServers instead)")
    old, u.text = u.text, o.url
    save(tree, path)
    print(f"Target URL {old} -> {o.url}")


def sync_manifest(root):
    mp = manifest_path(root)
    tree = load(mp)
    m = tree.getroot()
    kind = "apiproxy" if m.tag == "APIProxy" else "sharedflowbundle"

    def replace(tag, child, values):
        el = m.find(tag)
        if el is None:
            el = ET.SubElement(m, tag)
        for c in list(el):
            el.remove(c)
        for v in values:
            ET.SubElement(el, child).text = v
    pol = os.path.join(root, "policies")
    replace("Policies", "Policy", sorted(f[:-4] for f in os.listdir(pol) if f.endswith(".xml")) if os.path.isdir(pol) else [])
    res, resources = os.path.join(root, "resources"), []
    if os.path.isdir(res):
        for t in sorted(os.listdir(res)):
            if os.path.isdir(os.path.join(res, t)):
                resources += [f"{t}://{f}" for f in sorted(os.listdir(os.path.join(res, t))) if not f.startswith(".")]
    replace("Resources", "Resource", resources)
    if kind == "apiproxy":
        for sub, tag, child in (("proxies", "ProxyEndpoints", "ProxyEndpoint"), ("targets", "TargetEndpoints", "TargetEndpoint")):
            d = os.path.join(root, sub)
            names = [load(os.path.join(d, f)).getroot().get("name") for f in sorted(os.listdir(d)) if f.endswith(".xml")] if os.path.isdir(d) else []
            replace(tag, child, names)
    else:
        d = os.path.join(root, "sharedflows")
        names = [f[:-4] for f in sorted(os.listdir(d)) if f.endswith(".xml")] if os.path.isdir(d) else []
        replace("SharedFlows", "SharedFlow", names)
    save(tree, mp)


def cmd_sync_manifest(o):
    root, _ = root_of(o.dir)
    sync_manifest(root)
    print("Manifest synchronized")


def cmd_bump_revision(o):
    root, _ = root_of(o.dir)
    mp = manifest_path(root)
    t = load(mp)
    r = int(t.getroot().get("revision", "1")) + 1
    t.getroot().set("revision", str(r))
    save(t, mp)
    print(f"revision -> {r}")


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = a.add_subparsers(dest="cmd", required=True)

    def sp(n, fn):
        p = s.add_parser(n)
        p.set_defaults(fn=fn)
        p.add_argument("dir")
        return p
    sp("outline", cmd_outline)
    p = sp("add-policy", cmd_add_policy); p.add_argument("policy"); p.add_argument("--force", action="store_true")
    p = sp("add-resource", cmd_add_resource); p.add_argument("file")
    p.add_argument("--type", required=True, choices=["jsc", "py", "xsl", "oas", "properties", "java", "wsdl", "xsd"])
    p = sp("attach", cmd_attach); p.add_argument("policy"); p.add_argument("--endpoint")
    p.add_argument("--flow", default="PreFlow"); p.add_argument("--phase", default="Request", choices=["Request", "Response"])
    p.add_argument("--condition")
    g = p.add_mutually_exclusive_group(); g.add_argument("--before"); g.add_argument("--after"); g.add_argument("--first", action="store_true")
    p = sp("detach", cmd_detach); p.add_argument("policy"); p.add_argument("--endpoint"); p.add_argument("--flow")
    p.add_argument("--phase", choices=["Request", "Response"])
    p = sp("remove-policy", cmd_remove_policy); p.add_argument("policy")
    p = sp("add-flow", cmd_add_flow); p.add_argument("name"); p.add_argument("--endpoint"); p.add_argument("--condition", required=True)
    g = p.add_mutually_exclusive_group(); g.add_argument("--before"); g.add_argument("--first", action="store_true")
    p = sp("set-basepath", cmd_set_basepath); p.add_argument("basepath"); p.add_argument("--endpoint")
    p = sp("set-target-url", cmd_set_target_url); p.add_argument("url"); p.add_argument("--endpoint")
    sp("sync-manifest", cmd_sync_manifest)
    sp("bump-revision", cmd_bump_revision)
    o = a.parse_args()
    o.fn(o)


if __name__ == "__main__":
    main()
