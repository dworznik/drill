"""The HTTP fake, reached the way an agent's clients reach it: through HTTPS_PROXY with the drill CA.

Seeded from the prototype's demo.sh (proto/21-http-fake). The scripted agents in
tests/data/http-claim/agents follow the testnet-garden-claim skill with curl, urllib and node.
"""

import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from drill.lib.http_fake import HttpFake, NoOpenSSL, mint_ca, register
from drill.lib.shim import install_shims

DATA = Path(__file__).parent / "data" / "http-claim"
SECRET = "7Qm2XkP9fLr4TzWc8NbVh3"
HOST = "testnet.garden"


def fake_for(test: unittest.TestCase, hosts=(HOST,)) -> tuple[HttpFake, Path]:
    run_dir = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, run_dir, True)
    fake = HttpFake(run_dir / "ca", hosts)
    fake.start()
    test.addCleanup(fake.stop)
    return fake, run_dir


def workspace(run_dir: Path, name: str) -> Path:
    ws = run_dir / "runs" / name
    (ws / "tmp").mkdir(parents=True)
    shutil.copytree(DATA / "fixtures", ws / ".drill" / "fixtures")
    (ws / ".drill" / "env-keep.json").write_text(json.dumps(["RLN_AGENT"]))
    return ws


def agent_env(fake: HttpFake, bin_dir: Path, ws: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", TMPDIR=str(ws / "tmp"), SECRET=SECRET,
               HTTPS_PROXY=fake.url, HTTP_PROXY=fake.url, https_proxy=fake.url, http_proxy=fake.url)
    for var in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        env[var] = str(fake.ca)
    return env


def strict_context(cafile: Path) -> ssl.SSLContext:
    """Python 3.13's default verification, on any Python: VERIFY_X509_STRICT refuses a CA without keyUsage."""
    ctx = ssl.create_default_context(cafile=str(cafile))
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT
    return ctx


def read_trace(ws: Path) -> list[dict]:
    return [json.loads(line) for line in (ws / ".drill" / "trace.jsonl").read_text().splitlines()]


class ScriptedAgents(unittest.TestCase):
    """Each good agent runs the whole claim through the fake and the rln shim."""

    def setUp(self):
        self.fake, run_dir = fake_for(self)
        self.bin = run_dir / "bin"
        install_shims(self.bin, ["rln"])
        self.run_dir = run_dir

    def drive(self, agent: str, needs: str | None = None) -> tuple[subprocess.CompletedProcess, list[dict]]:
        if needs and not shutil.which(needs):
            self.skipTest(f"needs {needs}")
        ws = workspace(self.run_dir, agent)
        register(self.fake.url, ws)
        proc = subprocess.run([str(DATA / "agents" / agent)], cwd=ws, env=agent_env(self.fake, self.bin, ws),
                              capture_output=True, text=True, timeout=60)
        return proc, read_trace(ws)

    def assert_claimed(self, proc, trace):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"wei":"50000000000000000"', proc.stdout.replace(" ", ""))
        self.assertEqual([t.get("method") or t["bin"] for t in trace],
                         ["GET", "rln", "POST", "rln", "POST", "GET", "GET", "GET", "rln"])
        self.assertEqual([t["fixture"] for t in trace], [0, 0, 1, 1, 2, 3, 4, 5, 2])
        start = trace[2]
        self.assertEqual(start["url"], "https://testnet.garden/api/v1/acme/claims")
        self.assertEqual(start["status"], 201)
        self.assertEqual(json.loads(start["body"]),
                         {"secret": SECRET, "recipient": "0x1111111111111111111111111111111111111111"})
        self.assertTrue(start["headers"]["content-type"].startswith("application/json"))

    def test_curl_claims_through_the_fake(self):
        self.assert_claimed(*self.drive("good-curl", needs="curl"))

    def test_python_urllib_claims_through_the_fake(self):
        self.assert_claimed(*self.drive("good-python"))

    def test_node_fetch_reaches_the_fake(self):
        proc, trace = self.drive("good-node", needs="node")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("garden-sepolia", proc.stdout)
        self.assertEqual([(t["method"], t["url"], t["fixture"]) for t in trace],
                         [("GET", "https://testnet.garden/api/v1/acme", 0)])

    def test_request_lines_interleave_with_shim_lines_in_call_order(self):
        _, trace = self.drive("good-python")
        kinds = ["http" if "method" in t else "shim" for t in trace]
        self.assertEqual(kinds, ["http", "shim", "http", "shim", "http", "http", "http", "http", "shim"])

    def test_a_miss_answers_502_and_traces_a_null_fixture(self):
        proc, trace = self.drive("unfaked-path", needs="curl")
        self.assertIn("HTTP/1.1 502", proc.stdout)
        self.assertIn("x-drill-fixture: miss", proc.stdout)
        self.assertIn("no fixture for: GET https://testnet.garden/api/v1/acme/health", proc.stdout)
        self.assertEqual(trace, [{"method": "GET", "url": "https://testnet.garden/api/v1/acme/health",
                                  "headers": {"user-agent": trace[0]["headers"]["user-agent"], "accept": "*/*"},
                                  "body": None, "status": 502, "fixture": None}])


class Requests(unittest.TestCase):
    """Single requests through the fake with urllib, for the fixture and trace details."""

    def setUp(self):
        self.fake, run_dir = fake_for(self)
        self.ws = run_dir / "ws"
        (self.ws / ".drill" / "fixtures").mkdir(parents=True)
        register(self.fake.url, self.ws)

    def fixtures(self, table, files=None):
        (self.ws / ".drill" / "fixtures" / "http.json").write_text(json.dumps(table))
        for name, data in (files or {}).items():
            (self.ws / ".drill" / "fixtures" / name).write_bytes(data)

    def send(self, method, url, body=None, headers=None):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": self.fake.url, "http": self.fake.url}),
            urllib.request.HTTPSHandler(context=strict_context(self.fake.ca)))
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def trace(self):
        return read_trace(self.ws)

    def test_answers_with_status_headers_and_a_json_body(self):
        self.fixtures([{"method": "POST", "url": "https://testnet.garden/x", "status": 201,
                        "headers": {"X-Request-Id": "r1"}, "body": {"ok": True}}])
        status, headers, body = self.send("POST", "https://testnet.garden/x", b"hi")
        self.assertEqual((status, json.loads(body)), (201, {"ok": True}))
        self.assertEqual(headers["x-request-id"], "r1")
        self.assertEqual(headers["content-type"], "application/json")

    def test_method_defaults_to_get_and_a_string_body_is_text(self):
        self.fixtures([{"url": "https://testnet.garden/*", "body": "plain"}])
        status, headers, body = self.send("GET", "https://testnet.garden/a/b?c=1")
        self.assertEqual((status, body, headers["content-type"]), (200, b"plain", "text/plain"))
        self.assertEqual(self.trace()[0]["url"], "https://testnet.garden/a/b?c=1")

    def test_body_file_is_read_from_the_fixtures_dir(self):
        self.fixtures([{"url": "https://testnet.garden/blob", "body_file": "blob.bin"}], {"blob.bin": b"\x00\x01"})
        self.assertEqual(self.send("GET", "https://testnet.garden/blob")[2], b"\x00\x01")

    def test_a_broken_entry_is_a_miss(self):
        self.fixtures([{"url": "https://testnet.garden/gone", "body_file": "gone.bin"},
                       {"url": "https://testnet.garden/out", "body_file": "../../trace.jsonl"},
                       {"url": "https://testnet.garden/odd", "status": "two hundred"}])
        for path in ("gone", "out", "odd"):
            status, headers, body = self.send("GET", f"https://testnet.garden/{path}")
            self.assertEqual((status, headers["x-drill-fixture"]), (502, "miss"), path)
            self.assertIn(b"bad fixture", body)
        self.assertEqual([t["fixture"] for t in self.trace()], [None, None, None])

    def test_first_match_wins(self):
        self.fixtures([{"url": "https://testnet.garden/a", "body": "exact"}, {"url": "https://testnet.garden/*", "body": "glob"}])
        self.assertEqual(self.send("GET", "https://testnet.garden/a")[2], b"exact")
        self.assertEqual(self.send("GET", "https://testnet.garden/b")[2], b"glob")

    def test_identical_entries_answer_in_sequence_and_the_last_sticks(self):
        self.fixtures([{"url": "https://testnet.garden/poll", "body": s} for s in ("queued", "sent", "funded")])
        got = [self.send("GET", "https://testnet.garden/poll")[2] for _ in range(4)]
        self.assertEqual(got, [b"queued", b"sent", b"funded", b"funded"])
        self.assertEqual([t["fixture"] for t in self.trace()], [0, 1, 2, 2])

    def test_request_bodies_are_not_matched(self):
        self.fixtures([{"method": "POST", "url": "https://testnet.garden/c", "body": "ok"}])
        self.assertEqual(self.send("POST", "https://testnet.garden/c", b'{"a":1}')[0], 200)
        self.assertEqual(self.send("POST", "https://testnet.garden/c", b'{"a": 2}')[0], 200)

    def test_trace_line_lowercases_headers_and_drops_hop_by_hop_and_proxy_ones(self):
        self.fixtures([{"method": "POST", "url": "https://testnet.garden/c"}])
        self.send("POST", "https://testnet.garden/c", "zażółć".encode(),
                  {"X-Api-Key": "k", "Content-Type": "text/plain", "Connection": "close", "Proxy-Authorization": "p"})
        line = self.trace()[0]
        self.assertEqual(list(line), ["method", "url", "headers", "body", "status", "fixture"])
        self.assertEqual(line["body"], "zażółć")
        self.assertEqual(line["headers"]["x-api-key"], "k")
        self.assertEqual(line["headers"]["content-type"], "text/plain")
        for gone in ("connection", "proxy-authorization", "host", "content-length"):
            self.assertNotIn(gone, line["headers"])

    def test_a_body_that_is_not_utf8_is_traced_as_base64(self):
        self.fixtures([{"method": "PUT", "url": "https://testnet.garden/c"}])
        self.send("PUT", "https://testnet.garden/c", b"\xff\xfe")
        self.assertEqual(self.trace()[0]["body"], {"base64": "//4="})

    def test_plain_http_to_a_faked_host_is_answered_too(self):
        self.fixtures([{"url": "http://testnet.garden/p", "body": "plain http"}])
        self.assertEqual(self.send("GET", "http://testnet.garden/p")[:3:2], (200, b"plain http"))
        self.assertEqual(self.trace()[0]["url"], "http://testnet.garden/p")

    def test_a_missing_fixture_table_misses(self):
        status, headers, _ = self.send("GET", "https://testnet.garden/nothing")
        self.assertEqual((status, headers["x-drill-fixture"]), (502, "miss"))
        self.assertIsNone(self.trace()[0]["fixture"])

    def test_registering_a_new_workspace_moves_the_trace(self):
        self.fixtures([{"url": "https://testnet.garden/*"}])
        self.send("GET", "https://testnet.garden/one")
        other = self.ws.parent / "other"
        shutil.copytree(self.ws / ".drill" / "fixtures", other / ".drill" / "fixtures")
        register(self.fake.url, other)
        self.send("GET", "https://testnet.garden/two")
        self.assertEqual([t["url"] for t in self.trace()], ["https://testnet.garden/one"])
        self.assertEqual([t["url"] for t in read_trace(other)], ["https://testnet.garden/two"])


class Unfaked(unittest.TestCase):
    def test_connect_to_an_unfaked_host_is_tunnelled_to_the_upstream_proxy(self):
        upstream, _ = fake_for(self, hosts=("api.example",))
        ws = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ws, True)
        (ws / ".drill" / "fixtures").mkdir(parents=True)
        (ws / ".drill" / "fixtures" / "http.json").write_text(json.dumps([{"url": "https://api.example/v1", "body": "up"}]))
        register(upstream.url, ws)
        fake, run_dir = fake_for(self)
        fake.upstream = upstream.url
        both = run_dir / "both.pem"
        both.write_text(fake.ca.read_text() + upstream.ca.read_text())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": fake.url}),
                                             urllib.request.HTTPSHandler(context=strict_context(both)))
        with opener.open("https://api.example/v1", timeout=10) as r:
            self.assertEqual(r.read(), b"up")
        self.assertEqual([t["url"] for t in read_trace(ws)], ["https://api.example/v1"])


class CA(unittest.TestCase):
    def test_the_ca_validates_under_strict_x509_checks(self):
        fake, _ = fake_for(self)
        with socket.create_connection(("127.0.0.1", int(fake.url.rsplit(":", 1)[1]))) as raw:
            raw.sendall(b"CONNECT testnet.garden:443 HTTP/1.1\r\nHost: testnet.garden:443\r\n\r\n")
            self.assertIn(b" 200 ", raw.recv(4096))
            with strict_context(fake.ca).wrap_socket(raw, server_hostname=HOST) as tls:
                self.assertEqual(dict(x[0] for x in tls.getpeercert()["subject"])["commonName"], HOST)

    def test_a_missing_openssl_fails_with_a_clear_message(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.dict(os.environ, {"PATH": str(d)}):
            with self.assertRaises(NoOpenSSL) as e:
                mint_ca(d / "ca")
        self.assertIn("openssl", str(e.exception))
        self.assertIn("HTTP fake", str(e.exception))


if __name__ == "__main__":
    unittest.main()
