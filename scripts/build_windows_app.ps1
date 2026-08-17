param(
    [string]$Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

if ([string]::IsNullOrWhiteSpace($Python)) {
    if ($env:PYTHON) {
        $Python = $env:PYTHON
    } elseif (Test-Path -LiteralPath (Join-Path $Root ".venv\Scripts\python.exe")) {
        $Python = Join-Path $Root ".venv\Scripts\python.exe"
    } else {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name MemoryForge `
    --paths src `
    --add-data "$Root\src\memoryforge\portal\vendor;memoryforge\portal\vendor" `
    --specpath build `
    --exclude-module mypy `
    --exclude-module pytest `
    --exclude-module ruff `
    --collect-all webview `
    --collect-all tree_sitter `
    --collect-all tree_sitter_go `
    --collect-all tree_sitter_python `
    --collect-all tree_sitter_typescript `
    scripts/windows_desktop_app.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Artifact = Join-Path $Root "dist\MemoryForge.exe"
if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
    throw "PyInstaller did not create $Artifact"
}
Write-Host "Built: $Artifact"
