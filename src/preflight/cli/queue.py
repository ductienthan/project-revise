import json

import click

from preflight.cli.client import ApiError, Client, ServerUnreachable
from preflight.cli.credentials import config_dir


def _path():
    return config_dir() / "queue.jsonl"


def enqueue(path: str, body: dict) -> None:
    with _path().open("a") as f:
        f.write(json.dumps({"path": path, "body": body}) + "\n")


def pending() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def flush(client: Client) -> int:
    """Send queued entries in order. Each carries its own id, so a resend after a crash is harmless."""
    entries = pending()
    sent = 0
    for index, entry in enumerate(entries):
        try:
            client.post(entry["path"], json=entry["body"])
        except ServerUnreachable:
            _rewrite(entries[index:])
            return sent
        except ApiError as err:
            with (config_dir() / "queue.rejected.jsonl").open("a") as f:
                f.write(json.dumps({**entry, "error": err.message}) + "\n")
            click.echo(f"Dropped a queued entry the server rejected: {err.message}", err=True)
            continue
        sent += 1
    _rewrite([])
    if sent:
        click.echo(f"Sent {sent} queued entr{'y' if sent == 1 else 'ies'}.", err=True)
    return sent


def _rewrite(entries: list[dict]) -> None:
    _path().write_text("".join(json.dumps(e) + "\n" for e in entries))
