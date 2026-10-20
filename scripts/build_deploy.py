"""Build dist/AIOpHub_Deploy.zip: the application plus Windows Server entrypoints.

Secrets (.env, keys), local data (data/: sync DB and customer reports), virtualenvs, logs,
caches and VCS metadata are never packaged. The entrypoints from deploy/ are written to the
zip root as ASCII with CRLF line endings.

Usage (from the project root): python -m scripts.build_deploy
"""

import fnmatch
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "AIOpHub_Deploy.zip"
DEPLOY_DIR = ROOT / "deploy"
ENTRYPOINTS = ("setup_server.bat", "register_task.ps1", "run_batch_dry.bat")
EXCLUDED_DIRS = (
    ".venv*",
    "__pycache__",
    "logs*",
    "*.git*",
    "data",
    "scratchpad*",
    "dist*",
    "deploy",
    ".*_cache",
    ".claude",
    ".vscode",
)
EXCLUDED_FILES = ("*.git*", ".env", ".env.*", "*.db*", "*.pyc", "*.bat", "*.key", "*.pem", "*.cert")
KEPT_FILES = (".env.example",)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def package_files(root: Path) -> list[Path]:
    """Files to ship, relative to `root`; excluded folders are pruned, never walked."""
    files: list[Path] = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not _matches(d, EXCLUDED_DIRS))
        for name in sorted(names):
            if name in KEPT_FILES or not _matches(name, EXCLUDED_FILES):
                files.append((Path(folder) / name).relative_to(root))
    return files


def windows_text(path: Path) -> bytes:
    """ASCII-only text with CRLF line endings, as cmd.exe and Task Scheduler expect."""
    text = path.read_text(encoding="ascii")
    return "\r\n".join(text.replace("\r\n", "\n").split("\n")).encode("ascii")


def build(root: Path = ROOT, output: Path = OUTPUT, deploy_dir: Path = DEPLOY_DIR) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in package_files(root):
            archive.write(root / relative, relative.as_posix())
        for name in ENTRYPOINTS:
            archive.writestr(name, windows_text(deploy_dir / name))
    return output


def main() -> None:
    path = build()
    with zipfile.ZipFile(path) as archive:
        count = len(archive.namelist())
    print(f"{path} ({path.stat().st_size:,} bytes, {count} files)")


if __name__ == "__main__":
    main()
