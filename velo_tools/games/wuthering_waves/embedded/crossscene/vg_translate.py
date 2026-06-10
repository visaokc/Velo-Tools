"""Pure helpers for own-buffer host VG translation (no bpy; unit-testable).

A split own-buffer part (e.g. "Component 5.001") carries its base component's local digit
VG names; the producer's ``host_vg_remap`` table (see ``xscene_merge._build_host_vg_table``)
maps them onto the host scene-IB extract's local VG numbering. The planning logic is pure so
it can be unit-tested without Blender; the orchestrator applies the plan to the bpy object.
"""

TMP_PREFIX = "__xsvg_tmp_"


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
