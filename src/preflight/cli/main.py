import click

PROJECT_ACTIONS = (
    "requirements", "add-link", "links", "refresh", "start", "checklist", "check",
    "fail", "win", "predict", "status", "close",
)


class ProjectFirstGroup(click.Group):
    """Routes `preflight <project> <action> ...` to the `project` command."""

    def resolve_command(self, ctx, args):
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            return "project", self.commands["project"], args
        return super().resolve_command(ctx, args)


@click.group(cls=ProjectFirstGroup)
def cli() -> None:
    pass


@cli.command()
@click.argument("name")
def new(name: str) -> None:
    if name in cli.commands:
        raise click.BadParameter(f"'{name}' is a reserved command name", param_hint="project")
    click.echo(f"create project {name}")


@cli.command(hidden=True, context_settings={"ignore_unknown_options": True})
@click.argument("project")
@click.argument("action", type=click.Choice(PROJECT_ACTIONS))
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def project(project: str, action: str, args: tuple[str, ...]) -> None:
    click.echo(f"{project} {action} {' '.join(args)}".rstrip())


@cli.command()
def login() -> None:
    click.echo("device flow not implemented")


@cli.command()
def logout() -> None:
    click.echo("logout not implemented")


@cli.command()
def drill() -> None:
    click.echo("drill not implemented")


@cli.command()
@click.option("--week", is_flag=True)
def report(week: bool) -> None:
    click.echo("report not implemented")


@cli.command()
def ui() -> None:
    click.echo("ui not implemented")


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["csv", "json", "md"]), default="json")
def export(fmt: str) -> None:
    click.echo(f"export {fmt} not implemented")
