$ErrorActionPreference = "Stop"

$containerName = "noor-raqm-app"
$imageName = "noor-ul-quran:raqm"
$projectRoot = $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is not running. Start Docker Desktop and try again."
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Environment file not found: $envFile"
}

docker build -t $imageName $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Docker image build failed."
}

$container = @(docker inspect $containerName 2>$null | ConvertFrom-Json)
if ($container) {
    docker rm -f $containerName
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove old container $containerName."
    }
}

$dockerArgs = @(
    "run", "-d",
    "--name", $containerName,
    "--restart", "unless-stopped",
    "-p", "8001:10000",
    "--env-file", $envFile,
    "-e", "OUTPUT_DIR=/app/output",
    "-e", "BACKGROUNDS_DIR=/app/backgrounds",
    "-e", "CACHE_DIR=/app/cache",
    "-v", "${projectRoot}\audio:/app/audio",
    "-v", "${projectRoot}\backgrounds:/app/backgrounds",
    "-v", "${projectRoot}\cache:/app/cache",
    "-v", "${projectRoot}\output:/app/output",
    $imageName
)
docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Could not create container $containerName."
}

$healthUrl = "http://127.0.0.1:8001/api/health"
$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $healthy) {
    throw "Health check failed: $healthUrl did not return HTTP 200."
}

Write-Output "Container $containerName is healthy (HTTP 200)."
