"""Lazy aliases for import paths that moved in MemoryForge 0.4."""

import sys
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from importlib.util import spec_from_loader
from types import ModuleType

_LEGACY_MODULES = {
    "memoryforge._code_index_common": "memoryforge.code._code_index_common",
    "memoryforge.agent": "memoryforge.query.agent",
    "memoryforge.agent_access": "memoryforge.query.agent_access",
    "memoryforge.agent_evaluation": "memoryforge.evaluation.agent_evaluation",
    "memoryforge.apply_journal": "memoryforge.storage.apply_journal",
    "memoryforge.automation_apply": "memoryforge.automation.automation_apply",
    "memoryforge.automation_policy": "memoryforge.automation.automation_policy",
    "memoryforge.automation_validation": "memoryforge.automation.automation_validation",
    "memoryforge.botmux_adapter": "memoryforge.adapters.botmux_adapter",
    "memoryforge.capture_inbox": "memoryforge.storage.capture_inbox",
    "memoryforge.capture_models": "memoryforge.core.capture_models",
    "memoryforge.changesets": "memoryforge.storage.changesets",
    "memoryforge.cli": "memoryforge.interface.cli",
    "memoryforge.code_evaluation": "memoryforge.code.code_evaluation",
    "memoryforge.code_history": "memoryforge.code.code_history",
    "memoryforge.code_impact": "memoryforge.code.code_impact",
    "memoryforge.code_index": "memoryforge.code.code_index",
    "memoryforge.code_intelligence": "memoryforge.code.code_intelligence",
    "memoryforge.code_models": "memoryforge.code.code_models",
    "memoryforge.code_wiki_compiler": "memoryforge.compiler.code_wiki_compiler",
    "memoryforge.codex_adapter": "memoryforge.adapters.codex_adapter",
    "memoryforge.codex_connect": "memoryforge.interface.codex_connect",
    "memoryforge.desktop": "memoryforge.portal.desktop",
    "memoryforge.egress_models": "memoryforge.core.egress_models",
    "memoryforge.egress_policy": "memoryforge.compiler.egress_policy",
    "memoryforge.errors": "memoryforge.core.errors",
    "memoryforge.feishu_adapter": "memoryforge.adapters.feishu_adapter",
    "memoryforge.feishu_bot": "memoryforge.adapters.feishu_bot",
    "memoryforge.feishu_service": "memoryforge.adapters.feishu_service",
    "memoryforge.folder_adapter": "memoryforge.adapters.folder_adapter",
    "memoryforge.freshness": "memoryforge.compiler.freshness",
    "memoryforge.git_adapter": "memoryforge.adapters.git_adapter",
    "memoryforge.github_thread_adapter": "memoryforge.adapters.github_thread_adapter",
    "memoryforge.go_index": "memoryforge.code.go_index",
    "memoryforge.handoff": "memoryforge.storage.handoff",
    "memoryforge.host_config": "memoryforge.core.host_config",
    "memoryforge.importer": "memoryforge.adapters.importer",
    "memoryforge.knowledge_conflicts": "memoryforge.compiler.knowledge_conflicts",
    "memoryforge.knowledge_lifecycle": "memoryforge.compiler.knowledge_lifecycle",
    "memoryforge.lifecycle": "memoryforge.compiler.lifecycle",
    "memoryforge.linting": "memoryforge.compiler.linting",
    "memoryforge.local_portal": "memoryforge.portal.local_portal",
    "memoryforge.manifests": "memoryforge.core.manifests",
    "memoryforge.mcp_server": "memoryforge.interface.mcp_server",
    "memoryforge.models": "memoryforge.core.models",
    "memoryforge.module_planner": "memoryforge.compiler.module_planner",
    "memoryforge.obsidian": "memoryforge.adapters.obsidian",
    "memoryforge.platform_lock": "memoryforge.core.platform_lock",
    "memoryforge.portal_assets": "memoryforge.portal.portal_assets",
    "memoryforge.portal_catalog": "memoryforge.portal.portal_catalog",
    "memoryforge.portal_jobs": "memoryforge.portal.portal_jobs",
    "memoryforge.provider": "memoryforge.query.provider",
    "memoryforge.redaction": "memoryforge.compiler.redaction",
    "memoryforge.refresh": "memoryforge.compiler.refresh",
    "memoryforge.retrieval_models": "memoryforge.core.retrieval_models",
    "memoryforge.retrieval_v2": "memoryforge.query.retrieval_v2",
    "memoryforge.sessions": "memoryforge.query.sessions",
    "memoryforge.showcase": "memoryforge.portal.showcase",
    "memoryforge.typescript_index": "memoryforge.code.typescript_index",
    "memoryforge.version_store": "memoryforge.storage.version_store",
    "memoryforge.web_adapter": "memoryforge.adapters.web_adapter",
    "memoryforge.wiki_facts": "memoryforge.compiler.wiki_facts",
    "memoryforge.workspace": "memoryforge.storage.workspace",
}


class _AliasLoader(Loader):
    def __init__(self, target_name: str) -> None:
        self.target_name = target_name
        self.target_spec: ModuleSpec | None = None

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        module = import_module(self.target_name)
        self.target_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        if self.target_spec is None:
            return
        module.__spec__ = self.target_spec
        module.__loader__ = self.target_spec.loader
        module.__package__ = self.target_spec.parent


class _LegacyModuleFinder(MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        del path, target
        target_name = _LEGACY_MODULES.get(fullname)
        if target_name is None:
            return None
        return spec_from_loader(fullname, _AliasLoader(target_name))


_FINDER = _LegacyModuleFinder()


def install_legacy_imports() -> None:
    """Install the lazy legacy-module resolver once per interpreter."""
    if _FINDER not in sys.meta_path:
        sys.meta_path.append(_FINDER)
