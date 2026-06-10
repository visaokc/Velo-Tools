# Dev install via directory junction (no zip, no admin).
#
# Links the real Blender 4.4 user addons dir entry "velo_tools" to this repo's
# velo_tools/ folder, so editing source + reloading scripts takes effect immediately.
# This replaces the old "build_zip -> verify_install -> install zip" dev loop.
#
# Usage:
#   ./tools/dev_link.ps1            # create/refresh the junction
#   ./tools/dev_link.ps1 -Remove    # remove the junction
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$toolsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $toolsDir
$pkg = Join-Path $repoRoot 'velo_tools'
$addons = Join-Path $env:APPDATA 'Blender Foundation\Blender\4.4\scripts\addons'
$link = Join-Path $addons 'velo_tools'

function Remove-Existing($path) {
    if (-not (Test-Path $path)) { return }
    $item = Get-Item $path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        # Junction/symlink: delete the link only, never recurse into the target.
        [System.IO.Directory]::Delete($path, $false)
    } else {
        # A real folder from a previous zip install: remove it entirely.
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

if ($Remove) {
    Remove-Existing $link
    Write-Host "Removed dev junction: $link"
    return
}

if (-not (Test-Path $pkg)) { throw "package dir not found: $pkg" }
if (-not (Test-Path $addons)) { New-Item -ItemType Directory -Path $addons -Force | Out-Null }

Remove-Existing $link
New-Item -ItemType Junction -Path $link -Target $pkg | Out-Null
# Pre-create the host updater's runtime state dir on the target side: on some
# systems creating a directory THROUGH a junction fails with ERROR_ALREADY_EXISTS,
# which would abort velo_tools registration (updater's makedirs at startup).
$updaterState = Join-Path $pkg 'velo_tools_updater'
if (-not (Test-Path $updaterState)) { New-Item -ItemType Directory -Path $updaterState | Out-Null }
Write-Host "Linked $link -> $pkg"
Write-Host "Enable 'Velo-Tools' in Blender 4.4 (Edit > Preferences > Add-ons), then Reload Scripts after edits."
