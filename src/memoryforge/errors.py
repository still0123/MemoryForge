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


class UnmappedProjectError(MemoryForgeError):
    """Raised when an AI Host project path is not bound to any registered Git checkout.

    Agent Access fails closed: an unmapped project never degrades into an
    unscoped whole-Workspace query.
    """
