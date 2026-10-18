import json
import os

import httpx
import pytest

from preflight.ai.client import AIError, AnthropicClient
from preflight.ai.ollama import OllamaClient
from preflight.ai.providers import make_client
from preflight.ai.schemas import ClassifyOutput, strict_schema
from preflight.config import Settings
from preflight.db.models import AiCall
from tests.fakes import reply_for
from tests.test_capture import fail, new_project

TAGS = {"models": [{"name": "gemma4:12b", "size": 7_556_508_396}, {"name": "mistral:latest", "size": 4_100_000_000}]}


class FakeOllama:
    def __init__(self, done_reason="stop", status=200):
        self.chats: list[dict] = []
        self.done_reason, self.status = done_reason, status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        body = json.loads(request.content)
        self.chats.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "model 'nope' not found"})
        text = reply_for(body["format"], body["messages"][1]["content"])
        return httpx.Response(200, json={"model": body["model"], "message": {"role": "assistant", "content": text},
                                         "done_reason": self.done_reason, "prompt_eval_count": 900, "eval_count": 120})


def client(fake, model=None, small=None) -> OllamaClient:
    return OllamaClient("http://127.0.0.1:11434", model, small, 8192, transport=httpx.MockTransport(fake))


def test_request_carries_the_schema_and_context_and_reply_is_parsed():
    fake = FakeOllama()
    schema = strict_schema(ClassifyOutput)
    completion = client(fake, "gemma4:12b").complete(
        model="gemma4:12b", system="Classify.", content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        schema=schema, max_tokens=512,
    )
    sent = fake.chats[-1]
    assert sent["format"] == schema and sent["stream"] is False and sent["think"] is False
    assert sent["options"] == {"temperature": 0, "num_ctx": 8192, "num_predict": 512}
    assert sent["messages"] == [{"role": "system", "content": "Classify."}, {"role": "user", "content": "a\n\nb"}]
    assert ClassifyOutput.model_validate_json(completion.text).category == "missed edge case"
    assert (completion.input_tokens, completion.output_tokens) == (900, 120)


def test_without_a_configured_model_the_largest_installed_one_is_used():
    ollama = client(FakeOllama())
    assert ollama.model_for("review") == "gemma4:12b" and ollama.model_for("small") == "gemma4:12b"
    assert client(FakeOllama(), "mistral:latest").model_for("small") == "mistral:latest"


def test_availability_needs_the_server_and_the_configured_models():
    assert client(FakeOllama()).available()
    assert not client(FakeOllama(), "llama9:70b").available()
    down = OllamaClient("http://127.0.0.1:9", None, None, 8192, transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    assert not down.available()
    with pytest.raises(AIError, match="No Ollama models"):
        down.model_for("review")


def test_errors_and_cut_off_replies_are_reported():
    with pytest.raises(AIError, match="cut off"):
        client(FakeOllama(done_reason="length"), "gemma4:12b").complete(
            model="gemma4:12b", system="s", content=[{"type": "text", "text": "x"}], schema=strict_schema(ClassifyOutput), max_tokens=5)
    with pytest.raises(AIError, match="not found"):
        client(FakeOllama(status=404), "gemma4:12b").complete(
            model="gemma4:12b", system="s", content=[{"type": "text", "text": "x"}], schema=strict_schema(ClassifyOutput), max_tokens=5)


def test_local_models_are_free():
    assert client(FakeOllama()).price("gemma4:12b", 10**6, 10**6) == 0.0


@pytest.mark.parametrize(("provider", "key", "expected"), [
    ("auto", "sk-x", AnthropicClient), ("auto", None, OllamaClient), ("ollama", "sk-x", OllamaClient),
    ("anthropic", None, type(None)), ("off", "sk-x", type(None)),
])
def test_provider_choice(provider, key, expected):
    settings = Settings(ai_provider=provider, anthropic_api_key=key, _env_file=None)
    assert isinstance(make_client(settings), expected)


def test_a_review_runs_through_ollama_and_costs_nothing(app, api):
    fake = FakeOllama()
    app.state.ai = client(fake)
    app.state.settings.ai_monthly_budget_usd = 0.0
    new_project(api, "old")
    fail(api, "old", "Retry hang", severity="high", prevention="Every call has a deadline")
    new_project(api, "gateway", requirements="Retries on provider errors.")
    found = api.get("/api/v1/projects/gateway/review-items").json()
    assert (found["provider"], found["model"], found["max_cost_usd"]) == ("ollama", "gemma4:12b", 0.0)
    created = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1", "history"]})
    assert created.status_code == 201, created.text
    assert len(created.json()["questions"]) == 3 and created.json()["cost_usd"] == 0.0
    with app.state.sessionmaker() as db:
        call = db.query(AiCall).one()
    assert call.model == "gemma4:12b" and "REQ:1 Retries on provider errors." in call.payload


def test_review_items_say_when_ollama_is_down(app, api):
    app.state.ai = OllamaClient("http://127.0.0.1:9", None, None, 8192,
                                transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    new_project(api, "gateway", requirements="x")
    found = api.get("/api/v1/projects/gateway/review-items").json()
    assert found["ai_available"] is False and found["provider"] == "ollama"


@pytest.mark.skipif(os.environ.get("PREFLIGHT_TEST_OLLAMA") != "1", reason="set PREFLIGHT_TEST_OLLAMA=1 to call the local Ollama")
def test_live_ollama_classifies_a_failure():
    ollama = OllamaClient(os.environ.get("PREFLIGHT_OLLAMA_URL", "http://127.0.0.1:11434"), os.environ.get("PREFLIGHT_OLLAMA_MODEL"), None, 8192)
    assert ollama.available()
    completion = ollama.complete(model=ollama.model_for("small"), system="Classify one software failure.",
                                 content=[{"type": "text", "text": "Retry loop hung 40 minutes: the provider call had no timeout."}],
                                 schema=strict_schema(ClassifyOutput), max_tokens=512)
    parsed = ClassifyOutput.model_validate_json(completion.text)
    assert parsed.root_cause.strip() and parsed.prevention.strip() and completion.output_tokens > 0
