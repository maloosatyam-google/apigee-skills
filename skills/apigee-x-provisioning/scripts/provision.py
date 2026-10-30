#!/usr/bin/env python3
"""provision.py - provision Apigee X (no VPC peering) with the bundled Terraform module (stdlib only).

Defaults: billing PAYG · one environment "dev" of type INTERMEDIATE · Analytics add-on enabled · envgroup
"dev-group" · external access through PSC + global external HTTPS LB with a Google-managed certificate
(hostname <group>.<lb-ip>.nip.io unless you pass --hostname).

  provision.py plan  --project my-proj --region us-central1                 # review the plan (no changes)
  provision.py apply --project my-proj --region us-central1                 # create (asks for confirmation)
  provision.py apply --project my-proj --region us-central1 --sample        # + deploy httpbin sample proxy
  provision.py apply ... --env dev --env prod:COMPREHENSIVE --hostname prod-group=api.example.com
  provision.py output                                                       # show outputs / rewrite apigee-x.env

Actions: plan | apply | output | sample | destroy
Options
  --project ID  --region R                    required for plan/apply
  --workdir DIR                               where the Terraform root, tfvars and state live (default ./apigee-x-infra)
  --billing PAYG|SUBSCRIPTION                 default PAYG
  --env NAME[:TYPE][:noanalytics][@GROUP]     repeatable; default dev:INTERMEDIATE (Analytics on, group <name>-group)
  --hostname GROUP=HOST[,HOST]                repeatable; envgroup hostnames (default nip.io for external routing)
  --analytics-region R                        default --region
  --instance NAME                             default --region
  --routing external|internal|none            default external
  --network NET --subnet SUBNET               PSC NEG / endpoint network (internal routing requires both)
  --ssl-certificate SELF_LINK                 use an existing certificate instead of a Google-managed one
  --cmek                                      use Cloud KMS CMEK instead of Google-managed encryption keys (default GMEK)
  --control-plane-location LOC --consumer-data-region R   data residency
  --state-bucket BUCKET [--state-prefix P]    GCS backend for shared state (recommended for teams/CI)
  --auto-approve                              apply without the confirmation prompt
  --sample                                    after apply, deploy /httpbin -> httpbin.org to the first environment
Env: TERRAFORM (binary, default terraform), APIGEE_TOKEN (sample deploy), GOOGLE_* credentials for Terraform.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(os.path.dirname(HERE), "terraform")
SKILLS = os.path.dirname(os.path.dirname(HERE))
TYPES = ("BASE", "INTERMEDIATE", "COMPREHENSIVE")


def parse_env(spec):
    """dev | dev:INTERMEDIATE | test:BASE:noanalytics | prod:COMPREHENSIVE@prod-group"""
    group = None
    if "@" in spec:
        spec, group = spec.split("@", 1)
    parts = spec.split(":")
    name, cfg = parts[0], {}
    for p in parts[1:]:
        if p.upper() in TYPES:
            cfg["type"] = p.upper()
        elif p.lower() == "noanalytics":
            cfg["analytics"] = False
        else:
            raise argparse.ArgumentTypeError(f"bad --env part '{p}' (types: {', '.join(TYPES)}, or noanalytics)")
    if group:
        cfg["envgroup"] = group
    if not name:
        raise argparse.ArgumentTypeError("--env needs a name")
    return name, cfg


def tfvars(o):
    v = {"project_id": o.project, "region": o.region, "billing_type": o.billing,
         "environments": dict(o.env or [("dev", {})]), "routing": o.routing}
    for k, attr in (("analytics_region", "analytics_region"), ("instance_name", "instance"),
                    ("network", "network"), ("subnetwork", "subnet"), ("ssl_certificate", "ssl_certificate"),
                    ("control_plane_location", "control_plane_location"),
                    ("consumer_data_region", "consumer_data_region")):
        if getattr(o, attr):
            v[k] = getattr(o, attr)
    if o.cmek:
        v["use_cmek"] = True
    if o.hostname:
        v["envgroup_hostnames"] = {}
        for h in o.hostname:
            g, hosts = h.split("=", 1)
            v["envgroup_hostnames"][g] = [x.strip() for x in hosts.split(",") if x.strip()]
    return v


class TF:
    def __init__(self, workdir):
        self.bin = os.environ.get("TERRAFORM", "terraform")
        self.dir = workdir
        if not shutil.which(self.bin) and not os.path.isfile(self.bin):
            sys.exit(f"'{self.bin}' not found - install Terraform >= 1.5 (https://developer.hashicorp.com/terraform/install) "
                     "or set TERRAFORM=/path/to/terraform")

    def run(self, *args, capture=False):
        cmd = [self.bin, f"-chdir={self.dir}", *args]
        print("$ " + " ".join(cmd), flush=True)
        p = subprocess.run(cmd, text=True, capture_output=capture)
        if p.returncode != 0:
            if capture:
                sys.stderr.write(p.stdout + p.stderr)
            sys.exit(p.returncode)
        return p.stdout if capture else ""


def prepare(o):
    os.makedirs(o.workdir, exist_ok=True)
    for f in os.listdir(MODULE):        # refresh module files; never touch state or tfvars
        if f.endswith(".tf"):
            shutil.copyfile(os.path.join(MODULE, f), os.path.join(o.workdir, f))
    if o.state_bucket:
        with open(os.path.join(o.workdir, "backend.tf"), "w") as f:
            f.write('terraform {\n  backend "gcs" {\n    bucket = "%s"\n    prefix = "%s"\n  }\n}\n'
                    % (o.state_bucket, o.state_prefix or f"apigee-x/{o.project}"))
    if o.action in ("plan", "apply"):
        if not (o.project and o.region):
            sys.exit("--project and --region are required for plan/apply")
        with open(os.path.join(o.workdir, "terraform.tfvars.json"), "w") as f:
            json.dump(tfvars(o), f, indent=2)
        print(f"wrote {o.workdir}/terraform.tfvars.json")


def write_env(o, tf):
    out = json.loads(tf.run("output", "-json", capture=True) or "{}")
    if "env_file" in out:
        path = os.path.join(o.workdir, "apigee-x.env")
        with open(path, "w") as f:
            f.write(out["env_file"]["value"] + "\n")
        print(f"\nwrote {path} - run: source {path}")
    for k in ("org", "billing_type", "environments", "ingress_ip", "next_steps"):
        if k in out:
            val = out[k]["value"]
            print(f"\n{k}:\n{val if isinstance(val, str) else json.dumps(val, indent=2)}")
    return out


def sample(o, out):
    env = list(out["environments"]["value"])[0]
    org = out["org"]["value"]
    base = out.get("apigee_api_base", {}).get("value", "https://apigee.googleapis.com")
    tmp = tempfile.mkdtemp(prefix="apigee-sample-")
    scaffold = os.path.join(SKILLS, "apigee-api-proxy", "scripts", "scaffold_api_proxy.py")
    ctl = os.path.join(SKILLS, "apigee-proxy-lifecycle", "scripts", "apigee_ctl.py")
    subprocess.run([sys.executable, scaffold, "--out", f"{tmp}/httpbin", "--name", "httpbin", "--basepath", "/httpbin",
                    "--target", "https://httpbin.org", "--auth", "none", "--spike-arrest", "20ps"], check=True)
    rc = subprocess.call([sys.executable, ctl, "--org", org, "update", f"{tmp}/httpbin", "--env", env],
                         env=dict(os.environ, APIGEE_API_BASE=base))
    if rc:
        sys.exit(rc)
    host = out["environments"]["value"][env]["hostnames"][0]
    print(f"\nSample deployed. Once the certificate is ACTIVE:  curl -i https://{host}/httpbin/headers")


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("action", choices=["plan", "apply", "output", "sample", "destroy"])
    a.add_argument("--project")
    a.add_argument("--region")
    a.add_argument("--workdir", default="apigee-x-infra")
    a.add_argument("--billing", choices=["PAYG", "SUBSCRIPTION"], default="PAYG")
    a.add_argument("--env", action="append", type=parse_env)
    a.add_argument("--hostname", action="append")
    a.add_argument("--analytics-region")
    a.add_argument("--instance")
    a.add_argument("--routing", choices=["external", "internal", "none"], default="external")
    a.add_argument("--network")
    a.add_argument("--subnet")
    a.add_argument("--ssl-certificate")
    a.add_argument("--cmek", action="store_true")
    a.add_argument("--control-plane-location")
    a.add_argument("--consumer-data-region")
    a.add_argument("--state-bucket")
    a.add_argument("--state-prefix")
    a.add_argument("--auto-approve", action="store_true")
    a.add_argument("--sample", action="store_true")
    o = a.parse_args()
    if o.hostname and any("=" not in h for h in o.hostname):
        a.error("--hostname expects GROUP=HOST[,HOST]")
    if o.routing == "internal" and not (o.network and o.subnet):
        a.error("--routing internal needs --network and --subnet")

    prepare(o)
    tf = TF(o.workdir)
    if o.action in ("plan", "apply", "destroy"):
        tf.run("init", "-input=false", "-upgrade=false")
    if o.action == "plan":
        tf.run("plan", "-input=false")
    elif o.action == "apply":
        tf.run("plan", "-input=false", "-out=tfplan")
        if not o.auto_approve:
            if input("\nApply this plan? Organization + instance creation takes 30-90 min and starts billing. "
                     "Type 'yes': ").strip() != "yes":
                sys.exit("aborted")
        tf.run("apply", "-input=false", "tfplan")
        out = write_env(o, tf)
        if o.sample:
            sample(o, out)
    elif o.action == "output":
        write_env(o, tf)
    elif o.action == "sample":
        sample(o, write_env(o, tf))
    elif o.action == "destroy":
        sys.exit("Refusing to destroy automatically. Apigee org deletion and CMEK keys (prevent_destroy) need a "
                 "deliberate, reviewed change - see SKILL.md 'Teardown'.")


if __name__ == "__main__":
    main()
