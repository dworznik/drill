#!/usr/bin/env python3
"""Throwaway probe: a CONNECT proxy that fakes testnet.garden and tunnels everything else upstream.

Records every faked request as one JSON line in TRACE. Anything for a host not in FAKED is
forwarded verbatim to UPSTREAM (the proxy that was HTTPS_PROXY before we took the slot), so the
claude process's own traffic keeps working. Stdlib only.
"""
import json, os, socket, ssl, sys, threading, time
from urllib.parse import urlparse

HOST, PORT = "127.0.0.1", int(os.environ.get("FAKE_PORT", "4443"))
UPSTREAM = os.environ.get("UPSTREAM")  # host:port of the previous HTTPS_PROXY, or None for direct
CA_DIR = os.environ.get("CA_DIR", "/tmp/drill-probe/ca")
TRACE = os.environ.get("TRACE", "/tmp/drill-probe/trace.jsonl")
LOG = os.environ.get("LOG", "/tmp/drill-probe/proxy.log")
FAKED = {"testnet.garden"}
lock = threading.Lock()


def log(*a):
    with lock, open(LOG, "a") as f:
        f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")


def trace(rec):
    with lock, open(TRACE, "a") as f:
        f.write(json.dumps(rec) + "\n")


def read_head(sock):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return None, b""
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head.decode("latin1"), rest


def parse(head):
    lines = head.split("\r\n")
    method, target, version = lines[0].split(" ", 2)
    headers = {}
    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return method, target, version, headers


def read_body(sock, rest, headers):
    n = int(headers.get("content-length", "0") or 0)
    body = rest
    while len(body) < n:
        chunk = sock.recv(65536)
        if not chunk:
            break
        body += chunk
    return body[:n]


def answer_faked(sock, scheme, host, head, rest, via):
    method, target, version, headers = parse(head)
    body = read_body(sock, rest, headers)
    path = target if target.startswith("/") else urlparse(target).path or "/"
    rec = {"via": via, "scheme": scheme, "host": host, "method": method, "path": path,
           "headers": {k: headers.get(k) for k in ("content-type", "user-agent", "authorization") if k in headers},
           "body": body.decode("utf-8", "replace")}
    trace(rec)
    log("FAKED", json.dumps(rec))
    payload = json.dumps({"ok": True, "echo": {"method": method, "path": path, "via": via}}).encode()
    sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(payload) + payload)


def relay(a, b):
    def pump(src, dst):
        try:
            while True:
                d = src.recv(65536)
                if not d:
                    break
                dst.sendall(d)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    t = threading.Thread(target=pump, args=(b, a), daemon=True)
    t.start()
    pump(a, b)
    t.join(timeout=5)


def tunnel_upstream(client, raw_head, rest, hostport):
    try:
        if UPSTREAM:
            uh, up = UPSTREAM.rsplit(":", 1)
            up_sock = socket.create_connection((uh, int(up)), timeout=20)
            up_sock.sendall(raw_head.encode("latin1") + b"\r\n\r\n" + rest)
        else:
            h, p = hostport.rsplit(":", 1)
            up_sock = socket.create_connection((h, int(p)), timeout=20)
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                up_sock.sendall(rest)
        log("TUNNEL", hostport, "->", UPSTREAM or "direct")
        relay(client, up_sock)
    except OSError as e:
        log("TUNNEL-FAIL", hostport, e)
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass


def handle(client, addr):
    try:
        head, rest = read_head(client)
        if head is None:
            return
        method, target, version, headers = parse(head)
        if method == "CONNECT":
            host = target.rsplit(":", 1)[0]
            if host in FAKED:
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(f"{CA_DIR}/leaf.pem", f"{CA_DIR}/leaf.key")
                try:
                    tls = ctx.wrap_socket(client, server_side=True)
                except ssl.SSLError as e:
                    log("TLS-FAIL", host, e)
                    trace({"via": "connect", "host": host, "tls_error": str(e)})
                    return
                h2, r2 = read_head(tls)
                if h2:
                    answer_faked(tls, "https", host, h2, r2, "connect")
                return
            tunnel_upstream(client, head, rest, target)
            return
        # absolute-form plain HTTP
        u = urlparse(target)
        if u.hostname in FAKED:
            answer_faked(client, "http", u.hostname, head, rest, "absolute")
            return
        log("PLAIN-OTHER", method, target)
        client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
    except Exception as e:  # noqa
        log("ERR", addr, repr(e))
    finally:
        try:
            client.close()
        except OSError:
            pass


def main():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(64)
    log("LISTEN", f"{HOST}:{PORT}", "upstream", UPSTREAM)
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()


if __name__ == "__main__":
    main()
