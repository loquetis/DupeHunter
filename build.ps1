# Builds dist\DupeHunter.exe with the shared build venv.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$venv = 'C:\Users\corey\workspace\buildtools\pyenv315'
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    python -m venv $venv
    & "$venv\Scripts\python.exe" -m pip install --disable-pip-version-check pyinstaller
}
& "$venv\Scripts\python.exe" -m pip install --disable-pip-version-check pillow
# Optional HEIC/HEIF photo support: only if a wheel exists for this Python; the build works without it.
$old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
& "$venv\Scripts\python.exe" -m pip install --disable-pip-version-check pillow-heif 2>&1 | Out-Null
$ErrorActionPreference = $old
& "$venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath dist --workpath build DupeHunter.spec
Get-FileHash dist\DupeHunter.exe -Algorithm SHA256
