"""Break each key protection on purpose and confirm the tests notice.

A passing suite proves little unless it fails when the code is wrong. Each mutation below removes one protection
that a requirement depends on, runs only the tests named for it, and expects them to fail. Mutations are applied to
a throwaway copy of src/ and tests/, so the working tree is never touched and uncommitted work can be checked.

    uv run python scripts/mutation_check.py
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "src/preflight/"


@dataclass(frozen=True)
class Mutation:
    guards: str
    file: str
    old: str
    new: str
    tests: tuple[str, ...]
    needs: str | None = None


MUTATIONS = [
    Mutation("FR-14 the gate never offers sensitive items", "ai/gate.py",
             "return [item for item in items if not item.sensitive]", "return list(items)",
             ("test_requirements_extra.py::test_the_gate_itself_never_offers_or_sends_sensitive_items",)),
    Mutation("FR-14 sensitive links are left out of review material", "core/review.py",
             "        if link.sensitive:\n            continue\n", "",
             ("test_review.py::test_unknown_or_sensitive_items_cannot_be_approved",)),
    Mutation("FR-13 only approved items are sent", "ai/gate.py",
             "return [item for item in items if item.id in approved_ids]", "return list(items)",
             ("test_review.py::test_only_approved_items_are_sent_and_the_payload_is_logged",)),
    Mutation("PRIV-1 drafts cite only what their call saw", "core/review.py",
             "if entry.source_ref not in seen:", "if False:",
             ("test_review.py::test_drafts_must_cite_material_that_was_actually_sent",
              "test_review_findings_3.py::test_a_tag_typed_into_requirements_does_not_open_unapproved_history")),
    Mutation("FR-22 the gate blocks unresolved required items", "core/lifecycle.py",
             "    if unresolved:\n        return StartResult(False, unresolved)\n", "",
             ("test_checklist.py::test_checklist_comes_from_history_with_sources_priorities_and_required_flags",
              "test_checklist.py::test_project_starts_when_the_last_required_item_is_resolved")),
    Mutation("FR-20 the gate counts not applicable only with a reason", "core/lifecycle.py",
             "case ItemStatus.NOT_APPLICABLE:\n            return bool(item.reason.strip())",
             "case ItemStatus.NOT_APPLICABLE:\n            return True",
             ("test_requirements_extra.py::test_the_gate_counts_an_item_resolved_only_with_its_reason_and_date",)),
    Mutation("FR-20 marking not applicable asks for a reason", "core/lifecycle.py",
             "if status == ItemStatus.NOT_APPLICABLE and not reason.strip():", "if False:",
             ("test_checklist.py::test_not_applicable_and_deferred_need_reasons_and_a_future_date",)),
    Mutation("DES-auth-web CSRF on cookie writes", "server/deps.py",
             "if request.method in UNSAFE_METHODS and not _csrf_ok(request, session):",
             "if False:",
             ("test_auth.py::test_cookie_requests_need_csrf_header_for_writes",)),
    Mutation("DES-data data never crosses accounts", "core/projects.py",
             "select(Project).where(Project.user_id == user_id, clause)", "select(Project).where(clause)",
             ("test_capture.py::test_other_users_cannot_see_or_touch_my_data",)),
    Mutation("DES-offline offline replay never duplicates", "core/failures.py",
             "existing = db.get(Failure, failure_id) if failure_id else None", "existing = None",
             ("test_capture.py::test_replaying_a_failure_id_does_not_duplicate",)),
    Mutation("DES-capture-safety only public addresses", "capture/fetch.py",
             "return ip.is_global", "return True",
             ("test_links.py::test_private_addresses_are_refused_even_through_a_redirect",
              "test_review_findings_2.py::test_shared_address_space_is_not_public")),
    Mutation("DES-capture-safety connect to the checked address", "capture/fetch.py",
             'self.http.build_request("GET", pinned,', 'self.http.build_request("GET", url,',
             ("test_review_findings_2.py::test_capture_connects_to_the_address_it_checked",)),
    Mutation("FR-6 internal links are never fetched", "core/links.py",
             "    if link.type == LinkType.INTERNAL:\n        raise DomainError(\"Internal links are references only and are never fetched.\")\n", "",
             ("test_links.py::test_internal_links_are_never_fetched",)),
    Mutation("DES-auth-web lockout after repeated failures", "server/auth/lockout.py",
             "return bool(entry and entry.locked_until and entry.locked_until > now)", "return False",
             ("test_auth.py::test_lockout_after_repeated_failures_blocks_correct_password_until_it_expires",)),
    Mutation("DES-ai-gate the monthly budget holds", "ai/gate.py",
             "if spent + worst.max_cost_usd > settings.ai_monthly_budget_usd:", "if False:",
             ("test_review.py::test_monthly_budget_counts_each_call_at_its_worst_case",
              "test_review_findings_2.py::test_concurrent_calls_cannot_overspend_the_budget")),
    Mutation("MET-repeat-rate uses a 60 day window", "core/metrics.py",
             "REPEAT_WINDOW = timedelta(days=60)", "REPEAT_WINDOW = timedelta(days=600)",
             ("test_metrics.py::test_repeat_rate_counts_category_repeats_within_60_days",)),
    Mutation("FR-37 a poor answer restarts the drill ladder", "core/drills.py",
             "    if score < PASSING_SCORE:\n        return LADDER[0]\n", "",
             ("test_practice.py::test_intervals_climb_the_ladder_then_grow_by_ease",)),
    Mutation("NFR-5 export never includes auth tables", "core/export.py",
             "models.Prediction, models.Win,", "models.Prediction, models.Win, models.Device,",
             ("test_progress.py::test_export_contains_my_data_and_no_auth_secrets",)),
    Mutation("DES-ai-local Ollama refuses over-long input", "ai/ollama.py",
             '"truncate": False,', '"truncate": True,',
             ("test_review_findings_3.py::test_ollama_refuses_input_longer_than_its_context_instead_of_truncating",)),
    Mutation("FR-43 patterns below the threshold are not suggested", "core/patterns.py",
             "return sorted(repeated, key=lambda p: (-p.failure_count, p.category))",
             "return sorted(existing.values(), key=lambda p: (-p.failure_count, p.category))",
             ("test_checklist.py::test_a_pattern_that_stops_repeating_is_dropped_and_no_longer_suggested",
              "test_checklist.py::test_an_approved_rule_survives_its_pattern_falling_below_the_threshold")),
    Mutation("FR-8 the browser guard blocks refused requests", "capture/browser.py",
             'page.route("**/*", guard)', "page.route('**/*', lambda route: route.continue_())",
             ("test_browser.py::test_the_browser_renders_javascript_and_every_request_passes_the_guard",), needs="playwright"),
]


def pytest(copy: Path, tests: tuple[str, ...]) -> int:
    nodes = [f"tests/{t}" for t in tests]
    env = {**os.environ, "PYTHONPATH": str(copy / "src")}
    return subprocess.run(["uv", "run", "--project", str(ROOT), "pytest", "-q", "-x", "-p", "no:cacheprovider", *nodes],
                          cwd=copy, env=env, capture_output=True, text=True, check=False).returncode


def make_copy(tmp: Path) -> Path:
    copy = tmp / "repo"
    for part in ("src", "tests"):
        shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", copy / "pyproject.toml")
    probe = subprocess.run(["uv", "run", "--project", str(ROOT), "python", "-c", "import preflight; print(preflight.__file__)"],
                           cwd=copy, env={**os.environ, "PYTHONPATH": str(copy / "src")}, capture_output=True, text=True, check=True)
    if not probe.stdout.strip().startswith(str(copy)):
        raise SystemExit(f"The copy is not the code under test: preflight imports from {probe.stdout.strip()}")
    return copy


def main() -> int:
    caught = survived = skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        copy = make_copy(Path(tmp))
        for m in MUTATIONS:
            if m.needs and importlib.util.find_spec(m.needs) is None:
                print(f"SKIP     {m.guards} ({m.needs} not installed)")
                skipped += 1
                continue
            path = copy / SRC / m.file
            original = path.read_text()
            if original.count(m.old) != 1:
                print(f"STALE    {m.guards}: the code to mutate was not found exactly once in {m.file}")
                survived += 1
                continue
            if pytest(copy, m.tests) != 0:
                print(f"BASELINE {m.guards}: its tests fail before any mutation")
                survived += 1
                continue
            path.write_text(original.replace(m.old, m.new))
            failed = pytest(copy, m.tests) != 0
            path.write_text(original)
            print(f"{'caught' if failed else 'SURVIVED':<8} {m.guards}")
            caught += failed
            survived += not failed
    print(f"\n{caught} of {caught + survived} mutations caught, {skipped} skipped.")
    return 0 if survived == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
