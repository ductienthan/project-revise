class DomainError(Exception):
    """A rule of the domain was broken. The message is shown to the user as is."""


class NotFound(DomainError):
    pass
