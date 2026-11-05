param([switch]$Demo, [switch]$Standalone)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$guardianPython = (Get-Command python).Source
$guardianBase = & $guardianPython -c 'import sys; print(sys.base_prefix)'
$guardianPythonw = Join-Path $guardianBase 'pythonw.exe'
if (-not (Test-Path -LiteralPath $guardianPythonw)) { throw '找不到 pythonw.exe，无法无控制台启动。' }
$guardianLauncher = Join-Path $PSScriptRoot 'launch.pyw'
$guardianArgs = @(('"' + $guardianLauncher + '"'))
if ($Demo) { $guardianArgs += '--demo' }
if ($Standalone) { $guardianArgs += '--standalone' }
Start-Process -FilePath $guardianPythonw -ArgumentList $guardianArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
