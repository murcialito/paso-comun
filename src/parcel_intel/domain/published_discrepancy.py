"""Indicios de discrepancia publicables de un municipio (historias #94 y #316, ADR 0044 y
0057).

Parte del informe **publicable** de #32 (ya redactado, ADR 0030 §4) y deja, por
indicio, solo la zona del lado A, la ficha pública de #16 (`build_card`) y su tabla de
convergencia de fuentes (#316). No añade ni redacta ningún texto.

Reglas (ADR 0044 §4, con la corrección del ADR 0041):
- la zona sale del trazado A, así que la fuente A debe ser redistribuible; si
  no lo es (Catastro), la zona va redactada y el indicio se omite y se cuenta;
- la contraparte puede ser una fuente redistribuible o Catastro: un «no aparece
  en Catastro» es información derivada que no reproduce el dato original
  (ADR 0041 §1) y se publica con la atribución de `SOURCE_POLICY`;
- ningún identificador de trazado, referencia catastral, medida ni confianza
  numérica sale de aquí: solo `public_id`, la zona, la ficha y la tabla.

La tabla se calcula con el informe **interno** (el publicable no tiene las zonas de
Catastro), casado con el publicable registro a registro (`check_internal_report`). Qué
se publica y con qué `public_id` sale de un único recorrido (`_publishable_records`,
ADR 0057 §1). Ningún motivo de error lleva un `record_id`.

El dominio no conoce EPSG:4326: la zona sigue en EPSG:25830.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from parcel_intel.domain.convergence import (
    ConvergenceStretch,
    ConvergenceTable,
    stretch_from_discrepancy,
)
from parcel_intel.domain.discrepancy import Discrepancy, DiscrepancyKind
from parcel_intel.domain.discrepancy_detection import zone_coordinates
from parcel_intel.domain.discrepancy_policy import REDISTRIBUTABLE_SOURCES
from parcel_intel.domain.discrepancy_report import DiscrepancyRecord, MunicipalDiscrepancyReport
from parcel_intel.domain.geometry import Geometry, TrailShape
from parcel_intel.domain.parcel_identifiers import (
    CADASTRAL_REFERENCE_RE as CADASTRAL_REFERENCE_RE,
)
from parcel_intel.domain.parcel_identifiers import PARCEL_LOCATOR_RE as PARCEL_LOCATOR_RE
from parcel_intel.domain.provenance import Provenance
from parcel_intel.domain.public_card import PublicCard, build_card
from parcel_intel.domain.publication import PublishableLayer
from parcel_intel.domain.published_convergence import (
    ConvergenceNotes,
    PublishedConvergence,
    convergence_notes,
    publish_convergence,
)
from parcel_intel.domain.published_layers import PublishedFile
from parcel_intel.domain.trail import SourceKind

DISCREPANCY_LAYER_ID: Final = "discrepancias"
DISCREPANCY_LAYER_SOURCE = "Parcel Intel, indicios de discrepancia entre trazados"
DISCREPANCY_LAYER_LICENSE = "CC BY 4.0"  # ADR 0035 §2: datos derivados publicados

PUBLISHABLE_COUNTERPART_SOURCES: frozenset[SourceKind] = REDISTRIBUTABLE_SOURCES | {"catastro_vt"}
"""Fuentes que pueden ser contraparte de un indicio publicado. Catastro entra por el
ADR 0041: se publica el indicio y su evidencia derivada, nunca su geometría (que es
la del lado B y no se publica) ni sus referencias."""

# Motivos fijos de #316 (ADR 0057): ninguno lleva un `record_id`.
INTERNAL_MISSING = "sin informe interno; ejecuta discrepancias"
INTERNAL_REDACTED = "el informe interno está redactado; usa data/derived/discrepancias/interno"
INTERNAL_MISMATCH = (
    "el informe interno no corresponde al publicable; vuelve a ejecutar discrepancias"
)
NO_CONVERGENCE_TABLE = "sin tabla de convergencia"


class NotPublishableReportError(ValueError):
    """La entrada no es publicable (variante interna, registro sin redactar, sin
    nombre de municipio...). `reason` va en castellano para la CLI."""

    def __init__(self, municipality_code: str, reason: str) -> None:
        super().__init__(f"{municipality_code}: {reason}")
        self.municipality_code = municipality_code
        self.reason = reason


@dataclass(frozen=True)
class PublishedDiscrepancy:
    public_id: str
    zone: TrailShape  # EPSG:25830, lado A; nunca vacía
    card: PublicCard  # build_card(discrepancy) tal cual
    provenance: Provenance  # la del trazado A, de donde sale la zona (cumple HasProvenance)
    convergence: PublishedConvergence  # tabla de #240 sin id, municipio ni umbral (#316)


@dataclass(frozen=True)
class OmittedByLicense:
    subject: int  # lado A no redistribuible (zona redactada), CA4
    counterpart: int  # contraparte fuera de PUBLISHABLE_COUNTERPART_SOURCES

    @property
    def total(self) -> int:
        return self.subject + self.counterpart


@dataclass(frozen=True)
class DiscrepancyLayerPublication:
    municipality_code: str
    municipality_name: str
    criterion_id: str  # "discrepancia_trazados@1"
    content: PublishableLayer[PublishedDiscrepancy]  # pasa la puerta de ADR 0028
    convergence_notes: ConvergenceNotes  # bloque `convergencia` del .procedencia.json


@dataclass(frozen=True)
class DiscrepancyLayerBuild:
    publication: DiscrepancyLayerPublication | None  # None si no queda ningún indicio
    omitted: OmittedByLicense


@dataclass(frozen=True)
class DiscrepancyPublicationFailure:
    municipality_code: str
    reason: str


@dataclass(frozen=True)
class DiscrepancyPublicationResult:
    published: tuple[PublishedFile, ...]
    empty: tuple[str, ...]  # municipios sin indicios publicables
    omitted: tuple[tuple[str, OmittedByLicense], ...]  # por municipio, también los vacíos
    failures: tuple[DiscrepancyPublicationFailure, ...]  # si hay alguno, `published` es ()
    # Tipo de cada indicio publicado, por municipio y en orden (para el resumen de la CLI).
    kinds: tuple[tuple[str, tuple[DiscrepancyKind, ...]], ...] = ()


def _base_public_id(discrepancy: Discrepancy) -> str:
    """Solo datos ya públicos: criterio, tipo, fuentes y zona redondeada. No usa
    `record_id`, cuyo hash lleva identificadores de trazado (posibles referencias
    de Catastro) y se podría invertir por fuerza bruta."""
    payload = {
        "criterio": [discrepancy.criterion.identifier, discrepancy.criterion.version],
        "tipo": discrepancy.kind,
        "fuente_a": discrepancy.subject.source.kind,
        "fuente_b": discrepancy.counterpart_source.kind,
        "zona": zone_coordinates(discrepancy),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def public_indicio_id(discrepancy: Discrepancy) -> str:
    """Identificador público de un indicio (sin sufijo de colisión)."""
    return _base_public_id(discrepancy)


def _has_parts(shape: TrailShape) -> bool:
    parts = shape.rings if isinstance(shape, Geometry) else shape.lines
    return any(parts)


def is_publishable_indicio(discrepancy: Discrepancy) -> bool:
    """Fuente A redistribuible y contraparte en `PUBLISHABLE_COUNTERPART_SOURCES`.

    Es la regla de publicación de un indicio (ADR 0044 §4, ADR 0041): la usan
    `build_discrepancy_layer` y el manifiesto de imágenes de los indicios (#314)."""
    return (
        discrepancy.subject.source.kind in REDISTRIBUTABLE_SOURCES
        and discrepancy.counterpart_source.kind in PUBLISHABLE_COUNTERPART_SOURCES
    )


def _check_publishable_report(report: MunicipalDiscrepancyReport) -> str:
    """Variante publicable y con nombre de municipio (#94); mismos textos de siempre.
    Devuelve el nombre del municipio."""
    code = report.municipality_code
    if not report.redacted:
        raise NotPublishableReportError(code, "no es la variante publicable del informe")
    if not report.municipality_name:
        raise NotPublishableReportError(
            code,
            "el informe no trae el nombre del municipio; regenera las capas derivadas "
            "y `discrepancias`",
        )
    return report.municipality_name


def _publishable_records(
    report: MunicipalDiscrepancyReport,
) -> tuple[tuple[int, DiscrepancyRecord, str], ...]:
    """(índice en `report.records`, registro, `public_id`) de lo que se publica, en orden
    canónico. Es el ÚNICO sitio que decide qué se publica y con qué `public_id` (ADR 0057
    §1). Omite las zonas redactadas y las contrapartes no publicables; lanza
    `NotPublishableReportError` por una fuente A no redistribuible sin redactar o una zona
    vacía. Sufijo de colisión «-2», «-3»..."""
    code = report.municipality_code
    found: list[tuple[int, DiscrepancyRecord, str]] = []
    seen: dict[str, int] = {}
    for index, record in enumerate(report.records):
        discrepancy = record.discrepancy
        if record.zone_redacted:
            continue
        if not is_publishable_indicio(discrepancy):
            if discrepancy.subject.source.kind not in REDISTRIBUTABLE_SOURCES:
                raise NotPublishableReportError(
                    code,
                    f"registro sin redactar con fuente A {discrepancy.subject.source.kind!r}, "
                    "que no es redistribuible",
                )
            continue
        if not _has_parts(discrepancy.zone):
            raise NotPublishableReportError(code, "registro sin redactar con la zona vacía")
        base = _base_public_id(discrepancy)
        seen[base] = seen.get(base, 0) + 1
        public_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        found.append((index, record, public_id))
    return tuple(found)


def published_indicio_ids(report: MunicipalDiscrepancyReport) -> tuple[tuple[str, str], ...]:
    """(`record_id`, `public_id`) de cada indicio que se publica, en orden canónico.
    Lanza lo mismo que `build_discrepancy_layer` (reglas de #94)."""
    _check_publishable_report(report)
    return tuple(
        (record.record_id, public_id) for _, record, public_id in _publishable_records(report)
    )


def check_internal_report(
    report: MunicipalDiscrepancyReport, internal: MunicipalDiscrepancyReport
) -> None:
    """El informe interno es el que acompaña al publicable `report`. Lanza
    `NotPublishableReportError` con `INTERNAL_REDACTED` si está redactado, o con
    `INTERNAL_MISMATCH` si difiere el municipio, el criterio, la secuencia de `record_id`
    o, en un indicio que se publica, la zona o las fuentes A y B. Igualdad exacta: los dos
    informes salen del mismo escritor. El motivo es fijo, sin ids."""
    code = report.municipality_code
    if internal.redacted:
        raise NotPublishableReportError(code, INTERNAL_REDACTED)
    if (
        internal.municipality_code != report.municipality_code
        or internal.criterion_id != report.criterion_id
        or tuple(r.record_id for r in internal.records)
        != tuple(r.record_id for r in report.records)
    ):
        raise NotPublishableReportError(code, INTERNAL_MISMATCH)
    for index, record, _ in _publishable_records(report):
        twin = internal.records[index]
        public, private = record.discrepancy, twin.discrepancy
        if (
            twin.zone_redacted
            or private.zone != public.zone
            or private.subject.source.kind != public.subject.source.kind
            or private.counterpart_source.kind != public.counterpart_source.kind
        ):
            raise NotPublishableReportError(code, INTERNAL_MISMATCH)


def indicio_stretches(
    report: MunicipalDiscrepancyReport, internal: MunicipalDiscrepancyReport
) -> tuple[ConvergenceStretch, ...]:
    """Tramo de convergencia de cada indicio publicado, desde el registro INTERNO y en el
    orden de `published_indicio_ids`. Precondición: `check_internal_report` ya pasó."""
    return tuple(
        stretch_from_discrepancy(internal.records[index], report.municipality_code)
        for index, _, _ in _publishable_records(report)
    )


def build_discrepancy_layer(
    report: MunicipalDiscrepancyReport, tables: Mapping[str, ConvergenceTable]
) -> DiscrepancyLayerBuild:
    """Indicios publicables de `report`, en su orden canónico, cada uno con su tabla de
    convergencia (`tables`, por `record_id`).

    Lanza `NotPublishableReportError` si el informe no es la variante publicable,
    si le falta el nombre del municipio, si un registro no redactado tiene una
    fuente A no redistribuible o una zona vacía, o si a un indicio le falta su tabla.
    Un trazado A cuya procedencia no sea `PUBLIC` hace fallar la puerta
    `PublishableLayer` (`NotRedistributableError`). Sin indicios no mira `tables`.
    """
    name = _check_publishable_report(report)
    code = report.municipality_code
    selected = _publishable_records(report)
    omitted_subject = sum(1 for record in report.records if record.zone_redacted)
    omitted = OmittedByLicense(
        subject=omitted_subject,
        counterpart=len(report.records) - omitted_subject - len(selected),
    )
    if not selected:
        return DiscrepancyLayerBuild(publication=None, omitted=omitted)
    items: list[PublishedDiscrepancy] = []
    used: list[ConvergenceTable] = []
    for _, record, public_id in selected:
        table = tables.get(record.record_id)
        if table is None:
            raise NotPublishableReportError(code, f"indicio {public_id}: {NO_CONVERGENCE_TABLE}")
        discrepancy = record.discrepancy
        used.append(table)
        items.append(
            PublishedDiscrepancy(
                public_id=public_id,
                zone=discrepancy.zone,
                card=build_card(discrepancy),
                provenance=discrepancy.subject.source.provenance,
                convergence=publish_convergence(table),
            )
        )
    content = PublishableLayer[PublishedDiscrepancy](
        name=f"{DISCREPANCY_LAYER_ID}/{code}", items=tuple(items)
    )
    return DiscrepancyLayerBuild(
        publication=DiscrepancyLayerPublication(
            municipality_code=code,
            municipality_name=name,
            criterion_id=report.criterion_id,
            content=content,
            convergence_notes=convergence_notes(used),
        ),
        omitted=omitted,
    )
