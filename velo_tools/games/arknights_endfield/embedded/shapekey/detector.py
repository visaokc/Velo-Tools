"""Deform shape key name detection and validation.

Naming contract:
    Deform<num><name>      e.g. 'Deform1abc', 'Deform 2 ccb', 'deform  3  X '
Regex (case-insensitive):
    ^Deform\\s*(\\d+)\\s*(.+?)\\s*$

Rules:
- 'Basis' and any name not matching the regex are silently skipped.
- Two valid Deform shape keys whose extracted *name* part is identical but
  with different *slot* numbers are reported as a hard conflict and abort
  the export.
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


def validate_no_name_conflicts(deform_keys):
    """Return list of (name, [slots...]) for names assigned to >1 slot.
    Empty list = OK.
    """
    name_to_slots = {}
    for d in deform_keys:
        name_to_slots.setdefault(d["name"], set()).add(d["slot"])
    return [(n, sorted(slots)) for n, slots in name_to_slots.items() if len(slots) > 1]


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
    name. Used to catch the case where the user typed e.g.
        Component0 -> Deform 2 Key1123
        Component4 -> Deform 2 Key2
    Both share slot 2 so the generator emits two `$Shape_*` variables for
    the same IniParams channel, only one of which actually drives the
    blend. Block the export and ask the user to rename them consistently.
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
    lines = ["形态键命名冲突，无法导出 Mod。同一个 Deform 编号下出现了不同的名字："]
    for slot, names in disagreements:
        names_str = ", ".join(f"'{n}'" for n in names)
        lines.append(f"  - Deform {slot} 同时出现名字: {names_str}")
    lines.append("修复方法：把同一个 Deform 编号下的所有形态键改成相同的名字。")
    return "\n".join(lines)


def format_conflict_message(conflicts):
    """Human-readable Chinese error message for the UI / exception."""
    lines = ["形态键命名冲突，无法导出 Mod。请检查以下重名："]
    for name, slots in conflicts:
        slot_str = ", ".join(f"Deform {s} {name}" for s in slots)
        lines.append(f"  - 名字 '{name}' 同时被槽位 {slots} 占用 → {slot_str}")
    lines.append("修复方法：合并到同一槽位，或改名其中之一。")
    return "\n".join(lines)


def format_duplicate_message(duplicates):
    """Human-readable error for duplicate (slot, name) keys."""
    lines = ["形态键重复，无法导出 Mod。同一个槽位 + 名字出现了多个形态键块："]
    for slot, name, raws in duplicates:
        raw_str = ", ".join(f"'{r}'" for r in raws)
        lines.append(f"  - Deform {slot} '{name}' → 实际 shape key 名字: {raw_str}")
    lines.append("修复方法：删除多余的，或改名其中之一。")
    return "\n".join(lines)
