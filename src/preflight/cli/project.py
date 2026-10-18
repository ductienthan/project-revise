import uuid
from datetime import date
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from preflight.cli import queue
from preflight.cli.client import ApiError, Client, ServerUnreachable
from preflight.db.models import FailureCategory, Severity, WinTag

console = Console()
CATEGORIES = [c.value for c in FailureCategory]
STATUS_WORDS = {"done": "done", "na": "not applicable", "defer": "deferred"}


@click.group()
@click.argument("project")
@click.pass_context
def project(ctx: click.Context, project: str) -> None:
    """Work on one project: preflight <project> <action>."""
    ctx.obj = {"project": project, "client": Client()}


def _ctx() -> tuple[str, Client]:
    obj = click.get_current_context().obj
    return obj["project"], obj["client"]


def print_checklist(checklist: dict) -> None:
    items = checklist["items"]
    if not items:
        console.print("Checklist is empty. No past failures or rules apply yet.")
        return
    required = sum(i["required"] for i in items)
    version = checklist["project"]["checklist_requirements_version"]
    built = f", built from requirements v{version}" if version else ""
    console.print(f"Checklist ({required} required, {len(items) - required} optional{built}):")
    table = Table("#", "", "item", "source", "status", box=None, pad_edge=False)
    for i in items:
        flag = "[red]required[/]" if i["required"] else "optional"
        status = i["status"]
        if i["reason"]:
            status += f": {i['reason']}"
        if i["revisit_date"]:
            status += f" (until {i['revisit_date']})"
        if i["linked_failures"]:
            status += f"; failed anyway: {', '.join(i['linked_failures'])}"
        table.add_row(str(i["position"]), flag, i["text"], i["why"], status)
    console.print(table)


def _item_by_position(client: Client, name: str, n: int) -> dict:
    checklist = client.get(f"/projects/{name}/checklist")
    for item in checklist["items"]:
        if item["position"] == n:
            return item
    raise click.ClickException(f"No checklist item {n}. Run `preflight {name} checklist`.")


def _patch_item(client: Client, item: dict, status: str, reason: str = "", until: date | None = None) -> bool:
    body = {"status": status, "reason": reason, "revisit_date": until.isoformat() if until else None}
    result = client.patch(f"/checklist-items/{item['id']}", json=body)
    if result["project_started"]:
        console.print("Required items resolved. Project started.")
    return result["project_started"]


@project.command()
@click.option("--file", "path", type=click.Path(exists=True, dir_okay=False))
@click.option("--text")
def requirements(path: str | None, text: str | None) -> None:
    """Add or edit requirements. Each changed save is a new version."""
    name, client = _ctx()
    versions = client.get(f"/projects/{name}/requirements")
    current = versions[-1]["text"] if versions else ""
    if path:
        text = Path(path).read_text()
    elif text is None:
        text = click.edit(current or "# Requirements for " + name + "\n")
        if text is None:
            click.echo("No change.")
            return
    saved = client.put(f"/projects/{name}/requirements", json={"text": text})
    if versions and saved["version"] == versions[-1]["version"]:
        click.echo(f"Unchanged, still v{saved['version']}.")
    else:
        click.echo(f"Saved requirements v{saved['version']}.")


QUESTION_CHOICES = {"n": "not relevant", "d": "deferred"}


def _approve(n: int, item: dict) -> bool:
    while True:
        choice = click.prompt(f"  [{n}] {item['label']} (~{item['tokens']} tokens)  approve? [y]es, [n]o, [v]iew",
                              type=click.Choice(["y", "n", "v"]), default="n", show_choices=False, show_default=False)
        if choice != "v":
            return choice == "y"
        click.echo_via_pager(f"--- exactly what would be sent for [{n}] ---\n{item['text']}\n--- end ---\n")


def run_review(client: Client, name: str, again: bool) -> bool:
    """Offer each item for approval, run the review, and record answers. Returns whether a review ran."""
    found = client.get(f"/projects/{name}/review-items")
    if not found["ai_available"]:
        where = "Ollama is not running or has no models" if found["provider"] == "ollama" else "no AI is configured on the server"
        console.print(f"AI review is not available ({where}). Building the checklist from your history and rules.")
        return False
    if found["review_current"] and not again:
        console.print("Using the earlier review. Nothing it could see has changed. Pass --review-again to run a new one.")
        return False
    if found["reviewed"] and not found["review_current"]:
        console.print("Your requirements, links, or history changed since the last review.")
    if not found["items"]:
        console.print("Nothing to review yet.")
        return False
    calls = f", in {found['calls']} calls" if found["calls"] > 1 else ""
    if found["provider"] == "ollama":
        console.print(f"Will be sent to your Ollama model ({found['model']}, no cost{calls}). Approve each:")
    else:
        console.print(f"Will be sent to AI ({found['model']}, at most about ${found['max_cost_usd']:.2f}{calls}). Approve each:")
    if found["excluded_sensitive"]:
        console.print(f"  {found['excluded_sensitive']} sensitive item(s) are never offered.")
    approved = [item["id"] for n, item in enumerate(found["items"], start=1) if _approve(n, item)]
    if not approved:
        console.print("Nothing approved, nothing sent. Building the checklist from your history and rules.")
        return False
    review = client.post(f"/projects/{name}/reviews", json={"approved_item_ids": approved})
    console.print(f"Review done (${review['cost_usd']:.3f}). Questions based on your history:")
    answers = []
    for n, q in enumerate(review["questions"], start=1):
        reply = click.prompt(f"  Q{n} ({q['category']}): {q['question']}\n    answer, [n]ot relevant, [d]efer, or Enter to leave open",
                             default="", show_default=False)
        if reply in QUESTION_CHOICES:
            answers.append({"question_id": q["id"], "status": QUESTION_CHOICES[reply]})
        elif reply.strip():
            answers.append({"question_id": q["id"], "status": "answered", "answer": reply})
    accepted = []
    if review["drafts"]:
        console.print("Suggested checklist items:")
    for index, d in enumerate(review["drafts"]):
        label = "gap" if d["kind"] == "gap" else d["priority"]
        if click.confirm(f"  ({label}) {d['text']}. {d['why']} ({d['source_ref']})  add?", default=True):
            accepted.append(index)
    if review["dropped_drafts"]:
        console.print(f"Dropped {review['dropped_drafts']} suggestion(s) that cited nothing you sent.")
    client.post(f"/reviews/{review['id']}/answers", json={"answers": answers, "accepted_drafts": accepted})
    return True


@project.command("review")
def review_command() -> None:
    """Run the AI review again, for example after requirements or links change."""
    name, client = _ctx()
    if not run_review(client, name, again=True):
        return
    status = client.get(f"/projects/{name}")["status"]
    if status == "started":
        print_checklist(client.post(f"/projects/{name}/checklist/refresh"))
    else:
        console.print(f"Review saved. Run `preflight {name} start` to build the checklist from it.")


@project.command()
@click.option("--override", "override_reason", help="Start anyway, recording this reason.")
@click.option("--no-ai", is_flag=True, help="Skip the AI review and use only history and rules.")
@click.option("--review-again", is_flag=True, help="Run a new AI review even if one exists for these requirements.")
@click.option("--no-input", is_flag=True, help="Skip the AI review and do not walk through required items.")
def start(override_reason: str | None, no_ai: bool, review_again: bool, no_input: bool) -> None:
    """Review the plan, build the checklist, and pass the start gate."""
    name, client = _ctx()
    if not (no_ai or no_input or override_reason):
        run_review(client, name, review_again)
    result = client.post(f"/projects/{name}/start", json={"override_reason": override_reason})
    print_checklist(result["checklist"])
    if result["started"]:
        console.print("Project started." + (" Gate overridden." if override_reason else ""))
        return
    unresolved = result["checklist"]["unresolved_required"]
    console.print(f"{len(unresolved)} required item(s) to resolve before the project can start.")
    if no_input:
        console.print(f"Resolve them with `preflight {name} check <n> done|na|defer`, or use --override.")
        return
    items = {i["position"]: i for i in result["checklist"]["items"]}
    for n in unresolved:
        item = items[n]
        choice = click.prompt(f"{n}. {item['text']}\n   [d]one, [n]ot applicable, de[f]er, [s]kip", default="s")
        if choice == "d":
            started = _patch_item(client, item, "done")
        elif choice == "n":
            started = _patch_item(client, item, "not applicable", click.prompt("   Reason"))
        elif choice == "f":
            reason = click.prompt("   Reason")
            until = click.prompt("   Revisit on (YYYY-MM-DD)", type=click.DateTime(["%Y-%m-%d"])).date()
            started = _patch_item(client, item, "deferred", reason, until)
        else:
            continue
        if started:
            return


@project.command()
@click.option("--md", is_flag=True, help="Print as Markdown to paste into project docs.")
def checklist(md: bool) -> None:
    """Show the checklist and item statuses."""
    name, client = _ctx()
    if md:
        click.echo(client.get(f"/projects/{name}/checklist", params={"format": "md"}), nl=False)
        return
    print_checklist(client.get(f"/projects/{name}/checklist"))


@project.command()
@click.argument("n", type=int)
@click.argument("status", type=click.Choice(list(STATUS_WORDS)))
@click.option("--reason", default="")
@click.option("--until", type=click.DateTime(["%Y-%m-%d"]))
def check(n: int, status: str, reason: str, until) -> None:
    """Mark item <n> done, na (needs --reason) or defer (needs --reason and --until)."""
    name, client = _ctx()
    item = _item_by_position(client, name, n)
    if not _patch_item(client, item, STATUS_WORDS[status], reason, until.date() if until else None):
        click.echo(f"Item {n}: {STATUS_WORDS[status]}.")


def _category_prompt() -> str | None:
    for i, c in enumerate(CATEGORIES, start=1):
        click.echo(f"  {i}. {c}")
    choice = click.prompt("Category number (blank to skip)", default="", show_default=False)
    return CATEGORIES[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(CATEGORIES) else None


@project.command()
@click.argument("title")
@click.option("--what", "what_happened", default="")
@click.option("--guess", "guessed_cause", default="")
@click.option("--cause", "root_cause", default="")
@click.option("--fix", default="")
@click.option("--prevention", default="")
@click.option("--category", type=click.Choice(CATEGORIES))
@click.option("--severity", type=click.Choice([s.value for s in Severity]))
@click.option("--minutes", "minutes_lost", type=int)
@click.option("--guess-correct/--guess-wrong", default=None)
@click.option("--date", "occurred", type=click.DateTime(["%Y-%m-%d"]), help="Backfill an older failure.")
@click.option("--no-input", is_flag=True, help="Log only what was given on the command line.")
def fail(title: str, no_input: bool, occurred, **fields) -> None:
    """Log a failure. Blank prompts can be skipped and filled in later."""
    name, client = _ctx()
    if not no_input:
        for key, label in (("what_happened", "What happened"), ("guessed_cause", "Your first guess at the cause"),
                           ("root_cause", "Real cause (blank if unknown)"), ("fix", "Fix"),
                           ("prevention", "Check that would have prevented it")):
            if not fields[key]:
                fields[key] = click.prompt(label, default="", show_default=False)
        if fields["category"] is None:
            fields["category"] = _category_prompt()
        if fields["severity"] is None:
            fields["severity"] = click.prompt("Severity", type=click.Choice([s.value for s in Severity]), default="medium")
        if fields["minutes_lost"] is None:
            fields["minutes_lost"] = click.prompt("Minutes lost", type=int, default=0)
        if fields["guess_correct"] is None and fields["guessed_cause"] and fields["root_cause"]:
            fields["guess_correct"] = click.confirm("Was your first guess right?", default=False)
    body = {k: v for k, v in fields.items() if v is not None}
    body.update(id=str(uuid.uuid4()), title=title)
    if occurred:
        body["occurred_at"] = occurred.date().isoformat() + "T12:00:00Z"
    try:
        logged = client.post(f"/projects/{name}/failures", json=body)
    except ServerUnreachable:
        queue.enqueue(f"/projects/{name}/failures", body)
        click.echo("Server unreachable. Saved offline, will send on your next command.")
        return
    failure = logged["failure"]
    click.echo(f"Logged failure #{failure['id'][:8]}.")
    if not no_input and not fields.get("category") and click.confirm(
        "Ask AI to suggest a category and root cause? This sends the failure's text.", default=False
    ):
        _apply_suggestion(client, failure)
    if logged["project_status"] == "draft":
        click.echo(f"This project hasn't started yet. Run `preflight {name} start` to build its checklist.")
    for f in logged["similar"]:
        click.echo(f"  Similar: #{f['id'][:8]} {f['title']}" + (f" ({f['category']})" if f["category"] else ""))
    related = logged["related_items"]
    if related:
        click.echo("Related checklist items: " + ", ".join(f"{i['position']}. {i['text']}" for i in related))
    if no_input or logged["project_status"] == "draft":
        return
    choices = [str(i["position"]) for i in related] or ["<n>"]
    answer = click.prompt(f"Link to a checklist item? [{'/'.join(choices)}/none/skip]", default="skip")
    if answer == "none":
        client.patch(f"/failures/{failure['id']}", json={"not_on_checklist": True})
        click.echo("Marked as not on the checklist.")
    elif answer.isdigit():
        item = _item_by_position(client, name, int(answer))
        client.patch(f"/failures/{failure['id']}", json={"link_item_id": item["id"]})
        click.echo(f"Linked to item {answer}.")


def _apply_suggestion(client: Client, failure: dict) -> None:
    try:
        s = client.post(f"/failures/{failure['id']}/suggest", json={"approved": True})
    except ApiError as err:
        click.echo(f"No suggestion: {err.message}")
        return
    click.echo(f"  Category: {s['category']}\n  Root cause: {s['root_cause']}\n  Prevention: {s['prevention']}")
    if click.confirm("Use these?", default=False):
        patch = {"category": s["category"]}
        if not failure["root_cause"]:
            patch["root_cause"] = s["root_cause"]
        if not failure["prevention"]:
            patch["prevention"] = s["prevention"]
        client.patch(f"/failures/{failure['id']}", json=patch)
        click.echo("Saved.")


@project.command()
@click.argument("text")
@click.option("--tag", type=click.Choice([t.value for t in WinTag]))
def win(text: str, tag: str | None) -> None:
    """Log an achievement."""
    name, client = _ctx()
    body = {"id": str(uuid.uuid4()), "text": text, "tag": tag}
    try:
        client.post(f"/projects/{name}/wins", json=body)
    except ServerUnreachable:
        queue.enqueue(f"/projects/{name}/wins", body)
        click.echo("Server unreachable. Saved offline, will send on your next command.")
        return
    click.echo("Logged win.")


def _review_snapshot(client: Client, captured: dict, no_input: bool) -> None:
    if captured.get("capture_error"):
        click.echo(f"Not captured: {captured['capture_error']}")
        return
    snapshot = captured.get("snapshot")
    if snapshot is None:
        return
    title = captured["link"]["title"] or captured["link"]["url"]
    flag = " (truncated)" if snapshot["truncated"] else ""
    click.echo(f'Captured: "{title}" v{snapshot["version"]} ({snapshot["words"]:,} words){flag}.')
    if no_input:
        click.echo("Not yet approved for reviews.")
        return
    if click.confirm("Preview?", default=True):
        lines = snapshot["content"].splitlines()
        click.echo("\n".join(lines[:30]) + ("\n..." if len(lines) > 30 else ""))
    if click.confirm("Use this snapshot in reviews?", default=False):
        client.patch(f"/snapshots/{snapshot['id']}", json={"approved": True})
        click.echo("Approved for reviews.")


@project.command("add-link")
@click.argument("url")
@click.option("--internal", is_flag=True, help="A reference only. Never fetched.")
@click.option("--capture", "do_capture", is_flag=True, help="Capture a public page as a text snapshot.")
@click.option("--note", default="")
@click.option("--title", default="")
@click.option("--sensitive", is_flag=True, help="Never offer this link to AI.")
@click.option("--no-input", is_flag=True)
def add_link(url: str, internal: bool, do_capture: bool, note: str, title: str, sensitive: bool, no_input: bool) -> None:
    """Save a link, and optionally capture a public page."""
    name, client = _ctx()
    body = {"url": url, "title": title, "note": note, "internal": internal, "sensitive": sensitive, "capture": do_capture}
    captured = client.post(f"/projects/{name}/links", json=body)
    click.echo(f"Saved {'internal' if internal else 'public'} link {captured['link']['id'][:8]}.")
    _review_snapshot(client, captured, no_input)


@project.command()
def links() -> None:
    """List links and snapshots."""
    name, client = _ctx()
    rows = client.get(f"/projects/{name}/links")
    if not rows:
        click.echo("No links yet.")
        return
    table = Table("id", "type", "link", "note", "snapshots")
    for link in rows:
        snaps = ", ".join(f"v{s['version']}{' approved' if s['approved'] else ''}" for s in link["snapshots"])
        kind = link["type"] + (", sensitive" if link["sensitive"] else "")
        table.add_row(link["id"][:8], kind, link["title"] or link["url"], link["note"], snaps or "none")
    console.print(table)


@project.command()
@click.argument("link_id")
@click.option("--no-input", is_flag=True)
def refresh(link_id: str, no_input: bool) -> None:
    """Re-capture a public link. Old snapshots are kept."""
    name, client = _ctx()
    matches = [link for link in client.get(f"/projects/{name}/links") if link["id"].startswith(link_id)]
    if len(matches) != 1:
        raise click.ClickException(f"No single link matches '{link_id}'.")
    _review_snapshot(client, client.post(f"/links/{matches[0]['id']}/refresh"), no_input)


OUTCOME_KEYS = {"h": "helped", "m": "missed", "i": "irrelevant"}


@project.command()
@click.option("--summary", help="Retrospective summary. Prompted for when missing.")
def close(summary: str | None) -> None:
    """Run the retrospective and close the project."""
    name, client = _ctx()
    status = client.get(f"/projects/{name}/status")
    if status["project"]["status"] != "started":
        raise click.ClickException(f"Only a started project can be closed. '{name}' is {status['project']['status']}.")
    for reason in status["gate_overrides"]:
        click.echo(f"The start gate was overridden: {reason}")
    click.echo("Wins to record (blank to finish):")
    while text := click.prompt("  win", default="", show_default=False):
        client.post(f"/projects/{name}/wins", json={"id": str(uuid.uuid4()), "text": text})
    click.echo("Failures you have not logged yet (blank to finish):")
    while title := click.prompt("  failure", default="", show_default=False):
        client.post(f"/projects/{name}/failures", json={"id": str(uuid.uuid4()), "title": title})
    outcomes = {}
    items = client.get(f"/projects/{name}/checklist")["items"]
    if items:
        click.echo("For each checklist item: [h]elped, [m]issed (should have caught something), [i]rrelevant, Enter to skip.")
    for item in items:
        key = click.prompt(f"  {item['position']}. {item['text']}", default="", show_default=False)
        if key in OUTCOME_KEYS:
            outcomes[item["id"]] = OUTCOME_KEYS[key]
    summary = summary or click.prompt("Summary: what went well, what failed, what to change")
    client.post(f"/projects/{name}/close", json={"summary": summary, "outcomes": outcomes})
    click.echo(f"Closed {name}. Its failures now feed patterns, rules, drills, and the dashboard.")


@project.command()
@click.argument("hypothesis")
@click.option("--evidence", default="", help="What you will look at to check it.")
@click.option("--confidence", type=click.IntRange(0, 100), help="How sure you are, 0 to 100.")
def predict(hypothesis: str, evidence: str, confidence: int | None) -> None:
    """Record a hypothesis and your confidence before debugging."""
    name, client = _ctx()
    if confidence is None:
        confidence = click.prompt("Confidence (0-100)", type=click.IntRange(0, 100))
    p = client.post(f"/projects/{name}/predictions", json={"hypothesis": hypothesis, "evidence_planned": evidence, "confidence": confidence})
    click.echo(f"Prediction {p['id'][:8]} recorded at {confidence}%. Close it with `preflight {name} resolve {p['id'][:8]} right|wrong`.")


@project.command()
def predictions() -> None:
    """List predictions and their outcomes."""
    name, client = _ctx()
    rows = client.get(f"/projects/{name}/predictions")
    table = Table("id", "hypothesis", "confidence", "outcome")
    for p in rows:
        outcome = "open" if p["correct"] is None else ("right" if p["correct"] else "wrong")
        table.add_row(p["id"][:8], p["hypothesis"], f"{p['confidence']}%", outcome)
    console.print(table if rows else "No predictions yet.")


@project.command()
@click.argument("prediction_id")
@click.argument("outcome", type=click.Choice(["right", "wrong"]))
@click.option("--failure", "failure_id", help="The failure this prediction was about (id prefix).")
def resolve(prediction_id: str, outcome: str, failure_id: str | None) -> None:
    """Close a prediction with what actually happened."""
    name, client = _ctx()
    matches = [p for p in client.get(f"/projects/{name}/predictions") if p["id"].startswith(prediction_id)]
    if len(matches) != 1:
        raise click.ClickException(f"No single prediction matches '{prediction_id}'.")
    body = {"correct": outcome == "right"}
    if failure_id:
        found = [f for f in client.get(f"/projects/{name}/failures") if f["id"].startswith(failure_id)]
        if len(found) != 1:
            raise click.ClickException(f"No single failure matches '{failure_id}'.")
        body["failure_id"] = found[0]["id"]
    client.post(f"/predictions/{matches[0]['id']}/close", json=body)
    click.echo(f"Prediction closed as {outcome}.")


@project.command()
def status() -> None:
    """Show progress for the project."""
    name, client = _ctx()
    s = client.get(f"/projects/{name}/status")
    p = s["project"]
    version = f"v{s['requirements_version']}" if s["requirements_version"] else "none"
    console.print(f"[bold]{p['name']}[/] is {p['status']}. Requirements: {version}.")
    console.print(
        f"Checklist: {s['checklist_resolved']}/{s['checklist_total']} resolved, "
        f"{s['unresolved_required']} required open."
    )
    console.print(f"Failures: {s['failures']} ({s['minutes_lost']} min lost). Wins: {s['wins']}.")
