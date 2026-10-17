import re

from tests.test_capture import fail, new_project
from tests.test_checklist import ATOMIC_CHECK


def csrf(page: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def test_pages_show_projects_checklists_and_failures(web):
    new_project(web, "billing")
    fail(web, "billing", "Budget counter lost updates", category="state or concurrency bug", prevention=ATOMIC_CHECK)
    fail(web, "billing", "Two workers double-charged", category="state or concurrency bug", prevention=ATOMIC_CHECK)
    new_project(web, "gateway")
    web.post("/api/v1/projects/gateway/start", json={})

    home = web.get("/").text
    assert 'href="/projects/gateway"' in home and "draft" in home
    project = web.get("/projects/gateway").text
    assert ATOMIC_CHECK in project and "required" in project and ">open<" in project
    assert "Two workers double-charged" in web.get("/failures", params={"q": "workers"}).text
    assert "Budget counter" not in web.get("/failures", params={"q": "workers"}).text
    assert web.get("/projects/nope").status_code == 404


def test_rules_page_approves_a_suggested_rule(web):
    new_project(web, "billing")
    fail(web, "billing", "Lost update", category="state or concurrency bug", prevention=ATOMIC_CHECK)
    fail(web, "billing", "Double charge", category="state or concurrency bug", prevention=ATOMIC_CHECK)
    page = web.get("/rules").text
    pattern_id = re.search(r'name="pattern_id" value="([^"]+)"', page).group(1)

    forged = web.post("/rules", data={"pattern_id": pattern_id, "checklist_text": "x"}, headers={"X-CSRF-Token": ""})
    assert forged.status_code == 403
    approved = web.post(
        "/rules", data={"pattern_id": pattern_id, "checklist_text": "Rule: atomic", "csrf_token": csrf(page)},
        headers={"X-CSRF-Token": ""},
    )
    assert approved.status_code == 200 and "Rule: atomic" in approved.text


def test_user_text_is_escaped(web):
    new_project(web, "billing")
    fail(web, "billing", "<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in web.get("/failures").text
