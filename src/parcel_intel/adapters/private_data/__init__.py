"""Adapters de la sincronización de bajada de los datos privados (historia #295,
ADR 0059): el almacén sobre el CLI `gh` y la zona local `data/`."""

from parcel_intel.adapters.private_data.gh_store import (
    CommandResult,
    CommandRunner,
    GhCliPrivateDataStore,
    run_command,
)
from parcel_intel.adapters.private_data.local_area import LocalPrivateDataArea

__all__ = [
    "CommandResult",
    "CommandRunner",
    "GhCliPrivateDataStore",
    "LocalPrivateDataArea",
    "run_command",
]
