"""Runtime assets for canonical WWMI LOD skeleton import."""

import shutil
from pathlib import Path


_SHADER_NAMES = (
    "CanonicalSkeletonLodImporter.hlsl",
    "CanonicalSkeletonInitializer.hlsl",
)


def write_runtime_assets(output_folder) -> None:
    shaders = Path(output_folder) / "Shaders"
    shaders.mkdir(parents=True, exist_ok=True)
    for name in _SHADER_NAMES:
        source = Path(__file__).parent / "shaders" / name
        shutil.copyfile(source, shaders / name)
