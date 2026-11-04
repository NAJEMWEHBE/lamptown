"""The ``static`` collector: returns what the source table says. For tests, demos and fixed signs.

```toml
[[building.source]]
kind = "static"
state = "waiting"
label = "Two things to sign"
needs = [{ text = "Sign the form" }]
```

Every key except ``kind``, ``name`` and the reserved ``_`` keys is passed through as room data;
``state`` defaults to ``ok``. It reads nothing from disk.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

SKIP = {"kind", "name", "collector"}


def collect(paths: dict, now: dt.datetime) -> dict[str, Any]:
    out = {k: v for k, v in paths.items() if k not in SKIP and not k.startswith("_")}
    out.setdefault("state", "ok")
    return out
