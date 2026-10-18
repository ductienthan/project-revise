import platform
import time
import webbrowser

import click
from rich.console import Console
from rich.table import Table

from preflight.cli import credentials
from preflight.cli.client import ApiError, Client, is_secure

console = Console()


def device_login(client: Client, open_browser: bool) -> None:
    grant = client.post("/auth/device/code", auth=False, json={"device_name": platform.node() or "cli"})
    url = f"{client.base_url}{grant['verification_uri']}?code={grant['user_code']}"
    click.echo(f"Your code: {grant['user_code']}")
    click.echo(f"Confirm it at {url}", nl=True)
    if open_browser:
        webbrowser.open(url)
    interval = grant["interval"]
    deadline = time.monotonic() + grant["expires_in"]
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            tokens = client.post("/auth/device/token", auth=False, json={"device_code": grant["device_code"]})
        except ApiError as err:
            if err.detail == "authorization_pending":
                continue
            if err.detail == "slow_down":
                interval += 1
                continue
            raise click.ClickException(f"Sign-in failed: {err.detail}") from None
        credentials.save(client.base_url, tokens)
        click.echo("Signed in.")
        return
    raise click.ClickException("The code expired. Run `preflight login` again.")


def password_login(client: Client) -> None:
    if not is_secure(client.base_url):
        raise click.ClickException("Refusing to send a password over plain HTTP.")
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)
    totp = click.prompt("Authenticator code (blank if none)", default="", show_default=False)
    tokens = client.post(
        "/auth/token",
        auth=False,
        json={"username": username, "password": password, "totp": totp or None, "device_name": platform.node() or "cli"},
    )
    credentials.save(client.base_url, tokens)
    click.echo("Signed in.")


@click.command()
@click.option("--password", "use_password", is_flag=True, help="Sign in with a hidden password prompt instead of the browser.")
@click.option("--no-browser", is_flag=True, help="Print the sign-in link without opening a browser.")
def login(use_password: bool, no_browser: bool) -> None:
    """Sign this device in to the Preflight server."""
    client = Client()
    if use_password:
        password_login(client)
    else:
        device_login(client, open_browser=not no_browser)


@click.command()
def logout() -> None:
    """Forget this device's token."""
    credentials.clear(Client().base_url)
    click.echo("Signed out.")


@click.group()
def auth() -> None:
    """Manage signed-in devices."""


@auth.command("devices")
def devices() -> None:
    """List the devices signed in to your account."""
    table = Table("id", "name", "created", "last used", "revoked")
    for d in Client().get("/auth/devices"):
        table.add_row(d["id"], d["name"], d["created_at"][:16], (d["last_used_at"] or "")[:16], (d["revoked_at"] or "")[:16])
    console.print(table)


@auth.command("revoke")
@click.argument("device_id")
def revoke(device_id: str) -> None:
    """Sign a device out. Its token stops working at once."""
    Client().delete(f"/auth/devices/{device_id}")
    click.echo("Revoked.")
