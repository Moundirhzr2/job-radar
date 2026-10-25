# Nightly refresh of the radar, run by the Windows Task Scheduler (see README).
# Keys come from the user's environment variables; the log goes to data\refresh.log.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force data | Out-Null
Start-Transcript -Path data\refresh.log -Append | Out-Null
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ==="

# Docker Desktop stops when the PC restarts: start it and wait for the database.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Start-Process "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    for ($i = 0; $i -lt 60 -and $LASTEXITCODE -ne 0; $i++) { Start-Sleep 5; docker info *> $null }
}
docker compose up -d --wait

$env:UV_SYSTEM_CERTS = "1"  # an antivirus that inspects HTTPS signs with its own root
$uv = "$env:USERPROFILE\.local\bin\uv.exe"
& $uv run radar lba                                              # La bonne alternance, built at 3:00
& $uv run radar francetravail --france --kind apprenticeship     # new France Travail offers
& $uv run radar francetravail --refresh                          # withdrawn ones leave the radar
& $uv run radar index                                            # vectors of new offers
& $uv run radar index                                            # more than 5,000 in one night

Stop-Transcript | Out-Null
