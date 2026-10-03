# PyInstaller spec for the standalone Windows build (onedir). Build with: py packaging/build_exe.py
# vrec only attaches to an existing Chrome over CDP, so no Playwright browsers are bundled.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH).parent

datas = collect_data_files("vrec", includes=["js/*.js"])  # read through importlib.resources
binaries = []
hiddenimports = []
for package in ("playwright", "obsws_python", "PIL", "pyvda", "comtypes"):
    package_datas, package_binaries, package_imports = collect_all(package)  # playwright: its driver folder
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    [str(root / "packaging" / "vrec_entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="vrec", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="vrec", upx=False)
