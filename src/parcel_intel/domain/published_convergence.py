"""Tabla de convergencia publicable (historia #315, ADR 0057 §1, §6 y §8).

`PublishedConvergence` es la tabla de #240 (`ConvergenceTable`) sin lo que no se publica:
ni `stretch_id` (el id interno, que puede derivar de ids de trazado o de Catastro), ni
`municipality_code`, ni `appears_min_fraction` (el umbral va al `.procedencia.json`, en
`ConvergenceNotes`). Todo texto sale ya redactado: el visor no compone nada.

`check_published_convergence` es la última barrera antes de escribir (la llama el sink):
textos validados por su huella, rótulos de fila aprobados, atribuciones fijas, ninguna
palabra vetada y ninguna referencia ni localizador de parcela. La comparten los tramos a
verificar (#315) y los indicios (#316). Solo stdlib.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date

from parcel_intel.domain.comparison_image import COMPARISON_SLOTS, FLIGHTS, attribution_for
from parcel_intel.domain.convergence import (
    CONVERGENCE_ROW_SOURCES,
    CONVERGENCE_TEXT_VALIDATION,
    CONVERGENCE_TITLE,
    CONVERGENCE_WARNING,
    LAYER_ROW_LABEL,
    ORTHOPHOTO_ROW_LABEL,
    ConvergenceRow,
    ConvergenceTable,
    HistoricRowSource,
    LayerRowSource,
    RowSource,
    compute_convergence_text_hash,
    orthophoto_source_key,
    vetoed_terms_in,
)
from parcel_intel.domain.discrepancy_criterion import CriterionRef
from parcel_intel.domain.historic_coverage import HISTORIC_SOURCE_KIND
from parcel_intel.domain.parcel_identifiers import CADASTRAL_REFERENCE_RE, PARCEL_LOCATOR_RE
from parcel_intel.domain.public_card import CATASTRO_ATTRIBUTION, SOURCE_POLICY
from parcel_intel.domain.published_comparison import NOT_AVAILABLE_TEXT

IGN_NETWORK_NOTE = "sin asfaltar y asfaltada (ADR 0050)"
"""Red del IGN de la fila «Camino actual (IGN)»: la misma unión que la contraparte de los
indicios (ADR 0057 §5). Va al `.procedencia.json`, no a la ficha."""


def row_source_key(source: RowSource) -> str:
    """El `source_key` de la fila de una fuente, como lo pone `build_convergence_table`."""
    if isinstance(source, LayerRowSource):
        return source.kind
    if isinstance(source, HistoricRowSource):
        return HISTORIC_SOURCE_KIND[source.document_kind]
    return orthophoto_source_key(source.slot)


ROW_SOURCE_KEYS: tuple[str, ...] = tuple(row_source_key(s) for s in CONVERGENCE_ROW_SOURCES)
"""Claves de fila en el orden de `CONVERGENCE_ROW_SOURCES`."""

ROW_LABELS: frozenset[str] = frozenset(LAYER_ROW_LABEL.values()) | frozenset(
    ORTHOPHOTO_ROW_LABEL.format(franja=slot.name) for slot in COMPARISON_SLOTS
)
"""Rótulos de fila aprobados (#240): los únicos que pueden salir."""

ROW_ATTRIBUTIONS: frozenset[str] = (
    frozenset(policy.attribution for policy in SOURCE_POLICY.values())
    | frozenset(flight.attribution for flight in FLIGHTS.values())
    | frozenset(attribution_for(vintage) for vintage in FLIGHTS)
    | frozenset(flight.notice for flight in FLIGHTS.values() if flight.notice is not None)
    | {CATASTRO_ATTRIBUTION}
)
"""Atribuciones admitidas en una fila: las de la política de fuentes, las de los vuelos
(las mismas que acepta #172, con su «Obra derivada de») y sus avisos, y la de Catastro."""

_IMAGES_NOTES: frozenset[str | None] = frozenset({None, NOT_AVAILABLE_TEXT})


def _method_label(method: CriterionRef) -> str:
    """Mismo formato que la ficha de los tramos a verificar: «convergencia_fuentes v2»."""
    return f"{method.identifier} v{method.version}"


class ConvergenceNotPublishableError(ValueError):
    """La tabla no se puede publicar. `reason` es un texto fijo, sin valores ajenos."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PublishedConvergence:
    """La tabla publicable: SIN stretch_id, municipality_code ni appears_min_fraction."""

    title: str
    rows: tuple[ConvergenceRow, ...]
    warning: str
    method: str  # "convergencia_fuentes v2"
    criterion: str  # "discrepancia_trazados v1"
    images_note: str | None  # NOT_AVAILABLE_TEXT si images_unavailable; si no, None

    def strings(self) -> Iterator[str]:
        """Todo el texto que se publica de la tabla, aviso incluido."""
        yield self.title
        for row in self.rows:
            yield row.source_key
            yield from row.strings()
        if self.images_note is not None:
            yield self.images_note
        yield self.warning
        yield self.method
        yield self.criterion


@dataclass(frozen=True)
class ConvergenceNotes:
    """Bloque `convergencia` del `.procedencia.json` (ADR 0057 §6)."""

    method: str
    criterion: str
    appears_min_fraction: float
    ign_network: str  # IGN_NETWORK_NOTE
    # (source_key, atribuciones sin repetir en orden de aparición, fecha de consulta más
    # reciente o None), en el orden de CONVERGENCE_ROW_SOURCES; solo las que salen en
    # alguna tabla.
    sources: tuple[tuple[str, tuple[str, ...], date | None], ...]


def check_published_convergence(table: PublishedConvergence) -> None:
    """Última barrera (CA2 de #315): `ConvergenceNotPublishableError` con un motivo fijo
    si algo de la tabla no es un texto validado, un rótulo aprobado o una atribución
    fija, o si lleva una palabra vetada o una referencia o localizador de parcela."""
    if compute_convergence_text_hash() != CONVERGENCE_TEXT_VALIDATION.text_hash:
        raise ConvergenceNotPublishableError(
            "los textos de la tabla no son los que validó el fundador"
        )
    if table.title != CONVERGENCE_TITLE:
        raise ConvergenceNotPublishableError("el título no es el validado")
    if table.warning != CONVERGENCE_WARNING:
        raise ConvergenceNotPublishableError("el aviso no es el validado")
    if table.images_note not in _IMAGES_NOTES:
        raise ConvergenceNotPublishableError("la nota de imágenes no es la validada")
    for row in table.rows:
        if row.source_key not in ROW_SOURCE_KEYS:
            raise ConvergenceNotPublishableError("una fila es de una fuente desconocida")
        if row.label not in ROW_LABELS:
            raise ConvergenceNotPublishableError("una fila lleva un rótulo no aprobado")
        if any(attribution not in ROW_ATTRIBUTIONS for attribution in row.attributions):
            raise ConvergenceNotPublishableError("una fila lleva una atribución no admitida")
    for text in table.strings():
        # El aviso dice «si el camino es público» (ADR 0054 §9): está protegido por su
        # huella, así que no se busca en él «es público».
        if text != CONVERGENCE_WARNING and vetoed_terms_in(text):
            raise ConvergenceNotPublishableError("la tabla contiene términos vetados")
        if CADASTRAL_REFERENCE_RE.search(text) or PARCEL_LOCATOR_RE.search(text):
            raise ConvergenceNotPublishableError(
                "la tabla contiene una referencia o un localizador de parcela"
            )


def publish_convergence(table: ConvergenceTable) -> PublishedConvergence:
    """La tabla sin id, municipio ni umbral, ya comprobada."""
    published = PublishedConvergence(
        title=table.title,
        rows=table.rows,
        warning=table.warning,
        method=_method_label(table.method),
        criterion=_method_label(table.criterion),
        images_note=NOT_AVAILABLE_TEXT if table.images_unavailable else None,
    )
    check_published_convergence(published)
    return published


def convergence_notes(tables: Sequence[ConvergenceTable]) -> ConvergenceNotes:
    """Notas de procedencia de las tablas de una publicación. Todas deben compartir umbral,
    método y criterio (si no, es un error de programación)."""
    if not tables:
        raise ConvergenceNotPublishableError("no hay tablas de convergencia")
    first = tables[0]
    for table in tables[1:]:
        if table.appears_min_fraction != first.appears_min_fraction:
            raise ConvergenceNotPublishableError("las tablas usan umbrales de «Aparece» distintos")
        if table.method != first.method or table.criterion != first.criterion:
            raise ConvergenceNotPublishableError("las tablas usan métodos o criterios distintos")
    attributions: dict[str, list[str]] = {}
    consulted: dict[str, date | None] = {}
    for table in tables:
        for row in table.rows:
            found = attributions.setdefault(row.source_key, [])
            found.extend(a for a in row.attributions if a not in found)
            latest = consulted.get(row.source_key)
            if row.consulted_on is not None and (latest is None or row.consulted_on > latest):
                consulted[row.source_key] = row.consulted_on
            else:
                consulted.setdefault(row.source_key, latest)
    unknown = set(attributions) - set(ROW_SOURCE_KEYS)
    if unknown:
        raise ConvergenceNotPublishableError("una fila es de una fuente desconocida")
    return ConvergenceNotes(
        method=_method_label(first.method),
        criterion=_method_label(first.criterion),
        appears_min_fraction=first.appears_min_fraction,
        ign_network=IGN_NETWORK_NOTE,
        sources=tuple(
            (key, tuple(attributions[key]), consulted[key])
            for key in ROW_SOURCE_KEYS
            if key in attributions
        ),
    )
