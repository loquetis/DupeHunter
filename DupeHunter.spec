# PyInstaller spec: one-file, windowed DupeHunter.exe with the web UI bundled.
# Build:  pyinstaller --noconfirm --clean DupeHunter.spec
a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    datas=[('web', 'web')],
    hiddenimports=['server', 'dupes', 'storage', 'winshell', 'appserver'],
    excludes=['tkinter', 'unittest', 'pydoc', 'test', 'numpy'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='DupeHunter',
    console=False,
    icon='dupehunter.ico',
    upx=False,
    version='version_info.txt',
)
