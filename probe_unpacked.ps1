param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$RomRoot,

    [Parameter(Mandatory=$false)]
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Probe = Join-Path $PSScriptRoot "scripts\probe_unpacked.py"
if (-not (Test-Path $Probe)) {
    throw "Missing probe script: $Probe"
}

$ArgsList = @($Probe, $RomRoot)
if ($OutDir) {
    $ArgsList += @("--out-dir", $OutDir)
}

if (Get-Command python3 -ErrorAction SilentlyContinue) {
    & python3 @ArgsList
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @ArgsList
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @ArgsList
} else {
    throw "Python 3 was not found. Install Python 3 or run this probe in GitHub Actions/Linux."
}

exit $LASTEXITCODE
