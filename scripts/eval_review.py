"""Score the review pipeline on evals/review_cases.json against the configured AI provider.

A failure counts as addressed when its F: reference appears anywhere in the review: a draft's citation, a
draft's text, or a question. Models often cite the requirement line and name the failure in the text.

Uses the real server code in-process with a throwaway SQLite database. Provider and model come from the usual
PREFLIGHT_* settings, so with no Anthropic key this runs against local Ollama and costs nothing.

    uv run python scripts/eval_review.py --trials 2
"""

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from preflight.config import Settings
from preflight.db.base import Base
from preflight.server.auth import service
from preflight.server.main import create_app

PASSWORD = "eval correct horse battery"


def run_case(case: dict, settings: Settings) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(settings.model_copy(update={"database_url": f"sqlite:///{tmp}/eval.db", "rate_limit_per_minute": 10_000}))
        Base.metadata.create_all(app.state.engine)
        with app.state.sessionmaker() as db:
            service.create_user(db, "eval", PASSWORD)
            db.commit()
        api = TestClient(app)
        token = api.post("/api/v1/auth/token", json={"username": "eval", "password": PASSWORD}).json()["access_token"]
        api.headers["Authorization"] = f"Bearer {token}"
        api.post("/api/v1/projects", json={"name": "history"})
        ids = {}
        for f in case["history"]:
            ids[api.post("/api/v1/projects/history/failures", json=f).json()["failure"]["id"][:8]] = f["title"]
        api.post("/api/v1/projects", json={"name": "plan", "idea": case["idea"], "requirements": case["requirements"]})
        offered = [i["id"] for i in api.get("/api/v1/projects/plan/review-items").json()["items"]]
        started = time.monotonic()
        response = api.post("/api/v1/projects/plan/reviews", json={"approved_item_ids": offered})
        seconds = time.monotonic() - started
    if response.status_code != 201:
        return {"ok": False, "error": response.json().get("detail"), "seconds": seconds}
    review = response.json()
    everything = " ".join([d["source_ref"] + " " + d["text"] + " " + d["why"] for d in review["drafts"]] + [q["question"] for q in review["questions"]])
    cited = {title for short, title in ids.items() if f"F:{short}" in everything}
    total = len(review["drafts"]) + review["dropped_drafts"]
    asked = {q["category"] for q in review["questions"]}
    return {
        "ok": True,
        "recall": len(cited & set(case["relevant"])) / len(case["relevant"]),
        "irrelevant_cited": len(cited & set(case["irrelevant"])),
        "dropped_share": review["dropped_drafts"] / total if total else 0.0,
        "category_coverage": len(asked & set(case["categories"])) / len(case["categories"]),
        "questions": len(review["questions"]),
        "drafts": len(review["drafts"]),
        "seconds": round(seconds, 1),
        "cost_usd": review["cost_usd"],
        "cited_failures": sorted(cited),
        "drafts_detail": [f"{d['kind']} {d['source_ref']}: {d['text']}" for d in review["drafts"]],
        "questions_detail": [f"{q['category']}: {q['question']}" for q in review["questions"]],
    }


def summarize(values: list[float]) -> str:
    if not values:
        return "n/a"
    spread = f" (min {min(values):.2f}, max {max(values):.2f})" if len(values) > 1 else ""
    return f"{statistics.mean(values):.2f}{spread}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--cases", default=str(ROOT / "evals" / "review_cases.json"))
    parser.add_argument("--out", default=str(ROOT / "evals" / "results"))
    parser.add_argument("--only", help="Run one case by name.")
    args = parser.parse_args()

    settings = Settings()
    probe = create_app(settings.model_copy(update={"database_url": "sqlite://"})).state.ai
    if probe is None or not probe.available():
        raise SystemExit("No AI provider is available. Set PREFLIGHT_ANTHROPIC_API_KEY or start Ollama.")
    label = f"{probe.provider}:{probe.model_for('review')}"
    print(f"Evaluating {label}, {args.trials} trial(s) per case")

    cases = json.loads(Path(args.cases).read_text())
    cases = [c for c in cases if not args.only or c["name"] == args.only]
    results = {}
    for case in cases:
        results[case["name"]] = [run_case(case, settings) for _ in range(args.trials)]
        ok = [r for r in results[case["name"]] if r["ok"]]
        print(f"  {case['name']}: {len(ok)}/{args.trials} ok, recall {summarize([r['recall'] for r in ok])}, "
              f"irrelevant cited {summarize([r['irrelevant_cited'] for r in ok])}, "
              f"dropped {summarize([r['dropped_share'] for r in ok])}, "
              f"categories {summarize([r['category_coverage'] for r in ok])}, {summarize([r['seconds'] for r in ok])} s")
    every = [r for runs in results.values() for r in runs if r["ok"]]
    runs = sum(len(v) for v in results.values())
    print(f"Overall: {len(every)}/{runs} runs ok, recall {summarize([r['recall'] for r in every])}, "
          f"irrelevant cited per run {summarize([r['irrelevant_cited'] for r in every])}, "
          f"dropped share {summarize([r['dropped_share'] for r in every])}, "
          f"category coverage {summarize([r['category_coverage'] for r in every])}, "
          f"cost ${sum(r['cost_usd'] for r in every):.4f}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{stamp}-{label.replace(':', '_').replace('/', '_')}.json"
    path.write_text(json.dumps({"model": label, "trials": args.trials, "results": results}, indent=2))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
