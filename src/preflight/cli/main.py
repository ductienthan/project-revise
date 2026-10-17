import json
import webbrowser
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from preflight.cli import queue
from preflight.cli.auth import auth, login, logout
from preflight.cli.client import Client
from preflight.cli.project import project
from preflight.core.projects import RESERVED_NAMES

console = Console()


class ProjectFirstGroup(click.Group):
    """Routes `preflight <project> <action> ...` to the project group."""

    def resolve_command(self, ctx, args):
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            return "project", project, args
        return super().resolve_command(ctx, args)


@click.group(cls=ProjectFirstGroup)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Preflight: turn past failures into a checklist for the next project.

    Usage: preflight <project> <action>, or one of the commands below.
    """
    if ctx.invoked_subcommand not in ("login", "logout") and queue.pending():
        try:
            queue.flush(Client())
        except click.ClickException:
            pass


cli.add_command(login)
cli.add_command(logout)
cli.add_command(auth)


@cli.command()
@click.argument("name")
@click.option("--idea")
@click.option("--goals")
@click.option("--requirements", "requirements_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-input", is_flag=True)
def new(name: str, idea: str | None, goals: str | None, requirements_file: str | None, no_input: bool) -> None:
    """Create a project."""
    if name in RESERVED_NAMES:
        raise click.BadParameter(f"'{name}' is a reserved command name", param_hint="NAME")
    if not no_input:
        idea = idea if idea is not None else click.prompt("Idea", default="", show_default=False)
        goals = goals if goals is not None else click.prompt("Goals", default="", show_default=False)
    if requirements_file:
        requirements = Path(requirements_file).read_text()
    elif no_input:
        requirements = ""
    else:
        requirements = click.edit(f"# Requirements for {name}\n") or ""
    created = Client().post("/projects", json={"name": name, "idea": idea or "", "goals": goals or "", "requirements": requirements})
    click.echo(f"Created {created['name']}. Next: `preflight {name} start`.")


@cli.command()
@click.argument("query")
@click.option("--project")
def search(query: str, project: str | None) -> None:
    """Search failures across all projects."""
    params = {"q": query, **({"project": project} if project else {})}
    rows = Client().get("/failures", params=params)
    table = Table("id", "date", "title", "category", "root cause")
    for f in rows:
        table.add_row(f["id"][:8], f["occurred_at"][:10], f["title"], f["category"] or "", f["root_cause"])
    console.print(table if rows else "No matching failures.")


@cli.group(invoke_without_command=True)
@click.pass_context
def rules(ctx: click.Context) -> None:
    """Show repeated patterns, suggested rules, and active rules."""
    if ctx.invoked_subcommand:
        return
    client = Client()
    suggestions = client.get("/patterns")
    table = Table("pattern", "label", "suggested rule")
    for s in suggestions:
        if s["rule_id"] is None:
            table.add_row(s["pattern"]["id"][:8], s["pattern"]["label"], s["suggested_rule"])
    if table.row_count:
        console.print("Patterns without a rule. Approve with `preflight rules approve <pattern>`:")
        console.print(table)
    active = client.get("/rules")
    if active:
        table = Table("rule", "status", "text")
        for r in active:
            table.add_row(r["id"][:8], r["status"], r["checklist_text"])
        console.print(table)
    if not suggestions and not active:
        console.print("No repeated patterns yet.")


def _by_prefix(rows: list[dict], prefix: str, what: str) -> dict:
    matches = [r for r in rows if r["id"].startswith(prefix)]
    if len(matches) != 1:
        raise click.ClickException(f"{'No' if not matches else 'More than one'} {what} matches '{prefix}'.")
    return matches[0]


@rules.command("approve")
@click.argument("pattern_id")
@click.option("--text", help="Checklist text. Defaults to the suggested rule.")
@click.option("--ai", "use_ai", is_flag=True, help="Ask AI to draft the rule from this pattern's failures.")
def approve(pattern_id: str, text: str | None, use_ai: bool) -> None:
    client = Client()
    suggestion = _by_prefix([s["pattern"] | {"suggested_rule": s["suggested_rule"]} for s in client.get("/patterns")], pattern_id, "pattern")
    draft = suggestion["suggested_rule"]
    if use_ai and click.confirm(f"Send the failures in '{suggestion['label']}' to AI to draft a rule?", default=False):
        draft = client.post(f"/patterns/{suggestion['id']}/draft-rule", json={"approved": True})["checklist_text"]
    text = text or click.prompt("Rule text", default=draft)
    rule = client.post("/rules", json={"pattern_id": suggestion["id"], "checklist_text": text})
    click.echo(f"Rule {rule['id'][:8]} is active and will appear in new checklists.")


@rules.command("set")
@click.argument("rule_id")
@click.option("--status", type=click.Choice(["active", "working", "needs revision", "retired"]))
@click.option("--text")
def set_rule(rule_id: str, status: str | None, text: str | None) -> None:
    client = Client()
    rule = _by_prefix(client.get("/rules"), rule_id, "rule")
    client.patch(f"/rules/{rule['id']}", json={"status": status, "checklist_text": text})
    click.echo("Rule updated.")


@cli.command()
@click.option("--ai", "use_ai", is_flag=True, help="Have AI grade typed answers. Asks before sending each one.")
def drill(use_ai: bool) -> None:
    """Review today's due drills."""
    client = Client()
    due = client.get("/drills/today")
    if not due:
        click.echo("No drills due today.")
        return
    click.echo(f"{len(due)} drill(s) due.")
    for n, d in enumerate(due, start=1):
        click.echo(f"\n{n}. {d['prompt']}")
        attempt = click.prompt("Your answer (Enter to just reveal)", default="", show_default=False)
        click.echo(f"Expected: {d['answer']}")
        body: dict = {"attempt": attempt}
        if use_ai and attempt and click.confirm("Send this drill and your answer to AI for grading?", default=False):
            body["grade_with_ai"] = True
        else:
            body["score"] = click.prompt("Score 0 (forgot) to 5 (perfect)", type=click.IntRange(0, 5))
        result = client.post(f"/drills/{d['id']}/review", json=body)
        if result["feedback"]:
            click.echo(f"Score {result['score']}: {result['feedback']}")
        click.echo(f"Next review in {result['interval_days']} day(s).")


@cli.command()
@click.option("--week", is_flag=True, help="Show the weekly report instead of overall progress.")
def report(week: bool) -> None:
    """Show progress, or the weekly report."""
    client = Client()
    if week:
        click.echo(client.get("/reports/weekly", params={"format": "md"}), nl=False)
        return
    data = client.get("/progress")
    o = data["overall"]
    console.print(f"Repeat rate, last 12 weeks: {o['repeat_rate_12_weeks']['text']}. First guess right: {o['guess_accuracy']['text']}.")
    hours = o["median_hours_to_root_cause"]
    console.print(f"Failures: {o['failures']}, {o['minutes_lost']} minutes lost"
                  + (f", median {hours} h to root cause." if hours is not None else "."))
    if o["closed_predictions"]:
        console.print(f"Calibration gap: {o['calibration_gap']:+.0%} over {o['closed_predictions']} predictions (positive means overconfident).")
    console.print(f"Drill retention: {o['drill_retention']['text']}.")
    rules = ", ".join(f"{n} {status}" for status, n in o["rule_status"].items() if n)
    if rules:
        console.print(f"Rules: {rules}.")
    table = Table("project", "status", "failures", "repeat rate", "on checklist", "missed items", "wins")
    for p in data["projects"]:
        table.add_row(p["name"], p["status"], str(p["failures"]), p["repeat_rate"]["text"],
                      p["on_checklist_rate"]["text"], p["missed_item_rate"]["text"], str(p["wins"]))
    console.print(table)


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["json", "csv", "md"]), default="json")
@click.option("--out", type=click.Path(dir_okay=False, writable=True), help="Write to a file instead of stdout.")
def export(fmt: str, out: str | None) -> None:
    """Export all data as JSON, CSV (a zip of tables), or Markdown."""
    client = Client()
    if fmt == "csv":
        path = Path(out or "preflight-export.zip")
        response = client.raw("GET", "/export", params={"format": "csv"})
        path.write_bytes(response.content)
        click.echo(f"Wrote {path} ({len(response.content):,} bytes).")
        return
    body = client.get("/export", params={"format": fmt})
    text = body if isinstance(body, str) else json.dumps(body, indent=2)
    if out:
        Path(out).write_text(text)
        click.echo(f"Wrote {out}.")
    else:
        click.echo(text)


@cli.command()
def ui() -> None:
    """Open the dashboard."""
    url = Client().base_url
    click.echo(url)
    webbrowser.open(url)
