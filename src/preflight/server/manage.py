import click
import pyotp

from preflight.config import Settings
from preflight.db.models import User
from preflight.db.session import make_engine, make_sessionmaker
from preflight.server.auth import service


def _sessionmaker():
    return make_sessionmaker(make_engine(Settings().database_url))


@click.group()
def manage() -> None:
    """Server administration for Preflight."""


@manage.command("create-user")
@click.option("--username", required=True)
@click.password_option(envvar="PREFLIGHT_ADMIN_PASSWORD")
def create_user(username: str, password: str) -> None:
    with _sessionmaker()() as db:
        try:
            service.create_user(db, username, password)
        except service.AuthError as err:
            raise click.ClickException(str(err)) from None
        db.commit()
    click.echo(f"created user {username}")


@manage.command("enable-totp")
@click.option("--username", required=True)
def enable_totp(username: str) -> None:
    with _sessionmaker()() as db:
        user = db.query(User).filter_by(username=username).one_or_none()
        if user is None:
            raise click.ClickException("no such user")
        secret = service.enable_totp(db, user)
        db.commit()
    click.echo(pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Preflight"))
