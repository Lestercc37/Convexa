class QllError(Exception):
    code = "INTERNAL_ERROR"


class NotFoundError(QllError):
    code = "NOT_FOUND"


class UnauthorizedError(QllError):
    code = "UNAUTHORIZED"


class ForbiddenError(QllError):
    code = "FORBIDDEN"
