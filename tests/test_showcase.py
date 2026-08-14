from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.interface.cli import app
from memoryforge.portal.showcase import ShowcaseBuildError, _markdown_html, _page_group, build_showcase


def test_showcase_builds_complete_public_snapshot_without_mutating_workspace(
    tmp_path: Path,
) -> None:
    workspace, evidence = _public_workspace(tmp_path)
    output = tmp_path / "showcase"
    before = _tree_sha256(workspace)

    result = build_showcase(workspace, output, evidence=evidence)

    assert set(path.name for path in output.iterdir()) == {
        ".memoryforge-showcase",
        "index.html",
        "showcase.json",
    }
    assert result["status"] == "built"
    payload = json.loads((output / "showcase.json").read_text(encoding="utf-8"))
    html = (output / "index.html").read_text(encoding="utf-8")
    assert payload["schema_version"] == 1
    assert payload["privacy"] == {
        "include_local": False,
        "redacted_source_count": 1,
        "redacted_version_count": 1,
    }
    assert payload["sources"]
    assert all(
        version["sensitivity"] == "public"
        for source in payload["sources"]
        for version in source["versions"]
    )
    assert "Private launch plan" not in json.dumps(payload)
    assert payload["wiki"]["pages"]
    assert all(page["content"] for page in payload["wiki"]["pages"])
    assert all(page["evidence"] for page in payload["wiki"]["pages"])
    assert any(
        repository["name"] == "repo"
        for page in payload["wiki"]["pages"]
        for repository in page["repositories"]
    )
    assert payload["changeset"]["lifecycle"] == {
        "proposed": True,
        "reviewed": True,
        "approved": True,
        "applied": True,
    }
    assert payload["changeset"]["unified_diff"]
    assert payload["query"]["citations"]
    assert payload["query"]["trace"]
    assert payload["rejection"]["status"] == "unknown"
    assert payload["benchmark"]["metrics"]["answer_accuracy"] == 50.0
    assert [case["id"] for case in payload["benchmark"]["failures"]] == ["known-failure"]
    assert [case["id"] for case in payload["benchmark"]["abstentions"]] == ["unknown-case"]
    assert payload["architecture"]["mermaid"].startswith("flowchart ")
    assert payload["architecture"]["nodes"]
    assert payload["architecture"]["edges"]
    for section in (
        "sources",
        "overview",
        "wiki",
        "changeset-diff",
        "lifecycle",
        "query-trace",
        "benchmarks",
        "failures",
        "architecture",
    ):
        assert f'id="{section}"' in html
    assert '<html lang="zh-CN">' in html
    assert "我的技术知识库" in html
    assert "Private launch plan" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert 'id="wiki-search"' in html
    assert 'data-wiki-target="wiki-page-0"' in html
    assert 'data-wiki-key="wiki/pages/' in html
    assert "筛选当前资料库" in html
    assert 'class="wiki-markdown"' in html
    assert 'class="wiki-group"' in html
    assert "依据来源" in html
    assert "Code: src/service.py" in html
    assert "代码：src/service.py" in html
    assert "最近更新" in html
    assert "知识库目录" in html
    assert "项目 · repo" in html
    assert 'data-wiki-filter="repo"' in html
    assert 'data-wiki-filter-mode="project"' in html
    assert 'data-wiki-project="repo"' in html
    assert "querySelectorAll('.wiki-group[data-wiki-group]')" in html
    assert "const group = next.closest('.wiki-group');" in html
    assert "页面结构" in html
    assert 'class="page-meta"' in html
    assert 'class="citation-details"' in html
    assert "src.service" in html
    assert 'class="page-rail"' in html
    assert 'id="reader-toggle"' in html
    assert 'id="theme-toggle"' in html
    assert "memoryforge-reader-mode" in html
    assert "#page=" in html
    assert _tree_sha256(workspace) == before


def test_showcase_collapses_full_citation_identifiers() -> None:
    source_id = "177b02138e4d3a723f4ec02aa1768378a45e9708c999160646da571b241c753d"
    markdown = (
        f"Verified fact [^source-1-177b0213]\n\n"
        f"[^source-1-177b0213]: source `{source_id}` · revision `7` · `chars:10-20`\n"
    )

    rendered = _markdown_html(markdown, heading_prefix="wiki-page-0")

    assert '<sup class="citation-ref">[1]</sup>' in rendered
    assert '<details class="citation-details">' in rendered
    assert source_id in rendered
    assert rendered.index("citation-details") < rendered.index(source_id)


def test_showcase_localizes_generated_code_headings() -> None:
    rendered = _markdown_html(
        "# Code: src/service.py\n\n"
        "## Module\n\n"
        "- Path: `src/service.py`\n"
        "- Languages: python\n"
        "- Verified symbols: 4\n\n"
        "## Architecture\n\n"
        "## Verified dependencies\n\n"
        "## Verified facts\n\n"
        "## Sources\n"
    )

    assert "代码：src/service.py" in rendered
    assert "模块信息" in rendered
    assert "路径：" in rendered
    assert "语言：" in rendered
    assert "已验证符号数：" in rendered
    assert "架构关系" in rendered
    assert "已验证依赖" in rendered
    assert "已验证事实" in rendered
    assert "证据来源" in rendered


def test_showcase_groups_code_pages_by_stable_page_path() -> None:
    assert (
        _page_group(
            "Service",
            [{"name": "repo"}],
            page_path="wiki/pages/code/abc123/src/service.md",
        )
        == "代码 · repo · src"
    )


def test_showcase_rebuild_is_deterministic_and_replaces_only_owned_output(
    tmp_path: Path,
) -> None:
    workspace, evidence = _public_workspace(tmp_path)
    output = tmp_path / "showcase"
    build_showcase(workspace, output, evidence=evidence)
    first = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
    }

    build_showcase(workspace, output, evidence=evidence)

    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
    } == first
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("do not delete\n", encoding="utf-8")
    with pytest.raises(ShowcaseBuildError, match="not owned"):
        build_showcase(workspace, foreign, evidence=evidence)
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "do not delete\n"


def test_showcase_rejects_unsafe_or_workspace_internal_output(tmp_path: Path) -> None:
    workspace, evidence = _public_workspace(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    output_link = tmp_path / "showcase-link"
    output_link.symlink_to(external, target_is_directory=True)

    with pytest.raises(ShowcaseBuildError, match="symbolic link"):
        build_showcase(workspace, output_link, evidence=evidence)
    with pytest.raises(ShowcaseBuildError, match="outside the Workspace"):
        build_showcase(workspace, workspace / "showcase", evidence=evidence)


def test_showcase_nested_cli_builds_without_model_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, evidence = _public_workspace(tmp_path)
    output = tmp_path / "showcase"
    for name in (
        "OPENAI_API_KEY",
        "ARK_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    result = CliRunner().invoke(
        app,
        [
            "showcase",
            "build",
            "--workspace",
            str(workspace),
            "--output",
            str(output),
            "--evidence",
            str(evidence),
        ],
    )

    assert result.exit_code == 0, result.output
    response = json.loads(result.stdout)
    assert response["status"] == "built"
    assert (output / "index.html").is_file()


def _public_workspace(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "repo"
    source = checkout / "src"
    source.mkdir(parents=True)
    (source / "helper.py").write_text(
        "def helper(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    (source / "service.py").write_text(
        "from src.helper import helper\n\ndef service() -> str:\n    return helper('ready')\n",
        encoding="utf-8",
    )
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "showcase@example.com")
    _git(checkout, "config", "user.name", "Showcase")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "seed")

    workspace = tmp_path / "workspace"
    runner = CliRunner()
    _invoke(runner, "init", str(workspace))
    registered = _invoke_json(
        runner,
        "git-add",
        str(checkout),
        "--public",
        "--workspace",
        str(workspace),
    )
    repository_id = registered["repository_id"]
    _invoke(runner, "code-add", repository_id, "src", "--workspace", str(workspace))
    _invoke(runner, "git-sync", repository_id, "--workspace", str(workspace))
    proposed = _invoke_json(
        runner,
        "ingest",
        "--code-wiki",
        repository_id,
        "--workspace",
        str(workspace),
    )
    changeset_id = proposed["changeset_id"]
    _invoke(runner, "review", changeset_id, "--workspace", str(workspace))
    _invoke(runner, "approve", changeset_id, "--workspace", str(workspace))
    _invoke(runner, "apply", changeset_id, "--workspace", str(workspace))

    private_folder = tmp_path / "private-source"
    private_folder.mkdir()
    private_note = private_folder / "private.md"
    private_note.write_text("# Private launch plan\n\nPrivate launch plan.\n", encoding="utf-8")
    _invoke(
        runner,
        "folder-import",
        str(private_folder),
        "--workspace",
        str(workspace),
    )
    query = _invoke_json(
        runner,
        "ask",
        "Which module does src.service import?",
        "--debug",
        "--verify",
        "--repository",
        repository_id,
        "--workspace",
        str(workspace),
    )
    assert query["status"] == "answered"
    assert query["citations"]

    evidence = tmp_path / "public-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sample_query": {
                    "question": "Which module does src.service import?",
                    "answer": query["answer"],
                    "citations": query["citations"],
                    "trace": query["trace"],
                },
                "evaluation": {
                    "suite": "showcase fixture",
                    "case_count": 2,
                    "memoryforge": {
                        "answer_accuracy": 50.0,
                        "citation_grounding_accuracy": 100.0,
                        "abstention_accuracy": 100.0,
                    },
                    "cases": [
                        {
                            "id": "known-failure",
                            "category": "single_hop",
                            "error_classification": "FACT_SELECTION_MISS",
                            "memoryforge": {
                                "answer_correct": False,
                                "citation_grounded": True,
                                "abstention_correct": True,
                            },
                        },
                        {
                            "id": "unknown-case",
                            "category": "unanswerable",
                            "error_classification": None,
                            "memoryforge": {
                                "answer_correct": True,
                                "citation_grounded": True,
                                "abstention_correct": True,
                            },
                        },
                    ],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return workspace, evidence


def _invoke(runner: CliRunner, *args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def _invoke_json(runner: CliRunner, *args: str) -> dict[str, object]:
    return json.loads(_invoke(runner, *args))


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts
    ):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
