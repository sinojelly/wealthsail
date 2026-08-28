param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Invoke-Checked {
    param([scriptblock]$Command, [string]$Label)
    Write-Host "==> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Python virtual environment not found: $Python"
    }
    Invoke-Checked { & $Python scripts/check_conflict_markers.py } "Merge conflict marker check"
    Invoke-Checked { & $Python -m ruff check backend tests scripts } "Python lint"
    Invoke-Checked { & $Python -m compileall -q backend scripts } "Python compile"
    Invoke-Checked { & $Python -m pytest -q } "Backend tests"
    Invoke-Checked {
        & $Python -c "from pathlib import Path; from scripts.migrate import load_migrations; rows=load_migrations(Path('migrations')); print(f'{len(rows)} schema migrations discovered')"
    } "Migration discovery/checksum"
    Invoke-Checked {
        & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_docs.ps1
    } "Documentation contracts"

    Push-Location (Join-Path $projectRoot "frontend")
    try {
        Invoke-Checked { npm run lint } "Frontend lint"
        Invoke-Checked { npm test -- --run } "Frontend tests"
        Invoke-Checked { npm run test:smoke-config } "Frontend smoke safety contracts"
        Invoke-Checked { npm run build } "Frontend production build"
    } finally {
        Pop-Location
    }

    Invoke-Checked { git diff --check } "Git whitespace check"
    Write-Host "Verification completed successfully."
} finally {
    Pop-Location
}
