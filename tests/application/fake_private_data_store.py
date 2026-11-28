"""Dobles del almacén de datos privados (historia #295), sobre la fixture SINTÉTICA de
`tests/fixtures/private_data/`. Ninguno lanza `gh`.

- `FakePrivateDataStore` implementa el port: registra cada llamada y puede fallar o
  devolver otros bytes por objeto.
- `FakeGhRunner` es un ejecutor falso para el adapter real `GhCliPrivateDataStore`: responde
  a las tres formas de llamada del ADR 0059 §3 con la fixture y registra los argumentos.
"""

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote

from parcel_intel.adapters.private_data import CommandResult
from parcel_intel.adapters.private_data.gh_store import RAW_ACCEPT
from parcel_intel.domain.private_catalog import (
    CatalogDocument,
    GitFile,
    ReleaseAsset,
    StorageLocation,
)
from parcel_intel.ports.errors import PrivateDataStoreError, PrivateDataUnavailable
from tests.fixtures.private_data.generar import ASSETS_DIR, REPO_DIR

LISTING_KEY = "catalogo"


def location_key(location: StorageLocation) -> str:
    if isinstance(location, GitFile):
        return location.path
    return f"{location.tag}/{location.asset}"


def asset_key(tag: str, asset: str) -> str:
    return location_key(ReleaseAsset(tag=tag, asset=asset))


def catalog_documents(repo_dir: Path = REPO_DIR) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in (repo_dir / "catalogo").glob("*.json")}


@dataclass
class FakePrivateDataStore:
    repo_dir: Path = REPO_DIR
    assets_dir: Path = ASSETS_DIR
    unavailable: bool = False
    catalog_error: Exception | None = None
    catalog_overrides: dict[str, bytes] = field(default_factory=dict)
    replaced_bytes: dict[str, bytes] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)

    def read_catalogs(self) -> tuple[CatalogDocument, ...]:
        self.calls.append("read_catalogs")
        if self.unavailable:
            raise PrivateDataUnavailable()
        if self.catalog_error is not None:
            raise self.catalog_error
        documents = catalog_documents(self.repo_dir)
        documents.update(self.catalog_overrides)
        return tuple(
            CatalogDocument(name=name, content=content)
            for name, content in sorted(documents.items())
        )

    def download(self, location: StorageLocation, target: Path) -> None:
        key = location_key(location)
        self.calls.append(f"download {key}")
        self.downloads.append(key)
        assert not target.exists(), "el destino de una descarga no debe existir"
        if key in self.failing:
            raise PrivateDataStoreError("fallo simulado")
        if key in self.replaced_bytes:
            target.write_bytes(self.replaced_bytes[key])
            return
        source = (
            self.repo_dir / location.path
            if isinstance(location, GitFile)
            else self.assets_dir / location.tag / location.asset
        )
        if not source.is_file():
            raise PrivateDataStoreError("objeto inexistente")
        shutil.copyfile(source, target)


@dataclass(frozen=True)
class RunnerCall:
    args: tuple[str, ...]
    timeout: float | None
    has_stdout: bool


@dataclass
class FakeGhRunner:
    """Imita `gh` sobre la fixture. `failures` y `raises` van por clave: `catalogo` (el
    listado), la ruta de un fichero de git o `<etiqueta>/<adjunto>`."""

    repo: str
    repo_dir: Path = REPO_DIR
    assets_dir: Path = ASSETS_DIR
    failures: dict[str, CommandResult] = field(default_factory=dict)
    raises: dict[str, BaseException] = field(default_factory=dict)
    extra_listing: list[dict[str, object]] = field(default_factory=list)
    calls: list[RunnerCall] = field(default_factory=list)

    def __call__(
        self, args: Sequence[str], *, timeout: float | None, stdout: BinaryIO | None
    ) -> CommandResult:
        argv = tuple(args)
        self.calls.append(RunnerCall(argv, timeout, stdout is not None))
        if argv[:4] == ("gh", "api", "--method", "GET"):
            return self._api(argv, stdout)
        if argv[:3] == ("gh", "release", "download"):
            return self._release(argv)
        raise AssertionError(f"llamada inesperada a gh: {argv}")

    def _check(self, key: str) -> CommandResult | None:
        if key in self.raises:
            raise self.raises[key]
        return self.failures.get(key)

    def _api(self, argv: tuple[str, ...], stdout: BinaryIO | None) -> CommandResult:
        prefix = f"repos/{self.repo}/contents/"
        assert argv[-1].startswith(prefix)
        path = unquote(argv[-1][len(prefix) :])
        raw = argv[4:6] == ("-H", RAW_ACCEPT)
        failure = self._check(path)
        if failure is not None:
            return failure
        if not raw:
            assert path == LISTING_KEY
            listing: list[dict[str, object]] = [
                {"name": p.name, "path": f"catalogo/{p.name}", "type": "file"}
                for p in sorted((self.repo_dir / "catalogo").glob("*.json"))
            ]
            listing.append({"name": "subdirectorio", "path": "catalogo/sub", "type": "dir"})
            listing.extend(self.extra_listing)
            data = json.dumps(listing).encode()
        else:
            source = self.repo_dir / path
            if not source.is_file():
                return CommandResult(1, b"", b"gh: Not Found (HTTP 404)")
            data = source.read_bytes()
        if stdout is not None:
            stdout.write(data)
            return CommandResult(0, b"", b"")
        return CommandResult(0, data, b"")

    def _release(self, argv: tuple[str, ...]) -> CommandResult:
        assert argv[4] == "--repo" and argv[6] == "--pattern" and argv[8] == "--output"
        tag, asset, target = argv[3], argv[7], Path(argv[9])
        failure = self._check(f"{tag}/{asset}")
        if failure is not None:
            return failure
        source = self.assets_dir / tag / asset
        if not source.is_file():
            return CommandResult(1, b"", b"release not found")
        shutil.copyfile(source, target)
        return CommandResult(0, b"", b"")
