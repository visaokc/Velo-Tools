"""Keep EFMI tangent export independent of UV selection and batch contents."""

import numpy

from ._efmi_core.data_models.data_model_efmi import DataModelEFMI
from ._efmi_core.migoto_io.data_model.data_extractor import BlenderDataExtractor


_ORIGINAL_GET_LOOP_DATA = None
_ORIGINAL_ENCODE_TANGENTS = None


def _get_loop_data(self, mesh, *args, **kwargs):
    primary_index = mesh.uv_layers.find("TEXCOORD.xy")
    previous_index = mesh.uv_layers.active_index
    try:
        if primary_index >= 0:
            mesh.uv_layers.active_index = primary_index
        return _ORIGINAL_GET_LOOP_DATA(self, mesh, *args, **kwargs)
    finally:
        if primary_index >= 0:
            mesh.uv_layers.active_index = previous_index


def _encode_tangents(tangents, normals):
    reference = numpy.stack([
        normals[:, 1] - normals[:, 2],
        normals[:, 2] - normals[:, 0],
        normals[:, 0] - normals[:, 1],
    ], axis=1)
    lengths = numpy.linalg.norm(reference, axis=1, keepdims=True)
    singular = lengths[:, 0] < 1e-6
    # Normalize every regular reference, regardless of other vertices in the batch.
    reference[~singular] /= lengths[~singular]
    if numpy.any(singular):
        selected = normals[singular]
        helper = numpy.where(
            numpy.abs(selected[:, 0:1]) < 0.9,
            numpy.array([1.0, 0.0, 0.0]),
            numpy.array([0.0, 1.0, 0.0]),
        )
        perpendicular = numpy.cross(selected, helper)
        perpendicular /= numpy.linalg.norm(perpendicular, axis=1, keepdims=True)
        reference[singular] = perpendicular

    bitangent = numpy.cross(reference, normals)
    cosine = numpy.clip(numpy.sum(tangents * reference, axis=1), -1.0, 1.0)
    sine = numpy.clip(numpy.sum(tangents * bitangent, axis=1), -1.0, 1.0)
    denominator = numpy.abs(cosine) + numpy.abs(sine)
    # A collapsed UV basis has no tangent angle; use the reference direction.
    parameter = numpy.divide(cosine, denominator, out=numpy.ones_like(cosine), where=denominator > 0)
    parameter = 1 - (1 - parameter) / 2.0
    sign = numpy.where(sine == 0.0, 1.0, numpy.sign(sine))
    return numpy.copysign(parameter, sign)


def install_patch():
    global _ORIGINAL_GET_LOOP_DATA, _ORIGINAL_ENCODE_TANGENTS
    if _ORIGINAL_GET_LOOP_DATA is not None:
        return
    _ORIGINAL_GET_LOOP_DATA = BlenderDataExtractor.get_loop_data
    _ORIGINAL_ENCODE_TANGENTS = DataModelEFMI.__dict__["encode_tangents"]
    BlenderDataExtractor.get_loop_data = _get_loop_data
    DataModelEFMI.encode_tangents = staticmethod(_encode_tangents)


def remove_patch():
    global _ORIGINAL_GET_LOOP_DATA, _ORIGINAL_ENCODE_TANGENTS
    if _ORIGINAL_GET_LOOP_DATA is None:
        return
    BlenderDataExtractor.get_loop_data = _ORIGINAL_GET_LOOP_DATA
    DataModelEFMI.encode_tangents = _ORIGINAL_ENCODE_TANGENTS
    _ORIGINAL_GET_LOOP_DATA = None
    _ORIGINAL_ENCODE_TANGENTS = None
