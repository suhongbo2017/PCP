# PyInstaller onedir build for the Windows CLI + GUI executable.
from pathlib import Path

project = Path(SPECPATH)
datas = [
    (str(project / "vendor"), "vendor"),
    (str(project / "examples"), "examples"),
]

analysis = Analysis(
    [str(project / "src" / "phonectl_launcher.py")],
    pathex=[str(project / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "phonectl.automation",
        "phonectl.automation.device",
        "phonectl.automation.macro",
        "phonectl.automation.runner",
        "phonectl.automation.vision",
        "phonectl.ui.app",
        "av",
        "cv2",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="phonectl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="phonectl",
)
