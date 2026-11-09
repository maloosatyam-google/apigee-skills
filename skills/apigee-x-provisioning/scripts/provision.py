#!/usr/bin/env python3
"""provision.py - Full-fledged Apigee X Terraform provisioner (paid orgs only; stdlib Python 3.9+).

Covers all paid provisioning options on:
  https://docs.cloud.google.com/apigee/docs/api-platform/get-started/provisioning-options
Note: Evaluation organizations are NOT supported (PAYG or SUBSCRIPTION only).

Defaults (press Enter in wizard/interactive mode, or omit optional flags):
  - Billing       : PAYG (Pay-as-you-go)
  - Environment   : dev, type INTERMEDIATE, Analytics add-on enabled, envgroup dev-group
  - Networking    : PSC (Private Service Connect, without VPC peering)
  - Encryption    : Google-managed encryption keys (GMEK)
  - Certificate   : Google-managed SSL certificate
  - Data Residency: Disabled (global control plane)
  - Domain        : <envgroup>.<lb-ip-with-dashes>.nip.io

Usage:
  provision.py wizard                                                       # interactive Q&A -> plan / apply / export
  provision.py plan   --project my-proj --region us-central1                # review plan with defaults
  provision.py apply  --project my-proj --region us-central1 --sample       # provision + deploy /httpbin sample
  provision.py plan   --project my-proj --region us-central1 -i             # prompt interactively for options
  provision.py export --project my-proj --region asia-south1 --workdir ./out # write standalone .tf + terraform.tfvars
  provision.py output                                                       # print outputs & rewrite apigee-x.env

Actions: wizard | plan | apply | export | output | sample | destroy
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
ADDON_KEYS = ("api_security", "advanced_api_ops", "integration", "connectors", "monetization")


def parse_env(spec):
    """dev | dev:INTERMEDIATE | test:BASE:noanalytics | prod:COMPREHENSIVE@prod-group"""
    group = None
    if "@" in spec:
        spec, group = spec.split("@", 1)
    parts = spec.split(":")
    name, cfg = parts[0].strip(), {}
    for p in parts[1:]:
        if p.upper() in TYPES:
            cfg["type"] = p.upper()
        elif p.lower() == "noanalytics":
            cfg["analytics"] = False
        elif p.lower() == "analytics":
            cfg["analytics"] = True
        else:
            raise argparse.ArgumentTypeError(f"bad --env part '{p}' (types: {', '.join(TYPES)}, or noanalytics)")
    if group:
        cfg["envgroup"] = group.strip()
    if not name:
        raise argparse.ArgumentTypeError("--env needs a name")
    return name, cfg


def parse_southbound_psc(spec):
    """NAME=projects/.../serviceAttachments/...[@REGION]"""
    if "=" not in spec:
        raise argparse.ArgumentTypeError("--southbound-psc expects NAME=SERVICE_ATTACHMENT[@REGION]")
    name, rest = spec.split("=", 1)
    loc = None
    if "@" in rest:
        rest, loc = rest.rsplit("@", 1)
    cfg = {"service_attachment": rest.strip()}
    if loc:
        cfg["location"] = loc.strip()
    return name.strip(), cfg


def parse_peered_dns(spec):
    """ZONE_ID=corp.internal."""
    if "=" not in spec:
        raise argparse.ArgumentTypeError("--peered-dns expects ZONE_ID=DOMAIN")
    zid, dom = spec.split("=", 1)
    dom = dom.strip()
    if not dom.endswith("."):
        dom += "."
    return zid.strip(), {"domain": dom}


def ask(prompt, default="", choices=None):
    suffix = f" [{'/'.join(choices)}]" if choices else ""
    dflt = f" (default: {default})" if default != "" else ""
    while True:
        val = input(f"{prompt}{suffix}{dflt}: ").strip()
        if not val:
            val = default
        if choices and val not in choices:
            print(f"  Please choose one of: {', '.join(choices)}")
            continue
        return val


def ask_bool(prompt, default=False):
    d = "y" if default else "n"
    val = input(f"{prompt} [y/n] (default: {d}): ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes", "true", "1")


def run_interactive_prompts(o):
    print("\n=== Apigee X Full-Fledged Provisioning Configuration ===")
    print("Press Enter to accept the recommended defaults:")
    print("  PAYG · INTERMEDIATE env + Analytics · PSC · Google-Managed Keys · Google-Managed Cert · No Data Residency · nip.io\n")

    while not o.project:
        o.project = ask("1. GCP Project ID (required)")
    while not o.region:
        o.region = ask("2. Primary Runtime Region (e.g. us-central1, asia-south1)")

    extra_regs = ask("   Additional runtime regions for multi-region HA (comma-separated, or Enter for single-region)", "")
    if extra_regs:
        o.additional_region = (o.additional_region or []) + [r.strip() for r in extra_regs.split(",") if r.strip()]

    o.billing = ask("3. Paid Billing Model (Evaluation orgs are not supported)", o.billing or "PAYG", choices=["PAYG", "SUBSCRIPTION"])

    default_env_str = "dev:INTERMEDIATE" if o.billing == "PAYG" else "dev"
    if not o.env:
        env_in = ask(
            "4. Environments (comma-separated NAME[:BASE|INTERMEDIATE|COMPREHENSIVE][:noanalytics][@GROUP])",
            default_env_str,
        )
        o.env = [parse_env(x.strip()) for x in env_in.split(",") if x.strip()]

    o.network_mode = ask("5. Networking Topology", o.network_mode or "PSC", choices=["PSC", "VPC_PEERING"])
    if not o.network:
        net = ask("   Existing VPC network name/self-link (Enter to auto-create)", "")
        if net:
            o.network = net
    if not o.subnet and (o.network or o.routing == "internal"):
        sub = ask(f"   Existing subnetwork in {o.region} (Enter to auto-create)", "")
        if sub:
            o.subnet = sub

    enc = ask("6. Encryption Keys", "CMEK" if o.cmek else "GMEK", choices=["GMEK", "CMEK"])
    o.cmek = (enc == "CMEK")

    dr = ask_bool("7. Enable Data Residency?", bool(o.control_plane_location))
    if dr:
        o.control_plane_location = ask("   Control-plane location (e.g. us, eu, ch, au)", o.control_plane_location or "us")
        o.consumer_data_region = ask("   Consumer data region inside control-plane location", o.consumer_data_region or o.region)
    else:
        o.control_plane_location = None
        o.consumer_data_region = None

    o.routing = ask(
        "8. Northbound Routing",
        o.routing or "external",
        choices=["external", "external-regional", "internal", "none"],
    )
    if o.routing == "internal":
        while not o.network:
            o.network = ask("   VPC network for internal PSC endpoint (required)")
        while not o.subnet:
            o.subnet = ask(f"   Subnetwork in {o.region} for internal PSC endpoint (required)")

    if o.routing in ("external", "external-regional") and not o.hostname:
        dom = ask("9. Domain mode ('nip.io' for <envgroup>.<ip>.nip.io, or GROUP=api.example.com[,...])", "nip.io")
        if dom and dom.lower() != "nip.io":
            o.hostname = [x.strip() for x in dom.split(";") if x.strip()]
            if not o.dns_zone:
                dz = ask("   Optional Cloud DNS managed zone name to auto-create A records (Enter to skip)", "")
                if dz:
                    o.dns_zone = dz

    if o.routing in ("external", "external-regional"):
        default_cert = "self_managed" if o.routing == "external-regional" else (o.cert_mode or "google_managed")
        cert_choices = ["existing", "self_managed"] if o.routing == "external-regional" else ["google_managed", "existing", "self_managed"]
        o.cert_mode = ask("10. TLS Certificate Mode", default_cert, choices=cert_choices)
        if o.cert_mode == "existing" and not o.ssl_certificate:
            o.ssl_certificate = ask("    Existing SSL certificate self-link")
        elif o.cert_mode == "self_managed":
            if not o.ssl_cert_file:
                o.ssl_cert_file = ask("    Path to PEM certificate file")
            if not o.ssl_key_file:
                o.ssl_key_file = ask("    Path to PEM private key file")

    if ask_bool("11. Configure optional enterprise features (Cloud Armor WAF, Static NAT IPs, Add-ons, Remote State)?", False):
        if o.routing == "external":
            o.cloud_armor = ask_bool("    Enable baseline Google Cloud Armor WAF & DDoS policy?", o.cloud_armor)
            o.http_redirect = ask_bool("    Enable HTTP (:80) -> HTTPS (:443) redirect?", o.http_redirect)
        o.nat_ips = ask_bool("    Reserve & activate static outbound NAT IP(s) per runtime instance?", o.nat_ips)
        if ask_bool("    Enable Apigee Advanced API Security add-on?", False):
            o.addon = list(set((o.addon or []) + ["api_security"]))
        if not o.state_bucket:
            sb = ask("    GCS bucket for remote Terraform state (Enter for local state)", "")
            if sb:
                o.state_bucket = sb

    if o.action == "wizard":
        o.action = ask("\nSelect action to run now", "plan", choices=["plan", "apply", "export"])
    return o


def tfvars(o):
    v = {
        "project_id": o.project,
        "region": o.region,
        "billing_type": o.billing,
        "environments": dict(o.env or [("dev", {})]),
        "routing": o.routing,
    }
    if o.network_mode and o.network_mode != "PSC":
        v["network_mode"] = o.network_mode
    if o.cert_mode and o.cert_mode != "google_managed":
        v["cert_mode"] = o.cert_mode
    if o.cmek:
        v["use_cmek"] = True
    if o.cloud_armor:
        v["enable_cloud_armor"] = True
    if o.http_redirect:
        v["enable_http_redirect"] = True
    if o.nat_ips:
        v["enable_nat_ips"] = True
    if o.use_existing_psa:
        v["use_existing_psa_connection"] = True

    for k, attr in (
        ("analytics_region", "analytics_region"),
        ("instance_name", "instance"),
        ("network", "network"),
        ("subnetwork", "subnet"),
        ("ssl_certificate", "ssl_certificate"),
        ("cloud_armor_policy", "cloud_armor_policy"),
        ("dns_managed_zone", "dns_zone"),
        ("dns_project_id", "dns_project"),
        ("control_plane_location", "control_plane_location"),
        ("consumer_data_region", "consumer_data_region"),
        ("runtime_db_kms_key_id", "runtime_db_kms_key"),
        ("disk_kms_key_id", "disk_kms_key"),
        ("instance_ip_range", "instance_ip_range"),
    ):
        if getattr(o, attr, None):
            v[k] = getattr(o, attr)

    if o.ssl_cert_file:
        with open(o.ssl_cert_file) as fh:
            v["ssl_certificate_pem"] = fh.read()
    if o.ssl_key_file:
        with open(o.ssl_key_file) as fh:
            v["ssl_private_key_pem"] = fh.read()

    if o.additional_region:
        v["additional_regions"] = {r: {} for r in o.additional_region}
    if o.consumer_accept:
        v["consumer_accept_list"] = o.consumer_accept
    if o.addon:
        v["addons"] = {a: True for a in o.addon}
    if o.southbound_psc:
        v["southbound_psc_endpoints"] = dict(o.southbound_psc)
    if o.peered_dns:
        v["peered_dns_zones"] = dict(o.peered_dns)

    if o.hostname:
        v["envgroup_hostnames"] = {}
        for h in o.hostname:
            g, hosts = h.split("=", 1)
            v["envgroup_hostnames"][g.strip()] = [x.strip() for x in hosts.split(",") if x.strip()]
    return v


def format_hcl_value(val, indent=0):
    pad = "  " * indent
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return json.dumps(val)
    if isinstance(val, list):
        return "[" + ", ".join(format_hcl_value(x, indent) for x in val) + "]"
    if isinstance(val, dict):
        if not val:
            return "{}"
        lines = ["{"]
        for k, sub in val.items():
            key_str = k if k.replace("_", "").isalnum() and not k[0].isdigit() else json.dumps(k)
            lines.append(f"{pad}  {key_str} = {format_hcl_value(sub, indent + 1)}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    return "null"


def write_hcl_tfvars(path, data):
    lines = [
        "# Generated by apigee-x-provisioning/scripts/provision.py",
        "# Defaults: PAYG · INTERMEDIATE + Analytics · PSC · GMEK · Google-Managed Cert · No Data Residency · nip.io",
        "",
    ]
    for k, val in data.items():
        lines.append(f"{k} = {format_hcl_value(val, 0)}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


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
    if o.action in ("plan", "apply", "export"):
        if not (o.project and o.region):
            sys.exit("--project and --region are required for plan/apply/export (or pass -i / run 'wizard')")
        data = tfvars(o)
        if o.action == "export":
            hcl_path = os.path.join(o.workdir, "terraform.tfvars")
            write_hcl_tfvars(hcl_path, data)
            print(f"wrote standalone Terraform package + {hcl_path}")
        else:
            with open(os.path.join(o.workdir, "terraform.tfvars.json"), "w") as f:
                json.dump(data, f, indent=2)
            print(f"wrote {o.workdir}/terraform.tfvars.json")


def write_env(o, tf):
    out = json.loads(tf.run("output", "-json", capture=True) or "{}")
    if "env_file" in out:
        path = os.path.join(o.workdir, "apigee-x.env")
        with open(path, "w") as f:
            f.write(out["env_file"]["value"] + "\n")
        print(f"\nwrote {path} - run: source {path}")
    for k in ("org", "billing_type", "network_mode", "encryption_mode", "environments", "ingress_ip", "next_steps"):
        if k in out:
            val = out[k]["value"]
            print(f"\n{k}:\n{val if isinstance(val, str) else json.dumps(val, indent=2)}")
    return out


def sample(o, out):
    env = list(out["environments"]["value"])[0]
    org = out["org"]["value"]
    base = out.get("apigee_api_base", {}).get("value", "https://apigee.googleapis.com")
    tmp = tempfile.mkdtemp(prefix="apigee-sample-")
    scaffold = os.path.join(SKILLS, "apigee-proxy-builder", "scripts", "scaffold_api_proxy.py")
    ctl = os.path.join(SKILLS, "apigee-proxy-lifecycle", "scripts", "apigee_ctl.py")
    subprocess.run([sys.executable, scaffold, "--out", f"{tmp}/httpbin", "--name", "httpbin", "--basepath", "/httpbin",
                    "--target", "https://httpbin.org", "--auth", "none", "--spike-arrest", "20ps"], check=True)
    rc = subprocess.call([sys.executable, ctl, "--org", org, "update", f"{tmp}/httpbin", "--env", env],
                         env=dict(os.environ, APIGEE_API_BASE=base))
    if rc:
        sys.exit(rc)
    host = out["environments"]["value"][env]["hostnames"][0]
    print(f"\nSample deployed. Once the certificate is ACTIVE:  curl -i https://{host}/httpbin/headers")


def validate_options(a, o):
    if o.billing and o.billing.upper() == "EVALUATION":
        a.error("Evaluation organizations are not supported by this skill. Use --billing PAYG (default) or SUBSCRIPTION.")
    if o.billing not in ("PAYG", "SUBSCRIPTION"):
        a.error("--billing must be PAYG or SUBSCRIPTION (Evaluation orgs are not supported).")
    if o.hostname and any("=" not in h for h in o.hostname):
        a.error("--hostname expects GROUP=HOST[,HOST]")
    if o.routing == "internal" and not (o.network and o.subnet):
        a.error("--routing internal needs --network and --subnet")
    if o.control_plane_location and not o.consumer_data_region:
        a.error("--control-plane-location (data residency) requires --consumer-data-region")
    if o.peered_dns and o.network_mode != "VPC_PEERING":
        a.error("--peered-dns requires --network-mode VPC_PEERING")
    if o.routing == "external-regional" and not (o.ssl_certificate or (o.ssl_cert_file and o.ssl_key_file)):
        a.error("--routing external-regional requires --ssl-certificate or --ssl-cert-file + --ssl-key-file")


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("action", choices=["wizard", "plan", "apply", "export", "output", "sample", "destroy"])
    a.add_argument("-i", "--interactive", action="store_true", help="Prompt interactively for deployment options")
    a.add_argument("--project", help="GCP project ID (= Apigee org name)")
    a.add_argument("--region", help="Primary runtime region (e.g. us-central1, asia-south1)")
    a.add_argument("--additional-region", action="append", help="Additional runtime region for multi-region HA (repeatable)")
    a.add_argument("--workdir", default="apigee-x-infra", help="Terraform working directory (default: ./apigee-x-infra)")
    a.add_argument("--billing", default="PAYG", help="Paid billing model: PAYG (default) or SUBSCRIPTION")
    a.add_argument("--env", action="append", type=parse_env, help="NAME[:BASE|INTERMEDIATE|COMPREHENSIVE][:noanalytics][@GROUP]")
    a.add_argument("--hostname", action="append", help="GROUP=HOST[,HOST] (default: <group>.<lb-ip>.nip.io)")
    a.add_argument("--analytics-region", help="Analytics storage region (default: --region)")
    a.add_argument("--instance", help="Primary runtime instance name (default: --region)")
    a.add_argument("--network-mode", choices=["PSC", "VPC_PEERING"], default="PSC", help="PSC (default, no VPC peering) or VPC_PEERING")
    a.add_argument("--use-existing-psa", action="store_true", help="Reuse existing Shared VPC PSA connection when network-mode=VPC_PEERING")
    a.add_argument("--instance-ip-range", help="Explicit /22[,/28] CIDR range for primary instance in VPC_PEERING mode")
    a.add_argument("--routing", choices=["external", "external-regional", "internal", "none"], default="external")
    a.add_argument("--network", help="Existing VPC network name/self-link")
    a.add_argument("--subnet", help="Existing subnetwork name/self-link in primary region")
    a.add_argument("--cert-mode", choices=["google_managed", "existing", "self_managed"], default="google_managed")
    a.add_argument("--ssl-certificate", help="Existing SSL certificate self-link")
    a.add_argument("--ssl-cert-file", help="Path to PEM certificate file (when cert-mode=self_managed)")
    a.add_argument("--ssl-key-file", help="Path to PEM private key file (when cert-mode=self_managed)")
    a.add_argument("--http-redirect", action="store_true", help="Add HTTP (:80) -> HTTPS (:443) redirect on external LB")
    a.add_argument("--cloud-armor", action="store_true", help="Create & attach baseline Cloud Armor WAF/DDoS policy")
    a.add_argument("--cloud-armor-policy", help="Attach an existing Cloud Armor security policy self-link/name")
    a.add_argument("--dns-zone", help="Cloud DNS managed zone name to auto-create A records for custom --hostname")
    a.add_argument("--dns-project", help="GCP project hosting --dns-zone (default: --project)")
    a.add_argument("--cmek", action="store_true", help="Use Cloud KMS CMEK instead of Google-managed keys (default: GMEK)")
    a.add_argument("--runtime-db-kms-key", help="Existing KMS cryptoKey ID for runtime DB (implies --cmek)")
    a.add_argument("--disk-kms-key", help="Existing KMS cryptoKey ID for primary instance disk (implies --cmek)")
    a.add_argument("--control-plane-location", help="Data residency control-plane location (e.g. us, eu; default: disabled)")
    a.add_argument("--consumer-data-region", help="Data residency consumer data region (required with --control-plane-location)")
    a.add_argument("--consumer-accept", action="append", help="Extra consumer project ID/number for PSC service attachment")
    a.add_argument("--nat-ips", action="store_true", help="Provision & activate static outbound NAT IP(s) per runtime instance")
    a.add_argument("--addon", action="append", choices=ADDON_KEYS, help="Enable org add-on (repeatable)")
    a.add_argument("--southbound-psc", action="append", type=parse_southbound_psc, help="NAME=SERVICE_ATTACHMENT[@REGION]")
    a.add_argument("--peered-dns", action="append", type=parse_peered_dns, help="ZONE_ID=DOMAIN (requires --network-mode VPC_PEERING)")
    a.add_argument("--state-bucket", help="GCS bucket for remote Terraform state")
    a.add_argument("--state-prefix", help="GCS state prefix (default: apigee-x/<project>)")
    a.add_argument("--auto-approve", action="store_true", help="Apply without confirmation prompt")
    a.add_argument("--sample", action="store_true", help="After apply, deploy /httpbin sample proxy to first environment")
    o = a.parse_args()

    if o.runtime_db_kms_key or o.disk_kms_key:
        o.cmek = True
    if o.ssl_cert_file or o.ssl_key_file:
        o.cert_mode = "self_managed"
    elif o.ssl_certificate and o.cert_mode == "google_managed":
        o.cert_mode = "existing"

    # Trigger interactive wizard when requested or when plan/apply/export lacks required inputs on an interactive terminal
    if o.action == "wizard" or o.interactive or (
        o.action in ("plan", "apply", "export") and not (o.project and o.region) and sys.stdin.isatty()
    ):
        run_interactive_prompts(o)

    if o.action in ("plan", "apply", "export"):
        validate_options(a, o)

    if o.action == "export":
        prepare(o)
        return

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
        sys.exit("Refusing to destroy automatically. Apigee org deletion and optional CMEK keys (prevent_destroy) need a "
                 "deliberate, reviewed change - see SKILL.md 'Teardown'.")


if __name__ == "__main__":
    main()
