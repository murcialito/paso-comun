"""Zona local `data/` de los datos privados (historia #295, ADR 0059 §6 y §8).

Mismo contrato que la caché cruda (`CachedRawFetcher`): inmutable, sha256 comprobado y
escritura atómica. Todo se descarga a `data/privados/.preparacion/<temporal>` (mismo
sistema de ficheros) y solo se coloca, con `os.link`, cuando su tamaño y su sha256 son los
del catálogo; `os.link` falla si el destino ya existe, así que nunca se sobrescribe.
"""

import hashlib
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from parcel_intel.domain.private_catalog import LocalFileState
from parcel_intel.ports.errors import PrivateDataConflictError, PrivateDataStoreError

PRIVATE_DIR = "privados"
STAGING_DIR = ".preparacion"
SMALL_FILE_LIMIT_BYTES = 1_048_576


class LocalPrivateDataArea:
    def __init__(self, data_root: Path) -> None:
        self._root = data_root

    @property
    def _private(self) -> Path:
        return self._root / PRIVATE_DIR

    @contextmanager
    def staging(self) -> Iterator[Path]:
        base = self._private / STAGING_DIR
        base.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(dir=base))
        try:
            yield directory
        finally:
            shutil.rmtree(directory, ignore_errors=True)
            try:
                base.rmdir()
            except OSError:
                pass

    def file_state(self, path: Path) -> LocalFileState:
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        return LocalFileState(sha256=digest, size_bytes=path.stat().st_size)

    def read_small(self, path: Path) -> bytes:
        if path.stat().st_size > SMALL_FILE_LIMIT_BYTES:
            raise PrivateDataStoreError("procedencia demasiado grande")
        return path.read_bytes()

    def _resolve(self, destination: str) -> Path:
        """`data/<destination>`, comprobando que sigue dentro de `data/` (defensa además
        de `safe_destination`) y que nada en el camino es un enlace simbólico."""
        path = self._root / destination
        root = self._root.resolve()
        if not path.resolve().is_relative_to(root):
            raise PrivateDataConflictError("la ruta sale de data/")
        current = self._root
        for part in Path(destination).parts:
            current = current / part
            if current.is_symlink():
                raise PrivateDataConflictError("enlace simbólico en el destino")
        return path

    def existing_state(self, destination: str) -> LocalFileState | None:
        path = self._resolve(destination)
        if not path.exists():
            return None
        if not path.is_file():
            raise PrivateDataConflictError("el destino no es un fichero")
        return self.file_state(path)

    def place(self, staged: Path, destination: str) -> None:
        target = self._resolve(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        _link_without_overwrite(staged, target)

    def _record_path(self, entry_id: str) -> Path:
        return self._private / f"{entry_id}.json"

    def read_record(self, entry_id: str) -> bytes | None:
        path = self._record_path(entry_id)
        if path.is_symlink():
            raise PrivateDataConflictError("enlace simbólico en el registro")
        if not path.exists():
            return None
        return path.read_bytes()

    def write_record(self, entry_id: str, content: bytes) -> None:
        path = self._record_path(entry_id)
        existing = self.read_record(entry_id)
        if existing is not None:
            if existing == content:
                return
            raise PrivateDataConflictError("ya hay otro registro de esta entrada")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(content)
            _link_without_overwrite(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _link_without_overwrite(source: Path, target: Path) -> None:
    """Enlace físico que falla si `target` existe. Sin enlaces físicos (p. ej. exFAT),
    copia a un temporal hermano y lo renombra tras comprobar que el destino no existe."""
    try:
        os.link(source, target)
        return
    except FileExistsError:
        raise PrivateDataConflictError("el destino ya existe") from None
    except OSError:
        pass
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copyfile(source, temporary)
        if target.exists() or target.is_symlink():
            raise PrivateDataConflictError("el destino ya existe")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
