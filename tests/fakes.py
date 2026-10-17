import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from preflight.ai.client import Completion


class FakeAI:
    """In-process AI client. Replies come from `replies` in order, or from `reply_for` when the list runs out."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def complete(self, *, model, system, content, schema, max_tokens) -> Completion:
        self.calls.append({"model": model, "system": system, "content": content, "schema": schema})
        text = self.replies.pop(0) if self.replies else reply_for(schema, "\n".join(b["text"] for b in content))
        return Completion(text=text, model=model, input_tokens=1000, output_tokens=200)


def reply_for(schema: dict, text: str) -> str:
    """A plausible reply that cites real references found in the material, plus one invented reference."""
    props = schema.get("properties", {})
    if "questions" in props:
        failure = re.search(r"F:[0-9a-f]{8}", text)
        requirement = re.search(r"REQ:\d+", text)
        items = []
        if failure:
            items.append({"text": "Every provider call has a total deadline across retries", "why": "the retry loop hung before",
                          "source_ref": failure.group(), "priority": "high"})
        items.append({"text": "Made-up check", "why": "no source", "source_ref": "F:deadbeef", "priority": "low"})
        gaps = [{"text": "Define behavior when the provider returns 429", "why": "retries are mentioned without rate limits",
                 "source_ref": requirement.group()}] if requirement else []
        return json.dumps({
            "questions": [
                {"category": "missed edge case", "question": "What are the max attempts, backoff, and total deadline for retries?"},
                {"category": "state or concurrency bug", "question": "How do per-user budget updates stay atomic?"},
                {"category": "wrong assumption", "question": "What happens to requests when the cache is down?"},
            ],
            "gaps": gaps,
            "checklist_items": items,
        })
    if "root_cause" in props:
        return json.dumps({"category": "missed edge case", "root_cause": "No deadline on the call", "prevention": "Set a total deadline"})
    if "checklist_text" in props:
        return json.dumps({"checklist_text": "Shared counters change only inside one atomic update"})
    if "score" in props:
        return json.dumps({"score": 4, "feedback": "Right cause, prevention slightly vague."})
    raise AssertionError(f"unexpected schema {props}")


class FakeAnthropic:
    """A local HTTP server that answers POST /v1/messages like the Messages API, recording every request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.next_stop_reason: str | None = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                material = "\n".join(b["text"] for b in body["messages"][0]["content"])
                stop = fake.next_stop_reason or "end_turn"
                fake.next_stop_reason = None
                text = "" if stop == "refusal" else reply_for(body["output_config"]["format"]["schema"], material)
                reply = {
                    "id": "msg_fake", "type": "message", "role": "assistant", "model": body["model"],
                    "content": [{"type": "text", "text": text}] if text else [],
                    "stop_reason": stop, "stop_sequence": None,
                    "usage": {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                }
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class FixtureSite:
    """A local website for capture checks: path -> (status, headers, body)."""

    def __init__(self, pages: dict[str, tuple[int, dict, str]]):
        self.hits: list[str] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                site.hits.append(self.path)
                status, headers, body = pages.get(self.path, (404, {}, "not found"))
                data = body.encode()
                self.send_response(status)
                for key, value in {"content-type": "text/html; charset=utf-8", **headers}.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
