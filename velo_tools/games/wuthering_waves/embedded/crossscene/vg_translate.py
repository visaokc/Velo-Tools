"""Pure helpers for own-buffer host VG translation (no bpy; unit-testable).

A split own-buffer part (e.g. "Component 5.001") carries its base component's local digit
VG names; the producer's ``host_vg_remap`` table (see ``xscene_merge._build_host_vg_table``)
maps them onto the host scene-IB extract's local VG numbering. The planning logic is pure so
it can be unit-tested without Blender; the orchestrator applies the plan to the bpy object.
"""

TMP_PREFIX = "__xsvg_tmp_"


def build_inverse_vg_map(vg_map):
    inverse = {}
    for key, value in (vg_map or {}).items():
        local_id = int(key)
        unified_id = int(value)
        current = inverse.get(unified_id)
        if current is None or local_id < current:
            inverse[unified_id] = local_id
    return inverse


def _weighted_digit_ids(vg_entries):
    out = []
    for name, has_weight in vg_entries:
        if has_weight and str(name).lstrip("-").isdigit():
            out.append((str(name), int(name)))
    return out


def plan_own_buffer_vg_normalization(vg_entries, component_vg_map, host_remap,
                                     host_vg_count=None):
    """Plan the pre-host normalization for an own-buffer split copy.

    Returns ``(renames, skip_host_translation, strays)``. ``renames`` maps
    MERGED unified digit names to base-component-local digit names before the
    existing host translation runs. ``skip_host_translation`` is only used for
    copies that are already host-local while a non-identity host table would
    otherwise misread those names as base-local ids.
    """
    entries = [(str(name), bool(has_weight)) for name, has_weight in vg_entries]
    weighted = _weighted_digit_ids(entries)
    if not weighted:
        return {}, False, []

    _renames, _drops, host_strays = plan_host_vg_translation(
        entries, host_remap, host_vg_count)
    if not host_strays:
        return {}, False, []

    table = {int(k): int(v) for k, v in host_remap.items()} if host_remap else None
    if table is not None and host_vg_count is not None:
        host_ready = all(0 <= vid < int(host_vg_count) for _name, vid in weighted)
        overlaps_base_keys = any(vid in table for _name, vid in weighted)
        if host_ready and not overlaps_base_keys:
            return {}, True, []

    inverse = build_inverse_vg_map(component_vg_map)
    strays = []
    renames = {}
    for name, has_weight in entries:
        if not name.lstrip("-").isdigit():
            continue
        local = inverse.get(int(name))
        if local is None:
            if has_weight:
                strays.append(name)
        else:
            renames[name] = str(local)
    if strays:
        return {}, False, sorted(strays, key=int)

    adjusted = [(renames.get(name, name), has_weight) for name, has_weight in entries]
    _renames, _drops, remaining = plan_host_vg_translation(
        adjusted, host_remap, host_vg_count)
    if remaining:
        return {}, False, sorted(remaining, key=int)
    return renames, False, []


def plan_editable_vg_normalization(vg_entries, merged_component_vg_map,
                                   source_component_vg_map,
                                   target_scope="component"):
    """Plan editable-IB VG renames from merged-root IDs to export-local IDs."""
    entries = [(str(name), bool(has_weight)) for name, has_weight in vg_entries]
    weighted = _weighted_digit_ids(entries)
    if not weighted:
        return {}, []

    merged_to_local = build_inverse_vg_map(merged_component_vg_map)
    source_by_local = {
        int(key): int(value)
        for key, value in (source_component_vg_map or {}).items()
    }
    source_global_to_local = {
        int(value): int(key)
        for key, value in (source_component_vg_map or {}).items()
    }
    if str(target_scope).upper() == "MERGED":
        target_by_local = dict(source_by_local)
    else:
        target_by_local = {local: local for local in source_by_local}
    target_values = set(target_by_local.values())
    renames = {}
    strays = []
    for name, has_weight in entries:
        if not name.lstrip("-").isdigit():
            continue
        vid = int(name)
        if vid in target_values:
            continue
        local = merged_to_local.get(vid)
        if local is None and vid in source_global_to_local:
            local = source_global_to_local[vid]
        if local is not None and local in target_by_local:
            renames[name] = str(target_by_local[local])
        elif has_weight:
            strays.append(name)
    return renames, sorted(strays, key=int)


def plan_host_vg_translation(vg_entries, remap, host_vg_count=None):
    """Plan the split copy's VG renames toward host-local numbering.

    ``vg_entries``: iterable of ``(name, has_weight)`` for the object's vertex groups.
    ``remap``: {base_local_id: host_local_id} (keys/values may be str or int), or None =
    identity (split numbering already equals host numbering).
    ``host_vg_count``: host component VG total, used as the bounds check under identity.

    Returns ``(renames, drops, strays)``:
      * ``renames`` -- {old_name: host_digit_name} for digit VGs found in the table;
      * ``drops``   -- weightless digit VG names outside the table (safe to remove so the
        exporter never sees out-of-range digit names);
      * ``strays``  -- weighted digit VG names outside the table / host range; the caller
        must hard-error (weights must stay on the host's existing bones).
    Non-digit names are left untouched (not renamed, not dropped, not flagged)."""
    renames, drops, strays = {}, [], []
    table = {int(k): int(v) for k, v in remap.items()} if remap else None
    for name, has_weight in vg_entries:
        if not name.lstrip("-").isdigit():
            continue
        vid = int(name)
        if table is None:
            if has_weight and host_vg_count is not None and not (0 <= vid < host_vg_count):
                strays.append(name)
            continue
        host = table.get(vid)
        if host is None:
            (strays if has_weight else drops).append(name)
        else:
            renames[name] = str(host)
    return renames, sorted(drops, key=int), sorted(strays, key=int)
