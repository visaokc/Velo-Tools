# Velo Tools

A Blender add-on that hosts mod-making workflows for several games on top of the
GIMI-ecosystem tools, with shared, game-agnostic helpers. Currently supported games:

- **Arknights: Endfield** - a vendored fork of EFMI-Tools, plus Velo extensions:
  Cross Index Buffer (CrossIB), ShapeKey and slot-style texture export.
- **Wuthering Waves** - a vendored fork of WWMI-Tools (including the
  COLOR1 -> TEXCOORD1 fix), plus cross-scene multi-IB, LOD, slot-style texture
  export and Raw Mesh workflows.

Shared tools include vertex-group name matching, mesh/material helpers,
shape-key aggregation and Weight Tools.

Requires Blender 3.6+ (developed and tested on 4.4).

## Tutorial handbook

**[Download the interactive bilingual handbook](https://github.com/visaokc/Velo-Tools/releases/latest/download/velo_tools-manual.html)**
and open the HTML file in your browser. No server, account or internet connection
is needed after downloading. GitHub's file view displays HTML source rather than
running the reader.

Five tabs follow the add-on: **Vertex Group Tools · Mesh Tools · Weight Tools ·
Material Tools · Game**. Each tab has a grouped directory and short descriptions;
each tutorial explains its purpose, steps, a small example and what to check.
Search covers all tutorials. Language switching keeps the same tutorial open,
links can be bookmarked, and Print / PDF prints the current tutorial or tab.

Prefer reading directly on GitHub?

- [English tutorial handbook](docs/user-manual.en.md)
- [Chinese tutorial handbook](docs/user-manual.zh-CN.md)

### Keeping tutorials current

The two Markdown files are the canonical content. For every new or changed
user-facing feature:

1. Put its tutorial under the matching **existing tab and panel group**, next to
   related controls. Do not append new features to a chronological catch-all.
2. Add the same stable English anchor ID in both languages. Keep existing IDs
   when improving titles, so bookmarks continue to work.
3. Explain the actual scope, prerequisites and UI location, numbered steps, a
   concrete small example, expected results and common recovery paths. A feature
   name alone is not a tutorial. Verify behavior against the current source.
4. Run the dependency-free builder and its check:

   ~~~powershell
   python tools/build_manual.py
   python tools/build_manual.py --check
   ~~~

5. Review desktop/mobile layout, keyboard tab navigation, search, language
   switching, direct links and printing in a real browser. Commit both Markdown
   files, reader sources and the regenerated HTML in the same change.

The builder regenerates the Markdown directories and the self-contained
**docs/manual.html** from **docs/manual.css**, **docs/manual.js** and both
manuals. Do not hand-edit generated HTML. Attach that file to each release as
**velo_tools-manual.html**, alongside the installable add-on zip.

## UI language

Velo Tools follows Blender's interface language automatically. Simplified and
Traditional Chinese use the Simplified Chinese UI catalog; every other Blender
language uses the canonical English UI. This covers labels, tooltips, operator
messages, and updater controls.

Quick guide:

| Goal | Start here |
| --- | --- |
| Install or update Velo Tools | Blender Preferences -> Add-ons -> Velo-Tools |
| Choose EFMI or WWMI | 3D Viewport -> `N` -> Velo Tools -> Game |
| Arknights: Endfield mod workflow | EFMI panels, CrossIB and ShapeKey tools |
| EFMI texture-streaming compatibility | Export Mod -> Velo compatibility options -> slot-style texture export |
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
