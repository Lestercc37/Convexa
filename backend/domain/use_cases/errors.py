class QllError(Exception):
    code = "INTERNAL_ERROR"


class NotFoundError(QllError):
    code = "NOT_FOUND"


class NoOpeningPriceError(QllError):
    """ES/NQ have no data of their own: they are SPX/NDX shifted by the owner's 9:30 ET opening print,
    and that print has not been entered for today's session yet. The API answers 409 so the dashboard
    can say "no data" instead of showing numbers that are not today's."""

    code = "NO_OPENING_PRICE"


class UnauthorizedError(QllError):
    code = "UNAUTHORIZED"


class ForbiddenError(QllError):
    code = "FORBIDDEN"
