param(
  [string]$Version = '2.0.0'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
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

python -m unittest discover -s (Join-Path $root 'tests') -v
if ($LASTEXITCODE -ne 0) {
  throw "Tests failed with exit code $LASTEXITCODE"
}

& (Join-Path $root 'build_desktop_exe.ps1')
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
Copy-Item -LiteralPath (Join-Path $root 'WINDOWS_LOCAL_SETUP.md') -Destination $stage
Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination $stage

$hash = Get-FileHash -LiteralPath (Join-Path $stage 'YSU-Netlogin-Healing-Manager.exe') -Algorithm SHA256
$hashLine = "$($hash.Hash.ToLower())  YSU-Netlogin-Healing-Manager.exe"
Set-Content -LiteralPath (Join-Path $stage 'SHA256SUMS.txt') -Value $hashLine -Encoding ascii

$archiveInputs = Join-Path $stage '*'
Compress-Archive -Path $archiveInputs -DestinationPath $zip -CompressionLevel Optimal

python (Join-Path $root 'security_audit.py') $stage $zip
if ($LASTEXITCODE -ne 0) {
  throw "Release security audit failed with exit code $LASTEXITCODE"
}

$zipHash = Get-FileHash -LiteralPath $zip -Algorithm SHA256
Write-Host "Release ZIP: $zip"
Write-Host "ZIP SHA256: $($zipHash.Hash.ToLower())"
Write-Host "Packaged self-test: $selfTest"
