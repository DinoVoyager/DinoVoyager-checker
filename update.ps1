# ==============================================================================
# DinoVoyager-checker Auto-Update Script for Windows (PowerShell)
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "        🦖 DinoVoyager-checker Update Tool            " -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# 1. Check Git Repository & Pull Latest Commits
if (Test-Path ".git") {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        Write-Host ">> Checking for updates on GitHub..." -ForegroundColor Yellow
        $currentBranch = (git rev-parse --abbrev-ref HEAD 2>$null)
        if (-not $currentBranch) { $currentBranch = "main" }

        git fetch origin $currentBranch --quiet 2>$null

        $localHash = (git rev-parse HEAD 2>$null)
        $remoteHash = (git rev-parse "origin/$currentBranch" 2>$null)

        if ($localHash -and $remoteHash -and ($localHash -ne $remoteHash)) {
            Write-Host ">> New commits found on GitHub. Updating repository..." -ForegroundColor Green
            git pull origin $currentBranch
            Write-Host ">> Git repository updated to $($remoteHash.Substring(0, 8))" -ForegroundColor Green
        } else {
            Write-Host ">> Local repository is already up to date ($($localHash.Substring(0, 8)))." -ForegroundColor Green
        }
    } else {
        Write-Host ">> Warning: git command not found, skipping git pull." -ForegroundColor Yellow
    }
} else {
    Write-Host ">> Not a git repository. Skipping git pull." -ForegroundColor Yellow
}

# 2. Docker Service & Container Restart
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCmd) {
    $containerName = "dinovoyager-checker"
    $isCompose = (docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' $containerName 2>$null)

    if ($isCompose -and ((Test-Path "docker-compose.yml") -or (Test-Path "docker-compose.yaml") -or (Test-Path "compose.yaml"))) {
        Write-Host ">> Rebuilding and restarting via Docker Compose..." -ForegroundColor Yellow
        docker compose build --pull
        docker compose up -d --remove-orphans
        Write-Host ">> Compose services updated and running." -ForegroundColor Green
    } else {
        $existing = (docker ps -a --format '{{.Names}}' | Where-Object { $_ -eq $containerName })
        if ($existing) {
            Write-Host ">> Detected running container '$containerName'. Preserving environment settings..." -ForegroundColor Yellow
            
            $envVars = @("SUBSCRIPTION_URL", "POLLING_INTERVAL", "API_URL", "API_KEY", "CHECKER_NAME", "LOG_LEVEL", "PROXY_TIMEOUT")
            $envArgs = @()

            $rawEnv = (docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' $containerName 2>$null)
            foreach ($varName in $envVars) {
                foreach ($line in $rawEnv) {
                    if ($line -match "^$varName=(.*)$") {
                        $val = $Matches[1]
                        $envArgs += "-e"
                        $envArgs += "$varName=$val"
                        break
                    }
                }
            }

            Write-Host ">> Building updated image 'as1aw/checker:latest'..." -ForegroundColor Yellow
            docker build -t as1aw/checker:latest .

            Write-Host ">> Re-creating container '$containerName'..." -ForegroundColor Yellow
            docker stop $containerName 2>$null
            docker rm $containerName 2>$null

            $runArgs = @("run", "-d", "--name", $containerName, "--restart", "unless-stopped") + $envArgs + @("as1aw/checker:latest")
            & docker $runArgs
            Write-Host ">> Container '$containerName' successfully updated and started." -ForegroundColor Green
        } else {
            Write-Host ">> Building Docker image 'as1aw/checker:latest'..." -ForegroundColor Yellow
            docker build -t as1aw/checker:latest .
            Write-Host ">> Image built successfully." -ForegroundColor Green
        }
    }

    # Health check
    Start-Sleep -Seconds 4
    $running = (docker ps --format '{{.Names}}' | Where-Object { $_ -eq $containerName })
    if ($running) {
        Write-Host ">> Recent container logs:" -ForegroundColor Green
        docker logs --tail 8 $containerName
    }
} else {
    Write-Host ">> Docker not found on host. Local files updated." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host "    ✅ DinoVoyager-checker update completed!          " -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
