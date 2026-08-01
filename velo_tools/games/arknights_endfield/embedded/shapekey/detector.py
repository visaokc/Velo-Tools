"""Deform shape key name detection and validation.

Naming contract:
    Deform<num><name>      e.g. 'Deform1abc', 'Deform 2 ccb', 'deform  3  X '
Regex (case-insensitive):
    ^Deform\\s*(\\d+)\\s*(.+?)\\s*$

Rules:
- 'Basis' and any name not matching the regex are silently skipped.
- Names are descriptive only; the numeric Deform ID is the runtime identity.
- One object cannot contain multiple data payloads for the same Deform ID.
"""
import re

DEFORM_RE = re.compile(r'^Deform\s*(\d+)\s*(.+?)\s*$', re.IGNORECASE)


def parse_deform_name(raw_name):
    """Return (slot:int, name:str) or None if the key is not a Deform key."""
    m = DEFORM_RE.match(raw_name or "")
    if not m:
        return None
    slot = int(m.group(1))
    name = m.group(2).strip()
    if not name:
        return None
    return slot, name


def collect_deform_keys(blender_object):
    """Walk an object's shape_keys.key_blocks and return a list of dicts:
        [{"slot": int, "name": str, "key_block": ShapeKey, "raw": str}, ...]
    Sorted by slot ascending. Skips Basis and unmatched keys.
    """
    result = []
    if not blender_object or not getattr(blender_object, "data", None):
        return result
    sk_data = blender_object.data.shape_keys
    if not sk_data:
        return result
    for kb in sk_data.key_blocks:
        if kb.name == "Basis":
            continue
        parsed = parse_deform_name(kb.name)
        if parsed is None:
            continue
        slot, name = parsed
        result.append({"slot": slot, "name": name, "key_block": kb, "raw": kb.name})
    result.sort(key=lambda d: d["slot"])
    return result


def validate_no_duplicate_keys(deform_keys):
    """Return list of (slot, name, [raw_names...]) for the same (slot, name)
    appearing on more than one shape-key block. Empty list = OK.

    Example offender pair on the same object:
        'Deform 1 Key1'   ->  (1, 'Key1')
        'Deform1Key1'     ->  (1, 'Key1')
    Both parse to the same slot+name, baker can't tell them apart, and the
    user almost certainly intended only one. Block the export.
    """
    bucket = {}
    for d in deform_keys:
        key = (int(d["slot"]), d["name"])
        bucket.setdefault(key, []).append(d.get("raw") or d["name"])
    out = []
    for (slot, name), raws in bucket.items():
        if len(raws) > 1:
            out.append((slot, name, raws))
    return out


def validate_no_slot_name_disagreement(deform_keys):
    """Return list of (slot, [names...]) for slots that map to >1 distinct
    name within one object. Callers apply this per object so one numeric ID
    cannot address multiple delta payloads in the same component.
    """
    slot_to_names = {}
    for d in deform_keys:
        slot_to_names.setdefault(int(d["slot"]), set()).add(d["name"])
    out = []
    for slot, names in slot_to_names.items():
        if len(names) > 1:
            out.append((slot, sorted(names)))
    return sorted(out, key=lambda t: t[0])


def format_slot_name_disagreement_message(disagreements):
    """Human-readable error for same-slot-different-name conflicts."""
    lines = ["形态键编号重复，无法导出 Mod。同一个物体内的 Deform 编号对应了多个形态键："]
    for slot, names in disagreements:
        names_str = ", ".join(f"'{n}'" for n in names)
        lines.append(f"  - Deform {slot} 同时出现名字: {names_str}")
    lines.append("修复方法：为这些形态键分配不同的 Deform 编号。")
    return "\n".join(lines)


def format_duplicate_message(duplicates):
    """Human-readable error for duplicate (slot, name) keys."""
    lines = ["形态键重复，无法导出 Mod。同一个槽位 + 名字出现了多个形态键块："]
    for slot, name, raws in duplicates:
        raw_str = ", ".join(f"'{r}'" for r in raws)
        lines.append(f"  - Deform {slot} '{name}' → 实际 shape key 名字: {raw_str}")
    lines.append("修复方法：删除多余的，或改名其中之一。")
    return "\n".join(lines)
