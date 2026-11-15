"""Puts OpenRAC's extractor (and its shared tools) on the import path: the level editor's tools
read discs and levels with it. Found by OPENRAC_ROOT, which the launcher sets, or else an OpenRAC
checkout beside this repository (OpenRAC next to OpenRAC-Editor). The extractor states the version of what it offers
in extractor_api.json; these tools refuse one they were not made for."""

import json
import os
from pathlib import Path
import sys

# The extractor_api.json versions these tools work with.
API_VERSIONS = (1,)


def find() -> Path:
    given = os.environ.get("OPENRAC_ROOT")
    candidates = [Path(given)] if given else []
    here = Path(__file__).resolve()
    candidates += [here.parents[3] / name for name in ("OpenRAC", "openrac")]
    candidates += [parent for parent in here.parents if (parent / "extractor" / "extractor_api.json").is_file()]
    for root in candidates:
        if (root / "extractor" / "extractor_api.json").is_file():
            return root.resolve()
    raise SystemExit("OpenRAC not found: set OPENRAC_ROOT to an OpenRAC checkout or install"
                     + (f" ({given} has no extractor/extractor_api.json)" if given else ""))


ROOT = find()
API = json.loads((ROOT / "extractor" / "extractor_api.json").read_text())
if API.get("version") not in API_VERSIONS:
    raise SystemExit(f"OpenRAC at {ROOT} offers extractor API {API.get('version')}; these tools need "
                     f"{' or '.join(map(str, API_VERSIONS))}: update OpenRAC or OpenRAC Editor")
# The extractor first: OpenRAC's tools/ has a port.py of its own (the extractor's own scripts put
# tools/ just after themselves, as extract.py does).
for path in (ROOT / "tools", ROOT / "extractor"):
    if str(path) in sys.path:
        sys.path.remove(str(path))
    sys.path.insert(0, str(path))
