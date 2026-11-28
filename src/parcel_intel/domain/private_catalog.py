"""Catálogo del repo privado de datos internos (ADR 0055 §3) y resultado de su
sincronización de bajada (historia #295, ADR 0059).

Solo stdlib. Todo se valida antes de tocar nada: un catálogo con una entrada mal
formada, una ruta que sale de `data/`, una clave de titularidad o un valor con forma de
referencia catastral invalidan el conjunto. Los mensajes de error nombran el fichero de
catálogo, la posición y el campo; **nunca el valor rechazado** (lección de #168): las
claves desconocidas se cuentan sin nombrarlas.
"""

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from parcel_intel.domain.published_discrepancy import CADASTRAL_REFERENCE_RE

PRIVATE_CATALOG_SCHEMA: Final = 1
ENTRY_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
"""Identificador de entrada y etiqueta de release (ADR 0055 §3)."""
SOURCE_RE: Final = re.compile(r"^[a-z0-9_]{1,64}$")
CATALOG_FILE_RE: Final = re.compile(r"^[a-z0-9_]{1,64}\.json$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
MUNICIPALITY_CODE_RE: Final = re.compile(r"^\d{5}$")
ASSET_EXTENSION_RE: Final = re.compile(r"^[a-z0-9]{1,10}(\.[a-z0-9]{1,10})?$")
PART_ASSET_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,150}$")
ISO_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DOCUMENT_KEYS: Final = frozenset({"esquema", "fuente", "entradas"})
REQUIRED_ENTRY_KEYS: Final = frozenset(
    {
        "id",
        "fuente",
        "url",
        "fecha_descarga",
        "licencia",
        "publicable",
        "restricciones_uso",
        "sha256",
        "tamano_bytes",
        "almacen",
        "formato",
        "destino",
        "procedencia",
    }
)
OPTIONAL_ENTRY_KEYS: Final = frozenset({"producto", "municipio_ine", "partes", "sustituido_por"})
RELEASE_KEYS: Final = frozenset({"tipo", "etiqueta", "adjunto"})
GIT_KEYS: Final = frozenset({"tipo", "ruta"})
PART_KEYS: Final = frozenset({"adjunto", "sha256", "tamano_bytes"})
OWNERSHIP_KEY_PARTS: Final = (
    "titular",
    "propiet",
    "duen",
    "owner",
    "nif",
    "dni",
    "nombre_persona",
    "catastral",
    "refcat",
)
DESTINATION_ROOTS: Final = ("raw", "derived")
GIT_STORAGE_ROOT: Final = "derivados"
PROVENANCE_ROOT: Final = "procedencia"

_KNOWN_KEYS: Final = (
    DOCUMENT_KEYS | REQUIRED_ENTRY_KEYS | OPTIONAL_ENTRY_KEYS | RELEASE_KEYS | GIT_KEYS | PART_KEYS
)


class PrivateCatalogError(Exception):
    """Catálogo o procedencia no válidos. El mensaje nombra fichero, posición y campo;
    nunca el valor."""


@dataclass(frozen=True)
class CatalogDocument:
    """Lo que devuelve el almacén: el nombre del fichero de `catalogo/` y sus bytes."""

    name: str
    content: bytes


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    asset: str


@dataclass(frozen=True)
class GitFile:
    """Ruta relativa a la raíz del repo privado, ya validada con `safe_repo_path`."""

    path: str


StorageLocation = ReleaseAsset | GitFile


@dataclass(frozen=True)
class AssetPart:
    asset: str
    sha256: str
    size_bytes: int


class EntryLayout(StrEnum):
    FILE = "fichero"
    TAR = "tar"


@dataclass(frozen=True)
class PrivateCatalogEntry:
    entry_id: str
    source: str
    url: str
    downloaded_on: date
    license: str
    use_restrictions: tuple[str, ...]
    sha256: str
    size_bytes: int
    storage: StorageLocation
    layout: EntryLayout
    destination: str
    provenance: GitFile
    product: str | None = None
    municipality_code: str | None = None
    parts: tuple[AssetPart, ...] = ()
    replaced_by: str | None = None


@dataclass(frozen=True)
class PrivateCatalog:
    file_name: str
    source: str
    entries: tuple[PrivateCatalogEntry, ...]


class SyncOutcome(StrEnum):
    DOWNLOADED = "descargado"
    PRESENT = "ya estaba"
    SKIPPED = "saltado"
    FAILED = "fallo"


@dataclass(frozen=True)
class EntrySyncResult:
    entry_id: str
    outcome: SyncOutcome
    reason: str | None = None
    """Genérico: nunca el `stderr` de `gh` ni rutas absolutas."""


@dataclass(frozen=True)
class LocalFileState:
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SyncSummary:
    results: tuple[EntrySyncResult, ...]

    def _count(self, outcome: SyncOutcome) -> int:
        return sum(1 for result in self.results if result.outcome is outcome)

    @property
    def downloaded(self) -> int:
        return self._count(SyncOutcome.DOWNLOADED)

    @property
    def present(self) -> int:
        return self._count(SyncOutcome.PRESENT)

    @property
    def skipped(self) -> int:
        return self._count(SyncOutcome.SKIPPED)

    @property
    def failed(self) -> int:
        return self._count(SyncOutcome.FAILED)


def entry_state(entry: PrivateCatalogEntry) -> LocalFileState:
    """El (sha256, tamaño) que el catálogo declara para el fichero entero."""
    return LocalFileState(sha256=entry.sha256, size_bytes=entry.size_bytes)


# --- rutas ---------------------------------------------------------------------------


def _check_relative_path(text: str) -> tuple[str, ...]:
    if not text:
        raise ValueError("ruta vacía")
    if any(ord(ch) < 32 for ch in text) or "\\" in text or ":" in text:
        raise ValueError("ruta con caracteres no admitidos")
    if text.startswith("/"):
        raise ValueError("ruta absoluta")
    parts = tuple(text.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("ruta con componentes vacíos, '.' o '..'")
    if len(parts) < 2:
        raise ValueError("ruta sin directorio")
    return parts


def safe_destination(text: str) -> str:
    """`destino` del catálogo: relativo a `data/`, POSIX, bajo `raw/` o `derived/`.
    Lanza `ValueError` con un motivo que no repite la ruta."""
    parts = _check_relative_path(text)
    if parts[0] not in DESTINATION_ROOTS:
        raise ValueError("ruta fuera de raw/ y derived/")
    return text


def safe_repo_path(text: str, root: str) -> str:
    """Ruta relativa a la raíz del repo privado cuyo primer componente es `root`."""
    parts = _check_relative_path(text)
    if parts[0] != root:
        raise ValueError(f"ruta fuera de {root}/")
    return text


# --- titularidad ------------------------------------------------------------------------


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _key_label(key: str) -> str:
    return key if key in _KNOWN_KEYS else "<clave>"


def find_ownership_markers(value: object, where: str) -> tuple[str, ...]:
    """Rutas (`entradas[2].<clave>`) de las claves de titularidad y de las cadenas con
    forma de referencia catastral en un JSON ya decodificado. Las claves que no son del
    esquema salen como `<clave>`: así la ruta nunca repite lo rechazado."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            path = f"{where}.{_key_label(key_text)}" if where else _key_label(key_text)
            normalized = _normalize(key_text)
            if any(part in normalized for part in OWNERSHIP_KEY_PARTS):
                found.append(f"{path}: clave de titularidad")
            elif CADASTRAL_REFERENCE_RE.search(key_text):
                found.append(f"{path}: clave con forma de referencia catastral")
            found.extend(find_ownership_markers(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_ownership_markers(item, f"{where}[{index}]"))
    elif isinstance(value, str) and CADASTRAL_REFERENCE_RE.search(value):
        found.append(f"{where or '<raíz>'}: valor con forma de referencia catastral")
    return tuple(found)


# --- lectura del catálogo -----------------------------------------------------------------


class _Field:
    """Ayudante de validación de un objeto JSON: cada error lleva fichero, posición y
    campo, nunca el valor."""

    def __init__(self, file_name: str, where: str, data: Mapping[str, object]) -> None:
        self._file = file_name
        self._where = where
        self._data = data

    def fail(self, field: str | None, reason: str) -> PrivateCatalogError:
        place = f"{self._where}.{field}" if field else self._where
        return PrivateCatalogError(f"{self._file}: {place}: {reason}")

    def check_keys(self, required: frozenset[str], optional: frozenset[str] = frozenset()) -> None:
        for key in sorted(required):
            if key not in self._data:
                raise self.fail(key, "falta el campo obligatorio")
        unknown = [key for key in self._data if key not in required | optional]
        if unknown:
            raise self.fail(None, f"{len(unknown)} clave(s) desconocida(s)")

    def raw(self, field: str) -> object:
        return self._data.get(field)

    def text(self, field: str) -> str:
        value = self._data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise self.fail(field, "debe ser un texto no vacío")
        return value

    def matching(self, field: str, pattern: re.Pattern[str], reason: str) -> str:
        value = self._data.get(field)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise self.fail(field, reason)
        return value

    def non_negative_int(self, field: str) -> int:
        value = self._data.get(field)
        if type(value) is not int or value < 0:
            raise self.fail(field, "debe ser un entero no negativo")
        return value

    def optional(self, field: str) -> object | None:
        return self._data.get(field)


def _decode_json(content: bytes, fail: PrivateCatalogError) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise fail from None


def _storage(entry: _Field, entry_id: str, file_name: str, where: str) -> StorageLocation:
    raw = entry.raw("almacen")
    if not isinstance(raw, dict):
        raise entry.fail("almacen", "debe ser un objeto")
    storage = _Field(file_name, f"{where}.almacen", raw)
    kind = raw.get("tipo")
    if kind == "release":
        storage.check_keys(RELEASE_KEYS)
        tag = storage.matching("etiqueta", ENTRY_ID_RE, "etiqueta no válida")
        asset = storage.text("adjunto")
        prefix = f"{entry_id}."
        if not asset.startswith(prefix) or not ASSET_EXTENSION_RE.fullmatch(asset[len(prefix) :]):
            raise storage.fail("adjunto", "debe ser el id más una extensión")
        return ReleaseAsset(tag=tag, asset=asset)
    if kind == "git":
        storage.check_keys(GIT_KEYS)
        path = storage.text("ruta")
        try:
            return GitFile(path=safe_repo_path(path, GIT_STORAGE_ROOT))
        except ValueError as error:
            raise storage.fail("ruta", str(error)) from None
    raise storage.fail("tipo", "debe ser release o git")


def _parts(entry: _Field, entry_id: str, file_name: str, where: str) -> tuple[AssetPart, ...]:
    raw = entry.optional("partes")
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise entry.fail("partes", "debe ser una lista no vacía")
    parts: list[AssetPart] = []
    for index, item in enumerate(raw):
        part_where = f"{where}.partes[{index}]"
        if not isinstance(item, dict):
            raise PrivateCatalogError(f"{file_name}: {part_where}: debe ser un objeto")
        part = _Field(file_name, part_where, item)
        part.check_keys(PART_KEYS)
        asset = part.matching("adjunto", PART_ASSET_RE, "adjunto no válido")
        if not asset.startswith(f"{entry_id}."):
            raise part.fail("adjunto", "debe empezar por el id")
        parts.append(
            AssetPart(
                asset=asset,
                sha256=part.matching("sha256", SHA256_RE, "sha256 mal formado"),
                size_bytes=part.non_negative_int("tamano_bytes"),
            )
        )
    return tuple(parts)


def _entry(raw: object, file_name: str, source: str, index: int) -> PrivateCatalogEntry:
    where = f"entradas[{index}]"
    if not isinstance(raw, dict):
        raise PrivateCatalogError(f"{file_name}: {where}: debe ser un objeto")
    entry = _Field(file_name, where, raw)
    entry.check_keys(REQUIRED_ENTRY_KEYS, OPTIONAL_ENTRY_KEYS)
    if raw.get("fuente") != source:
        raise entry.fail("fuente", "no es la del catálogo")
    entry_id = entry.matching("id", ENTRY_ID_RE, "id no válido")
    downloaded = entry.matching("fecha_descarga", ISO_DATE_RE, "fecha no válida (AAAA-MM-DD)")
    try:
        downloaded_on = date.fromisoformat(downloaded)
    except ValueError:
        raise entry.fail("fecha_descarga", "fecha no válida (AAAA-MM-DD)") from None
    if raw.get("publicable") is not False:
        raise entry.fail("publicable", "debe ser false")
    restrictions = raw.get("restricciones_uso")
    if (
        not isinstance(restrictions, list)
        or not restrictions
        or not all(isinstance(item, str) and item.strip() for item in restrictions)
    ):
        raise entry.fail("restricciones_uso", "debe ser una lista no vacía de textos no vacíos")
    layout_text = raw.get("formato")
    if layout_text not in {layout.value for layout in EntryLayout}:
        raise entry.fail("formato", "debe ser fichero o tar")
    destination = entry.text("destino")
    try:
        safe_destination(destination)
    except ValueError as error:
        raise entry.fail("destino", str(error)) from None
    provenance = entry.text("procedencia")
    if provenance != f"{PROVENANCE_ROOT}/{source}/{entry_id}.procedencia.json":
        raise entry.fail("procedencia", "debe ser procedencia/<fuente>/<id>.procedencia.json")
    product = entry.optional("producto")
    if product is not None:
        product = entry.text("producto")
    municipality = entry.optional("municipio_ine")
    if municipality is not None:
        municipality = entry.matching("municipio_ine", MUNICIPALITY_CODE_RE, "código INE no válido")
    replaced_by = entry.optional("sustituido_por")
    if replaced_by is not None:
        replaced_by = entry.matching("sustituido_por", ENTRY_ID_RE, "id no válido")
    return PrivateCatalogEntry(
        entry_id=entry_id,
        source=source,
        url=entry.text("url"),
        downloaded_on=downloaded_on,
        license=entry.text("licencia"),
        use_restrictions=tuple(str(item) for item in restrictions),
        sha256=entry.matching("sha256", SHA256_RE, "sha256 mal formado"),
        size_bytes=entry.non_negative_int("tamano_bytes"),
        storage=_storage(entry, entry_id, file_name, where),
        layout=EntryLayout(str(layout_text)),
        destination=destination,
        provenance=GitFile(path=safe_repo_path(provenance, PROVENANCE_ROOT)),
        product=product,
        municipality_code=municipality,
        parts=_parts(entry, entry_id, file_name, where),
        replaced_by=replaced_by,
    )


def parse_catalog(document: CatalogDocument) -> PrivateCatalog:
    """Valida un `catalogo/<fuente>.json` entero (ADR 0055 §3) antes de devolver nada."""
    if not CATALOG_FILE_RE.fullmatch(document.name):
        raise PrivateCatalogError("catálogo con nombre de fichero no válido")
    file_name = document.name
    data = _decode_json(
        document.content, PrivateCatalogError(f"{file_name}: no es JSON UTF-8 válido")
    )
    if not isinstance(data, dict):
        raise PrivateCatalogError(f"{file_name}: debe ser un objeto JSON")
    markers = find_ownership_markers(data, "")
    if markers:
        raise PrivateCatalogError(f"{file_name}: {markers[0]}")
    top = _Field(file_name, "<raíz>", data)
    top.check_keys(DOCUMENT_KEYS)
    schema = data.get("esquema")
    if type(schema) is not int or schema != PRIVATE_CATALOG_SCHEMA:
        raise top.fail("esquema", f"debe ser {PRIVATE_CATALOG_SCHEMA}")
    source = top.matching("fuente", SOURCE_RE, "fuente no válida")
    if f"{source}.json" != file_name:
        raise top.fail("fuente", "no coincide con el nombre del fichero")
    raw_entries = data.get("entradas")
    if not isinstance(raw_entries, list):
        raise top.fail("entradas", "debe ser una lista")
    entries = tuple(_entry(raw, file_name, source, index) for index, raw in enumerate(raw_entries))
    return PrivateCatalog(file_name=file_name, source=source, entries=entries)


def validate_catalog_set(catalogs: Sequence[PrivateCatalog]) -> tuple[PrivateCatalogEntry, ...]:
    """Reglas entre entradas y catálogos: `id` únicos, `sustituido_por` que existe y no es
    el propio `id`, un solo fichero vigente por `destino` y ningún fichero vigente dentro
    del `destino` de un tar vigente. Devuelve las entradas ordenadas por (fuente, id)."""
    located: list[tuple[str, int, PrivateCatalogEntry]] = [
        (catalog.file_name, index, entry)
        for catalog in sorted(catalogs, key=lambda c: c.file_name)
        for index, entry in enumerate(catalog.entries)
    ]
    seen: set[str] = set()
    for file_name, index, entry in located:
        if entry.entry_id in seen:
            raise PrivateCatalogError(f"{file_name}: entradas[{index}].id: repetido")
        seen.add(entry.entry_id)
    for file_name, index, entry in located:
        if entry.replaced_by is None:
            continue
        if entry.replaced_by == entry.entry_id:
            raise PrivateCatalogError(
                f"{file_name}: entradas[{index}].sustituido_por: apunta a sí misma"
            )
        if entry.replaced_by not in seen:
            raise PrivateCatalogError(f"{file_name}: entradas[{index}].sustituido_por: no existe")
    current = [item for item in located if item[2].replaced_by is None]
    destinations: set[str] = set()
    for file_name, index, entry in current:
        if entry.layout is not EntryLayout.FILE:
            continue
        if entry.destination in destinations:
            raise PrivateCatalogError(
                f"{file_name}: entradas[{index}].destino: otra entrada vigente tiene el mismo"
            )
        destinations.add(entry.destination)
    tar_roots = [entry.destination for _, _, entry in current if entry.layout is EntryLayout.TAR]
    for file_name, index, entry in current:
        if entry.layout is not EntryLayout.FILE:
            continue
        if any(
            entry.destination == root or entry.destination.startswith(f"{root}/")
            for root in tar_roots
        ):
            raise PrivateCatalogError(
                f"{file_name}: entradas[{index}].destino: dentro del destino de un tar vigente"
            )
    return tuple(sorted((entry for _, _, entry in located), key=lambda e: (e.source, e.entry_id)))


# --- procedencia y registro local -----------------------------------------------------------


def parse_provenance(entry_id: str, content: bytes) -> Mapping[str, object]:
    """Procedencia de una entrada tal como está en el repo privado: un objeto JSON sin
    claves de titularidad ni referencias catastrales."""
    data = _decode_json(
        content, PrivateCatalogError(f"procedencia de {entry_id}: no es JSON UTF-8 válido")
    )
    if not isinstance(data, dict) or not data:
        raise PrivateCatalogError(f"procedencia de {entry_id}: debe ser un objeto JSON no vacío")
    markers = find_ownership_markers(data, "")
    if markers:
        raise PrivateCatalogError(f"procedencia de {entry_id}: {markers[0]}")
    return data


def _storage_record(storage: StorageLocation) -> dict[str, object]:
    if isinstance(storage, ReleaseAsset):
        return {"tipo": "release", "etiqueta": storage.tag, "adjunto": storage.asset}
    return {"tipo": "git", "ruta": storage.path}


def catalog_record(entry: PrivateCatalogEntry) -> dict[str, object]:
    """La entrada con las claves del catálogo (las opcionales, solo si tienen valor)."""
    record: dict[str, object] = {
        "id": entry.entry_id,
        "fuente": entry.source,
        "url": entry.url,
        "fecha_descarga": entry.downloaded_on.isoformat(),
        "licencia": entry.license,
        "publicable": False,
        "restricciones_uso": list(entry.use_restrictions),
        "sha256": entry.sha256,
        "tamano_bytes": entry.size_bytes,
        "almacen": _storage_record(entry.storage),
        "formato": entry.layout.value,
        "destino": entry.destination,
        "procedencia": entry.provenance.path,
    }
    if entry.product is not None:
        record["producto"] = entry.product
    if entry.municipality_code is not None:
        record["municipio_ine"] = entry.municipality_code
    if entry.parts:
        record["partes"] = [
            {"adjunto": part.asset, "sha256": part.sha256, "tamano_bytes": part.size_bytes}
            for part in entry.parts
        ]
    if entry.replaced_by is not None:
        record["sustituido_por"] = entry.replaced_by
    return record


def local_record(entry: PrivateCatalogEntry, provenance: Mapping[str, object]) -> bytes:
    """`data/privados/<id>.json`: la entrada y su procedencia en JSON canónico, sin fecha
    de sincronización, para que repetir dé los mismos bytes."""
    text = json.dumps(
        {"entrada": catalog_record(entry), "procedencia": dict(provenance)},
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
    )
    return f"{text}\n".encode()


def record_matches(entry: PrivateCatalogEntry, record: bytes) -> bool:
    """¿El registro local es de esta entrada? Compara solo `entrada`."""
    try:
        data = json.loads(record.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return False
    return isinstance(data, dict) and data.get("entrada") == catalog_record(entry)
