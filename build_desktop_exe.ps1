$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$viewer = Join-Path (Split-Path -Parent $root) 'tools\TigerVNC\vncviewer64-1.16.2.exe'
$audit = Join-Path $root 'security_audit.py'
if (-not (Test-Path -LiteralPath $viewer)) {
  throw "TigerVNC Viewer not found: $viewer"
}
$runId = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
$workPath = Join-Path $root ('build-secure\' + $runId)
$distPath = Join-Path $root 'dist-secure'
$sourceInputs = @(
  (Join-Path $root 'desktop_gui.py'),
  (Join-Path $root 'heartbeat.py'),
  (Join-Path $root 'netlogin.py'),
  (Join-Path $root 'self_service.py'),
  (Join-Path $root 'campus_network.py'),
  (Join-Path $root 'campus_adapters.ps1'),
  (Join-Path $root 'wifi_scan.py'),
  $viewer
)

python $audit @sourceInputs
if ($LASTEXITCODE -ne 0) {
  throw "Pre-build security audit failed. Packaging was refused."
}

$pyInstallerArgs = @(
  '-m'
  'PyInstaller'
  '--noconfirm'
  '--clean'
  '--onefile'
  '--windowed'
  '--name'
  'YSU-Netlogin-Healing-Manager'
  '--hidden-import'
  'cryptography'
  '--add-data'
  ((Join-Path $root 'netlogin.py') + ';.')
  '--add-data'
  ((Join-Path $root 'self_service.py') + ';.')
  '--add-data'
  ((Join-Path $root 'campus_adapters.ps1') + ';.')
  '--add-data'
  ((Join-Path $root 'campus_network.example.json') + ';.')
  '--add-binary'
  ($viewer + ';tools\TigerVNC')
  '--distpath'
  $distPath
  '--workpath'
  $workPath
  '--specpath'
  $workPath
  (Join-Path $root 'desktop_gui.py')
)
python @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}
$artifact = Join-Path $distPath 'YSU-Netlogin-Healing-Manager.exe'
python $audit $workPath $artifact
if ($LASTEXITCODE -ne 0) {
  throw "Post-build manifest security audit failed."
}
Write-Host "Secure EXE: $artifact"
