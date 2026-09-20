$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("smart-mt5-runtime-" + [guid]::NewGuid())
$venvPath = Join-Path $tempRoot "venv"

New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
    & py -3.12 -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12 virtual environment creation failed."
    }

    $python = Join-Path $venvPath "Scripts\python.exe"
    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "pip bootstrap failed."
    }

    Push-Location $projectRoot
    try {
        & $python -m pip install -r requirements\lock\full.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Full lockfile installation failed."
        }

        & $python -m compileall -q src tests
        if ($LASTEXITCODE -ne 0) {
            throw "Compilation validation failed."
        }

        & $python -m ruff check src tests
        if ($LASTEXITCODE -ne 0) {
            throw "Ruff check failed."
        }

        & $python -m ruff format --check src tests
        if ($LASTEXITCODE -ne 0) {
            throw "Ruff format check failed."
        }

        & $python -m mypy --follow-imports=skip src/python
        if ($LASTEXITCODE -ne 0) {
            throw "Mypy validation failed."
        }

        & $python -m pytest -q --import-mode=importlib `
            --ignore=tests/python/mt5_account `
            --ignore=tests/test_recent_mt5_trades.py
        if ($LASTEXITCODE -ne 0) {
            throw "Pytest validation failed."
        }

        $env:POSTGRES_PASSWORD = "runtime-lock-validation-placeholder"
        $env:API_AUTH_TOKEN = "runtime-lock-validation-placeholder"
        $env:GRAFANA_ADMIN_PASSWORD = "runtime-lock-validation-placeholder"
        & $python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic validation failed."
        }

        & docker compose --profile dashboard config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose validation failed."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
