from preflight.cli.importer import failure_id, parse

CSV = """﻿project,title,date,category,severity,minutes_lost,guess_correct,root_cause,prevention
billing,Retry hang,2026-03-04,missed edge case,high,40,false,No deadline,Every call has a deadline
billing,Bad row,03/04/2026,,,,,,
search,Index rebuild locked writes,2026-04-10,knowledge gap,,abc,,,
search,Typo,2026-04-11,typo category,,,,,
"""


def test_csv_rows_are_parsed_and_bad_rows_reported_with_their_line():
    parsed = parse(CSV, "old.csv")
    assert [r.body["title"] for r in parsed.rows] == ["Retry hang"]
    body = parsed.rows[0].body
    assert body["occurred_at"] == "2026-03-04T12:00:00Z" and body["minutes_lost"] == 40 and body["guess_correct"] is False
    assert body["category"] == "missed edge case" and body["prevention"] == "Every call has a deadline"
    assert parsed.errors == [
        "row 3: date '03/04/2026' is not YYYY-MM-DD",
        "row 4: minutes_lost 'abc' is not a number",
        "row 5: unknown category 'typo category'",
    ]


def test_json_lists_are_accepted_and_ids_are_stable():
    parsed = parse('[{"project": "billing", "title": "Retry hang", "date": "2026-03-04"}]', "old.json")
    assert parsed.rows[0].body["id"] == failure_id("billing", "Retry hang", "2026-03-04")
    try:
        parse('{"not": "a list"}', "x.json")
    except ValueError as err:
        assert "must be a list" in str(err)
    else:
        raise AssertionError("a JSON object is not a list of failures")


def test_replaying_imported_rows_adds_nothing(api):
    api.post("/api/v1/projects", json={"name": "billing"})
    row = parse(CSV, "old.csv").rows[0]
    first = api.post("/api/v1/projects/billing/failures", json=row.body).json()
    again = api.post("/api/v1/projects/billing/failures", json=row.body).json()
    assert first["created"] and not again["created"]
    assert api.get("/api/v1/failures").json()[0]["occurred_at"].startswith("2026-03-04")
