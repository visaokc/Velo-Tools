"""Short-lived caches shared by the stages of one synchronous export."""
from __future__ import annotations

from array import array
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from pathlib import Path

from . import model

_CURRENT = ContextVar('material_export_cache', default=None)


def current():
    return _CURRENT.get()


def material_indices(mesh):
    polygons = mesh.polygons
    if hasattr(polygons, 'foreach_get'):
        values = array('i', [0]) * len(polygons)
        polygons.foreach_get('material_index', values)
        return values
    return array('i', (polygon.material_index for polygon in polygons))


class ExportCache:
    def __init__(self):
        self.snapshots = {}
        self.catalogs = {}
        self.resolved = {}
        self.files = {}
        self.packed = {}
        self.digests = {}
        self.stats = {'source_snapshots': 0, 'catalogs': 0, 'file_reads': 0,
                      'file_hits': 0, 'material_hits': 0, 'hashes': 0}

    def evidence(self, folder, component):
        path = os.path.normcase(os.path.abspath(folder))
        if path not in self.snapshots:
            self.snapshots[path] = model.source_snapshot(folder)
            self.stats['source_snapshots'] += 1
        key = path, component
        if key not in self.catalogs:
            self.catalogs[key] = model.read_evidence(folder, component, snapshot=self.snapshots[path])
            self.stats['catalogs'] += 1
        return self.catalogs[key]

    def read(self, path):
        path = Path(path)
        key = os.path.normcase(os.path.abspath(path))
        stat = path.stat()
        stamp = stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        previous = self.files.get(key)
        if previous is not None and previous[0] == stamp:
            self.stats['file_hits'] += 1
            return previous[1]
        content = path.read_bytes()
        self.files[key] = stamp, content
        self.stats['file_reads'] += 1
        return content

    def packed_bytes(self, image):
        key = image.as_pointer(), image.packed_file.as_pointer()
        if key not in self.packed:
            self.packed[key] = bytes(image.packed_file.data)
        return self.packed[key]

    def signature(self, payload):
        _name, filename, content = payload
        key = id(content)
        if key not in self.digests:
            # Retain bytes alongside their ID to prevent allocator-ID reuse.
            self.digests[key] = content, hashlib.sha256(content).digest()
            self.stats['hashes'] += 1
        return filename, self.digests[key][1]


@contextmanager
def scope():
    cache = ExportCache()
    token = _CURRENT.set(cache)
    try:
        yield cache
    finally:
        _CURRENT.reset(token)
