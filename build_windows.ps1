# Build "Image Explorer.exe" (one-folder app) and a .zip on Windows.
#
#   powershell -ExecutionPolicy Bypass -File build_windows.ps1
#   (or just double-click build_windows.bat)
#
# Optional environment variables:
#   $env:PYTHON = "C:\Python312\python.exe"   interpreter for the build venv
#                                             (default: the "py -3" launcher)
#   $env:SKIP_TESTS = "1"                     don't run the test suite first

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$AppName = "Image Explorer"
$Venv = ".venv-build"
$Py = Join-Path $Venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# Native programs don't throw on failure in Windows PowerShell 5.1; check exit codes.
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

Step "Setting up build environment in $Venv"
if (-not (Test-Path $Py)) {
    if ($env:PYTHON) {
        Invoke-Checked $env:PYTHON @("-m", "venv", $Venv)
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Checked "py" @("-3", "-m", "venv", $Venv)
    } else {
        Invoke-Checked "python" @("-m", "venv", $Venv)
    }
}
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "-r", "requirements-build.txt")

$Version = (& $Py -c "import app; print(app.__version__)").Trim()
$Arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
Write-Host "Image Explorer $Version for Windows $Arch, $(& $Py --version)"

if (-not $env:SKIP_TESTS) {
    Step "Running tests"
    Invoke-Checked $Py @("-m", "pytest", "-q")
}

Step "Building with PyInstaller"
foreach ($dir in "build", "dist") {
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
}
Invoke-Checked $Py @("-m", "PyInstaller", "--noconfirm", "--clean", "packaging\image_explorer.spec")

Step "Self-test of the built app"
# The exe is a windowed (no console) app, so wait on the process and read the
# report file it writes instead of stdout.
$Exe = Join-Path "dist\$AppName" "$AppName.exe"
$Report = Join-Path $env:TEMP "image_explorer_selftest.txt"
if (Test-Path $Report) { Remove-Item $Report }
$proc = Start-Process -FilePath $Exe -ArgumentList "--self-test" -Wait -PassThru
if (Test-Path $Report) { Get-Content $Report | Write-Host }
if ($proc.ExitCode -ne 0) { throw "Self-test failed (exit code $($proc.ExitCode))" }

Step "Creating zip"
$Zip = "dist\Image-Explorer-$Version-windows-$Arch.zip"
Compress-Archive -Path "dist\$AppName" -DestinationPath $Zip -Force

Step "Done"
Write-Host "App: dist\$AppName\$AppName.exe"
Write-Host "Zip: $Zip  ($([math]::Round((Get-Item $Zip).Length / 1MB)) MB)"
Write-Host ""
Write-Host "Note: the exe is unsigned, so SmartScreen may warn on first run"
Write-Host "('More info' > 'Run anyway'). Sign it with signtool for distribution."
