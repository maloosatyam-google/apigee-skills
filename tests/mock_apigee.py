#!/usr/bin/env python3
"""Tiny in-memory mock of the Apigee X management API - enough for apigee_ctl.py tests (stdlib only)."""
import io
import json
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote


class State:
    def __init__(self):
        self.bundles = {"apis": {}, "sharedflows": {}}   # coll -> name -> {rev: zipbytes}
        self.deployments = {}                            # (coll, env, name) -> rev
        self.products = {}
        self.kvms = {}                                   # (env, map) -> {key: value}
        self.targetservers = {}
        self.developers = {}
        self.apps = {}          # (email, app) -> app
        self.keys = 0
        self.flowhooks = {}
        self.debugmasks = {}
        self.calls = []


def make_handler(st):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=None, raw=None, ctype="application/json"):
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"{}")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _route(self, method):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            p = unquote(u.path)
            st.calls.append((method, p, q))
            m = re.match(r"^/v1/organizations/[^/]+(.*)$", p)
            if not m:
                return self._send(404, {"error": {"message": "bad org path"}})
            path = m.group(1)
            body = self._body() if method in ("POST", "PUT", "PATCH") else b""

            # import bundle
            mm = re.match(r"^/(apis|sharedflows)$", path)
            if mm and method == "POST" and q.get("action") == "import":
                raw = body
                start = raw.find(b"PK\x03\x04")
                end = raw.rfind(b"\r\n--")
                z = raw[start:end]
                zipfile.ZipFile(io.BytesIO(z))  # must be a valid zip
                revs = st.bundles[mm.group(1)].setdefault(q["name"], {})
                rev = str(len(revs) + 1)
                revs[rev] = z
                return self._send(200, {"name": q["name"], "revision": rev})
            if mm and method == "GET":
                key = "proxies" if mm.group(1) == "apis" else "sharedFlows"
                return self._send(200, {key: [{"name": n} for n in st.bundles[mm.group(1)]]})
            mm = re.match(r"^/(apis|sharedflows)/([^/]+)$", path)
            if mm and method == "GET":
                revs = st.bundles[mm.group(1)].get(mm.group(2))
                if revs is None:
                    return self._send(404, {"error": {"message": "not found"}})
                return self._send(200, {"name": mm.group(2), "revision": sorted(revs, key=int),
                                        "latestRevisionId": max(revs, key=int)})
            mm = re.match(r"^/(apis|sharedflows)/([^/]+)/revisions/(\d+)$", path)
            if mm and method == "GET" and q.get("format") == "bundle":
                return self._send(200, raw=st.bundles[mm.group(1)][mm.group(2)][mm.group(3)], ctype="application/zip")
            mm = re.match(r"^/(apis|sharedflows)/([^/]+)/deployments$", path)
            if mm and method == "GET":
                deps = [{"environment": e, "revision": r} for (c, e, n), r in st.deployments.items()
                        if c == mm.group(1) and n == mm.group(2)]
                return self._send(200, {"deployments": deps})
            mm = re.match(r"^/environments/([^/]+)/(apis|sharedflows)/([^/]+)/deployments$", path)
            if mm and method == "GET":
                r = st.deployments.get((mm.group(2), mm.group(1), mm.group(3)))
                return self._send(200, {"deployments": [{"revision": r}] if r else []})
            mm = re.match(r"^/environments/([^/]+)/(apis|sharedflows)/([^/]+)/revisions/(\d+)/deployments$", path)
            if mm:
                env, coll, name, rev = mm.groups()
                if method == "POST":
                    if rev not in st.bundles[coll].get(name, {}):
                        return self._send(404, {"error": {"message": "no such revision"}})
                    st.deployments[(coll, env, name)] = rev
                    return self._send(200, {"revision": rev})
                if method == "GET":
                    ok = st.deployments.get((coll, env, name)) == rev
                    return self._send(200, {"state": "READY" if ok else "PROGRESSING", "revision": rev})
                if method == "DELETE":
                    st.deployments.pop((coll, env, name), None)
                    return self._send(200, {})
            nf = lambda: self._send(404, {"error": {"message": "nf"}})  # noqa: E731
            # products
            if path == "/apiproducts":
                if method == "POST":
                    d = json.loads(body)
                    st.products[d["name"]] = d
                    return self._send(201, d)
                return self._send(200, {"apiProduct": list(st.products.values())})
            mm = re.match(r"^/apiproducts/(.+)$", path)
            if mm:
                n = mm.group(1)
                if n not in st.products and method != "PUT":
                    return nf()
                if method == "GET":
                    return self._send(200, st.products[n])
                if method == "PUT":
                    st.products[n] = json.loads(body)
                    return self._send(200, st.products[n])
                if method == "DELETE":
                    return self._send(200, st.products.pop(n))
            # developers / apps / keys
            if path == "/developers":
                if method == "POST":
                    d = json.loads(body)
                    st.developers[d["email"]] = dict(d, status="active")
                    return self._send(201, d)
                return self._send(200, {"developer": list(st.developers.values())})
            mm = re.match(r"^/developers/([^/]+)$", path)
            if mm:
                e = mm.group(1)
                if e not in st.developers:
                    return nf()
                if method == "DELETE":
                    st.apps = {k: v for k, v in st.apps.items() if k[0] != e}
                    return self._send(200, st.developers.pop(e))
                return self._send(200, st.developers[e])
            mm = re.match(r"^/developers/([^/]+)/apps$", path)
            if mm:
                e = mm.group(1)
                if e not in st.developers:
                    return nf()
                if method == "POST":
                    d = json.loads(body)
                    st.keys += 1
                    app = {"name": d["name"], "appId": f"app-{st.keys}", "status": "approved",
                           "credentials": [{"consumerKey": f"KEY{st.keys:04d}abcdefgh", "consumerSecret": f"SECRET{st.keys:04d}xyz",
                                            "status": "approved",
                                            "apiProducts": [{"apiproduct": x, "status": "approved"} for x in d.get("apiProducts", [])]}]}
                    st.apps[(e, d["name"])] = app
                    return self._send(201, app)
                return self._send(200, {"app": [v for k, v in st.apps.items() if k[0] == e]})
            mm = re.match(r"^/developers/([^/]+)/apps/([^/]+)$", path)
            if mm:
                if mm.groups() not in st.apps:
                    return nf()
                if method == "DELETE":
                    return self._send(200, st.apps.pop(mm.groups()))
                return self._send(200, st.apps[mm.groups()])
            mm = re.match(r"^/developers/([^/]+)/apps/([^/]+)/keys/([^/]+)(?:/apiproducts/([^/]+))?$", path)
            if mm:
                e, a, k, prod = mm.groups()
                cred = next(c for c in st.apps[(e, a)]["credentials"] if c["consumerKey"] == k)
                action = q.get("action")
                if action and prod:
                    next(x for x in cred["apiProducts"] if x["apiproduct"] == prod)["status"] = action + "d"
                elif action:
                    cred["status"] = action + "d"
                else:
                    cred["apiProducts"] += [{"apiproduct": x, "status": "approved"} for x in json.loads(body)["apiProducts"]]
                return self._send(200, cred)
            # kvms (env / org / proxy scope); scope id: env name, "_org" or "proxy:<name>"
            mm = re.match(r"^(?:/environments/([^/]+)|/apis/([^/]+))?/keyvaluemaps(?:/([^/]+))?(/entries(?:/(.+))?)?$", path)
            if mm:
                env, proxy, mp, entries, key = mm.groups()
                scope = env or (f"proxy:{proxy}" if proxy else "_org")
                if mp is None:
                    if method == "GET":
                        return self._send(200, [m for (sc, m) in st.kvms if sc == scope])
                    d = json.loads(body)
                    st.kvms[(scope, d["name"])] = {}
                    return self._send(201, d)
                if (scope, mp) not in st.kvms:
                    return nf()
                kv = st.kvms[(scope, mp)]
                if not entries:
                    if method == "DELETE":
                        st.kvms.pop((scope, mp))
                        return self._send(200, {"name": mp})
                    return self._send(200, {"name": mp})
                if key is None and method == "GET":
                    return self._send(200, {"keyValueEntries": [{"name": k, "value": v} for k, v in kv.items()]})
                if method == "GET":
                    return self._send(200, {"name": key, "value": kv[key]}) if key in kv else nf()
                if method == "DELETE":
                    return self._send(200, {"name": key, "value": kv.pop(key)}) if key in kv else nf()
                d = json.loads(body)
                kv[d["name"]] = d["value"]
                return self._send(200, d)
            # target servers
            mm = re.match(r"^/environments/([^/]+)/targetservers(?:/(.+))?$", path)
            if mm:
                env, n = mm.groups()
                if n is None and method == "GET":
                    return self._send(200, [k[1] for k in st.targetservers if k[0] == env])
                if method == "GET":
                    return self._send(200, st.targetservers[(env, n)]) if (env, n) in st.targetservers else nf()
                if method == "DELETE":
                    return self._send(200, st.targetservers.pop((env, n))) if (env, n) in st.targetservers else nf()
                d = json.loads(body)
                st.targetservers[(env, d["name"])] = d
                return self._send(200, d)
            mm = re.match(r"^/environments/([^/]+)/debugmask$", path)
            if mm:
                if method == "PATCH":
                    st.debugmasks[mm.group(1)] = json.loads(body)
                return self._send(200, st.debugmasks.get(mm.group(1), {}))
            mm = re.match(r"^/environments/([^/]+)/flowhooks/(.+)$", path)
            if mm and method == "PUT":
                st.flowhooks[mm.groups()] = json.loads(body)
                return self._send(200, {})
            return self._send(404, {"error": {"message": f"mock: no route {method} {path}"}})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_PATCH(self):
            self._route("PATCH")

        def do_DELETE(self):
            self._route("DELETE")
    return H


def start():
    st = State()
    srv = HTTPServer(("127.0.0.1", 0), make_handler(st))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, st
