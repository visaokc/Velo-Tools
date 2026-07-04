# Velo Tools

A Blender add-on that hosts mod-making workflows for several games on top of the
GIMI-ecosystem tools, with shared, game-agnostic helpers. Currently supported games:

- **Arknights: Endfield** - a vendored fork of EFMI-Tools, plus Velo extensions:
  Cross Index Buffer (CrossIB) and ShapeKey export.
- **Wuthering Waves** - a vendored fork of WWMI-Tools (including the
  COLOR1 -> TEXCOORD1 fix), plus cross-scene multi-IB, LOD, slot-style texture
  export and Raw Mesh workflows.

Shared tools include vertex-group name matching, mesh/material helpers,
shape-key aggregation and Weight Tools.

Requires Blender 3.6+ (developed and tested on 4.4).

## Manuals

- [English user manual](docs/user-manual.en.md)
- [Chinese user manual](docs/user-manual.zh-CN.md)

Quick guide:

| Goal | Start here |
| --- | --- |
| Install or update Velo Tools | Blender Preferences -> Add-ons -> Velo-Tools |
| Choose EFMI or WWMI | 3D Viewport -> `N` -> Velo Tools -> Game |
| Arknights: Endfield mod workflow | EFMI panels, CrossIB and ShapeKey tools |
| Wuthering Waves character workflow | WWMI Import Object and Export Mod |
| WWMI cross-scene or LOD workflow | Cross-scene fold merge and LOD Data Extraction |
| WWMI texture-streaming compatibility | Export Mod -> Velo compatibility options -> slot-style texture export |
| Non-character WWMI geometry | WWMI Raw Mesh |
| Weight transfer / mirror / repair | Velo Weight Tools |

## Install

1. Download the latest `velo_tools-<version>.zip` from the
   [Releases](https://github.com/visaokc/Velo-Tools/releases) page.
2. In Blender: Edit -> Preferences -> Add-ons -> Install from Disk... -> choose the zip.
3. Enable **Velo-Tools**.
4. Press `N` in the 3D Viewport and open the **Velo Tools** tab.

## Updating

Velo Tools updates itself from its GitHub Releases. Open Edit -> Preferences ->
Add-ons -> **Velo-Tools** and use the update panel (*Check for update*): it offers
to download and install the latest stable release, then asks you to restart
Blender. Enable pre-release updates only if you intentionally want pre-release
builds.

## Build from source

The add-on package lives in `velo_tools/`. To produce an installable zip:

```powershell
./pack.ps1                 # package the committed HEAD tree -> dist/velo_tools-<version>.zip
./pack.ps1 -Ref v1.4.0     # package a specific tag/commit
```

For local development, link the source straight into Blender instead of repacking:

```powershell
./tools/dev_link.ps1       # junction this repo's velo_tools/ into the Blender 4.4 add-ons dir
```

Then edit the source and use **Reload Scripts** (or restart) in Blender.

## Versioning

Semantic versioning (`MAJOR.MINOR.PATCH`), bumped by hand at release time based on the
nature of the change. During development the in-panel version shows a `-dev` marker;
releases are tagged `vX.Y.Z` and published on GitHub Releases.

## License

GPL-3.0-or-later - see [LICENSE](LICENSE). Velo Tools bundles forks of EFMI-Tools and
WWMI-Tools together with other GPL/BSD components; see [NOTICE](NOTICE) for full
attribution.
