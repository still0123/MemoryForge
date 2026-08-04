"""Domain-specific errors surfaced by the CLI."""


class MemoryForgeError(Exception):
    """Base class for recoverable MemoryForge errors."""


class WorkspaceError(MemoryForgeError):
    """Raised when a workspace is missing or cannot be initialized."""


class ImportSafetyError(MemoryForgeError):
    """Raised when a source is not safe to import."""


class UnsupportedSourceError(MemoryForgeError):
    """Raised when a source media type is outside the MVP boundary."""


class FeatureUnavailableError(MemoryForgeError):
    """Raised for lifecycle commands scheduled for a later milestone."""


class ChangeSetStoreError(MemoryForgeError):
    """Raised when staged ChangeSet data is missing, corrupt, or conflicting."""


class LifecycleError(MemoryForgeError):
    """Raised when compilation or a ChangeSet transition cannot proceed."""
