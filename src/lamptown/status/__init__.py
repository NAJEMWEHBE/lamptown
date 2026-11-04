"""The ``agent-status/1`` standard: one small JSON file per agent, task or job.

Anything that can write a JSON file can light a Lamptown lamp. This package is the
reference implementation: schema check, atomic writer, a context manager, the
``lamptown status`` command, provider adapters and the ``status_dir`` collector.

    from lamptown.status import agent_status

    with agent_status("nightly-report", "Nightly report") as s:
        s.progress(3, 8)
"""

from lamptown.status.context import StatusHandle, agent_status
from lamptown.status.schema import load_schema, parse_time, validate
from lamptown.status.store import (
    DEFAULT_STALE_AFTER,
    ENV_DIR,
    KINDS,
    MAX_BYTES,
    SPEC,
    STATES,
    StatusError,
    default_dir,
    read_status,
    status_path,
    update_status,
    write_status,
)

__all__ = [
    "DEFAULT_STALE_AFTER",
    "ENV_DIR",
    "KINDS",
    "MAX_BYTES",
    "SPEC",
    "STATES",
    "StatusError",
    "StatusHandle",
    "agent_status",
    "default_dir",
    "load_schema",
    "parse_time",
    "read_status",
    "status_path",
    "update_status",
    "validate",
    "write_status",
]
