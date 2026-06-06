"""Mapping text format I/O (PLAN §2.1).

Format example:
    @mod 陈千语v3
    @component 0
    5  20      # native -> unified (comments start at #)
    @component 4
    36 20
    @profile 默认映射
    shoulder.L 20
"""

from __future__ import annotations

from typing import List, Tuple


def parse_mapping_text(text: str) -> dict:
    """Returns:
    {
        "mod_name": str,
        "components": list[(component_id:int, [(native:str, unified:str), ...])],
        "profile_name": str,
        "rows": list[(mmd_name:str, unified_name:str)],
    }
    """
    mod_name = ""
    components = []  # list[(cid, list[(native, unified)])]
    profile_name = "默认映射"
    rows = []  # list[(mmd, unified)]

    cur_mode = 'profile'  # default to profile mode, allowing header-less plain <a> <b> tables to interop
    cur_cid = -1
    cur_pairs = []

    def flush_component():
        nonlocal cur_pairs, cur_cid
        if cur_cid >= 0:
            components.append((cur_cid, list(cur_pairs)))
        cur_pairs = []
        cur_cid = -1

    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('@mod'):
            mod_name = line[len('@mod'):].strip()
            continue
        if line.startswith('@component'):
            flush_component()
            try:
                cur_cid = int(line[len('@component'):].strip())
            except ValueError:
                cur_cid = -1
            cur_mode = 'component'
            continue
        if line.startswith('@profile'):
            flush_component()
            profile_name = line[len('@profile'):].strip() or "默认映射"
            cur_mode = 'profile'
            continue
        # data line
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        a, b = parts[0].strip(), parts[1].strip()
        if cur_mode == 'component':
            cur_pairs.append((a, b))  # native, unified
        elif cur_mode == 'profile':
            rows.append((a, b))  # mmd, unified

    flush_component()

    return {
        "mod_name": mod_name,
        "components": components,
        "profile_name": profile_name,
        "rows": rows,
    }


def serialize_mapping(settings) -> str:
    """Serialize from VELO_EF_Settings to text (V0.1.1 simplified version only exports profile.rows)."""
    lines: List[str] = []
    mod_name = getattr(settings, "mod_name", "") or ""
    if mod_name:
        lines.append(f"@mod {mod_name}")
    component_maps = getattr(settings, "component_maps", None)
    if component_maps:
        for cm in component_maps:
            lines.append(f"@component {cm.component_id}")
            for p in cm.pairs:
                if p.native_name and p.unified_name:
                    lines.append(f"{p.native_name} {p.unified_name}")
    profile = getattr(settings, "mmd_profile", None)
    if profile is not None and len(profile.rows) > 0:
        # no longer write the @profile header, to stay interoperable with the general area's plain <name> <name> format
        for r in profile.rows:
            if r.mmd_name and r.unified_name:
                lines.append(f"{r.mmd_name}\t{r.unified_name}")
    return "\n".join(lines) + "\n"


def load_into_settings(settings, parsed: dict) -> dict:
    """Write the parse_mapping_text result back into VELO_EF_Settings (V0.1.1 simplified version only handles profile)."""
    report = {"components": 0, "pairs": 0, "rows": 0}
    if hasattr(settings, "mod_name") and parsed.get("mod_name"):
        try:
            settings.mod_name = parsed["mod_name"]
        except Exception:
            pass

    component_maps = getattr(settings, "component_maps", None)
    if component_maps is not None:
        try:
            component_maps.clear()
            for cid, pairs in parsed.get("components", []):
                cm = component_maps.add()
                cm.component_id = cid
                cm.source = "manual"
                for native, unified in pairs:
                    p = cm.pairs.add()
                    p.native_name = native
                    p.unified_name = unified
                    report["pairs"] += 1
                report["components"] += 1
        except Exception:
            pass

    profile = getattr(settings, "mmd_profile", None)
    if profile is not None:
        profile.name = parsed.get("profile_name") or "默认映射"
        profile.rows.clear()
        for mmd, unified in parsed.get("rows", []):
            r = profile.rows.add()
            r.mmd_name = mmd
            r.unified_name = unified
            r.enabled = True
            report["rows"] += 1
    return report
