"""Child-only bootstrap; preserve an existing startup customization and stay opaque."""

import importlib.machinery
import importlib.util
import json
import os
import sys

context = os.environ.pop("ANAXIGRAPH_CHILD", None)  # Descendants cannot reuse this receipt.
folder = os.path.dirname(__file__)
paths = [path for path in sys.path if os.path.realpath(path) != os.path.realpath(folder)]
spec = importlib.machinery.PathFinder.find_spec("sitecustomize", paths)
if spec and spec.loader:
    original = importlib.util.module_from_spec(spec)
    sys.modules["sitecustomize"] = original
    spec.loader.exec_module(original)
elif context:
    from anaxigraph_children import record_child  # type: ignore[import-not-found]

    record_child(json.loads(context))
