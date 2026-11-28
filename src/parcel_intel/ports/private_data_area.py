"""Port de la zona local `data/` donde se sincronizan los datos privados (historia #295,
ADR 0059). Inmutable: nunca sobrescribe."""

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from parcel_intel.domain.private_catalog import LocalFileState


class PrivateDataArea(Protocol):
    def staging(self) -> AbstractContextManager[Path]:
        """Directorio temporal nuevo en el mismo sistema de ficheros que `data/`; se borra
        entero al salir."""
        ...

    def file_state(self, path: Path) -> LocalFileState:
        """sha256 (por bloques) y tamaño de un fichero de la zona de preparación."""
        ...

    def read_small(self, path: Path) -> bytes:
        """Bytes de un fichero pequeño de la zona de preparación (una procedencia).
        `PrivateDataStoreError` si pasa del tope."""
        ...

    def existing_state(self, destination: str) -> LocalFileState | None:
        """Estado de `data/<destination>`, o `None` si no existe.
        `PrivateDataConflictError` si es un enlace simbólico, no es un fichero o sale
        de `data/`."""
        ...

    def place(self, staged: Path, destination: str) -> None:
        """Mueve sin sobrescribir; `PrivateDataConflictError` si ya existe."""
        ...

    def read_record(self, entry_id: str) -> bytes | None:
        """Registro local `data/privados/<id>.json`, o `None`."""
        ...

    def write_record(self, entry_id: str, content: bytes) -> None:
        """Atómico y sin sobrescribir un registro distinto (`PrivateDataConflictError`)."""
        ...
