# Velo Tools

A Blender add-on that hosts mod-making workflows for several games on top of the
GIMI-ecosystem tools, with shared, game-agnostic helpers. Currently supported games:

- **Arknights: Endfield** — a vendored fork of EFMI-Tools, plus Velo extensions:
  Cross Index Buffer (CrossIB) and ShapeKey export.
- **Wuthering Waves** — a vendored fork of WWMI-Tools (including the
  COLOR1 → TEXCOORD1 fix), plus a cross-scene multi-IB merge workflow.

Shared tools: vertex-group name matching, mesh tools, and weight tools.

Requires Blender 3.6+ (developed and tested on 4.4).

## Install

1. Download the latest `velo_tools-<version>.zip` from the
   [Releases](https://github.com/visaokc/Velo-Tools/releases) page.
2. In Blender: Edit → Preferences → Add-ons → Install from Disk… → choose the zip.
3. Enable **Velo-Tools**.
4. Press `N` in the 3D Viewport and open the **Velo Tools** tab.

## Updating

Velo Tools updates itself from its GitHub Releases. Open Edit → Preferences →
Add-ons → **Velo-Tools** and use the update panel (检查更新 / *Check for update*):
it offers to download and install the latest stable release, then asks you to
restart Blender. Tick **接收预发布版本** to also receive pre-releases.

## Build from source

The add-on package lives in `velo_tools/`. To produce an installable zip:

```powershell
./pack.ps1                 # package the committed HEAD tree -> dist/velo_tools-<version>.zip
./pack.ps1 -Ref v1.3.0     # package a specific tag/commit
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

GPL-3.0-or-later — see [LICENSE](LICENSE). Velo Tools bundles forks of EFMI-Tools and
WWMI-Tools together with other GPL/BSD components; see [NOTICE](NOTICE) for full
attribution.
