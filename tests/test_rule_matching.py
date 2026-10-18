from tests.test_capture import fail, new_project

WEBHOOK = "Webhook handlers dedupe on the event id"


def approve(api, scope_is_global: bool, text: str) -> None:
    pattern = next(s["pattern"] for s in api.get("/api/v1/patterns").json() if (s["pattern"]["project_scope"] is None) == scope_is_global)
    api.post("/api/v1/rules", json={"pattern_id": pattern["id"], "checklist_text": text})


def checklist(api, name, **fields):
    new_project(api, name, **fields)
    return {i["text"]: i for i in api.post(f"/api/v1/projects/{name}/start", json={}).json()["checklist"]["items"]}


def history(api):
    new_project(api, "payments")
    fail(api, "payments", "Stripe webhook charged twice", category="state or concurrency bug", prevention=WEBHOOK)
    fail(api, "payments", "Webhook retry processed a refund twice", category="state or concurrency bug", prevention=WEBHOOK)


def test_a_cross_project_rule_applies_to_every_project(api):
    history(api)
    approve(api, scope_is_global=True, text="Rule: idempotent webhooks")
    assert "Rule: idempotent webhooks" in checklist(api, "blog", requirements="A static blog with markdown posts.")


def test_a_single_project_rule_applies_only_where_the_plan_looks_alike(api):
    history(api)
    approve(api, scope_is_global=False, text="Rule: idempotent webhooks")
    similar = checklist(api, "billing", requirements="Receive Stripe webhook events and record each refund once.")
    unrelated = checklist(api, "blog", requirements="A static blog with markdown posts.")
    assert "Rule: idempotent webhooks" in similar and WEBHOOK not in similar
    assert "Rule: idempotent webhooks" not in unrelated
    assert WEBHOOK in unrelated, "where the rule does not apply, its category's failures still reach the checklist"


def test_failures_linked_to_an_item_are_shown_with_it(api):
    history(api)
    items = checklist(api, "billing")
    item = items[WEBHOOK]
    logged = fail(api, "billing", "Duplicate webhook again")["failure"]
    api.patch(f"/api/v1/failures/{logged['id']}", json={"link_item_id": item["id"]})
    shown = {i["text"]: i for i in api.get("/api/v1/projects/billing/checklist").json()["items"]}
    assert shown[WEBHOOK]["linked_failures"] == ["Duplicate webhook again"]
    md = api.get("/api/v1/projects/billing/checklist", params={"format": "md"}).text
    assert "  - failed anyway: Duplicate webhook again" in md
