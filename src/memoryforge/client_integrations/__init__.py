from memoryforge.client_integrations import codex as codex
from memoryforge.client_integrations import claude as claude
from memoryforge.client_integrations import cursor as cursor
from memoryforge.client_integrations import gemini as gemini
from memoryforge.client_integrations.common import (
    IntegrationPlan as IntegrationPlan,
)
from memoryforge.client_integrations.common import (
    IntegrationResult as IntegrationResult,
)
from memoryforge.client_integrations.common import (
    ManagedFileChange as ManagedFileChange,
)
from memoryforge.client_integrations.common import (
    host_id as host_id,
)
from memoryforge.client_integrations.common import (
    mcp_command as mcp_command,
)


def get_adapter(agent: str):
    adapters = {
        "codex": "memoryforge.client_integrations.codex",
        "claude": "memoryforge.client_integrations.claude",
        "cursor": "memoryforge.client_integrations.cursor",
        "gemini": "memoryforge.client_integrations.gemini",
    }
    module_name = adapters.get(agent)
    if module_name is None:
        raise ValueError(f"unknown client adapter: {agent}")
    from importlib import import_module

    return import_module(module_name)

__all__ = [
    "codex",
    "claude",
    "cursor",
    "gemini",
    "IntegrationPlan",
    "IntegrationResult",
    "ManagedFileChange",
    "host_id",
    "mcp_command",
    "get_adapter",
]
