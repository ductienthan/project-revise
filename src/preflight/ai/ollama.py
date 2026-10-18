import json

import httpx

from preflight.ai.client import AIError, Completion, Role


class OllamaClient:
    """A local model served by Ollama. Calls cost nothing and the data never leaves the machine."""

    provider = "ollama"

    def __init__(self, url: str, model: str | None, small_model: str | None, context_tokens: int, transport=None):
        self.url = url.rstrip("/")
        self.context_tokens = context_tokens
        self.http = httpx.Client(base_url=self.url, timeout=httpx.Timeout(600, connect=3), transport=transport)
        self._models = {"review": model, "small": small_model or model}

    def installed(self) -> list[dict]:
        try:
            response = self.http.get("/api/tags", timeout=3)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return response.json().get("models", [])

    def available(self) -> bool:
        installed = {m["name"] for m in self.installed()}
        return bool(installed) and all(self.model_for(role) in installed for role in ("review", "small"))

    def model_for(self, role: Role) -> str:
        """The configured model, or the largest installed one when none is configured."""
        if self._models[role] is None:
            installed = self.installed()
            if not installed:
                raise AIError(f"No Ollama models found at {self.url}. Start Ollama and pull a model.")
            self._models[role] = max(installed, key=lambda m: m.get("size", 0))["name"]
        return self._models[role]

    def price(self, model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
        return 0.0

    def complete(self, *, model: str, system: str, content: list[dict], schema: dict, max_tokens: int) -> Completion:
        body = {
            "model": model,
            "stream": False,
            "think": False,
            "truncate": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": self.context_tokens, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n\n".join(block["text"] for block in content)},
            ],
        }
        try:
            response = self.http.post("/api/chat", content=json.dumps(body))
        except httpx.HTTPError as err:
            raise AIError(f"Could not reach Ollama at {self.url} ({err.__class__.__name__}).") from None
        if response.status_code != 200:
            raise AIError(_error_message(response, model))
        data = response.json()
        if data.get("done_reason") == "length":
            raise AIError("The reply was cut off at the output limit.")
        return Completion(
            text=data["message"]["content"],
            model=data.get("model", model),
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )


def _error_message(response: httpx.Response, model: str) -> str:
    try:
        error = response.json().get("error", response.text)
        if isinstance(error, str) and error.startswith("{"):
            error = json.loads(error).get("error", error)
    except ValueError:
        error = response.text
    if isinstance(error, dict) and error.get("type") == "exceed_context_size_error":
        return (
            f"The material is {error.get('n_prompt_tokens')} tokens but {model}'s context is {error.get('n_ctx')}. "
            "Raise PREFLIGHT_OLLAMA_CONTEXT_TOKENS or approve less."
        )
    message = error.get("message", error) if isinstance(error, dict) else error
    return f"Ollama returned an error: {message}"
