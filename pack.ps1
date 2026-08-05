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
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkgDir = Join-Path $root 'velo_tools'
$initFile = Join-Path $pkgDir '__init__.py'
$verFile = Join-Path $pkgDir '_version.py'

function Get-DependencyLockFromPackage {
    param([string]$ZipPath)

    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entry = $archive.GetEntry('velo_tools/weights/_native_dependencies.json')
        if ($null -eq $entry) {
            $entry = $archive.GetEntry('velo_tools\weights\_native_dependencies.json')
        }
        if ($null -eq $entry) { return $null }
        $stream = $entry.Open()
        $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
        try {
            return ($reader.ReadToEnd() | ConvertFrom-Json)
        } finally {
            $reader.Dispose()
            $stream.Dispose()
        }
    } finally {
        $archive.Dispose()
    }
}

function Get-VerifiedWheel {
    param(
        [object]$Dependency,
        [string]$CacheDir
    )

    if (-not (Test-Path -LiteralPath $CacheDir)) {
        New-Item -ItemType Directory -Path $CacheDir | Out-Null
    }
    $wheelPath = Join-Path $CacheDir ([string]$Dependency.filename)
    $expectedHash = ([string]$Dependency.sha256).ToLowerInvariant()
    if (Test-Path -LiteralPath $wheelPath) {
        $actualHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -eq $expectedHash) { return $wheelPath }
        Remove-Item -LiteralPath $wheelPath -Force
    }

    $downloadPath = "$wheelPath.download-$PID"
    try {
        Invoke-WebRequest -Uri ([string]$Dependency.url) -OutFile $downloadPath -UseBasicParsing
        $actualHash = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $expectedHash) {
            throw "SHA256 mismatch for $($Dependency.filename): expected $expectedHash, got $actualHash"
        }
        Move-Item -LiteralPath $downloadPath -Destination $wheelPath -Force
    } finally {
        if (Test-Path -LiteralPath $downloadPath) {
            Remove-Item -LiteralPath $downloadPath -Force
        }
    }
    return $wheelPath
}

function Add-NativeDependencies {
    param([string]$ZipPath)

    $lock = Get-DependencyLockFromPackage -ZipPath $ZipPath
    if ($null -eq $lock) { return }
    if ([int]$lock.schema_version -ne 1) {
        throw "unsupported native dependency lock schema: $($lock.schema_version)"
    }
    if ($env:OS -ne 'Windows_NT') {
        throw "the current native dependency bundle supports Windows x64 only"
    }

    $platformKey = 'Python311-win_amd64'
    $platformProperty = $lock.platforms.PSObject.Properties[$platformKey]
    if ($null -eq $platformProperty) {
        throw "native dependency lock is missing platform $platformKey"
    }
    $dependencies = @($platformProperty.Value)
    $cacheBase = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Join-Path ([System.IO.Path]::GetTempPath()) 'velo-tools-wheel-cache'
    } else {
        Join-Path $env:LOCALAPPDATA 'VeloTools\wheel-cache'
    }
    $wheels = @()
    foreach ($dependency in $dependencies) {
        $wheels += Get-VerifiedWheel -Dependency $dependency -CacheDir $cacheBase
    }

    $archive = [System.IO.Compression.ZipFile]::Open(
        $ZipPath,
        [System.IO.Compression.ZipArchiveMode]::Update
    )
    try {
        foreach ($wheelPath in $wheels) {
            $wheel = [System.IO.Compression.ZipFile]::OpenRead($wheelPath)
            try {
                foreach ($entry in $wheel.Entries) {
                    if ([string]::IsNullOrEmpty($entry.Name)) { continue }
                    $destination = 'velo_tools/weights/_native_deps/Python311/site-packages/' + $entry.FullName
                    if ($null -ne $archive.GetEntry($destination)) {
                        throw "duplicate native dependency entry: $destination"
                    }
                    $outputEntry = $archive.CreateEntry(
                        $destination,
                        [System.IO.Compression.CompressionLevel]::Optimal
                    )
                    $outputEntry.LastWriteTime = $entry.LastWriteTime
                    $inputStream = $entry.Open()
                    $outputStream = $outputEntry.Open()
                    try {
                        $inputStream.CopyTo($outputStream)
                    } finally {
                        $outputStream.Dispose()
                        $inputStream.Dispose()
                    }
                }
            } finally {
                $wheel.Dispose()
            }
        }
    } finally {
        $archive.Dispose()
    }
    Write-Host "Bundled native Weight Tools dependencies: $($dependencies.project -join ', ')"
}

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
