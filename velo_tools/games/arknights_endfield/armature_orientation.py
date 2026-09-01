"""Structure-derived bone tails adapted from the Endfield character unpacker.

Why this exists: Unity rigs store each bone as a transform whose rotation is
whatever the rig author left there; Blender renders a bone along its local Y
(tail - head). Copying the Unity rotation onto edit bones (what armature_builder
used to do via eb.matrix) yields correct POSITIONS with visually arbitrary bone
axes. Derive every tail from the skeleton's own structure --
child heads, chain continuation, cluster coherence -- and never trust the stored
rotation for display.

The source rigs use the Bip001 root convention. Bip001 therefore keeps
the original short +Z display stem; all other bones remain structure-derived.
"""

from __future__ import annotations

import re

from mathutils import Vector

_MIN_TAIL_LENGTH = 0.025
_MAX_TAIL_LENGTH = 0.08
_ROOT_STEM_LENGTH = 0.1

_SIDE_SUFFIX = re.compile(r"\.(?:L|R)$")
_LEADING_TOKENS_SPLIT = re.compile(r"[_\- ]+")
_SEQUENCE = re.compile(r"(.*\D)(\d+)")


def _side_name_candidates(name: str):
    if name.endswith((".L", ".R")):
        return ()
    match = re.match(r"^(.*?)([_\- ]?)([LR])$", name)
    if match and match.group(2):
        prefix, separator, side = match.groups()
        counterpart = prefix + separator + ("R" if side == "L" else "L")
        return ((counterpart, prefix, side),)
    if re.match(r"^[LR](?=[A-Z_])", name):
        return (("R" if name[0] == "L" else "L") + name[1:], name[1:], name[0]),
    match = re.match(r"^(.*?)([LR])(?=[A-Z_])(.+)$", name)
    if match:
        prefix, side, suffix = match.groups()
        counterpart = prefix + ("R" if side == "L" else "L") + suffix
        prefix_core = prefix.rstrip("_- ")
        suffix_core = suffix.lstrip("_- ")
        separator = "_" if prefix != prefix_core or suffix != suffix_core else ""
        return ((counterpart, prefix_core + separator + suffix_core, side),)
    return ()


def _side_suffix_names(names):
    name_set = set(names)
    renamed = {}
    for name in names:
        if name.endswith((".L", ".R")):
            renamed[name] = name
            continue
        candidates = _side_name_candidates(name)
        if not candidates:
            continue
        counterpart, base, side = candidates[0]
        if counterpart in name_set or re.search(r"[_\- ][LR]$", name):
            renamed[name] = f"{base}.{side}"
    return renamed


def _chain_family(name: str) -> tuple[str, ...]:
    """Stable semantic family of a bone name, ignoring side suffix and digits."""
    base = _SIDE_SUFFIX.sub("", name)
    prefix = re.split(r"\d", base, maxsplit=1)[0]
    tokens = [token.casefold() for token in _LEADING_TOKENS_SPLIT.split(prefix)
              if token and token.upper() not in {"L", "R", "M"}]
    if tokens:
        return tuple(tokens[:2])
    return (base.casefold(),)


def _numbered_chain_identity(name: str) -> tuple[str, int, str]:
    """(semantic stem, trailing number, side marker) -- spine_2.L style chains."""
    side = ""
    side_match = re.search(r"(?:\.|_|-| )([LRM])$", name)
    if side_match:
        side = side_match.group(1)
        name = name[:side_match.start()]
    sequence_match = _SEQUENCE.fullmatch(name)
    if sequence_match:
        return sequence_match.group(1).casefold(), int(sequence_match.group(2)), side
    return name.casefold(), 0, side


class _Bone:
    """Minimal structural view of one hierarchy node."""

    __slots__ = ("name", "head", "parent_index", "children_indexes")

    def __init__(self, name, head):
        self.name = name
        self.head = head
        self.parent_index = -1
        self.children_indexes: list[int] = []


def _numbered_chain_child(index, bones):
    children = bones[index].children_indexes
    if len(children) < 2:
        return None
    stem, sequence, side = _numbered_chain_identity(bones[index].name)
    matches = [child for child in children
               if _numbered_chain_identity(bones[child].name) == (stem, sequence + 1, side)]
    return matches[0] if len(matches) == 1 else None


def _has_cross_family_unique_child(index, bones):
    children = bones[index].children_indexes
    return (len(children) == 1
            and _chain_family(bones[index].name) != _chain_family(bones[children[0]].name))


def _continuation_direction(index, bones, heads, clustered_children):
    """Direction the chain is flowing from clustered children only."""
    continuations = []
    for child_index in clustered_children:
        grandchildren = [heads[other] for other, candidate in enumerate(bones)
                         if candidate.parent_index == child_index]
        if not grandchildren:
            continue
        child_head = heads[child_index]
        target = sum(grandchildren, Vector()) / len(grandchildren)
        continuation = target - child_head
        if continuation.length > 1e-5:
            continuations.append(continuation.normalized())
    if len(continuations) >= 2:
        combined = sum(continuations, Vector())
        if combined.length / len(continuations) >= 0.85:
            return combined
        return None
    return None


def _leaf_tail(index, bones, heads):
    """Leaf bones: extend along the smoothed parent-chain flow, capped turn-gate
    stops distant auxiliary chains from dragging the tail sideways."""
    head = heads[index]
    chain = [index]
    while len(chain) < 5:
        parent_index = bones[chain[-1]].parent_index
        if not 0 <= parent_index < len(bones):
            break
        chain.append(parent_index)
    segment = heads[chain[0]] - heads[chain[1]] if len(chain) > 1 else Vector()
    segment_length = segment.length
    direction = segment.copy()
    if segment_length > 1e-5:
        base_direction = segment.normalized()
        smooth_segments = [segment]
        for current, parent_index in zip(chain[1:], chain[2:]):
            candidate = heads[current] - heads[parent_index]
            if candidate.length < 1e-5:
                break
            if base_direction.dot(candidate.normalized()) < 0.5:
                break
            smooth_segments.append(candidate)
            base_direction = candidate.normalized()
        direction = sum(
            (segment.normalized() * (len(smooth_segments) - offset)
             for offset, segment in enumerate(smooth_segments)),
            Vector(),
        )
    if direction.length < 1e-5 and 0 <= bones[index].parent_index < len(bones):
        direction = head - heads[bones[index].parent_index]
    if direction.length < 1e-5:
        direction = Vector((0.0, 0.0, 1.0))
    length = _leaf_length(index, bones, heads)
    return head + direction.normalized() * length


def _leaf_length(index, bones, heads):
    """Leaf display length from nearby structure: unique-child parents give their
    own length; otherwise half the median sibling spacing; else 35% of the
    parent segment. Pure geometry -- no name lists."""
    bone = bones[index]
    parent = bone.parent_index
    siblings = [candidate for candidate in bones
                if candidate.parent_index == parent] if 0 <= parent < len(bones) else []
    local = None
    if len(siblings) == 1 and 0 <= parent < len(bones):
        parent_tail = _bone_tail(parent, bones, heads)
        parent_length = (parent_tail - heads[parent]).length
        local = parent_length if parent_length > 1e-5 else _MIN_TAIL_LENGTH
    if local is None:
        sibling_spacings = sorted(
            (heads[other] - heads[index]).length
            for other in range(len(bones))
            if other != index and bones[other].parent_index == parent
            and (heads[other] - heads[index]).length > 1e-5
        )
        if sibling_spacings:
            local = sibling_spacings[len(sibling_spacings) // 2] * 0.5
        elif 0 <= parent < len(bones):
            local = (heads[index] - heads[parent]).length * 0.35
        else:
            local = _MIN_TAIL_LENGTH
    elif len(siblings) == 1:
        return max(local, 1e-5)
    return max(min(local, _MAX_TAIL_LENGTH), _MIN_TAIL_LENGTH)


def _symmetrize_side_pairs(bones, heads, tails):
    """Mirror attachment-like display tails across X for .L/.R pairs while
    preserving hierarchy (Velo's cross-family symmetrization, minus its UE
    attachment special case which needs name families we deliberately ignore)."""
    pairs = {}
    renamed = _side_suffix_names([bone.name for bone in bones])
    for index, bone in enumerate(bones):
        normalized = renamed.get(bone.name, "")
        if normalized.endswith((".L", ".R")):
            pairs.setdefault(normalized[:-2], {})[normalized[-1]] = index
    for sides in pairs.values():
        if "L" not in sides or "R" not in sides:
            continue
        left = sides["L"]
        right = sides["R"]
        left_attachment = _has_cross_family_unique_child(left, bones)
        right_attachment = _has_cross_family_unique_child(right, bones)
        if not left_attachment and not right_attachment:
            continue
        left_delta = tails[left] - heads[left]
        right_delta = tails[right] - heads[right]
        if left_delta.length <= 1e-5 or right_delta.length <= 1e-5:
            continue
        mirrored_right = Vector((-right_delta.x, right_delta.y, right_delta.z))
        if left_attachment and not right_attachment:
            direction = mirrored_right.normalized()
            length = right_delta.length
        elif right_attachment and not left_attachment:
            direction = left_delta.normalized()
            length = left_delta.length
        else:
            combined = left_delta.normalized() + mirrored_right.normalized()
            direction = combined.normalized() if combined.length > 1e-5 else left_delta.normalized()
            length = (left_delta.length + right_delta.length) * 0.5
        tails[left] = heads[left] + direction * length
        tails[right] = heads[right] + Vector((-direction.x, direction.y, direction.z)) * length


def _bone_tail(index, bones, heads):
    """One bone's structural tail. Order mirrors Velo: numbered-chain first, then
    clustered multi-child handling, then plain child mean, then leaf flow."""
    head = heads[index]
    children = bones[index].children_indexes
    if bones[index].name == "Bip001":
        return head + Vector((0.0, 0.0, _ROOT_STEM_LENGTH))
    numbered_child = _numbered_chain_child(index, bones)
    if numbered_child is not None:
        return heads[numbered_child]
    if children and not _has_cross_family_unique_child(index, bones):
        child_entries = [(child, heads[child]) for child in children]
        child_heads = [point for _child, point in child_entries]
        distances = sorted((point - head).length for point in child_heads)
        if len(distances) >= 3:
            median = distances[len(distances) // 2]
            deviations = sorted(abs(distance - median) for distance in distances)
            mad = deviations[len(deviations) // 2]
            cutoff = median + 3.0 * max(mad, median * 0.5)
            clustered = [
                (child, point) for child, point in child_entries
                if (point - head).length <= cutoff
            ]
            if len(clustered) >= 2 and len(clustered) < len(child_heads):
                excluded = [
                    (point - head).normalized() for _child, point in child_entries
                    if (point - head).length > cutoff
                ]
                if len(excluded) >= 2:
                    excluded_coherence = sum(excluded, Vector()).length / len(excluded)
                    if excluded_coherence >= 0.85:
                        return sum(child_heads, Vector()) / len(child_heads)
                clustered_children = [child for child, _point in clustered]
                direction = _continuation_direction(index, bones, heads, clustered_children)
                if direction is None or direction.length <= 1e-5:
                    target = sum((point for _child, point in clustered), Vector()) / len(clustered)
                    direction = target - head
                if direction.length > 1e-5:
                    length = max(_MIN_TAIL_LENGTH, min(_MAX_TAIL_LENGTH, median * 2.5))
                    return head + direction.normalized() * length
        return sum(child_heads, Vector()) / len(child_heads)
    if bones[index].parent_index < 0:
        # Root without children: short upward stem (generic; UE's Root/Bip001
        # stubs are name knowledge we don't carry).
        return head + Vector((0.0, 0.0, _ROOT_STEM_LENGTH))
    return _leaf_tail(index, bones, heads)


def _valid_tail(index, bones, heads, tails):
    head = heads[index]
    tail = tails[index]
    if (tail - head).length > 1e-5:
        return tail
    parent = bones[index].parent_index
    if 0 <= parent < len(bones):
        direction = head - heads[parent]
        if direction.length > 1e-5:
            return head + direction.normalized() * _MIN_TAIL_LENGTH
    children = bones[index].children_indexes
    for child in children:
        direction = heads[child] - head
        if direction.length > 1e-5:
            return head + direction.normalized() * _MIN_TAIL_LENGTH
    return head + Vector((0.0, 0.0, _MIN_TAIL_LENGTH))


def derive_tails(ordered_nodes, head_of):
    """Public entry. ordered_nodes must be parent-before-child; head_of converts a
    node to its Blender-space head position. Returns {node_id(node): Vector}."""
    bones = []
    node_ids = []
    position_of = {}
    for position, node in enumerate(ordered_nodes):
        bones.append(_Bone(node.name, head_of(node)))
        node_ids.append(id(node))
        position_of[id(node)] = position
    for position, node in enumerate(ordered_nodes):
        parent_node = getattr(node, "parent", None)
        bones[position].parent_index = position_of.get(id(parent_node), -1) if parent_node is not None else -1
        parent_position = bones[position].parent_index
        if 0 <= parent_position < len(bones):
            bones[parent_position].children_indexes.append(position)

    heads = [bone.head for bone in bones]
    tails = [_bone_tail(index, bones, heads) for index in range(len(bones))]
    _symmetrize_side_pairs(bones, heads, tails)
    tails = [_valid_tail(index, bones, heads, tails) for index in range(len(bones))]
    return dict(zip(node_ids, tails))
