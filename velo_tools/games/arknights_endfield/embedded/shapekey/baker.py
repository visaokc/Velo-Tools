"""Bake per-Deform-slot position deltas + freq_indices (v0.2 / TheHerta3).

Output files (all written under the mod's Meshes/)
-----------------------------------
Component<cid>_Position_S<slot>_delta.buf      stride=12, packed float3 deltas
Component<cid>_Position_S<slot>_map.buf        stride=4,  int32 map (-1 means the vertex has no delta)
Component<cid>_Position_freq_indices.buf       stride=4,  vcount*MAX_SLOTS uint32

Note: `Component<cid>_Position_0.buf` is not generated here -- we let the ini
directly reuse the `Component<cid>_VB0.buf` already written by EFMI as the
pristine copy (same bytes, no need to copy again).

VB0 vertex-count alignment
--------------
EFMI converts each loop corner into a VB0 record and then dedups, so
`len(VB0) >= len(mesh.vertices)`. The real export must use the VertexId mapping
retained during the EFMI export stage to strictly map VB0 rows back to Blender
mesh vertices; the POSITION0 coordinate reverse-lookup is kept only as a legacy
diagnostic and is no longer used for shape-key bake output.
"""
import os

try:
    import numpy as np
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False


# Merged-mode HLSL has 128 real slots.
# Compatibility rule:
# 1) Preserve legacy mapping for Deform 1..MAX_SLOTS -> shader_slot = Deform-1.
# 2) Remap only out-of-range Deform numbers into remaining free slots.
# This avoids reshuffling existing low-slot channels that many mods already use.
# 3DMigoto IniParams total capacity is 256 (x0..x255); x100..x227 are used.
MAX_SLOTS = 128
SPLIT_MAX_SLOTS = 24
NO_FREQ_INDEX = 255

_QUANT_SCALE = 1e5  # position-match quantization: 1e-5 ≈ 10 µm


# ---------------------------------------------------------------- helpers

def _read_coords(key_block, vertex_count):
    if NUMPY_OK:
        flat = np.empty(vertex_count * 3, dtype=np.float32)
        key_block.data.foreach_get("co", flat)
        return flat.reshape((vertex_count, 3))
    return [tuple(key_block.data[i].co) for i in range(vertex_count)]


def _extract_vb0_positions(vb0_buffer):
    """Get VB0's POSITION0 attribute from the EFMI NumpyBuffer; return (M,3) float32 or None."""
    if vb0_buffer is None or not NUMPY_OK:
        return None
    layout = getattr(vb0_buffer, "layout", None)
    if layout is None:
        return None
    target_name = None
    for sem in layout.semantics:
        abs_sem = getattr(sem, "abstract", None)
        if abs_sem is None:
            continue
        enum = getattr(abs_sem, "enum", None)
        idx = getattr(abs_sem, "index", -1)
        enum_value = getattr(enum, "value", str(enum))
        if str(enum_value).upper() == "POSITION" and idx == 0:
            target_name = sem.get_name()
            break
    if target_name is None:
        return None
    try:
        field = vb0_buffer.data[target_name]
    except Exception:
        return None
    arr = np.asarray(field, dtype=np.float32)
    if arr.ndim == 1:
        try:
            arr = arr.reshape((-1, 3))
        except ValueError:
            return None
    if arr.shape[1] >= 3:
        return arr[:, :3].copy()
    return None


def _build_vb0_to_mesh_map(vb0_pos, mesh_basis):
    """Legacy diagnostic only: POSITION0 matching is ambiguous for split verts."""
    n = mesh_basis.shape[0]
    m = vb0_pos.shape[0]
    base_q = np.round(mesh_basis * _QUANT_SCALE).astype(np.int64)
    vb0_q = np.round(vb0_pos * _QUANT_SCALE).astype(np.int64)

    pos_to_mesh = {}
    for i in range(n):
        key = (int(base_q[i, 0]), int(base_q[i, 1]), int(base_q[i, 2]))
        if key not in pos_to_mesh:
            pos_to_mesh[key] = i

    out = np.full(m, -1, dtype=np.int32)
    miss = 0
    for k in range(m):
        key = (int(vb0_q[k, 0]), int(vb0_q[k, 1]), int(vb0_q[k, 2]))
        idx = pos_to_mesh.get(key)
        if idx is None:
            miss += 1
        else:
            out[k] = idx
    return out, miss


def _get_vb0_vertex_count(vb0_buffer, component_id):
    data = getattr(vb0_buffer, "data", None) if vb0_buffer is not None else None
    if data is None:
        raise RuntimeError(
            f"ShapeKey export requires a valid VB0 buffer for component {component_id} "
            "to validate the VB0->mesh VertexId mapping."
        )
    return len(data)


def _validate_vb0_vertex_ids(vertex_ids, m_count, mesh_vertex_count, component_id=None):
    """Return a strict int32 VB0 row -> Blender mesh vertex map."""
    prefix = f"component {component_id}: " if component_id is not None else ""
    if vertex_ids is None:
        raise RuntimeError(
            f"ShapeKey export missing/invalid VB0->mesh VertexId mapping for {prefix}"
            "DataModelEFMI did not provide last_export_vertex_ids. Refusing to bake "
            "shape keys because POSITION0 fallback is ambiguous for duplicate Basis coordinates."
        )
    try:
        arr = np.asarray(vertex_ids)
    except Exception as exc:
        raise RuntimeError(
            f"ShapeKey export missing/invalid VB0->mesh VertexId mapping for {prefix}"
            f"could not convert mapping to an array: {exc}"
        ) from exc
    if arr.ndim == 0:
        raise RuntimeError(
            f"ShapeKey export missing/invalid VB0->mesh VertexId mapping for {prefix}"
            "mapping is scalar, expected one value per VB0 row."
        )
    arr = arr.reshape(-1)
    if len(arr) != int(m_count):
        raise RuntimeError(
            f"ShapeKey export missing/invalid VB0->mesh VertexId mapping for {prefix}"
            f"length {len(arr)} does not match VB0 vertex count {int(m_count)}."
        )
    if arr.size:
        if not np.issubdtype(arr.dtype, np.integer):
            rounded = np.rint(arr)
            if not np.allclose(arr, rounded, atol=0.0):
                raise RuntimeError(
                    f"ShapeKey export missing/invalid VB0->mesh VertexId mapping for {prefix}"
                    "mapping contains non-integer values."
                )
            arr = rounded
        arr64 = arr.astype(np.int64, copy=False)
        bad_negative = arr64 < 0
        if bool(np.any(bad_negative)):
            first = int(np.where(bad_negative)[0][0])
            raise RuntimeError(
                f"ShapeKey export missing/invalid VB0->mesh VertexId mapping for {prefix}"
                f"row {first} maps to negative mesh vertex {int(arr64[first])}."
            )
        max_id = int(arr64.max())
        if max_id >= int(mesh_vertex_count):
            raise RuntimeError(
                f"ShapeKey export missing/invalid VB0->mesh VertexId mapping for {prefix}"
                f"max mesh vertex id {max_id} exceeds mesh vertex count {int(mesh_vertex_count)}."
            )
        return arr64.astype(np.int32, copy=True)
    return np.zeros(0, dtype=np.int32)


def slot_capacity(merge_buffers=True):
    return MAX_SLOTS if merge_buffers else SPLIT_MAX_SLOTS


def build_export_slot_map(deform_keys, max_slots):
    """Build export slot map with legacy-slot compatibility.

    Legacy slots (1..max_slots) keep their historical `slot-1` channel.
    Out-of-range Deform ids are mapped into remaining free channels, preferring
    high channel indices first to minimize collision risk with existing mods.
    """
    deform_slots = sorted({int(d["slot"]) for d in deform_keys})
    if len(deform_slots) > int(max_slots):
        raise RuntimeError(
            f"ShapeKey export has {len(deform_slots)} unique Deform slots, "
            f"but the current buffer/shader path only supports {int(max_slots)}. "
            "Reduce the exported shape key count or increase the shader capacity."
        )

    slot_map = {}
    used_channels = set()

    for slot in deform_slots:
        if 1 <= slot <= int(max_slots):
            ch = slot - 1
            if ch not in used_channels:
                slot_map[slot] = ch
                used_channels.add(ch)

    remaining = [slot for slot in deform_slots if slot not in slot_map]
    free_channels_desc = [ch for ch in range(int(max_slots) - 1, -1, -1) if ch not in used_channels]
    if len(free_channels_desc) < len(remaining):
        raise RuntimeError(
            "ShapeKey export cannot allocate channels for all Deform slots "
            f"within capacity {int(max_slots)}."
        )

    for slot, ch in zip(remaining, free_channels_desc):
        slot_map[slot] = ch

    return slot_map


# ---------------------------------------------------------------- main

def bake_deform_keys(merged_obj, deform_keys, meshes_path, component_id,
                     vb0_buffer=None, mirror_mesh=False, merge_buffers=True,
                     slot_map=None, vb0_vertex_ids=None):
    """Bake all Deform shape keys into VB0-ordered delta + map (+ optional merge).

    merge_buffers=True (added in v0.2.2, default)
    -------------------------------------
    Merge all slots' (delta, map) of the same component into two files:
        Component<cid>_Position_deltas.buf   stride=12, all slots' active deltas concatenated in order
        Component<cid>_Position_lookup.buf   stride=4,  vcount*MAX_SLOTS int32 entries
                                              -1 means (vertex, slot) has no deformation,
                                              otherwise it is the offset into the merged deltas
    Advantage: each component has only 2 extra bufs instead of 2N+1.
    No information loss: channel = slot is deterministic, so no freq_indices file is needed.

    merge_buffers=False
    -------------------
    Keep v0.2.1 behavior: write 2 files per slot + 1 freq_indices per component.

    mirror_mesh handling (v0.2.1, independent of whether merging is used)
    --------------------------------------------
    EFMI has a Mirror Mesh option at both import and export stages; when enabled,
    game space and Blender space are X-mirrored. We negate X on both the mesh
    basis and target first, and the rest of the flow is unchanged -> delta
    orientation matches the game-side VB0.

    Returns
    -------
    list of dict, one per slot:
        {
            "slot": int (original Blender Deform number),
            "shader_slot": int (dense export slot),
            "channel_idx": int (dense export slot, i.e. IniParams[100+ch].x),
            "name": str,
            "active_count": int,
            "vertex_count": int (VB0 vertex count, used for dispatch),
            "component_id": int,
            "merge_buffers": bool,
            # only present in merge=False mode:
            "delta_filename": str,
            "map_filename": str,
        }
    and write under meshes_path (varies by mode):
        merge=True:
            Component<cid>_Position_deltas.buf
            Component<cid>_Position_lookup.buf
        merge=False:
            Component<cid>_Position_S<slot>_delta.buf  ×N
            Component<cid>_Position_S<slot>_map.buf    ×N
            Component<cid>_Position_freq_indices.buf
    """
    if not deform_keys or merged_obj is None or merged_obj.data is None:
        return []
    sk_data = merged_obj.data.shape_keys
    if not sk_data or "Basis" not in sk_data.key_blocks:
        print("[ShapeKey] WARNING: object has no Basis shape key, skipping bake.")
        return []
    if not NUMPY_OK:
        print("[ShapeKey] ERROR: numpy missing, baking aborted.")
        return []
    capacity = slot_capacity(merge_buffers)
    if slot_map is None:
        slot_map = build_export_slot_map(deform_keys, capacity)

    # ---- 0. Build the VB0 -> mesh vertex mapping ----
    n = len(merged_obj.data.vertices)
    base = _read_coords(sk_data.key_blocks["Basis"], n)
    if mirror_mesh:
        # ★ consistent with EFMI's converter_mirror_vector: data[:, 0] *= -1
        base = base.copy()
        base[:, 0] *= -1.0
        print(f"[ShapeKey] component {component_id}: mirror_mesh=True, basis X inverted before VB0 match.")

    m_count = _get_vb0_vertex_count(vb0_buffer, component_id)
    vb0_to_mesh = _validate_vb0_vertex_ids(
        vb0_vertex_ids,
        m_count=m_count,
        mesh_vertex_count=n,
        component_id=component_id,
    )
    print(f"[ShapeKey] component {component_id}: VB0 {m_count} verts -> mesh {n} verts "
          f"via strict VertexId mapping (split factor {m_count/max(n,1):.3f})")

    os.makedirs(meshes_path, exist_ok=True)

    # ==============================================================
    # Branch A: merge mode (merge_buffers=True)
    # ==============================================================
    if merge_buffers:
        # Collect every slot's delta + mask, then concatenate into two files at the end
        per_slot = []                                            # [(deform_slot, shader_slot, name, mask, packed_delta)]
        for d in deform_keys:
            slot = int(d["slot"])
            shader_slot = int(slot_map[slot])
            if not (0 <= shader_slot < MAX_SLOTS):
                raise RuntimeError(
                    f"ShapeKey export slot map sent Deform {slot} to shader slot "
                    f"{shader_slot}, outside merged capacity {MAX_SLOTS}."
                )
            target = _read_coords(d["key_block"], n)
            if mirror_mesh:
                target = target.copy()
                target[:, 0] *= -1.0
            mesh_delta = target - base
            vb0_delta = np.zeros((m_count, 3), dtype=np.float32)
            valid = vb0_to_mesh >= 0
            vb0_delta[valid] = mesh_delta[vb0_to_mesh[valid]]
            mask = ~np.isclose(vb0_delta, 0.0, atol=1e-6).all(axis=1)
            packed_delta = vb0_delta[mask].astype(np.float32)
            per_slot.append((
                slot, shader_slot, d["name"], d.get("raw"), mask, packed_delta))

        # Concatenate deltas + build the lookup
        # lookup[v*MAX_SLOTS + s] = -1 (none) or the offset into the merged deltas
        lookup = np.full(m_count * MAX_SLOTS, -1, dtype=np.int32)
        all_deltas = []
        running_offset = 0
        results = []
        for slot, shader_slot, name, raw_name, mask, packed_delta in per_slot:
            channel_idx = shader_slot
            active_count = int(mask.sum())
            if active_count > 0:
                active_idx = np.where(mask)[0]
                lookup[active_idx * MAX_SLOTS + shader_slot] = (
                    running_offset + np.arange(active_count, dtype=np.int32)
                )
                all_deltas.append(packed_delta)
                running_offset += active_count
            print(f"[ShapeKey] (merge) Baked C{component_id} Deform {slot} -> "
                  f"slot {shader_slot} '{name}': "
                  f"{active_count}/{m_count} active")
            results.append({
                "slot": slot,
                "deform_slot": slot,
                "export_slot": shader_slot,
                "shader_slot": shader_slot,
                "channel_idx": channel_idx,
                "name": name,
                "raw_name": raw_name or f"Deform {slot} {name}",
                "active_count": active_count,
                "vertex_count": m_count,
                "component_id": component_id,
                "merge_buffers": True,
            })

        if results:
            if all_deltas:
                merged = np.concatenate(all_deltas, axis=0).astype(np.float32)
            else:
                merged = np.zeros((0, 3), dtype=np.float32)
            deltas_filename = f"Component{component_id}_Position_deltas.buf"
            lookup_filename = f"Component{component_id}_Position_lookup.buf"
            with open(os.path.join(meshes_path, deltas_filename), "wb") as f:
                f.write(merged.tobytes())
            with open(os.path.join(meshes_path, lookup_filename), "wb") as f:
                f.write(lookup.tobytes())
            print(f"[ShapeKey] Wrote {deltas_filename} ({running_offset} active deltas, "
                  f"{running_offset*12} B) + {lookup_filename} ({m_count}×{MAX_SLOTS} slots, "
                  f"{m_count*MAX_SLOTS*4} B)")
        return results

    # ==============================================================
    # Branch B: split-file mode (merge_buffers=False, v0.2.1 behavior)
    #
    # Note: split-mode HLSL (shapekey_blend.hlsl) uses a StructuredBuffer array
    # occupying registers t51..t(50+MAX_SLOTS); the DX11 SM5.0 SRV limit is t127,
    # so the practical limit of this path is 24 slots. The Deform number itself is
    # no longer used as the slot index; it only fails when the number of unique
    # exported shape keys actually exceeds 24.
    # ==============================================================
    # Preallocate freq_indices for the whole component
    freq_indices = np.full(m_count * SPLIT_MAX_SLOTS, NO_FREQ_INDEX, dtype=np.uint32)
    results = []
    for d in deform_keys:
        slot = int(d["slot"])
        shader_slot = int(slot_map[slot])
        if not (0 <= shader_slot < SPLIT_MAX_SLOTS):
            raise RuntimeError(
                f"ShapeKey export slot map sent Deform {slot} to shader slot "
                f"{shader_slot}, outside split capacity {SPLIT_MAX_SLOTS}."
            )

        # The HLSL-side StructuredBuffer array starts at 0; Deform numbers have been compacted into contiguous slots.

        target = _read_coords(d["key_block"], n)
        if mirror_mesh:
            target = target.copy()
            target[:, 0] *= -1.0
        mesh_delta = target - base                               # (N, 3) per mesh vertex (already in game space)
        vb0_delta = np.zeros((m_count, 3), dtype=np.float32)
        valid = vb0_to_mesh >= 0
        vb0_delta[valid] = mesh_delta[vb0_to_mesh[valid]]

        mask = ~np.isclose(vb0_delta, 0.0, atol=1e-6).all(axis=1)
        active_count = int(mask.sum())

        packed = vb0_delta[mask].astype(np.float32)
        index_map = np.full(m_count, -1, dtype=np.int32)
        index_map[mask] = np.arange(active_count, dtype=np.int32)

        delta_filename = f"Component{component_id}_Position_S{slot}_delta.buf"
        map_filename   = f"Component{component_id}_Position_S{slot}_map.buf"
        with open(os.path.join(meshes_path, delta_filename), "wb") as f:
            f.write(packed.tobytes())
        with open(os.path.join(meshes_path, map_filename), "wb") as f:
            f.write(index_map.tobytes())

        # Fill freq_indices: channel_idx == shader_slot
        channel_idx = shader_slot
        active_idx = np.where(mask)[0]
        if len(active_idx):
            freq_indices[active_idx * SPLIT_MAX_SLOTS + shader_slot] = channel_idx

        print(f"[ShapeKey] (split) Baked C{component_id} Deform {slot} -> "
              f"slot {shader_slot} '{d['name']}': "
              f"{active_count}/{m_count} active")

        results.append({
            "slot": slot,
            "deform_slot": slot,
            "export_slot": shader_slot,
            "shader_slot": shader_slot,
            "channel_idx": channel_idx,
            "name": d["name"],
            "raw_name": d.get("raw") or f"Deform {slot} {d['name']}",
            "active_count": active_count,
            "vertex_count": m_count,
            "delta_filename": delta_filename,
            "map_filename": map_filename,
            "component_id": component_id,
            "merge_buffers": False,
        })

    if results:
        freq_filename = f"Component{component_id}_Position_freq_indices.buf"
        with open(os.path.join(meshes_path, freq_filename), "wb") as f:
            f.write(freq_indices.tobytes())
        print(f"[ShapeKey] Wrote {freq_filename} ({m_count} verts × {SPLIT_MAX_SLOTS} slots)")

    return results
