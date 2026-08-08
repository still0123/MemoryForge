param(
    [string]$Output
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

if ($env:PYTHON_BIN) {
    $Python = $env:PYTHON_BIN
} elseif (Test-Path -LiteralPath (Join-Path $Root ".venv\Scripts\python.exe")) {
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
} else {
    $PythonCommand = Get-Command python -ErrorAction Stop
    $Python = $PythonCommand.Source
}

Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$env:PYTHONNOUSERSITE = "1"
$env:SOURCE_DATE_EPOCH = "315532800"

if (-not $Output) {
    $Timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $Output = Join-Path $Root "local-evidence\$Timestamp"
}
$Output = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $Output) {
    throw "output already exists: $Output"
}

$Workdir = Join-Path ([IO.Path]::GetTempPath()) (
    "memoryforge-local-check." + [Guid]::NewGuid().ToString("N")
)
$Dist = Join-Path $Output "dist"
[IO.Directory]::CreateDirectory($Dist) | Out-Null
[IO.Directory]::CreateDirectory($Workdir) | Out-Null

function Invoke-External {
    param(
        [string]$Command,
        [string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "command failed ($LASTEXITCODE): $Command $($Arguments -join ' ')"
    }
}

function Get-SingleArtifact {
    param(
        [string]$Directory,
        [string]$Filter
    )
    $Artifacts = @(Get-ChildItem -LiteralPath $Directory -Filter $Filter -File)
    if ($Artifacts.Count -ne 1) {
        throw "expected one $Filter artifact, found $($Artifacts.Count)"
    }
    return $Artifacts[0].FullName
}

try {
    # Contract: ruff check --no-cache .
    Invoke-External $Python @("-m", "ruff", "check", "--no-cache", ".")
    # Contract: ruff format --check .
    Invoke-External $Python @("-m", "ruff", "format", "--check", ".")
    # Contract: mypy
    Invoke-External $Python @("-m", "mypy")
    # Contract: demo/validate_benchmark_registry.py
    Invoke-External $Python @("demo/validate_benchmark_registry.py")

    & $Python -m pip --version *> $null
    if ($LASTEXITCODE -eq 0) {
        Invoke-External $Python @("-m", "pip", "check")
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        Invoke-External "uv" @("pip", "check", "--python", $Python)
    } else {
        throw "dependency check requires pip or uv"
    }

    # Contract: demo/run_cross_platform_smoke.py
    Invoke-External $Python @(
        "demo/run_cross_platform_smoke.py",
        "--workspace", (Join-Path $Workdir "smoke-workspace"),
        "--output", (Join-Path $Output "platform-smoke.json")
    )

    # Contract: pytest -W error::ResourceWarning
    Invoke-External $Python @(
        "-m", "pytest",
        "-W", "error::ResourceWarning",
        "-W", "error::pytest.PytestUnraisableExceptionWarning",
        "--cov=memoryforge",
        "--cov-report=term-missing"
    )

    $BuildEnvironment = Join-Path $Workdir "build"
    Invoke-External $Python @("-m", "venv", $BuildEnvironment)
    $BuildPython = Join-Path $BuildEnvironment "Scripts\python.exe"
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Invoke-External "uv" @(
            "pip", "install",
            "--python", $BuildPython,
            "-c", (Join-Path $Root "constraints\dev.txt"),
            "build", "hatchling"
        )
    } else {
        Invoke-External $BuildPython @(
            "-m", "pip", "install",
            "-c", (Join-Path $Root "constraints\dev.txt"),
            "build", "hatchling"
        )
    }
    # Contract: --wheel --sdist --no-isolation
    Invoke-External $BuildPython @(
        "-m", "build",
        "--wheel", "--sdist", "--no-isolation",
        "--outdir", $Dist
    )

    $Wheel = Get-SingleArtifact $Dist "memoryforge-*.whl"
    $PreviousConstraint = $env:PIP_CONSTRAINT
    $env:PIP_CONSTRAINT = Join-Path $Root "constraints\dev.txt"
    try {
        # Contract: demo/run_release_check.py
        Invoke-External $Python @(
            "demo/run_release_check.py",
            "--wheel", $Wheel,
            "--workdir", (Join-Path $Workdir "wheel"),
            "--output", (Join-Path $Output "release-provenance.json")
        )
    } finally {
        if ($null -eq $PreviousConstraint) {
            Remove-Item Env:PIP_CONSTRAINT -ErrorAction SilentlyContinue
        } else {
            $env:PIP_CONSTRAINT = $PreviousConstraint
        }
    }

    $SdistEnvironment = Join-Path $Workdir "sdist"
    Invoke-External $Python @("-m", "venv", $SdistEnvironment)
    $SdistPython = Join-Path $SdistEnvironment "Scripts\python.exe"
    Invoke-External $SdistPython @(
        "-m", "pip", "install",
        "-c", (Join-Path $Root "constraints\dev.txt"),
        "hatchling"
    )
    $Sdist = Get-SingleArtifact $Dist "memoryforge-*.tar.gz"
    $SdistCheck = @(
        "import sys,tarfile",
        "names=tarfile.open(sys.argv[1]).getnames()",
        "bad=[n for n in names if '/demo/results/artifacts/' in '/'+n or n.endswith(('.whl','.tar.gz'))]",
        "sys.exit(f'sdist contains retained or nested artifacts: {bad[:3]}') if bad else None"
    ) -join ";"
    Invoke-External $BuildPython @("-c", $SdistCheck, $Sdist)
    # Contract: --no-build-isolation
    Invoke-External $SdistPython @(
        "-m", "pip", "install",
        "-c", (Join-Path $Root "constraints\dev.txt"),
        "--no-build-isolation", $Sdist
    )
    $PreviousPythonPath = $env:PYTHONPATH
    $PreviousNoUserSite = $env:PYTHONNOUSERSITE
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $env:PYTHONNOUSERSITE = "1"
    Push-Location -LiteralPath $Workdir
    try {
        # Contract: pip check
        Invoke-External $SdistPython @("-I", "-m", "pip", "check")
        $ImportPath = & $SdistPython -I -c "import memoryforge; print(memoryforge.__file__)"
        if ($LASTEXITCODE -ne 0) {
            throw "sdist import probe failed"
        }
        $ResolvedImport = (Resolve-Path -LiteralPath $ImportPath.Trim()).Path
        $SdistPrefix = (Resolve-Path -LiteralPath $SdistEnvironment).Path.TrimEnd("\", "/")
        $SdistPrefix += [IO.Path]::DirectorySeparatorChar
        if (-not $ResolvedImport.StartsWith(
            $SdistPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "sdist import escaped clean environment: $ResolvedImport"
        }
        Invoke-External $SdistPython @("-I", "-m", "memoryforge", "--version")
    } finally {
        Pop-Location
        if ($null -eq $PreviousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONPATH = $PreviousPythonPath
        }
        if ($null -eq $PreviousNoUserSite) {
            Remove-Item Env:PYTHONNOUSERSITE -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONNOUSERSITE = $PreviousNoUserSite
        }
    }

    $HashTargets = @(
        Get-ChildItem -LiteralPath $Dist -File | Sort-Object Name
    )
    $HashTargets += Get-Item -LiteralPath (Join-Path $Output "platform-smoke.json")
    $HashTargets += Get-Item -LiteralPath (Join-Path $Output "release-provenance.json")
    $Lines = foreach ($Artifact in $HashTargets) {
        # Contract: Get-FileHash and SHA256SUMS
        $Digest = (
            Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact.FullName
        ).Hash.ToLowerInvariant()
        $Prefix = $Output.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar
        if (-not $Artifact.FullName.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "artifact escaped evidence directory: $($Artifact.FullName)"
        }
        $Relative = $Artifact.FullName.Substring($Prefix.Length).Replace("\", "/")
        "$Digest  $Relative"
    }
    $Sums = Join-Path $Output "SHA256SUMS"
    Set-Content -LiteralPath $Sums -Value $Lines -Encoding ascii

    foreach ($Line in $Lines) {
        $Parts = $Line -split "  ", 2
        $Artifact = Join-Path $Output $Parts[1]
        $Actual = (
            Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact
        ).Hash.ToLowerInvariant()
        if ($Actual -ne $Parts[0]) {
            throw "SHA256 verification failed: $($Parts[1])"
        }
    }

    Write-Output "Local checks passed. Evidence: $Output"
} finally {
    Remove-Item -LiteralPath $Workdir -Recurse -Force -ErrorAction SilentlyContinue
}
