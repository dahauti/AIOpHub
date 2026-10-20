"""Configure an embeddable (portable) Python's ._pth so `python -m src.cli` works.

Runs under the portable interpreter itself and uses the standard library only: it enables
`import site` (so packages pip installs into Lib\\site-packages load) and adds the application
folder to the import path, which embeddable Python otherwise ignores.

Usage: python.exe scripts\\configure_pth.py <application folder>
"""

import sys
from pathlib import Path

SITE_LINE = "import site"


def patch_pth(lines: list[str], app_dir: str) -> list[str]:
    """Uncomment (or add) `import site` and list `app_dir` before it, idempotently."""
    patched = [
        SITE_LINE if line.strip().lstrip("#").strip() == SITE_LINE else line for line in lines
    ]
    if SITE_LINE not in patched:
        patched.append(SITE_LINE)
    if app_dir not in patched:
        patched.insert(patched.index(SITE_LINE), app_dir)
    return patched


def find_pth(python_dir: Path) -> Path:
    candidates = sorted(python_dir.glob("python*._pth"))
    if not candidates:
        raise SystemExit(f"No python*._pth in {python_dir}: is this an embeddable Python?")
    return candidates[0]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    app_dir = str(Path(argv[1]).resolve())
    pth = find_pth(Path(sys.executable).resolve().parent)
    lines = pth.read_text(encoding="utf-8").splitlines()
    patched = patch_pth(lines, app_dir)
    if patched == lines:
        print(f"{pth.name} already configured")
    else:
        pth.write_text("\n".join(patched) + "\n", encoding="utf-8")
        print(f"{pth.name} updated: 'import site' enabled, {app_dir} on the import path")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
