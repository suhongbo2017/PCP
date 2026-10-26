# Single-file, GUI-only PyInstaller build. Double-click opens the app (no console).
from pathlib import Path

project = Path(SPECPATH)
datas = [
    (str(project / "vendor"), "vendor"),
    (str(project / "examples"), "examples"),
]

a = Analysis(
    [str(project / "src" / "phonectl_gui_launcher.py")],
    pathex=[str(project / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "phonectl.automation",
        "phonectl.automation.device",
        "phonectl.automation.macro",
        "phonectl.automation.runner",
        "phonectl.automation.vision",
        "phonectl.ui.main_window",
        "phonectl.ui.wifi_dialog",
        "av",
        "cv2",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="phonectl-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
