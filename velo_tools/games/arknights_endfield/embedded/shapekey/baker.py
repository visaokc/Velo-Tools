"""Bake per-Deform-slot position deltas + freq_indices (v0.2 / TheHerta3).

输出文件（全部落在 mod 的 Meshes/ 下）
-----------------------------------
Component<cid>_Position_S<slot>_delta.buf      stride=12, packed float3 deltas
Component<cid>_Position_S<slot>_map.buf        stride=4,  int32 map (-1 表示该顶点无 delta)
Component<cid>_Position_freq_indices.buf       stride=4,  vcount*MAX_SLOTS uint32

注意 `Component<cid>_Position_0.buf` 不在这里生成 —— 我们让 ini 直接复用 EFMI
已经写出的 `Component<cid>_VB0.buf` 作为 pristine 副本（同字节，无需重复拷贝）。

VB0 顶点数对齐
--------------
EFMI 把每个 loop corner 转成一条 VB0 记录再去重，于是
`len(VB0) >= len(mesh.vertices)`。正式导出必须使用 EFMI 导出阶段保留的
VertexId 映射，把 VB0 行严格映射回 Blender mesh 顶点；POSITION0 坐标反查
只保留为 legacy diagnostic，不再用于 shape-key bake 输出。
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

_QUANT_SCALE = 1e5  # 位置匹配量化：1e-5 ≈ 10 µm


# ---------------------------------------------------------------- helpers

def _read_coords(key_block, vertex_count):
    if NUMPY_OK:
        flat = np.empty(vertex_count * 3, dtype=np.float32)
        key_block.data.foreach_get("co", flat)
        return flat.reshape((vertex_count, 3))
    return [tuple(key_block.data[i].co) for i in range(vertex_count)]


def _extract_vb0_positions(vb0_buffer):
    """从 EFMI NumpyBuffer 拿 VB0 的 POSITION0 属性，返回 (M,3) float32 或 None。"""
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
    """把所有 Deform shape key 烘成 VB0 顺序的 delta + map (+ 可选合并).

    merge_buffers=True (v0.2.2 新增,默认)
    -------------------------------------
    把同一 component 的所有 slot 的 (delta, map) 合并成两份文件：
        Component<cid>_Position_deltas.buf   stride=12, 全部 slot 活动 delta 顺序拼接
        Component<cid>_Position_lookup.buf   stride=4,  vcount*MAX_SLOTS 个 int32
                                              -1 表示 (vertex, slot) 无形变,
                                              否则是合并 deltas 中的偏移
    优势：每个 component 只有 2 份额外 buf，而不是 2N+1 份。
    没有信息损失：channel = slot 是确定的,所以不需要 freq_indices 文件。

    merge_buffers=False
    -------------------
    保持 v0.2.1 行为：每个 slot 写 2 份 + 每个 component 1 份 freq_indices。

    mirror_mesh 处理（v0.2.1，与是否合并无关）
    --------------------------------------------
    EFMI 在导入 / 导出阶段都有 Mirror Mesh 选项，开启时游戏空间和
    Blender 空间是 X 镜像。我们对 mesh basis / target 都先 X 取反，
    其余流程不变 → delta 朝向匹配游戏侧 VB0。

    Returns
    -------
    list of dict，每个 slot 一个：
        {
            "slot": int (original Blender Deform number),
            "shader_slot": int (dense export slot),
            "channel_idx": int (dense export slot, 即 IniParams[100+ch].x),
            "name": str,
            "active_count": int,
            "vertex_count": int (VB0 顶点数, 用于 dispatch),
            "component_id": int,
            "merge_buffers": bool,
            # merge=False 模式才有：
            "delta_filename": str,
            "map_filename": str,
        }
    并在 meshes_path 写出（按模式不同）：
        merge=True ：
            Component<cid>_Position_deltas.buf
            Component<cid>_Position_lookup.buf
        merge=False：
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

    # ---- 0. 建立 VB0 → mesh vertex 映射 ----
    n = len(merged_obj.data.vertices)
    base = _read_coords(sk_data.key_blocks["Basis"], n)
    if mirror_mesh:
        # ★ 与 EFMI 的 converter_mirror_vector 一致：data[:, 0] *= -1
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
    # 分支 A：合并模式（merge_buffers=True）
    # ==============================================================
    if merge_buffers:
        # 收集所有 slot 的 delta + mask，最后一次性拼接成两个文件
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
            per_slot.append((slot, shader_slot, d["name"], mask, packed_delta))

        # 拼接 deltas + 构造 lookup
        # lookup[v*MAX_SLOTS + s] = -1 (无) 或 在合并 deltas 里的偏移
        lookup = np.full(m_count * MAX_SLOTS, -1, dtype=np.int32)
        all_deltas = []
        running_offset = 0
        results = []
        for slot, shader_slot, name, mask, packed_delta in per_slot:
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
    # 分支 B：分文件模式（merge_buffers=False，v0.2.1 行为）
    #
    # 注意：split-mode HLSL (shapekey_blend.hlsl) 用 StructuredBuffer 数组占用
    # t51..t(50+MAX_SLOTS) 寄存器，DX11 SM5.0 SRV 上限是 t127，所以这条路径
    # 的实际上限是 24 个槽位。Deform 编号本身不再作为槽号；只有实际导出的
    # 唯一形态键数量超过 24 时才会失败。
    # ==============================================================
    # 为整个 component 预分配 freq_indices
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

        # HLSL 端的 StructuredBuffer array 从 0 起；Deform 编号已被压缩到连续槽位。

        target = _read_coords(d["key_block"], n)
        if mirror_mesh:
            target = target.copy()
            target[:, 0] *= -1.0
        mesh_delta = target - base                               # (N, 3) 针对 mesh vertex（已是游戏空间）
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

        # 填 freq_indices：channel_idx == shader_slot
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
