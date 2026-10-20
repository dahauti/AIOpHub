"""Deployment pack: zip contents (no secrets, no data), Windows entrypoints and ._pth patching."""

import zipfile
from pathlib import Path

import pytest

from scripts.build_deploy import ENTRYPOINTS, build
from scripts.configure_pth import SITE_LINE, patch_pth

REPO_DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
EMBED_PTH = [
    "python312.zip",
    ".",
    "",
    "# Uncomment to run site.main() automatically",
    "#import site",
]


def _touch(root: Path, relative: str, text: str = "x") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    kept = ["src/cli.py", "src/templates/base.html", "requirements.txt", ".env.example", "SPEC.MD"]
    dropped = [
        ".env",
        ".env.local",
        ".venv/Lib/site.py",
        ".venv-old/x.py",
        "src/__pycache__/cli.cpython-312.pyc",
        "logs/app.log",
        ".git/HEAD",
        ".gitignore",
        "data/sync.db",
        "data/reports/legacy_adoption_candidates.csv",
        "scratchpad/notes.txt",
        "dist/old.zip",
        "start_hub.bat",
        "secrets/client.pem",
        "deploy/setup_server.bat",
    ]
    for relative in kept + dropped:
        _touch(root, relative)
    return root


def test_zip_keeps_app_files_and_drops_secrets_data_and_tooling(project: Path) -> None:
    output = build(project, project / "dist" / "AIOpHub_Deploy.zip", REPO_DEPLOY)

    names = set(zipfile.ZipFile(output).namelist())

    assert {"src/cli.py", "src/templates/base.html", "requirements.txt", ".env.example"} <= names
    assert set(ENTRYPOINTS) <= names
    leaked = [n for n in names if n.startswith((".env", ".venv", "logs", ".git", "data", "dist"))]
    assert leaked == [".env.example"]
    for fragment in ("__pycache__", "scratchpad", "start_hub.bat", ".pem", "deploy/"):
        assert not any(fragment in name for name in names), fragment


@pytest.mark.parametrize("name", ["setup_server.bat", "register_task.ps1", "run_batch_dry.bat"])
def test_entrypoints_are_ascii_with_crlf_line_endings(project: Path, name: str) -> None:
    output = build(project, project / "dist" / "AIOpHub_Deploy.zip", REPO_DEPLOY)

    raw = zipfile.ZipFile(output).read(name)

    raw.decode("ascii")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_entrypoint_contracts() -> None:
    setup = (REPO_DEPLOY / "setup_server.bat").read_text(encoding="ascii")
    task = (REPO_DEPLOY / "register_task.ps1").read_text(encoding="ascii")
    dry = (REPO_DEPLOY / "run_batch_dry.bat").read_text(encoding="ascii")

    assert "scripts\\configure_pth.py" in setup and "-m pip install -r" in setup
    assert setup.rstrip().splitlines()[-2:] == ["pause", "exit /b %RC%"]
    for fragment in ("AIOpHUB_BC_Frontu_Sync", "-Daily", "'02:00'", "'SYSTEM'", "IgnoreNew"):
        assert fragment in task
    assert "-m src.cli sync --batch" in task
    assert 'set "SYNC_DRY_RUN=true"' in dry and "-m src.cli sync --batch" in dry


def test_pth_patch_enables_site_and_adds_app_dir_once() -> None:
    patched = patch_pth(EMBED_PTH, r"D:\Apps\AIOpHub")

    assert patched.count(SITE_LINE) == 1 and "#import site" not in patched
    assert patched.index(r"D:\Apps\AIOpHub") < patched.index(SITE_LINE)
    assert patch_pth(patched, r"D:\Apps\AIOpHub") == patched


def test_pth_patch_appends_site_line_when_absent() -> None:
    assert patch_pth(["python312.zip", "."], "C:\\app") == [
        "python312.zip",
        ".",
        "C:\\app",
        SITE_LINE,
    ]
