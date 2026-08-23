"""Velo Tools version metadata — single source for the pre-release / build marker.

The released version *tuple* lives as a literal in ``bl_info["version"]``
(``velo_tools/__init__.py``) because Blender statically parses ``bl_info`` before
importing the package, so it cannot be a computed value. This module only carries
the development markers that ``bl_info`` cannot hold:

- ``PRERELEASE``: ``None`` on a tagged release; ``"dev"`` while developing toward the
  next release (the displayed version then reads e.g. ``v1.2.8-dev``).
- ``BUILD``: optional build stamp (e.g. a ``git describe`` string) for a throwaway
  dev build handed to a tester; left empty on a release.

Versioning policy (see CLAUDE.md): semantic versioning, bumped by a human at release
time (patch / minor / major by the nature of the change). No auto-increment. During
development the iteration is tracked by git, not by a hand-maintained counter.
"""

PRERELEASE = None   # None on a release; "dev" during development toward the next version.
BUILD = ""          # optional build id (e.g. a git describe string); empty on a release.
