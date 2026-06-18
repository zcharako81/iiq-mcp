"""Typed exceptions for SCIM and auth errors."""


class ScimError(Exception):
    """Base exception for all SCIM-related errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.detail:
            parts.append(self.detail)
        return " — ".join(parts)


class ScimAuthError(ScimError):
    """401 — bad credentials or session expired."""


class ScimNotFoundError(ScimError):
    """404 — resource not found."""


class ScimBadRequestError(ScimError):
    """400 — invalid request payload."""


class ScimServerError(ScimError):
    """5xx — IIQ server error."""


class ScimNetworkError(ScimError):
    """Connection/timeout/transport errors (status_code is always None)."""


class CredentialsNotFound(Exception):
    """No credentials found in keychain."""


class CredentialsInvalid(Exception):
    """Credentials exist but failed validation against IIQ."""
