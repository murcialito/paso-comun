"""Almacén de datos privados sobre el CLI `gh` del usuario (historia #295, ADR 0059 §3).

Solo tres formas de llamada: el listado de `catalogo/` con `gh api`, el contenido de un
fichero de git con `gh api` y la cabecera `Accept` de contenido en bruto, y
`gh release download` de un adjunto. Siempre sin shell y heredando el entorno tal cual:
`gh` se autentica con su propia sesión y este módulo no lee ni pasa ninguna credencial.

La salida de error de `gh` se captura siempre y solo sirve para clasificar el fallo:
nunca se imprime ni entra en un mensaje, porque puede llevar el nombre del repo.
"""

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import quote

from parcel_intel.domain.private_catalog import (
    CATALOG_FILE_RE,
    CatalogDocument,
    GitFile,
    PrivateCatalogError,
    ReleaseAsset,
    StorageLocation,
)
from parcel_intel.ports.errors import PrivateDataStoreError, PrivateDataUnavailable

GH = "gh"
API_TIMEOUT_S = 120.0
RAW_ACCEPT = "Accept: application/vnd.github.raw+json"
NO_ACCESS_MARKERS = ("HTTP 401", "HTTP 403", "HTTP 404", "gh auth login")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    def __call__(
        self, args: Sequence[str], *, timeout: float | None, stdout: BinaryIO | None
    ) -> CommandResult: ...


def run_command(
    args: Sequence[str], *, timeout: float | None, stdout: BinaryIO | None
) -> CommandResult:
    """Lanza `args` sin shell, con la entrada cerrada y la salida de error capturada."""
    completed = subprocess.run(
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or b"",
        stderr=completed.stderr or b"",
    )


class GhCliPrivateDataStore:
    def __init__(
        self,
        repo: str,
        runner: CommandRunner = run_command,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._repo = repo
        self._runner = runner
        self._which = which

    # --- catálogos -----------------------------------------------------------------

    def _contents_args(self, path: str, *, raw: bool) -> list[str]:
        args = [GH, "api", "--method", "GET"]
        if raw:
            args += ["-H", RAW_ACCEPT]
        return [*args, f"repos/{self._repo}/contents/{quote(path, safe='/')}"]

    def _read_for_catalogs(self, args: Sequence[str]) -> bytes:
        """Una llamada al leer los catálogos: aquí se distingue «sin acceso» de un fallo."""
        try:
            result = self._runner(args, timeout=API_TIMEOUT_S, stdout=None)
        except subprocess.TimeoutExpired:
            raise PrivateDataStoreError("tiempo agotado al leer los catálogos") from None
        except OSError:
            raise PrivateDataUnavailable() from None
        if result.returncode != 0:
            errors = result.stderr.decode("utf-8", errors="replace")
            if any(marker in errors for marker in NO_ACCESS_MARKERS):
                raise PrivateDataUnavailable()
            raise PrivateDataStoreError("no se pudieron leer los catálogos")
        return result.stdout

    def read_catalogs(self) -> tuple[CatalogDocument, ...]:
        if self._which(GH) is None:
            raise PrivateDataUnavailable()
        listing_bytes = self._read_for_catalogs(self._contents_args("catalogo", raw=False))
        try:
            listing = json.loads(listing_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PrivateDataStoreError("listado de catálogos ilegible") from None
        if not isinstance(listing, list):
            raise PrivateDataStoreError("listado de catálogos ilegible")
        names: list[str] = []
        for item in listing:
            if not isinstance(item, dict) or item.get("type") != "file":
                continue
            name = item.get("name")
            if not isinstance(name, str) or not CATALOG_FILE_RE.fullmatch(name):
                raise PrivateCatalogError("catalogo/: un fichero con nombre no válido")
            names.append(name)
        return tuple(
            CatalogDocument(
                name=name,
                content=self._read_for_catalogs(self._contents_args(f"catalogo/{name}", raw=True)),
            )
            for name in sorted(names)
        )

    # --- descargas -----------------------------------------------------------------

    def download(self, location: StorageLocation, target: Path) -> None:
        try:
            if isinstance(location, GitFile):
                self._download_git(location, target)
            else:
                self._download_asset(location, target)
        except PrivateDataStoreError:
            target.unlink(missing_ok=True)
            raise
        except (OSError, subprocess.TimeoutExpired):
            target.unlink(missing_ok=True)
            raise PrivateDataStoreError("no se pudo descargar") from None

    def _download_git(self, location: GitFile, target: Path) -> None:
        args = self._contents_args(location.path, raw=True)
        with target.open("xb") as handle:
            result = self._runner(args, timeout=API_TIMEOUT_S, stdout=handle)
        if result.returncode != 0:
            raise PrivateDataStoreError("no se pudo descargar un fichero de git")

    def _download_asset(self, location: ReleaseAsset, target: Path) -> None:
        args = [
            GH,
            "release",
            "download",
            location.tag,
            "--repo",
            self._repo,
            "--pattern",
            location.asset,
            "--output",
            str(target),
        ]
        result = self._runner(args, timeout=None, stdout=None)
        if result.returncode != 0 or not target.is_file():
            raise PrivateDataStoreError("no se pudo descargar un adjunto")
