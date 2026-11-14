"""drill's HTTP fake (ADR-0012, ADR-0013, ADR-0014): a CONNECT proxy with its own CA.

`drill run` points the run's `HTTPS_PROXY` and `HTTP_PROXY` at it. For a faked host it
terminates TLS with a leaf from the drill CA, answers each request from the registered
workspace's `.drill/fixtures/http.json` and appends one line to `.drill/trace.jsonl`:
{method, url, headers, body, status, fixture}. CONNECTs to any other host (Claude Code's
own API traffic) are tunnelled, through the upstream proxy when there is one; the sandbox's
allowlist is what keeps the agent's commands off them. A workspace registers itself with a
POST of `{"workspace": <abs path>}` to `/_drill/run`, sent straight to the proxy port.

The CA is minted with the `openssl` CLI: Python's stdlib can load certs but not make them.
"""

import base64
import fnmatch
import json
import os
import secrets
import select
import shutil
import socket
import ssl
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Not the agent's to choose, or not about the request: never recorded.
DROPPED = {"connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
           "content-length", "host"}
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CERT_DAYS = "7"  # the CA and its leaves only outlive one drill by a few days


class NoOpenSSL(RuntimeError):
    """The `openssl` CLI isn't on PATH, so drill can't mint the HTTP fake's CA."""


def require_openssl() -> str:
    """The openssl CLI's path, or NoOpenSSL with what to do about it."""
    exe = shutil.which("openssl")
    if exe is None:
        raise NoOpenSSL("drill's HTTP fake needs the openssl CLI to mint its CA, and there is no openssl on PATH; "
                        "install it (it ships with macOS and most Linux distributions)")
    return exe


def _openssl(*args: str, cwd: Path) -> None:
    exe = require_openssl()
    proc = subprocess.run([exe, *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"openssl {args[0]} failed (exit {proc.returncode}): {proc.stderr.strip()}")


# Python 3.13's default context is X509-strict: it refuses a CA without keyUsage and a leaf
# without authorityKeyIdentifier (curl and node don't mind). Config files, not -addext, so
# LibreSSL's openssl on macOS takes them too.
CA_CONFIG = """[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
CN = drill throwaway CA
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
"""

LEAF_CONFIG = """[req]
distinguished_name = dn
prompt = no
[dn]
CN = {host}
"""

LEAF_EXT = """subjectAltName = DNS:{host}
basicConstraints = CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
authorityKeyIdentifier = keyid
subjectKeyIdentifier = hash
"""


def mint_ca(certs: Path) -> Path:
    """A throwaway drill CA in certs/ (ca.pem, ca.key), unless one is there; return ca.pem."""
    certs.mkdir(parents=True, exist_ok=True)
    pem = certs / "ca.pem"
    if not pem.is_file():
        (certs / "ca.cnf").write_text(CA_CONFIG)
        _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", CERT_DAYS, "-config", "ca.cnf",
                 "-keyout", "ca.key", "-out", "ca.pem", cwd=certs)
    return pem


def mint_leaf(certs: Path, host: str) -> tuple[Path, Path]:
    """A leaf cert for host signed by the CA in certs/; return (cert, key)."""
    if "/" in host or host.startswith("."):
        raise ValueError(f"not a host name: {host!r}")
    pem, key = certs / f"{host}.pem", certs / f"{host}.key"
    if not pem.is_file():
        (certs / f"{host}.cnf").write_text(LEAF_CONFIG.format(host=host))
        (certs / f"{host}.ext").write_text(LEAF_EXT.format(host=host))
        _openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-config", f"{host}.cnf",
                 "-keyout", key.name, "-out", f"{host}.csr", cwd=certs)
        _openssl("x509", "-req", "-in", f"{host}.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
                 "-set_serial", str(secrets.randbits(63)), "-days", CERT_DAYS, "-extfile", f"{host}.ext",
                 "-out", pem.name, cwd=certs)
    return pem, key


PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
CA_VARS = ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")


def upstream_proxy(env=os.environ) -> str | None:
    """The proxy drill itself was told to use, which unfaked hosts keep going through."""
    return next((env[v] for v in ("HTTPS_PROXY", "https_proxy") if env.get(v)), None)


def user_bundles(env=os.environ) -> list[Path]:
    """The CA files the user already trusts: each one their env names, plus the system's default bundle."""
    named = [Path(env[v]) for v in CA_VARS if env.get(v)]
    defaults = ssl.get_default_verify_paths()
    system = [Path(p) for p in (defaults.cafile, defaults.openssl_cafile, "/etc/ssl/cert.pem") if p]
    system = [p for p in system if p.is_file()][:1]
    seen, out = set(), []
    for p in [*named, *system]:
        if p.is_file() and p.resolve() not in seen:
            seen.add(p.resolve())
            out.append(p)
    return out


def trust_bundle(dest: Path, ca: Path, env=os.environ) -> Path:
    """Write the user's bundle plus the drill CA to dest, the one file a run's clients trust."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    parts = [p.read_text(errors="replace").rstrip("\n") + "\n" for p in user_bundles(env)]
    dest.write_text("".join(parts) + "# drill throwaway CA\n" + ca.read_text())
    return dest


def run_env(fake: "HttpFake", bundle: Path) -> dict:
    """What `drill run` exports so the run's clients reach the fake and trust it (ADR-0013)."""
    return {**{v: fake.url for v in PROXY_VARS}, **{v: str(bundle) for v in CA_VARS}}


NODE_PASSTHROUGH = """#!{python} -I
# Written by drill. Not a fixture shim: it records nothing and runs the real node. node's fetch
# ignores HTTPS_PROXY unless NODE_USE_ENV_PROXY=1, which the sandbox doesn't pass through (ADR-0013).
import os, sys
os.environ["NODE_USE_ENV_PROXY"] = "1"
os.execv({node!r}, [{node!r}, *sys.argv[1:]])
"""


def install_node_passthrough(bin_dir: Path, python: str, path: str | None = None) -> Path | None:
    """A `node` in bin_dir that turns on node's env proxy and runs the real one; None when there's no node."""
    search = os.pathsep.join(d for d in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
                             if d and Path(d).resolve() != bin_dir.resolve())
    node = shutil.which("node", path=search)
    if node is None:
        return None
    bin_dir.mkdir(parents=True, exist_ok=True)
    passthrough = bin_dir / "node"
    passthrough.write_text(NODE_PASSTHROUGH.format(python=python, node=os.path.realpath(node)))
    passthrough.chmod(0o755)
    return passthrough


def register(url: str, workspace: Path) -> None:
    """Tell the fake at url that the next run lives in workspace (what drill's scaffold does)."""
    body = json.dumps({"workspace": str(workspace)}).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"{url}/_drill/run", data=body, method="POST",
                                 headers={"content-type": "application/json"})
    with opener.open(req, timeout=10):
        pass


def pick(table: list, method: str, url: str, prior_calls: int) -> int | None:
    """Index of the answering entry, or None. First match wins; identical (method, url) entries form a sequence."""
    def key(e):
        return e.get("method", "GET").upper(), e.get("url")
    hits = [i for i, e in enumerate(table)
            if isinstance(e, dict) and isinstance(e.get("url"), str) and isinstance(e.get("method", "GET"), str)
            and key(e)[0] == method and fnmatch.fnmatchcase(url, e["url"])]
    if not hits:
        return None
    seq = [i for i in hits if key(table[i]) == key(table[hits[0]])]
    return seq[min(prior_calls, len(seq) - 1)]


def _body_text(raw: bytes):
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(raw).decode()}


class HttpFake:
    """The fake for one drill: `start()`, point clients at `url` trusting `ca`, `stop()`."""

    def __init__(self, certs: Path, hosts, upstream: str | None = None):
        self.certs = Path(certs)
        self.hosts = {h.lower() for h in hosts}
        self.upstream = upstream  # http://[user:pass@]host:port of the proxy unfaked hosts go through
        self.workspace: Path | None = None
        self.lock = threading.Lock()
        self._contexts: dict[str, ssl.SSLContext] = {}
        self.tunnelled: set[str] = set()  # host:port of every CONNECT passed through unfaked
        self._server: ThreadingHTTPServer | None = None

    @property
    def ca(self) -> Path:
        return self.certs / "ca.pem"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "HttpFake":
        mint_ca(self.certs)
        for host in sorted(self.hosts):
            cert, key = mint_leaf(self.certs, host)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self._contexts[host] = ctx
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.fake = self
        threading.Thread(target=self._server.serve_forever, args=(0.05,), name="drill-http-fake", daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()

    def context(self, host: str) -> ssl.SSLContext | None:
        return self._contexts.get(host.lower())

    def answer(self, method: str, url: str, headers: dict, raw: bytes) -> tuple[int, dict, bytes]:
        """Answer one request from the workspace's fixtures and append its trace line."""
        with self.lock:
            if self.workspace is None:
                return 503, {"content-type": "text/plain"}, b"drill-http: no run registered at /_drill/run\n"
            drill = self.workspace / ".drill"
            trace = drill / "trace.jsonl"
            why = None
            try:
                table = json.loads((drill / "fixtures" / "http.json").read_text())
            except FileNotFoundError:
                table = []
            except (OSError, ValueError) as e:
                table, why = [], f"drill-http: unreadable fixtures/http.json: {e}\n"
            if not isinstance(table, list):
                table, why = [], "drill-http: fixtures/http.json must be a list\n"
            index = pick(table, method, url, _prior_calls(trace, method, url))
            entry = table[index] if index is not None else None
            if entry is not None:
                try:
                    status = int(entry.get("status", 200))
                    body, ctype = _fixture_body(entry, drill / "fixtures")
                except (OSError, ValueError, TypeError) as e:  # a broken entry answers nothing: it counts as a miss
                    entry, index, why = None, None, f"drill-http: bad fixture for {method} {url}: {e}\n"
            if entry is None:
                status = 502
            line = {"method": method, "url": url, "headers": headers, "body": _body_text(raw),
                    "status": status, "fixture": index}
            drill.mkdir(exist_ok=True)
            with open(trace, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        if entry is None:
            return 502, {"x-drill-fixture": "miss", "content-type": "text/plain"}, \
                (why or f"drill-http: no fixture for: {method} {url}\n").encode()
        out = {"content-type": ctype} if ctype else {}
        out.update({k.lower(): str(v) for k, v in (entry.get("headers") or {}).items()})
        return status, out, body


def _fixture_body(entry: dict, fixtures: Path) -> tuple[bytes, str | None]:
    """An entry's answer body and its default content type; body_file must stay inside fixtures/."""
    if "body_file" in entry:
        path = (fixtures / entry["body_file"]).resolve()
        if not path.is_relative_to(fixtures.resolve()):
            raise ValueError(f"body_file {entry['body_file']!r} is outside fixtures/")
        return path.read_bytes(), "application/octet-stream"
    if isinstance(entry.get("body"), str):
        return entry["body"].encode(), "text/plain"
    if "body" in entry:
        return json.dumps(entry["body"], ensure_ascii=False).encode(), "application/json"
    return b"", None


def _prior_calls(trace: Path, method: str, url: str) -> int:
    n = 0
    try:
        with open(trace, encoding="utf-8") as f:
            for line in f:
                try:
                    t = json.loads(line)
                except ValueError:
                    continue
                n += isinstance(t, dict) and t.get("method") == method and t.get("url") == url
    except FileNotFoundError:
        pass
    return n


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    origin: str | None = None  # "https://host[:port]" once inside a terminated CONNECT tunnel

    def log_message(self, *_):
        pass

    @property
    def fake(self) -> HttpFake:
        return self.server.fake

    def do_CONNECT(self):
        host, _, port = self.path.rpartition(":")
        if not host or not port.isdigit():
            host, port = self.path, "443"
        host = host.strip("[]")
        ctx = self.fake.context(host) if self.origin is None else None
        if ctx is None:
            return self._tunnel(host, int(port))
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.wfile.flush()
        try:
            tls = ctx.wrap_socket(self.connection, server_side=True)
        except (ssl.SSLError, OSError):
            self.close_connection = True
            return
        origin = f"https://{host}" + ("" if port == "443" else f":{port}")
        try:
            type("_Inner", (_Handler,), {"origin": origin})(tls, self.client_address, self.server)
        finally:
            tls.close()
            self.close_connection = True

    def _tunnel(self, host: str, port: int):
        self.fake.tunnelled.add(f"{host}:{port}")
        try:
            up = self._open_upstream(host, port)
        except OSError as e:
            self.close_connection = True
            return self._reply(502, {"content-type": "text/plain"}, f"drill-http: can't reach {host}:{port}: {e}\n".encode())
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        socks = [self.connection, up]
        try:
            while True:
                ready, _, _ = select.select(socks, [], [], 300)
                if not ready:
                    return
                for s in ready:
                    data = s.recv(65536)
                    if not data:
                        return
                    (up if s is self.connection else self.connection).sendall(data)
        except OSError:
            return
        finally:
            up.close()

    def _open_upstream(self, host: str, port: int) -> socket.socket:
        if not self.fake.upstream:
            return socket.create_connection((host, port), timeout=30)
        u = urlsplit(self.fake.upstream)
        up = socket.create_connection((u.hostname, u.port or 80), timeout=30)
        hdrs = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        if u.username:
            cred = base64.b64encode(f"{unquote(u.username)}:{unquote(u.password or '')}".encode()).decode()
            hdrs += f"Proxy-Authorization: Basic {cred}\r\n"
        up.sendall((hdrs + "\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = up.recv(4096)
            if not chunk:
                break
            reply += chunk
        status = reply.split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"200":
            up.close()
            first = reply.split(b"\r\n", 1)[0].decode(errors="replace")
            raise OSError(f"upstream proxy answered {first!r}")
        up.settimeout(None)
        return up

    def _read_body(self) -> bytes:
        if "chunked" in self.headers.get("transfer-encoding", "").lower():
            out = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return out
                out += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n) if n else b""

    def _any(self):
        if self.origin is None and self.command == "POST" and self.path == "/_drill/run":
            return self._register()
        url = self.origin + self.path if self.origin and self.path.startswith("/") else self.path
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not self.fake.context(parts.hostname or ""):
            self._read_body()
            return self._reply(403, {"content-type": "text/plain"}, f"drill-http: not a faked host: {url}\n".encode())
        raw = self._read_body()
        headers = {k.lower(): v for k, v in self.headers.items()
                   if k.lower() not in DROPPED and not k.lower().startswith("proxy-")}
        self._reply(*self.fake.answer(self.command, url, headers, raw))

    def _register(self):
        try:
            ws = Path(json.loads(self._read_body())["workspace"])
        except (ValueError, KeyError, TypeError) as e:
            return self._reply(400, {"content-type": "text/plain"}, f"drill-http: bad registration: {e}\n".encode())
        if not ws.is_absolute():
            return self._reply(400, {"content-type": "text/plain"}, b"drill-http: workspace must be absolute\n")
        with self.fake.lock:
            self.fake.workspace = ws
        self._reply(204, {}, b"")

    def _reply(self, status: int, headers: dict, body: bytes):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


for _m in METHODS:
    setattr(_Handler, f"do_{_m}", _Handler._any)
