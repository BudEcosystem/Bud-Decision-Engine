# One-step setup from a source checkout on Windows (the desktop app does the same thing with buttons).
#   powershell -ExecutionPolicy Bypass -File install.ps1 [--device cuda|xpu|cpu]
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv, the Python package manager the studio uses..."
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv run --no-project --python 3.12 installer/engine.py setup --venv .venv --data data @args
