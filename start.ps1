$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created local .env. Development credentials only.'
}
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose failed. Start Docker Desktop, then retry.' }
Write-Host 'App: http://localhost:65030'
Write-Host 'Verification email inbox: http://localhost:8025'
Write-Host 'Scanner starts after signature download. Check: docker compose logs clamav'
