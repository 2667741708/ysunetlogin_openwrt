param(
  [string]$Version = '2.2.0'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$releaseRoot = Join-Path $root 'release'
$stage = Join-Path $releaseRoot ("ysu-netlogin-healing-$Version-windows-x64")
$zip = Join-Path $releaseRoot ("ysu-netlogin-healing-$Version-windows-x64.zip")
$exe = Join-Path $root 'dist-secure\YSU-Netlogin-Healing-Manager.exe'

if (Test-Path -LiteralPath $stage) {
  throw "Release staging directory already exists: $stage"
}
if (Test-Path -LiteralPath $zip) {
  throw "Release archive already exists: $zip"
}

python -m pytest (Join-Path $root 'tests') -q
if ($LASTEXITCODE -ne 0) {
  throw "Tests failed with exit code $LASTEXITCODE"
}

& (Join-Path $PSScriptRoot 'build_desktop_exe.ps1')
if ($LASTEXITCODE -ne 0) {
  throw "Desktop build failed with exit code $LASTEXITCODE"
}

$selfTestPath = [IO.Path]::GetTempFileName()
try {
  $selfTestProcess = Start-Process -FilePath $exe -ArgumentList '--self-test-file',$selfTestPath -Wait -PassThru -WindowStyle Hidden
  if ($selfTestProcess.ExitCode -ne 0) {
    throw "Packaged self-test failed with exit code $($selfTestProcess.ExitCode)"
  }
  $selfTest = Get-Content -LiteralPath $selfTestPath -Raw -Encoding UTF8
  $selfTestObject = $selfTest | ConvertFrom-Json
  if (-not $selfTestObject.ok) {
    throw "Packaged self-test did not report success"
  }
  if ($selfTestObject.version -ne $Version) {
    throw "Packaged version does not match release version"
  }
}
finally {
  Remove-Item -LiteralPath $selfTestPath -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -LiteralPath $exe -Destination $stage
New-Item -ItemType Directory -Path (Join-Path $stage 'docs') -Force | Out-Null
Copy-Item -Path (Join-Path $root 'docs\*.md') -Destination (Join-Path $stage 'docs')
Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination $stage

New-Item -ItemType Directory -Path (Join-Path $stage 'examples') -Force | Out-Null
Copy-Item -Path (Join-Path $root 'examples\*.json') -Destination (Join-Path $stage 'examples')
$screenshots = Join-Path $root 'docs\screenshots\v2.1.0'
if (Test-Path -LiteralPath $screenshots) {
  $screenshotTarget = Join-Path $stage 'docs\screenshots\v2.1.0'
  New-Item -ItemType Directory -Path $screenshotTarget -Force | Out-Null
  Copy-Item -Path (Join-Path $screenshots '*') -Destination $screenshotTarget
}

$hash = Get-FileHash -LiteralPath (Join-Path $stage 'YSU-Netlogin-Healing-Manager.exe') -Algorithm SHA256
$hashLine = "$($hash.Hash.ToLower())  YSU-Netlogin-Healing-Manager.exe"
Set-Content -LiteralPath (Join-Path $stage 'SHA256SUMS.txt') -Value $hashLine -Encoding ascii

$archiveInputs = Join-Path $stage '*'
Compress-Archive -Path $archiveInputs -DestinationPath $zip -CompressionLevel Optimal

python (Join-Path $PSScriptRoot 'security_audit.py') $stage $zip
if ($LASTEXITCODE -ne 0) {
  throw "Release security audit failed with exit code $LASTEXITCODE"
}

$zipHash = Get-FileHash -LiteralPath $zip -Algorithm SHA256
Write-Host "Release ZIP: $zip"
Write-Host "ZIP SHA256: $($zipHash.Hash.ToLower())"
Write-Host "Packaged self-test: $selfTest"
