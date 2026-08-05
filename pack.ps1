# Package velo_tools/ into an installable Blender addon zip under dist/.
#
# This is a PURE packager: it does NOT bump the version, does NOT write back to
# __init__.py, and does NOT archive into _archive/. Versioning is manual (semver,
# decided at release time); GitHub Releases is the version store. See CLAUDE.md.
#
# Usage:
#   ./pack.ps1                 # package the committed HEAD tree
#   ./pack.ps1 -Ref v1.2.7     # package a specific tag/commit
#
# Preferred path uses `git archive` for the tracked source at the given ref.
# Falls back to Compress-Archive of the working tree when git/ref is unavailable.
param(
    [string]$Ref = "HEAD"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkgDir = Join-Path $root 'velo_tools'
$initFile = Join-Path $pkgDir '__init__.py'
$verFile = Join-Path $pkgDir '_version.py'

if (-not (Test-Path $pkgDir)) { throw "package dir not found: $pkgDir" }
if (-not (Test-Path $initFile)) { throw "__init__.py not found: $initFile" }

# Release tuple (literal in bl_info).
$content = [System.IO.File]::ReadAllText($initFile, [System.Text.Encoding]::UTF8)
$m = [regex]::Match($content, '"version"\s*:\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)')
if (-not $m.Success) { throw "cannot parse version from __init__.py" }
$version = '{0}.{1}.{2}' -f $m.Groups[1].Value, $m.Groups[2].Value, $m.Groups[3].Value

# Pre-release marker from _version.py (PRERELEASE = None on a release).
if (Test-Path $verFile) {
    $vtext = [System.IO.File]::ReadAllText($verFile, [System.Text.Encoding]::UTF8)
    $pm = [regex]::Match($vtext, "PRERELEASE\s*=\s*(?:None|`"([^`"]*)`"|'([^']*)')")
    if ($pm.Success) {
        $pre = if ($pm.Groups[1].Success) { $pm.Groups[1].Value } elseif ($pm.Groups[2].Success) { $pm.Groups[2].Value } else { '' }
        if (-not [string]::IsNullOrWhiteSpace($pre)) { $version = "$version-$pre" }
    }
}

$distDir = Join-Path $root 'dist'
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }
$outZip = Join-Path $distDir "velo_tools-$version.zip"
if (Test-Path $outZip) { Remove-Item -LiteralPath $outZip -Force }

# Prefer git archive (zip == tracked source at $Ref, gitignored files excluded).
$useGit = $false
Push-Location $root
try {
    & git rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -eq 0) {
        & git rev-parse --verify --quiet "${Ref}:velo_tools" *> $null
        if ($LASTEXITCODE -eq 0) { $useGit = $true }
    }
} finally { Pop-Location }

if ($useGit) {
    Push-Location $root
    try {
        & git archive --format=zip --prefix=velo_tools/ -o $outZip "${Ref}:velo_tools"
        if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
    } finally { Pop-Location }
    Write-Host "Packed via git archive ($Ref): $outZip"
} else {
    # Fallback: zip the working tree, stripping __pycache__ first.
    Get-ChildItem -Path $pkgDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force
    Push-Location $root
    try {
        Compress-Archive -Path 'velo_tools' -DestinationPath $outZip
    } finally { Pop-Location }
    Write-Host "Packed via Compress-Archive (working tree): $outZip"
}

Write-Host "Version: $version"
Write-Host "OK: $outZip"
