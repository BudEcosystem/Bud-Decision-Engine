# Installs Bud Decision Studio, the desktop app, from the latest GitHub release (Windows 10 and 11, x64).
#
#   irm https://raw.githubusercontent.com/BudEcosystem/Bud-Decision-Engine/main/get.ps1 | iex
#
# Installs for the current user (no administrator rights needed), adds a Start menu shortcut, and opens it.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$repo = "BudEcosystem/Bud-Decision-Engine"
Write-Host "==> Finding the latest release of Bud Decision Studio" -ForegroundColor Magenta
$release = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest"
$asset = $release.assets | Where-Object { $_.name -match "x64-setup\.exe$" } | Select-Object -First 1
if (-not $asset) { throw "No Windows installer found in release $($release.tag_name)." }
$file = Join-Path $env:TEMP $asset.name
Write-Host "==> Downloading $($asset.name)" -ForegroundColor Magenta
Invoke-WebRequest $asset.browser_download_url -OutFile $file
Write-Host "==> Installing" -ForegroundColor Magenta
Start-Process -FilePath $file -ArgumentList "/S" -Wait
Remove-Item $file -ErrorAction SilentlyContinue
$dir = Join-Path $env:LOCALAPPDATA "Bud Decision Studio"
$exe = @("Bud Decision Studio.exe", "bud-decision-studio.exe") | ForEach-Object { Join-Path $dir $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($exe) {
  Write-Host "==> Done. Opening Bud Decision Studio (also in your Start menu)" -ForegroundColor Magenta
  Start-Process $exe
} else {
  Write-Host "==> Done. Open Bud Decision Studio from the Start menu." -ForegroundColor Magenta
}
