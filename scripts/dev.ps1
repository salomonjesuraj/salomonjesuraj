param(
    [ValidateSet(
        "setup-dev",
        "test",
        "test-all",
        "test-integration",
        "lint",
        "format",
        "typecheck",
        "compile-check",
        "compose-check"
    )]
    [string]$Task = "test"
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$VenvRuff = Join-Path $Root ".venv\Scripts\ruff.exe"
$VenvMypy = Join-Path $Root ".venv\Scripts\mypy.exe"

function Ensure-Venv {
    if (-not (Test-Path $VenvPython)) {
        python -m venv (Join-Path $Root ".venv")
    }
}

function Run-Python {
    param([string[]]$PyArgs)
    & $VenvPython @PyArgs
}

Set-Location $Root

switch ($Task) {
    "setup-dev" {
        Ensure-Venv
        Run-Python @("-m", "pip", "install", "-U", "pip", "setuptools", "wheel")
        Run-Python @("-m", "pip", "install", "-r", "requirements-dev.txt")
        Run-Python @("-m", "pip", "install", "-e", "libs\infusion-models", "-e", "libs\infusion-streams", "-e", "libs\infusion-common")
        Run-Python @("-m", "pip", "install", "-e", "services\feature-engine", "-e", "services\api", "-e", "services\scanner", "-e", "services\archiver")
    }
    "test" {
        Run-Python @("-m", "pytest", "tests\unit", "-v")
    }
    "test-all" {
        Run-Python @("-m", "pytest", "tests", "-v")
    }
    "test-integration" {
        Run-Python @("-m", "pytest", "tests\integration", "-v")
    }
    "lint" {
        & $VenvRuff check .
        & $VenvRuff format --check .
    }
    "format" {
        & $VenvRuff format .
    }
    "typecheck" {
        & $VenvMypy libs services
    }
    "compile-check" {
        Run-Python @("-m", "compileall", "-q", "libs", "services", "scripts", "tests")
    }
    "compose-check" {
        docker compose config --quiet
    }
}
