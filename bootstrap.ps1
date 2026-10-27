param(
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if ($Reinstall -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".venv"))) {
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot ".venv") -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3.12 -m venv .venv
    }
    else {
        & python -m venv .venv
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Could not create the demo virtual environment."
    }
}

& $venvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Could not upgrade pip in the demo environment."
}
$requirements = Join-Path $PSScriptRoot "requirements.txt"
if (-not (Test-Path -LiteralPath $requirements)) {
    throw "Missing requirements.txt for this demo."
}
& $venvPython -m pip install --quiet -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Could not install demo dependencies from PyPI."
}

& $venvPython -c "import spe, sys; print(f'independent env: Python {sys.version.split()[0]}, spe {spe.__version__} from {spe.__file__}')"
