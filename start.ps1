$ErrorActionPreference = "Stop"

$containerName = "noor-raqm-app"
$imageName = "noor-ul-quran:raqm"
$projectRoot = $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is not running. Start Docker Desktop and try again."
}

$container = @(docker inspect $containerName 2>$null | ConvertFrom-Json)
if ($container) {
    if ($container[0].State.Status -ne "running") {
        docker start $containerName
        if ($LASTEXITCODE -ne 0) {
            throw "Could not start container $containerName."
        }
    }
} else {
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw "Environment file not found: $envFile"
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
        "-e", "RENDER_MEMORY_PROFILE=low",
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
}

Start-Process "http://127.0.0.1:8001/"
