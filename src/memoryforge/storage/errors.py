"""Storage-boundary errors shared by workspace components."""


class WorkspaceSecurityError(ValueError):
    """Raised when a workspace path violates the local storage boundary."""


class WorkspaceIntegrityError(RuntimeError):
    """Raised when immutable evidence no longer matches its recorded digest."""
