"""Typed exceptions for SCIM and auth errors."""


class ScimError(Exception):
    """Base exception for all SCIM-related errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class ScimAuthError(ScimError):
    """401 — bad credentials or session expired."""


class ScimNotFoundError(ScimError):
    """404 — resource not found."""


class ScimBadRequestError(ScimError):
    """400 — invalid request payload."""


class ScimServerError(ScimError):
    """5xx — IIQ server error."""


class ScimNetworkError(ScimError):
    """Connection/timeout/transport errors."""


class CredentialsNotFound(Exception):
    """No credentials found in keychain."""


class CredentialsInvalid(Exception):
    """Credentials exist but failed validation against IIQ."""
