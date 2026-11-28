"""Port del almacén remoto de datos privados (historia #295, ADR 0055 §3 y ADR 0059)."""

from pathlib import Path
from typing import Protocol

from parcel_intel.domain.private_catalog import CatalogDocument, StorageLocation


class PrivateDataStore(Protocol):
    def read_catalogs(self) -> tuple[CatalogDocument, ...]:
        """Todos los `catalogo/*.json`, ordenados por nombre.

        `PrivateDataUnavailable` si no hay acceso; `PrivateDataStoreError` si falla otra
        cosa; `PrivateCatalogError` si un nombre de fichero no es válido."""
        ...

    def download(self, location: StorageLocation, target: Path) -> None:
        """Escribe el objeto en `target`, que no existe. `PrivateDataStoreError` si falla
        (y entonces no deja `target` a medias)."""
        ...
