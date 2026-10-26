$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "dist\phonectl-gui.exe"
if (!(Test-Path $source)) {
    throw "Executable not found. Build it first: $source"
}

$installDir = Join-Path $env:LOCALAPPDATA "Programs\phonectl"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$target = Join-Path $installDir "phonectl.exe"
Copy-Item -Force $source $target

$desktop = [Environment]::GetFolderPath("Desktop")
$link = Join-Path $desktop "Phone Control.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $installDir
$shortcut.Description = "Android phone mirror and control"
$shortcut.Save()

Write-Host "Installed: $target"
Write-Host "Desktop shortcut: $link"
