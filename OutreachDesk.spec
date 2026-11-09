# PyInstaller spec: builds a single-file app that opens a console window and your browser.
# Build with:  pyinstaller --noconfirm OutreachDesk.spec
block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[("web", "web")],
    hiddenimports=["openpyxl", "engine", "server", "storage", "mailer", "reports", "importer"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "numpy", "pandas", "matplotlib"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="OutreachDesk",
    debug=False,
    strip=False,
    upx=False,
    console=True,      # keep the console window: closing it quits the app
)
