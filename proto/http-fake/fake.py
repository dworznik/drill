#!/usr/bin/env python3
"""PROTOTYPE (drill#21, throwaway branch proto/21-http-fake). Not lib/ code.

drill's HTTP fake: an HTTP proxy that `drill run` points HTTPS_PROXY/HTTP_PROXY at.

- CONNECT to a faked host (one with a cert in --certs): TLS-terminate with the
  drill CA's leaf cert, read each request, answer it from the run's
  .drill/fixtures/http.json and append one line to .drill/trace.jsonl.
- Plain http:// to a faked host (absolute-form request line): same, no TLS.
- CONNECT to any other host: blind tunnel, not recorded. That is Claude
  Code's own API traffic; the sandbox already refuses the agent's commands
  any host the rig didn't grant.
- POST /_drill/run {"workspace": "<abs path>"} (origin-form, sent straight to
  the proxy port): the next run starts. Its fixtures and trace live in that
  workspace's .drill/, and the sequence counters reset.

usage: fake.py --port 8080 --certs <mkca out-dir> [--workspace <dir>]
"""
import argparse, fnmatch, json, os, select, socket, ssl, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade", "content-length", "host"}

state = {"workspace": None, "lock": threading.Lock()}


def load_fixtures():
    p = os.path.join(state["workspace"], ".drill", "fixtures", "http.json")
    return json.load(open(p)) if os.path.exists(p) else []


def trace_lines():
    p = os.path.join(state["workspace"], ".drill", "trace.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def pick(fixtures, method, url):
    """First matching entry wins; later entries with the identical
    (method, url) pattern form a sequence, the last one sticks. Same rule
    as the shim fixtures (#7)."""
    hits = [i for i, f in enumerate(fixtures)
            if f.get("method", "GET") == method and fnmatch.fnmatchcase(url, f["url"])]
    if not hits:
        return None
    first = fixtures[hits[0]]
    seq = [i for i in hits if fixtures[i].get("method", "GET") == first.get("method", "GET")
           and fixtures[i]["url"] == first["url"]]
    prior = sum(1 for t in trace_lines() if t.get("method") == method and t.get("url") == url)
    return seq[min(prior, len(seq) - 1)]


def body_text(raw):
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import base64
        return {"base64": base64.b64encode(raw).decode()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    origin = None  # "https://host" once inside a CONNECT tunnel

    def log_message(self, *a):
        pass

    # ---- proxy entry points -------------------------------------------
    def do_CONNECT(self):
        host, _, port = self.path.partition(":")
        port = int(port or 443)
        cert = os.path.join(self.server.certs, host + ".pem")
        if not os.path.exists(cert):
            return self.tunnel(host, port)
        self.send_response(200, "Connection Established")
        self.end_headers()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, os.path.join(self.server.certs, host + ".key"))
        tls = ctx.wrap_socket(self.connection, server_side=True)
        origin = "https://" + host + ("" if port == 443 else f":{port}")
        # serve the requests inside the tunnel with a fresh handler
        sub = type("Inner", (Handler,), {"origin": origin})
        sub(tls, self.client_address, self.server)
        self.close_connection = True

    def tunnel(self, host, port):
        try:
            up = socket.create_connection((host, port), timeout=10)
        except OSError as e:
            self.send_error(502, f"drill-http: can't reach {host}:{port}: {e}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        socks = [self.connection, up]
        while True:
            r, _, _ = select.select(socks, [], [], 60)
            if not r:
                break
            for s in r:
                data = s.recv(65536)
                if not data:
                    up.close()
                    self.close_connection = True
                    return
                (up if s is self.connection else self.connection).sendall(data)

    def handle_any(self):
        if self.origin is None and self.path == "/_drill/run" and self.command == "POST":
            return self.register()
        url = self.origin + self.path if self.origin else self.path
        if self.origin is None:
            if not url.startswith("http://") or not os.path.exists(
                    os.path.join(self.server.certs, urlsplit(url).hostname + ".pem")):
                return self.send_error(403, "drill-http: not a faked host")
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        headers = {k.lower(): v for k, v in self.headers.items() if k.lower() not in HOP}
        with state["lock"]:
            if state["workspace"] is None:
                return self.send_error(503, "drill-http: no run registered")
            fixtures = load_fixtures()
            i = pick(fixtures, self.command, url)
            f = fixtures[i] if i is not None else None
            status = f.get("status", 200) if f else 502
            line = {"method": self.command, "url": url, "headers": headers,
                    "body": body_text(raw), "status": status, "fixture": i}
            with open(os.path.join(state["workspace"], ".drill", "trace.jsonl"), "a") as t:
                t.write(json.dumps(line, separators=(",", ":")) + "\n")
        if f is None:
            return self.answer(502, {"x-drill-fixture": "miss", "content-type": "text/plain"},
                               f"drill-http: no fixture for: {self.command} {url}\n".encode())
        if "body_file" in f:
            body = open(os.path.join(state["workspace"], ".drill", "fixtures", f["body_file"]), "rb").read()
            ctype = "application/octet-stream"
        elif isinstance(f.get("body"), str):
            body, ctype = f["body"].encode(), "text/plain"
        elif "body" in f:
            body, ctype = json.dumps(f["body"]).encode(), "application/json"
        else:
            body, ctype = b"", None
        hdrs = {"content-type": ctype} if ctype else {}
        hdrs.update({k.lower(): v for k, v in f.get("headers", {}).items()})
        self.answer(status, hdrs, body)

    def register(self):
        n = int(self.headers.get("content-length") or 0)
        ws = json.loads(self.rfile.read(n))["workspace"]
        with state["lock"]:
            state["workspace"] = ws
        self.answer(204, {}, b"")

    def answer(self, status, headers, body):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = handle_any


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--certs", required=True)
    ap.add_argument("--workspace")
    a = ap.parse_args()
    state["workspace"] = a.workspace
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.certs = a.certs
    print(f"drill-http: listening on 127.0.0.1:{a.port}", file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
