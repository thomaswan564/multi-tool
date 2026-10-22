$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$app = Join-Path $PSScriptRoot "code-multi-tool-v3.0.py"
$vendor = Join-Path $PSScriptRoot "_vendor"
$qrcode = Join-Path $vendor "qrcode"
$name = "Multi-Tool-v3.0.2"

py -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name $name `
  --paths $PSScriptRoot `
  --paths $vendor `
  --collect-all playwright `
  --collect-all pdfplumber `
  --add-data "$qrcode;qrcode" `
  --collect-data certifi `
  $app

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $PSScriptRoot "dist\$name.exe"
if (-not (Test-Path -LiteralPath $exe)) {
  throw "Build did not produce $exe"
}
Write-Host "Built: $exe"
