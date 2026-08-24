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
        "compose-check",
        "dashboard-check"
    )]
    [string]$Task = "test"
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$VenvRuff = Join-Path $Root ".venv\Scripts\ruff.exe"
$VenvMypy = Join-Path $Root ".venv\Scripts\mypy.exe"
$BundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$NodeExe = if (Get-Command node -ErrorAction SilentlyContinue) {
    "node"
}
elseif (Test-Path $BundledNode) {
    $BundledNode
}
else {
    "node"
}

function Ensure-Venv {
    if (-not (Test-Path $VenvPython)) {
        python -m venv (Join-Path $Root ".venv")
    }
}

function Run-Python {
    param([string[]]$PyArgs)
    & $VenvPython @PyArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Run-Checked {
    param(
        [string]$Command,
        [string[]]$CommandArgs
    )
    & $Command @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
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
        Run-Checked $VenvRuff @("check", ".")
        Run-Checked $VenvRuff @("format", "--check", ".")
    }
    "format" {
        Run-Checked $VenvRuff @("format", ".")
    }
    "typecheck" {
        Run-Checked $VenvMypy @("libs", "services")
    }
    "compile-check" {
        Run-Python @("-m", "compileall", "-q", "libs", "services", "scripts", "tests")
    }
    "compose-check" {
        docker compose config --quiet
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    "dashboard-check" {
        Push-Location (Join-Path $Root "services\dashboard")
        try {
            & $NodeExe scripts\verify-js.mjs
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
            & $NodeExe scripts\verify-shell.mjs
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        }
        finally {
            Pop-Location
        }
    }
}
