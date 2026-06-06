import time

from dataclasses import dataclass, field
from pathlib import Path

from .migoto_model.migoto_mesh import GeometryMatcherConfig, GeometryMatcherMethod

from .object_extractor.migoto_object.migoto_object_builder import MigotoObject, MigotoObjectFilter
from .object_extractor.migoto_object.textures_descriptor import TextureFilter
from .object_extractor.raw_object.raw_object_extractor import RawObjectFilter
from .object_extractor.object_extractor import ObjectExtractor
from .object_extractor.lod_matcher import LODMatcher


def extract_frame_data(extract_lods=False):
    if not extract_lods:
        dump_path = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\!DUMPS\Endmin Menu Dump 1.2")
        # dump_path = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\!DUMPS\Wuling Open World 1.2")
        dump_path = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\!DUMPS\Endmin Open World Dump 1.2")
    else:
        dump_path = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\!DUMPS\Endmin Open World Dump 1.2")

    output_path = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\Extracted Objects")

    start_time = time.time()

    object_extractor = ObjectExtractor()

    frame_model = object_extractor.build_frame_model(dump_path)

    migoto_objects = object_extractor.extract_objects(
        model=frame_model,
        raw_object_filter=RawObjectFilter(
            min_component_count=0,
            min_texture_count=1,
            lookup_resource_hashes="",
        ),
        migoto_object_filter=MigotoObjectFilter(
            ignore_errors=False,
            skip_static_objects=False,
        ),
    )

    if not extract_lods:
        texture_filter = TextureFilter(
            exclude_extensions=["jpg", "buf"],
            exclude_hashes=[],
            min_file_size=256 * 1024,
        )
        object_extractor.export_objects(migoto_objects, texture_filter, output_path)

    else:

        lod_object_source_folder = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\Extracted Objects\Character 32978")
        migoto_objects = [MigotoObject.from_exported_files(lod_object_source_folder)]

        object_source_folder = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\Extracted Objects\Character 58860")

        lod_matcher = LODMatcher(
            geo_matcher_main_config=GeometryMatcherConfig(
                method=GeometryMatcherMethod.Voxel,
                sensitivity=0.5,
                voxel_size=0.01,
                samples_count=1000,
            ),
            geo_matcher_prefilter_config=GeometryMatcherConfig(
                method=GeometryMatcherMethod.Voxel,
                sensitivity=0.5,
                voxel_size=0.05,
                samples_count=250,
            ),
            geo_matcher_prefilter_candidates_count=5,
            vg_matcher_candidates_count=3,
        )
        full_object = MigotoObject.from_exported_files(object_source_folder)
        lod_object, matched_components = lod_matcher.find_matching_lods(full_object, migoto_objects)

        for full_component in full_object.components:
            (lod_component, vg_map) = matched_components.get(full_component, (None, None))
            if lod_component is None:
                continue
            full_component.import_lod_metadata(lod_object.id, lod_component, vg_map)

        full_object.export_metadata(Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\Extracted Objects\Character 58860"))

        print(1)

    print(f"Execution time: %s seconds" % (time.time() - start_time))


if __name__ == "__main__":
    extract_frame_data(
        extract_lods=False,
    )