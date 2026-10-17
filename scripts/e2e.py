"""End-to-end check: real migration, real server, real CLI binary, real dashboard forms.

Run with `uv run python scripts/e2e.py`. Exits non-zero on the first failed check.
"""

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fakes import FakeAnthropic

PASSWORD = "e2e correct horse battery"
TIMEOUT_CHECK = "Every external call has a timeout, retry cap, and backoff"
ATOMIC_CHECK = "Read-then-write on shared state is atomic"
CACHE_CHECK = "Cache lookup cannot block the request path"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Run:
    def __init__(self, workdir: Path, anthropic: FakeAnthropic):
        self.anthropic = anthropic
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {
            **os.environ,
            "PREFLIGHT_DATABASE_URL": os.environ.get("PREFLIGHT_E2E_DATABASE_URL", f"sqlite:///{workdir / 'e2e.db'}"),
            "PREFLIGHT_DEVICE_POLL_SECONDS": "1",
            "PREFLIGHT_API_URL": self.base,
            "PREFLIGHT_NO_KEYRING": "1",
            "COLUMNS": "250",
            "PREFLIGHT_ANTHROPIC_API_KEY": "sk-e2e-fake",
            "PREFLIGHT_ANTHROPIC_BASE_URL": anthropic.url,
            "PREFLIGHT_CONFIG_DIR": str(workdir / "laptop"),
        }
        self.workdir = workdir
        self.checks = 0

    def ok(self, condition: bool, message: str, detail: str = "") -> None:
        if not condition:
            print(f"FAIL  {message}\n{detail}")
            raise SystemExit(1)
        self.checks += 1
        print(f"ok    {message}")

    def sh(self, *args: str, input: str | None = None, env: dict | None = None, expect: int = 0) -> str:
        result = subprocess.run(
            args, cwd=ROOT, env={**self.env, **(env or {})}, input=input, capture_output=True, text=True, timeout=60, check=False
        )
        output = result.stdout + result.stderr
        if result.returncode != expect:
            print(f"FAIL  `{' '.join(args)}` exited {result.returncode}, expected {expect}\n{output}")
            raise SystemExit(1)
        return output

    def cli(self, *args: str, **kwargs) -> str:
        return self.sh("preflight", *args, **kwargs)

    def start_server(self) -> None:
        self.server = subprocess.Popen(
            ["uvicorn", "preflight.server.asgi:app", "--port", str(self.port)],
            cwd=ROOT, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                if httpx.get(f"{self.base}/healthz").status_code == 200:
                    return
            except httpx.TransportError:
                time.sleep(0.1)
        raise SystemExit("server did not start")

    def stop_server(self) -> None:
        self.server.terminate()
        self.server.wait(timeout=10)


def dashboard_login(base: str) -> httpx.Client:
    web = httpx.Client(base_url=base, follow_redirects=False)
    token = re.search(r'name="csrf_token" value="([^"]+)"', web.get("/login").text).group(1)
    response = web.post("/login", data={"username": "me", "password": PASSWORD, "csrf_token": token})
    assert response.status_code == 303, response.text
    return web


def page_csrf(web: httpx.Client, path: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', web.get(path).text).group(1)


def main() -> None:
    anthropic = FakeAnthropic()
    with tempfile.TemporaryDirectory() as tmp:
        run = Run(Path(tmp), anthropic)
        run.sh("alembic", "upgrade", "head")
        tables = run.sh("python", "-c", "from preflight.config import Settings; from sqlalchemy import create_engine, inspect; print(sorted(inspect(create_engine(Settings().database_url)).get_table_names()))")
        run.ok("'checklist_items'" in tables and "'users'" in tables, "alembic migration created the schema", tables)
        out = run.sh("preflight-server", "create-user", "--username", "me", env={"PREFLIGHT_ADMIN_PASSWORD": PASSWORD})
        run.ok("created user me" in out, "admin command created the dashboard account")

        run.start_server()
        try:
            run.ok(httpx.get(f"{run.base}/healthz").json() == {"status": "ok"}, "server answers /healthz")
            scenario(run)
        finally:
            run.stop_server()
            anthropic.close()
        print(f"\nAll {run.checks} end-to-end checks passed.")


def scenario(run: Run) -> None:
    unauthenticated = run.cli("billing", "status", expect=1)
    run.ok("Not signed in" in unauthenticated, "CLI refuses to work before login")

    login = subprocess.Popen(
        ["preflight", "login", "--no-browser"], cwd=ROOT, env=run.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    first_line = login.stdout.readline()
    code = re.search(r"Your code: (\S+)", first_line)
    run.ok(code is not None, "device login prints a short code", first_line)
    web = dashboard_login(run.base)
    approved = web.post("/device", data={"code": code.group(1), "csrf_token": page_csrf(web, "/device")})
    run.ok("Connected" in approved.text, "dashboard approves the device code")
    login_out = login.communicate(timeout=30)[0]
    run.ok(login.returncode == 0 and "Signed in." in login_out, "CLI receives its device token", login_out)
    creds = (Path(run.env["PREFLIGHT_CONFIG_DIR"]) / "credentials.json")
    run.ok(oct(creds.stat().st_mode & 0o777) == "0o600", "token file is readable only by the owner")
    run.ok(PASSWORD not in creds.read_text(), "the password is never stored by the CLI")

    desktop = {"PREFLIGHT_CONFIG_DIR": str(run.workdir / "desktop")}
    out = run.cli("login", "--password", input=f"me\n{PASSWORD}\n\n", env=desktop)
    run.ok("Signed in." in out, "password fallback signs in a second device with a hidden prompt")
    devices = run.cli("auth", "devices")
    run.ok(len(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}", devices)) == 2, "two devices are listed", devices)

    run.cli("new", "billing", "--no-input")
    out = run.cli("billing", "start", "--no-input")
    run.ok("Checklist is empty" in out and "Project started." in out, "first project starts with an empty checklist")
    for args in (
        ["Retry loop hung 40 minutes", "--category", "missed edge case", "--severity", "high", "--minutes", "40",
         "--prevention", TIMEOUT_CHECK, "--guess", "DNS", "--cause", "no timeout on the provider call", "--guess-wrong"],
        ["Budget counter lost updates", "--category", "state or concurrency bug", "--minutes", "25", "--prevention", ATOMIC_CHECK],
        ["Two workers double-charged", "--category", "state or concurrency bug", "--minutes", "15", "--prevention", ATOMIC_CHECK],
        ["Cache outage blocked requests", "--category", "wrong assumption", "--severity", "high", "--prevention", CACHE_CHECK],
        ["Typo in log message", "--category", "no verification", "--severity", "low"],
    ):
        out = run.cli("billing", "fail", *args, "--no-input")
        run.ok("Logged failure #" in out, f"logged history failure: {args[0]}")

    rules = run.cli("rules")
    run.ok("state or concurrency bug (2 failures across projects)" in rules and ATOMIC_CHECK in rules,
           "repeated pattern is detected with a rule drafted from prevention notes", rules)

    req = run.workdir / "requirements.md"
    req.write_text("Proxy for LLM providers with fallback, caching, and cost tracking.\nRetries on provider errors.\n")
    out = run.cli("new", "llm-gateway", "--idea", "LLM proxy", "--goals", "p95 under 800ms", "--requirements", str(req), "--no-input")
    run.ok("Created llm-gateway" in out, "created project with requirements from a file")
    out = run.cli("llm-gateway", "requirements", "--text", req.read_text() + "Per-user budgets.\n")
    run.ok("Saved requirements v2" in out, "editing requirements creates v2")
    out = run.cli("llm-gateway", "requirements", "--text", req.read_text() + "Per-user budgets.\n")
    run.ok("Unchanged, still v2" in out, "saving identical requirements does not create a version")
    out = run.cli("new", "drill", "--no-input", expect=2)
    run.ok("reserved command name" in out, "a project cannot be named like a command")

    out = run.cli("llm-gateway", "start", "--no-input")
    run.ok("Checklist (3 required, 1 optional, built from requirements v2)" in out, "checklist built from history and v2", out)
    run.ok(all(t in out for t in (TIMEOUT_CHECK, ATOMIC_CHECK, CACHE_CHECK)), "required items come from past failures", out)
    run.ok("3 required item(s) to resolve" in out, "gate blocks the start while required items are open")
    status = run.cli("llm-gateway", "status")
    run.ok("is draft" in status, "project is still a draft")

    out = run.cli("llm-gateway", "check", "1", "na", expect=1)
    run.ok("needs a reason" in out, "not applicable without a reason is refused")
    lines = {m.group(2).strip(): m.group(1) for m in re.finditer(r"^\s*(\d+)\s+(?:required|optional)\s+(.+?)\s{2,}", run.cli("llm-gateway", "checklist"), re.MULTILINE)}
    run.cli("llm-gateway", "check", lines[TIMEOUT_CHECK], "done")
    run.cli("llm-gateway", "check", lines[CACHE_CHECK], "defer", "--reason", "after the load test", "--until", "2099-01-01")
    out = run.cli("llm-gateway", "check", lines[ATOMIC_CHECK], "done")
    run.ok("Required items resolved. Project started." in out, "resolving the last required item starts the project", out)
    run.ok("is started" in run.cli("llm-gateway", "status"), "status shows the project as started")

    out = run.cli("llm-gateway", "fail", "Semantic cache returned stale answers and the lookup blocked requests",
                  input="the cache\nTTL ignored\nkeyed on prompt hash\nadd a lookup deadline\nCache lookup cannot block the request path\n2\nmedium\n20\nn\n" + lines[CACHE_CHECK] + "\n")
    run.ok("Related checklist items: " + lines[CACHE_CHECK] + "." in out, "interactive fail shows the related checklist item", out)
    run.ok(f"Linked to item {lines[CACHE_CHECK]}." in out, "failure linked to the checklist item")
    run.ok("Similar: #" in out and "Cache outage blocked requests" in out, "similar past failure is shown")

    out = run.cli("llm-gateway", "fail", "Surprise outage", "--no-input")
    run.ok("Logged failure" in out, "one-line failure logging works with no prompts")
    run.cli("llm-gateway", "win", "Caught 429 handling early", "--tag", "caught a risk early")

    run.stop_server()
    out = run.cli("llm-gateway", "fail", "Logged on a plane", "--no-input")
    run.ok("Saved offline" in out, "failure is queued when the server is down")
    out = run.cli("llm-gateway", "win", "Shipped offline mode")
    run.ok("Saved offline" in out, "win is queued when the server is down")
    run.start_server()
    queue_file = Path(run.env["PREFLIGHT_CONFIG_DIR"]) / "queue.jsonl"
    queued_body = queue_file.read_text()
    out = run.cli("llm-gateway", "status")
    run.ok("Sent 2 queued entries." in out, "queue flushes on the next command", out)
    run.ok("Failures: 3 (20 min lost). Wins: 2." in out, "status counts include the replayed entries", out)
    queue_file.write_text(queued_body)
    out = run.cli("llm-gateway", "status")
    run.ok("Failures: 3 (20 min lost). Wins: 2." in out, "replaying the same queue again does not duplicate", out)

    md = run.cli("llm-gateway", "checklist", "--md")
    run.ok(md.startswith("# Checklist: llm-gateway") and f"- [x] **{lines[TIMEOUT_CHECK]}.** {TIMEOUT_CHECK}" in md, "checklist exports as Markdown")
    found = run.cli("search", "stale TTL")
    run.ok("Semantic cache returned stale" in found and "Retry loop" not in found, "search finds failures by text", found)

    pattern = re.search(r"([0-9a-f]{8})\s+.*state or concurrency bug \(2 failures across", rules).group(1)
    out = run.cli("rules", "approve", pattern, "--text", "Rule: shared-state-safety")
    run.ok("is active" in out, "a suggested rule can be approved")
    run.cli("new", "next-thing", "--no-input")
    out = run.cli("next-thing", "start", "--no-input")
    run.ok("Rule: shared-state-safety" in out and ATOMIC_CHECK not in out, "the rule replaces its failures on the next checklist", out)
    out = run.cli("next-thing", "start", "--override", "spike, accept the risk")
    run.ok("Gate overridden" in out, "the gate can be overridden with a typed reason")

    ai_review(run)

    home = web.get("/")
    run.ok(home.status_code == 200 and 'href="/projects/llm-gateway"' in home.text, "dashboard lists projects")
    page = web.get("/projects/llm-gateway").text
    run.ok(TIMEOUT_CHECK in page and "after the load test" in page and "Semantic cache" in page, "project page shows checklist and failures")
    bearer = httpx.get(f"{run.base}/", headers={"Authorization": "Bearer nope"}, follow_redirects=False)
    run.ok(bearer.status_code == 303, "a bearer token cannot open the dashboard")

    desktop_id = re.findall(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", run.cli("auth", "devices"))[1]
    run.cli("auth", "revoke", desktop_id)
    out = run.cli("llm-gateway", "status", env=desktop, expect=1)
    run.ok("Run `preflight login`" in out, "a revoked device is locked out")
    run.ok("is started" in run.cli("llm-gateway", "status"), "other devices keep working after a revoke")
    run.ok(web.get("/").status_code == 200, "the dashboard session survives a device revoke")


def ai_review(run: Run) -> None:
    req = run.workdir / "ai-req.md"
    req.write_text("Retries on provider errors.\nPer-user budgets.\n")
    run.cli("new", "ai-gateway", "--idea", "SECRET-IDEA-TEXT", "--requirements", str(req), "--no-input")
    answers = "n\ny\ny\n" + "3 attempts, 2s backoff, 10s deadline\nn\n\n" + "y\ny\n" + "d\n" * 20
    out = run.cli("ai-gateway", "start", input=answers)
    run.ok("Will be sent to AI (claude-opus-5-5, at most about $" in out, "review shows the model and a cost ceiling", out)
    run.ok("[1] Idea and goals" in out and "[2] Requirements v1" in out and "[3] Failure history and rules" in out, "each item is offered for approval")
    run.ok("Q1 (missed edge case): What are the max attempts" in out, "questions are grouped by past failure category")
    run.ok("Dropped 1 suggestion(s) that cited nothing you sent." in out, "a suggestion with an invented reference is dropped")
    run.ok("Define behavior when the provider returns 429" in out and "No answer yet: What happens to requests when the cache is down?" in out,
           "accepted gap and the open question are on the checklist", out)
    run.ok("Every provider call has a total deadline across retries" in out, "accepted AI checklist item is on the checklist")
    run.ok("Required items resolved. Project started." in out, "walking the required items starts the project", out)

    sent = run.anthropic.requests[-1]
    material = "\n".join(b["text"] for b in sent["body"]["messages"][0]["content"])
    run.ok("REQ:1 Retries on provider errors." in material and "Retry loop hung 40 minutes" in material, "approved items reached the model")
    run.ok("SECRET-IDEA-TEXT" not in material, "an item that was not approved never left the server")
    run.ok(sent["body"]["model"] == "claude-opus-5-5" and sent["body"]["fallbacks"] == "default"
           and "server-side-fallback-2026-07-01" in sent["headers"]["anthropic-beta"], "review uses Opus 5.5 with refusal fallbacks")
    run.ok(sent["body"]["messages"][0]["content"][0].get("cache_control") == {"type": "ephemeral"}, "failure history is the cached prefix")

    out = run.cli("ai-gateway", "fail", "Webhook retries never stopped", input="\n" * 8 + "y\ny\nskip\n")
    run.ok("Root cause: No deadline on the call" in out and "Saved." in out, "AI classification is suggested, confirmed and saved", out)
    run.ok(run.anthropic.requests[-1]["body"]["model"] == "claude-haiku-5-5", "classification uses the cheaper model")
    found = run.cli("search", "Webhook")
    run.ok("missed edge case" in found and "No deadline on the call" in found, "suggested category and cause were stored", found)


if __name__ == "__main__":
    sys.exit(main())
