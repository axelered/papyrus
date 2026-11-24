from papyrus_sdk.models import ProblemIssue


class PapyrusError(Exception):
    """Base of every error raised by a request"""


class ConnectionFailed(PapyrusError):
    """The server is unreachable or the connection was lost"""


class TimedOut(ConnectionFailed):
    """The client side timeout expired"""


class InvalidResponse(PapyrusError):
    """A successful status whose body is not a Papyrus response of this SDK version"""


class APIError(PapyrusError):
    """Error response; branch on `code`, which is None when the body is not a Papyrus problem (e.g. from a proxy)"""

    def __init__(
        self,
        status: int,
        code: str | None = None,
        detail: str | None = None,
        errors: list[ProblemIssue] | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ):
        # positional args only, so the error survives pickling across process pools
        super().__init__(status, code, detail, errors, request_id, retry_after)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors or []
        self.request_id = request_id
        self.retry_after = retry_after

    def __str__(self) -> str:
        text = f'{self.status} {self.code or "error"}'
        if self.detail:
            text += f': {self.detail}'
        issues = '; '.join(f'{".".join(map(str, issue.loc))}: {issue.msg}' for issue in self.errors)
        return f'{text} ({issues})' if issues else text
