# LOD matching stack ported from the EFMI toolchain (GPL-3.0):
#   _efmi_core/migoto_io/migoto_model/migoto_mesh.py   (matcher classes)
#   _efmi_core/migoto_io/object_extractor/lod_matcher.py
#
# Adapted for WWMI data shapes (no cross-game imports, numpy only, no bpy):
#   - Meshes are duck-typed through a 4-method protocol instead of EFMI's
#     MigotoMesh.get_data(Semantic): positions() -> (N,3) float, triangles()
#     -> (T,3) int, blend_indices() / blend_weights() -> (N,K) | None.
#   - WWMI has no per-component IB hash, so component identity for the
#     "match by hash" stage is a content hash (sha1 over ib+vb bytes)
#     exposed as `component.content_hash`.
#   - The hash blacklist filters whole LOD *objects* by vb0_hash (WWMI users
#     have no per-component hashes to blacklist).
#   - EFMI's "already imported from other lod object" component prefilter is
#     dropped: WWMI handles duplicate imports at persist time, object-level
#     (same lod_object_name requires the overwrite flag).

import re
import time

import numpy

from dataclasses import dataclass, field
from enum import Enum
from operator import itemgetter


class LODMatcherError(Exception):
    pass


class ObjectLowSimilarityError(LODMatcherError):
    pass


class ComponentLowSimilarityError(LODMatcherError):
    pass


class ChamferMixin:

    @staticmethod
    def calculate_linear_chamfer_distance(points_a: numpy.ndarray, points_b: numpy.ndarray) -> numpy.ndarray:
        """Calculates symmetric Chamfer distance using linear distances."""
        dist1 = ChamferMixin.calculate_min_distances(points_a, points_b)
        dist2 = ChamferMixin.calculate_min_distances(points_b, points_a)
        return numpy.mean(dist1) + numpy.mean(dist2)

    @staticmethod
    def calculate_min_distances(points_a: numpy.ndarray, points_b: numpy.ndarray, chunk_size=256):
        """Computes minimum distance for each vertex in points_a to any vertex in points_b in chunks to save memory."""
        cd_chunks = []
        for start in range(0, len(points_a), chunk_size):
            end = start + chunk_size
            diff_chunk = points_a[start:end, None, :] - points_b[None, :, :]
            dist_chunk = numpy.min(numpy.linalg.norm(diff_chunk, axis=2), axis=1)
            cd_chunks.append(dist_chunk)
        return numpy.concatenate(cd_chunks)


class VertexGroupsMatcher(ChamferMixin):
    def __init__(self, candidates_count=3):
        self.candidates_count = candidates_count

    def match_vertex_groups(self, mesh_a, mesh_b) -> dict[int, int]:
        """Maps vertex group IDs from mesh A to vertex group IDs of mesh B based on geometry.

        Supports multiple vertex groups per vertex (any 2nd dim size).
        Uses centroid pre-filtering to reduce number of Chamfer computations.

        Args:
            mesh_a: Source mesh whose vertex group IDs will be mapped.
            mesh_b: Target mesh whose vertex group IDs will be matched against.

        Returns:
            A dictionary mapping vertex group IDs from mesh A to the corresponding
            vertex group IDs of mesh B. Keys are vertex group IDs from mesh A, and
            values are the best-matching vertex group IDs from mesh B.
        """
        positions_a = mesh_a.positions()
        indices_a = mesh_a.blend_indices()
        weights_a = mesh_a.blend_weights()

        positions_b = mesh_b.positions()
        indices_b = mesh_b.blend_indices()
        weights_b = mesh_b.blend_weights()

        if indices_a is None:
            return {0: 0}

        # Remap zero-rows to virtual ID to simplify VG=0 handling
        # It allows to avoid branching for VG 0 with numpy.all instead of numpy.any
        indices_a, zero_id_a = self.remap_zero_rows(indices_a, weights_a)
        indices_b, zero_id_b = self.remap_zero_rows(indices_b, weights_b)

        # Unique VG IDs
        unique_indices_a = numpy.unique(indices_a[indices_a != 0])
        unique_indices_b = numpy.unique(indices_b[indices_b != 0])

        # Build point clouds for all VGs of mesh B
        points_list_b = [positions_b[numpy.any(indices_b == vg_b, axis=1)] for vg_b in unique_indices_b]
        centroids_b = numpy.array(
            [pts.mean(axis=0) if len(pts) > 0 else numpy.array([numpy.inf] * 3) for pts in points_list_b],
            dtype=numpy.float32
        )

        mapping = {}

        for vg_a in unique_indices_a:
            points_a = positions_a[numpy.any(indices_a == vg_a, axis=1)].astype(numpy.float32)
            if len(points_a) == 0:
                mapping[vg_a] = None
                continue

            # Compute mesh A centroid
            full_centroid = points_a.mean(axis=0)

            # Pre-filter VG candidates from mesh B using centroid distance
            dists = numpy.linalg.norm(centroids_b - full_centroid, axis=1)
            candidate_indices = numpy.argsort(dists)[:self.candidates_count]

            best_cd = numpy.inf
            best_vg_b = None

            for idx in candidate_indices:

                points_b = points_list_b[idx]
                if len(points_b) == 0:
                    continue

                cd = self.calculate_linear_chamfer_distance(points_a, points_b)
                if cd < best_cd:
                    best_cd = cd
                    best_vg_b = unique_indices_b[idx]

            vg_a, best_vg_b = int(vg_a), int(best_vg_b)

            vg_a = vg_a if vg_a != zero_id_a else 0
            best_vg_b = best_vg_b if best_vg_b != zero_id_b else 0

            mapping[vg_a] = best_vg_b

        return dict(sorted(mapping.items()))

    @staticmethod
    def remap_zero_rows(vg_ids: numpy.ndarray, vg_weights: numpy.ndarray):
        """Returns a copy of vg_ids with VG 0 replaced with max VG ID + 1."""
        # Widen first: WWMI blend ids are uint8, where max()+1 could wrap to 0.
        vg_ids = vg_ids.astype(numpy.int32, copy=True)
        virtual_id = int(vg_ids.max()) + 1
        # Create dummy weights array if not found (aka [[1, 0, 0, 0] * len(vg_ids)])
        if vg_weights is None:
            num_rows = vg_ids.shape[0]
            num_zeros = vg_ids.shape[1] - 1
            vg_weights = numpy.hstack([
                numpy.ones((num_rows, 1), dtype=numpy.uint8),
                numpy.zeros((num_rows, num_zeros), dtype=numpy.uint8)
            ])
        # Replace zeros where weight > 0
        mask = (vg_ids == 0) & (vg_weights > 0)
        vg_ids[mask] = virtual_id

        return vg_ids, virtual_id


class GeometryMatcherMethod(Enum):
    Voxel = "VOXEL"
    PointCloud = "POINT_CLOUD"


@dataclass
class GeometryMatcherConfig:
    method: GeometryMatcherMethod = GeometryMatcherMethod.Voxel
    sensitivity: float = 0.5
    voxel_size: float = 0.05
    samples_count: int = 5000


@dataclass
class GeometryMatcher(ChamferMixin):
    cfg: GeometryMatcherConfig
    _voxel_cache: dict = field(default_factory=dict, init=False, repr=False)
    _similarity_cache: dict = field(default_factory=dict, init=False, repr=False)

    def calculate_similarity(self, mesh_a, mesh_b) -> float:
        cache_key = (id(mesh_a), id(mesh_b), self.cfg.method, self.cfg.voxel_size,
                     self.cfg.sensitivity, self.cfg.samples_count)
        cached = self._similarity_cache.get(cache_key)
        if cached is not None:
            return cached
        if self.cfg.method == GeometryMatcherMethod.Voxel:
            similarity = self.calculate_similarity_voxel(mesh_a, mesh_b)
        elif self.cfg.method == GeometryMatcherMethod.PointCloud:
            similarity = self.calculate_similarity_point_cloud(mesh_a, mesh_b)
        else:
            raise ValueError(f'Unknown geometry matching method {self.cfg.method}!')
        self._similarity_cache[cache_key] = similarity
        return similarity

    def calculate_similarity_point_cloud(self, mesh_a, mesh_b) -> float:
        """Calculates similarity between Mesh A and Mesh B.

        Algo is based on average Chamfer distance between uniformly sampled triangles.
        """
        points_a = self.sample_points_on_mesh(mesh_a)
        points_b = self.sample_points_on_mesh(mesh_b)

        cd = self.calculate_linear_chamfer_distance(points_a, points_b)

        # Use bounding box diagonal as scale
        positions_a = mesh_a.positions()
        scale = numpy.linalg.norm(positions_a.max(axis=0) - positions_a.min(axis=0))

        # Calculate similarity percentage with desired sensitivity
        similarity = max(0.0, 1 - cd / (scale * self.cfg.sensitivity)) * 100

        return similarity

    def sample_points_on_mesh(self, mesh) -> numpy.ndarray:
        """Uniformly samples points on a mesh surface using triangle areas."""
        indices = mesh.triangles()
        positions = mesh.positions()

        v0 = positions[indices[:, 0]]
        v1 = positions[indices[:, 1]]
        v2 = positions[indices[:, 2]]

        # Triangle areas
        tri_areas = 0.5 * numpy.linalg.norm(numpy.cross(v1 - v0, v2 - v0), axis=1)
        tri_probs = tri_areas / numpy.sum(tri_areas)

        # Sample triangles proportional to area
        tri_indices = numpy.random.choice(len(indices), size=self.cfg.samples_count, p=tri_probs)

        # Barycentric coordinates
        r1 = numpy.sqrt(numpy.random.rand(self.cfg.samples_count))
        r2 = numpy.random.rand(self.cfg.samples_count)
        a = 1 - r1
        b = r1 * (1 - r2)
        c = r1 * r2

        sampled_points = a[:, None] * v0[tri_indices] + b[:, None] * v1[tri_indices] + c[:, None] * v2[tri_indices]

        return sampled_points

    def calculate_similarity_voxel(self, mesh_a, mesh_b):

        points_a = self._voxel_points(mesh_a)
        points_b = self._voxel_points(mesh_b)

        if len(points_a) == 0 or len(points_b) == 0:
            return 0.0

        d_ab = self.calculate_min_distances(points_a, points_b)
        d_ba = self.calculate_min_distances(points_b, points_a)

        mean_ab = d_ab.mean()
        mean_ba = d_ba.mean()

        chamfer = 0.5 * (mean_ab + mean_ba)
        asym = abs(mean_ab - mean_ba)

        coverage_tol = float(self.cfg.voxel_size)
        coverage = min(
            numpy.mean(d_ab < coverage_tol),
            numpy.mean(d_ba < coverage_tol)
        )

        raw = chamfer + 0.5 * asym

        similarity = max(0.0, 1.0 - raw / float(self.cfg.sensitivity))

        similarity *= (0.7 + 0.3 * coverage)

        return similarity * 100.0

    def _voxel_points(self, mesh):
        key = (id(mesh), self.cfg.voxel_size)
        points = self._voxel_cache.get(key)
        if points is None:
            points = self.voxel_sample_mesh(mesh, voxel_size=self.cfg.voxel_size)
            self._voxel_cache[key] = points
        return points

    def voxel_sample_mesh(self, mesh, voxel_size=0.05):
        """
        Deterministic voxel-grid sampling of mesh surface.
        """
        positions = mesh.positions()
        indices = mesh.triangles()

        tris = positions[indices]  # (T,3,3)

        # Sample triangle centers (cheap & deterministic)
        tri_centers = tris.mean(axis=1)

        # Normalize to unit bbox
        center = tri_centers.mean(axis=0)
        tri_centers = tri_centers - center

        bbox = tri_centers.max(axis=0) - tri_centers.min(axis=0)
        scale = numpy.linalg.norm(bbox)
        if scale > 0:
            tri_centers = tri_centers / scale

        # Voxelize
        vox = numpy.floor(tri_centers / voxel_size).astype(numpy.int32)

        # Unique voxels
        _, unique_idx = numpy.unique(vox, axis=0, return_index=True)
        sampled_points = tri_centers[unique_idx]

        return sampled_points


@dataclass
class SimilarityGraph:

    data: dict

    def calculate_object_similarity(self) -> float:
        total_similarity = 0
        for lod_component, similarities in self.data.items():
            if not similarities:
                continue
            similarity = next(iter(similarities.values()))
            total_similarity += similarity
        weighted_similarity = total_similarity / len(self.data)
        return weighted_similarity


@dataclass
class LODMatcher:

    component_min_vertex_count: int
    object_hash_blacklist: str

    object_similarity_threshold: float
    component_similarity_threshold: float
    skip_components_below_similarity_threshold: bool

    geo_matcher_main_config: GeometryMatcherConfig

    geo_matcher_prefilter_config: GeometryMatcherConfig
    geo_matcher_prefilter_candidates_count: int

    vg_matcher_candidates_count: int

    geo_matcher: GeometryMatcher = field(init=False)
    vg_matcher: VertexGroupsMatcher = field(init=False)

    def __post_init__(self):
        self.geo_matcher = GeometryMatcher(self.geo_matcher_main_config)
        self.vg_matcher = VertexGroupsMatcher(candidates_count=self.vg_matcher_candidates_count)

    def find_matching_lods(self, full_object, lod_candidate_objects):
        """Returns (lod_object, {full_component: (lod_component, vg_map|None)}).

        vg_map maps full component-local VG ids -> LOD component-local VG ids;
        None means the skeletons matched 1:1 (identity).
        """
        t = time.time()

        lod_object_candidates = self.prefilter_lod_object_candidates(full_object, lod_candidate_objects)

        lod_object, hash_matched_components = self.find_lod_object_by_hash(full_object, lod_object_candidates)

        if lod_object is None:
            lod_object, object_similarity, similarity_graph = self.find_lod_object_by_similarity(full_object, lod_object_candidates)
            if object_similarity < self.object_similarity_threshold:
                raise ObjectLowSimilarityError(f"Best matching LoD for object {full_object.id} has {object_similarity:.2f}% similarity!")
        else:
            similarity_graph = self.match_components_by_similarity(full_object, lod_object, hash_matched_components)

        geo_matched_components = self.get_best_matching_components(similarity_graph)

        matched_components = hash_matched_components | geo_matched_components

        for lod_component in lod_object.components:
            if lod_component.mesh_name.startswith("Skipped"):
                continue
            if lod_component not in matched_components:
                lod_component.mesh_name = f"Skipped Component hash={lod_component.content_hash[:8]} (no matching full component found)"

        print(f'Meshes match time: {time.time()-t:.2f}s')

        vg_maps = self.remap_vertex_groups(matched_components)

        result = {}

        for lod_component, full_component in matched_components.items():
            result[full_component] = (lod_component, vg_maps.get(lod_component))

        return lod_object, result

    def prefilter_lod_object_candidates(self, full_object, lod_candidate_objects):

        candidates = []

        object_hash_blacklist = set([x for x in re.split(r"[,; ]", self.object_hash_blacklist) if x])

        for lod_object in lod_candidate_objects:
            # Never match the full object against itself (same dump may contain it).
            if lod_object.id == full_object.id:
                continue

            if lod_object.id in object_hash_blacklist:
                continue

            # Skip object with 2+ times fewer components.
            if len(lod_object.components) < len(full_object.components) / 2:
                continue

            for lod_component in lod_object.components:

                if lod_component.vertex_count < self.component_min_vertex_count:
                    lod_component.mesh_name = f"Skipped Component hash={lod_component.content_hash[:8]} (vertex count below minimum)"
                    continue

            candidates.append(lod_object)

        return candidates

    def remap_vertex_groups(self, matched_components):

        print(f"Remapping Vertex Groups for {len(matched_components)} components...")

        t = time.time()

        vg_maps = {}

        for lod_component, full_component in matched_components.items():
            vg_map = self.vg_matcher.match_vertex_groups(
                full_component.mesh,
                lod_component.mesh,
            )

            remapped = sum(1 for k, v in vg_map.items() if k != v)

            component_desc = f"{full_component.mesh_name} LoD (full={full_component.content_hash[:8]}, lod={lod_component.content_hash[:8]})"

            if remapped > 0:
                vg_maps[lod_component] = vg_map
                print(f"{component_desc}: {remapped} out of used {len(vg_map) or 1} VGs are different (LoD mesh uses simplified skeleton)")
            else:
                print(f"{component_desc}: all {len(vg_map)} VGs are identical (LoD mesh uses full skeleton)")

        print(f"Vertex Groups match time: {time.time() - t:.03f}s")

        return vg_maps

    def find_lod_object_by_hash(self, full_object, lod_object_candidates):

        full_by_hash = {component.content_hash: component for component in full_object.components}

        lods = {}

        for lod_object in lod_object_candidates:
            matches = {}

            for lod_component in lod_object.components:
                if lod_component.mesh_name.startswith("Skipped"):
                    continue

                full_component = full_by_hash.get(lod_component.content_hash)

                if full_component is None:
                    continue

                matches[lod_component] = full_component

                similarity = self.geo_matcher.calculate_similarity(full_component.mesh, lod_component.mesh)

                lod_component.mesh_name = self.make_matched_mesh_name(full_component, lod_component, "hash")

                print(f"Match by hash (mesh similarity: {similarity:.2f}%): {full_component.mesh_name} == {lod_component.mesh_name} ")

            if matches:
                lods[lod_object] = matches

        if not lods:
            return None, {}

        matched_lod_object = max(
            lods,
            key=lambda obj: len(lods[obj]),
        )

        return matched_lod_object, lods[matched_lod_object]

    def find_lod_object_by_similarity(self, full_object, lod_object_candidates):

        if not lod_object_candidates:
            raise ObjectLowSimilarityError(
                f"No LoD candidate objects survived prefiltering for object {full_object.id}!")

        lod_object_similarity_graphs = {}
        lod_object_similarities = {}

        for lod_object in lod_object_candidates:
            similarity_graph = self.calculate_similarity_graph(full_object.components, lod_object.components)
            lod_object_similarity_graphs[lod_object] = similarity_graph
            lod_object_similarities[lod_object] = similarity_graph.calculate_object_similarity()

        matched_lod_object = max(
            lod_object_similarity_graphs,
            key=lambda obj: lod_object_similarities[obj],
        )

        object_similarity = lod_object_similarities[matched_lod_object]
        similarity_graph = lod_object_similarity_graphs[matched_lod_object]

        return matched_lod_object, object_similarity, similarity_graph

    def calculate_component_similarities(self, component, candidates):
        mesh_similarities = {}

        for candidate_component in candidates:
            similarity = self.geo_matcher.calculate_similarity(candidate_component.mesh, component.mesh)
            mesh_similarities[candidate_component] = similarity

        mesh_similarities = dict(
            sorted(mesh_similarities.items(), key=itemgetter(1), reverse=True)
        )

        return mesh_similarities

    def calculate_similarity_graph(self, full_components, lod_components) -> SimilarityGraph:

        similarities = {}

        for lod_component in lod_components:
            if lod_component.mesh_name.startswith("Skipped"):
                continue

            self.geo_matcher.cfg = self.geo_matcher_prefilter_config

            valid_full_components = [
                full_component for full_component in full_components
                if full_component.vertex_count >= lod_component.vertex_count
            ]

            prefilter_similarities = self.calculate_component_similarities(lod_component, valid_full_components)

            self.geo_matcher.cfg = self.geo_matcher_main_config

            prefiltered_full_components = list(prefilter_similarities.keys())[:self.geo_matcher_prefilter_candidates_count]

            similarities[lod_component] = self.calculate_component_similarities(lod_component, prefiltered_full_components)

        return SimilarityGraph(data=similarities)

    def match_components_by_similarity(self, full_object, lod_object, matched_lod_to_full_components) -> SimilarityGraph:

        # Exclude already matched full components from matching.
        full_components = [
            full_component for full_component in full_object.components
            if full_component not in matched_lod_to_full_components.values()
        ]

        # Exclude already matched lod components from matching.
        lod_components = [
            lod_component for lod_component in lod_object.components
            if lod_component not in matched_lod_to_full_components.keys()
        ]

        similarity_graph = self.calculate_similarity_graph(full_components, lod_components)

        return similarity_graph

    def make_matched_mesh_name(self, full_component, lod_component, similarity) -> str:
        match_type = f"{similarity:.2f}%" if isinstance(similarity, float) else similarity
        mesh_name = f"{full_component.mesh_name} full={full_component.content_hash[:8]} lod={lod_component.content_hash[:8]} match={match_type}"
        if lod_component.content_hash == full_component.content_hash:
            mesh_name += " (full mesh)"
        else:
            mesh_name += " (simplified mesh)"
        return mesh_name

    def get_best_matching_components(self, similarity_graph: SimilarityGraph):
        result = {}
        for lod_component, similarities in similarity_graph.data.items():
            if not similarities:
                continue
            full_component, similarity = next(iter(similarities.items()))

            if similarity < self.object_similarity_threshold:
                if self.skip_components_below_similarity_threshold:
                    print(f"Skipped match by geometry below {self.object_similarity_threshold:.2f}% threshold (mesh similarity: {similarity:.2f}%): {full_component.mesh_name} == {lod_component.mesh_name} ")
                    lod_component.mesh_name = f"Skipped Component hash={lod_component.content_hash[:8]} (mesh similarity {similarity:.2f}% is below configured {self.object_similarity_threshold:.2f}% threshold)"
                    continue
                raise ComponentLowSimilarityError(f"Best matching LoD for {full_component.mesh_name} has {similarity:.2f}% similarity!")

            lod_component.mesh_name = self.make_matched_mesh_name(full_component, lod_component, similarity)

            result[lod_component] = full_component

            print(f"Match by geometry (mesh similarity: {similarity:.2f}%): {full_component.mesh_name} == {lod_component.mesh_name} ")

        return result
