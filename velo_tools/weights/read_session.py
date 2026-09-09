from contextlib import contextmanager


_active = None


@contextmanager
def weight_read_session():
    """Share sparse membership reads only within a synchronous read-only phase."""
    global _active
    owner = _active is None
    if owner:
        _active = {}
    try:
        yield
    finally:
        if owner:
            _active = None


def group_memberships(obj):
    if _active is None:
        return None
    key = obj.as_pointer() if hasattr(obj, "as_pointer") else id(obj)
    if key not in _active:
        groups = {}
        for vertex in obj.data.vertices:
            for item in vertex.groups:
                groups.setdefault(item.group, []).append((vertex.index, float(item.weight)))
        _active[key] = groups
    return _active[key]
