"""A scripted Anthropic Messages API, so real `claude plugin eval` runs with no model and no key.

The agent runs the script's commands through Bash, one per turn (by default `greet --name Ana`),
then says its reply. Requests without tools (the llm grader) get a passing verdict. Everything
else plugin eval does is real: its sandbox, drill's scaffold, the shims and the kept temp dirs.
"""

import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _tool_results(messages: list) -> int:
    return sum(1 for m in messages if isinstance(m.get("content"), list)
               for b in m["content"] if b.get("type") == "tool_result")


def _reply(req: dict, ids, commands: list[str], text: str) -> tuple[list, str]:
    tools = {t.get("name") for t in req.get("tools") or []}
    done = _tool_results(req.get("messages", []))
    if "Bash" in tools and done < len(commands):
        return [{"type": "tool_use", "id": f"toolu_{next(ids):04d}", "name": "Bash",
                 "input": {"command": commands[done], "description": f"Step {done + 1}"}}], "tool_use"
    if "Bash" in tools:
        return [{"type": "text", "text": text}], "end_turn"
    return [{"type": "text", "text": '{"pass": true, "score": 1, "reason": "scripted"}'}], "end_turn"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        self.server.requests.append(req)
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            return self._json({"input_tokens": 10})
        content, stop = _reply(req, self.server.ids, self.server.commands, self.server.text)
        msg = {"id": f"msg_{next(self.server.ids)}", "type": "message", "role": "assistant",
               "model": req.get("model", "fake"), "content": content, "stop_reason": stop, "stop_sequence": None,
               "usage": {"input_tokens": 10, "output_tokens": 5}}
        if not req.get("stream"):
            return self._json(msg)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def event(kind, data):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(dict(data, type=kind))}\n\n".encode())

        event("message_start", {"message": dict(msg, content=[], stop_reason=None)})
        for i, block in enumerate(content):
            if block["type"] == "text":
                event("content_block_start", {"index": i, "content_block": {"type": "text", "text": ""}})
                event("content_block_delta", {"index": i, "delta": {"type": "text_delta", "text": block["text"]}})
            else:
                event("content_block_start", {"index": i, "content_block": dict(block, input={})})
                event("content_block_delta", {"index": i, "delta": {"type": "input_json_delta",
                                                                    "partial_json": json.dumps(block["input"])}})
            event("content_block_stop", {"index": i})
        event("message_delta", {"delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 5}})
        event("message_stop", {})

    def do_GET(self):
        self._json({})

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(commands=("greet --name Ana",), text="Hi Ana, from greet") -> ThreadingHTTPServer:
    """Start the fake API on a free localhost port; its base URL is `server.url`. Call shutdown() when done."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests, server.ids = [], itertools.count(1)
    server.commands, server.text = list(commands), text
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
