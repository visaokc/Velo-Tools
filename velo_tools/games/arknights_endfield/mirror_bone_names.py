"""Blender-compatible aliases for unambiguous L/R bone-name pairs."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable


_TRAILING_SIDE_SUFFIXES = (".L", ".R")


def _alias_stem(name: str, side_index: int) -> str:
    stem = name[:side_index] + name[side_index + 1 :]
    stem = re.sub(r"([_.\- ])\1+", r"\1", stem)
    return stem.strip("_.- ")


def mirror_suffix_aliases(names: Iterable[str]) -> dict[str, str]:
    """Return original-to-.L/.R aliases for unique one-character mirror pairs."""
    all_names = {str(name).strip() for name in names if str(name).strip()}
    unique_names = {
        name for name in all_names if not name.endswith(_TRAILING_SIDE_SUFFIXES)
    }
    buckets = defaultdict(lambda: {"L": set(), "R": set()})
    for name in unique_names:
        for index, char in enumerate(name):
            if char in {"L", "R"}:
                buckets[(index, name[:index], name[index + 1 :])][char].add(name)

    candidates = defaultdict(set)
    pair_indexes = {}
    for (index, _prefix, _suffix), sides in buckets.items():
        if len(sides["L"]) != 1 or len(sides["R"]) != 1:
            continue
        left = next(iter(sides["L"]))
        right = next(iter(sides["R"]))
        candidates[left].add(right)
        candidates[right].add(left)
        pair_indexes[frozenset((left, right))] = index

    aliases = {}
    reserved = set(all_names)
    for left in sorted(name for name in candidates if "L" in name):
        if len(candidates[left]) != 1:
            continue
        right = next(iter(candidates[left]))
        if len(candidates[right]) != 1 or next(iter(candidates[right])) != left:
            continue
        index = pair_indexes.get(frozenset((left, right)))
        if index is None or left[index] != "L" or right[index] != "R":
            continue
        stem = _alias_stem(left, index)
        if not stem:
            continue
        left_alias = f"{stem}.L"
        right_alias = f"{stem}.R"
        if left_alias in reserved or right_alias in reserved:
            continue
        aliases[left] = left_alias
        aliases[right] = right_alias
        reserved.update((left_alias, right_alias))
    return aliases
