"""Fixed capability ABI for EFMI CrossIB.

This module intentionally contains no shader hashes and no frame-dump pass
classifier. ShaderRegex assigns the ABI; the main INI only routes capabilities.
"""
from __future__ import annotations


POSE_CAPTURE = 200
PREPASS_CB2 = 211
MATERIAL_CB2 = 212
OUTLINE_CB2 = 213
EFFECT_CB2 = 214
EFFECT_CB3 = 224
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

    def record_filters(self, _component_id=None):
        return [POSE_CAPTURE]

    def provider_filters(self, target_is_transparent=False):
        values = [POSE_CAPTURE]
        if not target_is_transparent:
            values.append(PREPASS_CB2)
        values.extend((EFFECT_CB2, EFFECT_CB3))
        return values

    def consumer_borrow_filters(self, _component_id=None):
        return [MATERIAL_CB2, OUTLINE_CB2]

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
