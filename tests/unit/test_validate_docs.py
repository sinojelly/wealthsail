"""Run the real documentation gate against isolated document workspaces."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def powershell() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("documentation behavior tests require PowerShell")
    return executable


@pytest.fixture(scope="module")
def document_template(tmp_path_factory) -> Path:
    workspace = tmp_path_factory.mktemp("documentation-template")
    (workspace / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts" / "validate_docs.ps1",
        workspace / "scripts" / "validate_docs.ps1",
    )
    shutil.copytree(ROOT / "migrations", workspace / "migrations")
    (workspace / "docs").mkdir()
    documents = list((ROOT / "docs").glob("*.md"))
    for document in documents:
        shutil.copyfile(document, workspace / "docs" / document.name)

    # Keep real linked files and security targets without cloning runtimes,
    # secrets, or databases. Only link existence is relevant to these fixtures.
    linked_files = {ROOT / "adapters" / "messaging" / "wecom_adapter.py"}
    for document in documents:
        for target in re.findall(
            r"\]\(([^)]+)\)", document.read_text(encoding="utf-8-sig")
        ):
            target = target.strip().strip("<>")
            if target.startswith(("http:", "https:", "mailto:", "#")):
                continue
            linked_files.add(
                (document.parent / unquote(target.split("#", 1)[0])).resolve()
            )
    for source in linked_files:
        relative = source.relative_to(ROOT)
        destination = workspace / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    return workspace


@pytest.fixture
def document_workspace(tmp_path, document_template) -> Path:
    workspace = tmp_path / "repository"
    shutil.copytree(document_template, workspace)
    return workspace


def run_validator(powershell: str, workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(workspace / "scripts" / "validate_docs.ps1"),
        ],
        cwd=workspace,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def test_accepts_percentage_math_and_asset_allocations(
    powershell, document_workspace
) -> None:
    (document_workspace / "docs" / "99.percentages.md").write_text(
        "# 资产配置数学示例\n\n"
        "| 仓位 | 跌幅 |\n| --- | --- |\n| 100% | -30% |\n\n"
        "80% + 20% = 100%。资产配置合计100%。\n"
        "换手率100%只是观察值；不代表系统已完成验收。\n",
        encoding="utf-8",
    )

    result = run_validator(powershell, document_workspace)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Documentation validation passed." in result.stdout


@pytest.mark.parametrize(
    "claim",
    [
        "本项目100%完成。",
        "全部测试100%通过。",
        "功能100%覆盖。",
        "完成：100%",
        "完成度达到100%",
        "自动化测试通过率100%",
        "测试覆盖率为100%",
        "100% complete",
        "100% completed",
        "100% completion",
        "Completed: 100%",
        "100% pass",
        "Tests passed: 100%",
        "100% coverage",
        "Coverage: 100%",
        "Test coverage is 100%",
        "PASS RATE: 100%",
    ],
)
def test_rejects_unsupported_completion_claims(
    powershell, document_workspace, claim
) -> None:
    (document_workspace / "docs" / "99.completion.md").write_text(
        claim + "\n", encoding="utf-8"
    )

    result = run_validator(powershell, document_workspace)

    assert result.returncode != 0
    assert "99.completion.md" in result.stderr
    assert "unsupported 100 percent completion claim" in result.stderr


def test_still_rejects_private_wecom_delivery_to_everyone(
    powershell, document_workspace
) -> None:
    (document_workspace / "adapters" / "messaging" / "wecom_adapter.py").write_text(
        'def deliver(to_user: str = "@all"):\n    pass\n', encoding="utf-8"
    )

    result = run_validator(powershell, document_workspace)

    assert result.returncode != 0
    assert "WeCom tenant-private delivery must not default to @all." in result.stderr


def test_still_requires_binding_token_entropy(powershell, document_workspace) -> None:
    migration = document_workspace / "migrations" / "002_identity_and_tenancy.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8").replace("128 bit", "16 bit"),
        encoding="utf-8",
    )

    result = run_validator(powershell, document_workspace)

    assert result.returncode != 0
    assert (
        "Binding-token schema guidance must require at least 128 bits" in result.stderr
    )
