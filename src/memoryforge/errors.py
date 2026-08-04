"""Domain errors rendered safely by the CLI."""


class MemoryForgeError(Exception):
    """Base class for recoverable MemoryForge failures."""


class WorkspaceError(MemoryForgeError):
    """Raised when a workspace cannot be opened or initialized."""


class FeatureUnavailableError(MemoryForgeError):
    """Raised for lifecycle commands scheduled for a later milestone."""


class ChangeSetStoreError(MemoryForgeError):
    """Raised when staged ChangeSet data is invalid, corrupt, or conflicting."""


class LifecycleError(MemoryForgeError):
    """Raised when a ChangeSet review transition cannot proceed."""
