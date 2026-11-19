"""Expose the source trees of both apps to the tests.

This is the only place in the repository where ``apps/api/src`` and
``apps/rag/src`` may share one ``sys.path``: each app is an autonomous project
and neither may import the other. Integration is verified here, from outside.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

for app in ("api", "rag"):
    src = str(REPO / "apps" / app / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
