Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$keyPath = Join-Path $repoRoot ".deploy-keys\simulacro-production.key"

if (-not (Test-Path $keyPath)) {
    throw "No se encontro la llave de despliegue en $keyPath"
}

$env:GIT_SSH_COMMAND = "ssh -i `"$keyPath`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

try {
    git push production HEAD:master
}
finally {
    Remove-Item Env:GIT_SSH_COMMAND -ErrorAction SilentlyContinue
}
