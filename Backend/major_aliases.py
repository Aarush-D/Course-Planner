"""Major-name/synonym -> department code lookup used by app.py's chat
prompt parsing (e.g. "I'm a Computer Science major" -> CMPSC).

Split out of app.py verbatim (pure data + its loader, no Flask dependency)
as part of a size-reduction refactor -- see the code-review note that
flagged app.py/planner_engine.py for splitting.
"""
from __future__ import annotations

import json
import os
from typing import Dict

_MAJOR_ALIASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "major_aliases.json")


def _load_major_aliases() -> Dict[str, str]:
    """Major-name/synonym -> department code, e.g. 'COMPUTER SCIENCE' -> 'CMPSC'.
    Lives in data/major_aliases.json (same pattern as degree plans/catalogs
    under Backend/degree_plans and Backend/catalogs) so adding or fixing an
    alias is a data edit, not a code change + redeploy."""
    with open(_MAJOR_ALIASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_MAJOR_ALIASES: Dict[str, str] = _load_major_aliases()
