"""Versioned ShaderRegex capability classifier for CrossIB."""
from __future__ import annotations

import json
import re
from pathlib import Path


CLASSIFIER_FILENAME = "CrossIBClassifier.ini"
CLASSIFIER_PROFILE = "crossib-capability-v1"
LEGACY_CLASSIFIER_PROFILES = frozenset({"efmi-crossib-capability-v1"})
CAPABILITY_FILTERS = (200, 201, 202, 203, 204, 205)

_NAMESPACE_RE = re.compile(r"^\s*namespace\s*=\s*([^;\r\n]+)", re.MULTILINE | re.IGNORECASE)
_SAFE_NAMESPACE_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_TEMPLATE = r"""namespace = __NAMESPACE__

; Adaptive CrossIB capability classifier.
; Character isolation remains in mod.ini through the existing IB gates.
; Hashes, exact palette lengths, declaration ordering noise, and exact temp counts are not used.

; 200: pose capture with the GPU skinning palette in CB1.
[ShaderRegexCrossIBCapabilityPoseCB1]
shader_model = vs_5_0
filter_index = 200

[ShaderRegexCrossIBCapabilityPoseCB1.Pattern]
dcl_constantbuffer CB1\[4\d{3}\], dynamicIndexed\n(?:dcl_[^\n]*\n)*?dcl_resource_structured t0, 16\n

; 201: prepass capability. The compact final output selects the prepass texture layout.
[ShaderRegexCrossIBCapabilityPrepassCB2]
shader_model = vs_5_0
filter_index = 201

[ShaderRegexCrossIBCapabilityPrepassCB2.Pattern]
(?s)dcl_constantbuffer CB2\[4\d{3}\], dynamicIndexed\n.*?dcl_constantbuffer CB3\[11\], immediateIndexed\n.*?dcl_output o(?:6|7)\.x\ndcl_temps \d+\n

; 202: material/colour capability. The extended final output enables borrowing.
[ShaderRegexCrossIBCapabilityMaterialCB2]
shader_model = vs_5_0
filter_index = 202

[ShaderRegexCrossIBCapabilityMaterialCB2.Pattern]
(?s)dcl_constantbuffer CB2\[4\d{3}\], dynamicIndexed\n.*?dcl_constantbuffer CB3\[11\], immediateIndexed\n.*?dcl_output o(?:9|10)\.[xyzw]+\ndcl_temps \d+\n

; 203: outline capability.
[ShaderRegexCrossIBCapabilityOutlineCB2]
shader_model = vs_5_0
filter_index = 203

[ShaderRegexCrossIBCapabilityOutlineCB2.Pattern]
(?s)dcl_constantbuffer CB2\[4\d{3}\], dynamicIndexed\n.*?dcl_constantbuffer CB3\[1[4-6]\], immediateIndexed\n.*?dcl_output o7\.x\ndcl_temps \d+\n

; 204: CB2 effect/self-render capability selected by two positive declaration families.
; The compact family covers ordinary self-render/afterimage passes. The dual-dynamic
; family covers multi-stage attack afterimages. No cross-body negative lookahead is used.
[ShaderRegexCrossIBCapabilityEffectCB2]
shader_model = vs_5_0
filter_index = 204

[ShaderRegexCrossIBCapabilityEffectCB2.Pattern]
(?:dcl_constantbuffer CB1\[(?:[1-9]|[12]\d|3\d)\], immediateIndexed\n(?:dcl_[^\n]*\n)*?dcl_constantbuffer CB2\[4\d{3}\], dynamicIndexed\n(?:dcl_[^\n]*\n)*?dcl_constantbuffer CB3\[(?:[1-9]|1\d)\], immediateIndexed\n(?:dcl_[^\n]*\n)*?dcl_resource_structured t0, 16\n(?:dcl_[^\n]*\n)*?dcl_input v2\.xyzw\n(?:dcl_[^\n]*\n)*?dcl_input v3\.xyzw\n|dcl_constantbuffer CB1\[(?:[1-9]|[12]\d|3\d)\], immediateIndexed\n(?:dcl_[^\n]*\n)*?dcl_constantbuffer CB2\[4\d{3}\], dynamicIndexed\n(?:dcl_[^\n]*\n)*?dcl_constantbuffer CB[34]\[4\d{3}\], dynamicIndexed\n(?:dcl_[^\n]*\n)*?dcl_input v2\.xyzw\n(?:dcl_[^\n]*\n)*?dcl_input v3\.xyzw\n)

; 205: CB3 effect/self-render capability. The stable five-CB character family retains
; both extended and compact input/output topologies while rejecting unrelated CB3 layouts.
[ShaderRegexCrossIBCapabilityEffectCB3]
shader_model = vs_5_0
filter_index = 205

[ShaderRegexCrossIBCapabilityEffectCB3.Pattern]
dcl_constantbuffer CB0\[2\], immediateIndexed\ndcl_constantbuffer CB1\[82\], immediateIndexed\ndcl_constantbuffer CB2\[104\], immediateIndexed\ndcl_constantbuffer CB3\[4\d{3}\], dynamicIndexed\ndcl_constantbuffer CB4\[\d+\], immediateIndexed\n(?:dcl_(?!constantbuffer)[^\n]*\n)*?dcl_resource_structured t0, 16\n
"""


def namespace_from_ini(ini_text: str) -> str | None:
    match = _NAMESPACE_RE.search(ini_text or "")
    if not match:
        return None
    value = _SAFE_NAMESPACE_RE.sub("_", match.group(1).strip())
    return value or None


def namespace_from_metadata(source_folder) -> str:
    source = Path(source_folder)
    payload = json.loads((source / "Metadata.json").read_text(encoding="utf-8"))
    for component in payload.get("components") or []:
        if not isinstance(component, dict):
            continue
        ib_hash = str(component.get("ib_hash") or "").lower()
        if re.fullmatch(r"[0-9a-f]{8}", ib_hash):
            return f"C{ib_hash}"
    raise ValueError("Metadata.json has no canonical component IB hash for the CrossIB namespace")


def render_classifier(namespace: str) -> str:
    safe = _SAFE_NAMESPACE_RE.sub("_", str(namespace or "").strip())
    if not safe:
        raise ValueError("CrossIB classifier namespace is empty")
    return _TEMPLATE.replace("__NAMESPACE__", safe)
