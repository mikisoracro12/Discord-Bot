param(
  # Use -NoConsole to build a background/no-window exe
  [switch]$NoConsole
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt

# Clean previous builds (ignore errors)
Remove-Item -Recurse -Force ".\\build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force ".\\dist" -ErrorAction SilentlyContinue
Remove-Item -Force ".\\discord-tracker.spec" -ErrorAction SilentlyContinue

if ($NoConsole) {
  pyinstaller `
    --onefile `
    --name "discord-tracker" `
    --clean `
    --noconsole `
    main.py
} else {
  # Console build (recommended): you can close the window or Ctrl+C.
  pyinstaller `
    --onefile `
    --name "discord-tracker" `
    --clean `
    main.py
}

Write-Host ""
Write-Host "Built: $PSScriptRoot\\dist\\discord-tracker.exe"

