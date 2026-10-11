#!/usr/bin/env python3
"""Fake terraform for wrapper tests: logs args to $FAKE_TF_LOG, prints canned outputs."""
import json, os, sys
open(os.environ["FAKE_TF_LOG"], "a").write(" ".join(sys.argv[1:]) + "\n")
if "output" in sys.argv:
    print(json.dumps({
        "org": {"value": "my-proj"}, "billing_type": {"value": "PAYG"},
        "environments": {"value": {"dev": {"type": "INTERMEDIATE", "analytics": True, "envgroup": "dev-group",
                                           "hostnames": ["dev-group.34-1-2-3.nip.io"]}}},
        "ingress_ip": {"value": "34.1.2.3"}, "apigee_api_base": {"value": "https://apigee.googleapis.com"},
        "env_file": {"value": "export APIGEE_ORG=my-proj\nexport APIGEE_ENV=dev\nexport APIGEE_HOST=dev-group.34-1-2-3.nip.io"},
        "next_steps": {"value": "curl -i https://dev-group.34-1-2-3.nip.io/httpbin/headers"}}))
