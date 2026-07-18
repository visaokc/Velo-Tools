"""Fixed capability ABI for EFMI CrossIB.

This module intentionally contains no shader hashes and no frame-dump pass
classifier. ShaderRegex assigns the ABI; the main INI only routes capabilities.
"""
from __future__ import annotations


POSE_CAPTURE = 200
PREPASS_CB2 = 201
MATERIAL_CB2 = 202
OUTLINE_CB2 = 203
EFFECT_CB2 = 204
EFFECT_CB3 = 205
CAPABILITY_FILTERS = (
    POSE_CAPTURE,
    PREPASS_CB2,
    MATERIAL_CB2,
    OUTLINE_CB2,
    EFFECT_CB2,
    EFFECT_CB3,
)


class CapabilityPassRegistry:
    """Small routing adapter consumed by the CrossIB INI generator."""

    def __init__(self, component_topology=None):
        self._component_topology = {
            int(component_id): dict(topology)
            for component_id, topology in (component_topology or {}).items()
        }

    def _topology(self, component_id):
        if component_id is None:
            return None
        return self._component_topology.get(int(component_id))

    def record_filters(self, _component_id=None):
        return [POSE_CAPTURE]

    def provider_filters(self, target_component_id=None, target_is_transparent=False):
        values = [POSE_CAPTURE]
        topology = self._topology(target_component_id)
        has_prepass = not target_is_transparent and (
            bool(topology.get("prepass_cb2")) if topology is not None else True
        )
        if has_prepass:
            values.append(PREPASS_CB2)
        values.extend((EFFECT_CB2, EFFECT_CB3))
        return values

    def consumer_borrow_filters(self, component_id=None):
        topology = self._topology(component_id)
        if topology is None:
            return [MATERIAL_CB2, OUTLINE_CB2]
        values = []
        if topology.get("material_cb2"):
            values.append(MATERIAL_CB2)
        if topology.get("outline_cb2"):
            values.append(OUTLINE_CB2)
        return values or [MATERIAL_CB2]

    def condition(self, filters):
        values = sorted({int(value) for value in filters})
        if not values:
            raise ValueError("CrossIB capability condition cannot be empty")
        return "if " + " || ".join(f"vs == {value}" for value in values)


# Kept as a source-compatible name for external scripts. Its semantics are now
# the fixed capability ABI, not a hash classifier.
CrossIBPassRegistry = CapabilityPassRegistry


def build_pass_registry(_source_folder=None):
    return CapabilityPassRegistry()
