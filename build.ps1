$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (!(Test-Path $python)) {
    throw "Python virtual environment not found: $python"
}

& $python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "phonectl.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "Build complete: $PSScriptRoot\dist\phonectl\phonectl.exe"
