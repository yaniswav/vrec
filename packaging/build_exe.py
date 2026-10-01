"""Build the standalone Windows zip: dist/vrec-<version>-windows.zip (run from anywhere).

py -m pip install ".[build]"
py packaging/build_exe.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build"
EXTRA_FILES = ["README.md", "LICENSE", "CHANGELOG.md", "config.example.toml", "videos.example.txt"]


def read_version() -> str:
    text = (ROOT / "src" / "vrec" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("Could not read __version__ from src/vrec/__init__.py")
    return match.group(1)


def main() -> None:
    version = read_version()
    name = f"vrec-{version}-windows"
    folder = DIST / name
    if folder.exists():
        shutil.rmtree(folder)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST / "_pyinstaller"),
            "--workpath",
            str(WORK),
            str(ROOT / "packaging" / "vrec.spec"),
        ],
        check=True,
    )
    shutil.move(str(DIST / "_pyinstaller" / "vrec"), folder)
    shutil.rmtree(DIST / "_pyinstaller")

    for filename in EXTRA_FILES:
        shutil.copy2(ROOT / filename, folder / filename)
    shutil.copytree(ROOT / "docs", folder / "docs")
    for bat in sorted((ROOT / "packaging" / "windows").glob("*.bat")):
        shutil.copy2(bat, folder / bat.name)

    archive = DIST / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, Path(name) / path.relative_to(folder))
    print(f"Built {archive} ({archive.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    main()
