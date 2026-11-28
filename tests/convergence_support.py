"""Constructores SINTÉTICOS de la tabla de convergencia (historia #240). Nada es real.

Coordenadas relativas a `X0 = 450000`, `Y0 = 4435000` (EPSG:25830), como en el diseño de
la historia. Los identificadores de Catastro llevan el prefijo de municipio inexistente
`99999` (lecciones #46 y #95). Las URL de imagen son inventadas (`example.invalid`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from parcel_intel.domain.comparison_image import (
    COMPARISON_POLICY,
    COMPARISON_SLOTS,
    DECLARED_GAPS,
    FLIGHTS,
    ComparisonImage,
    ComparisonManifest,
    ComparisonWindow,
    OmittedSlot,
    StretchComparison,
    attribution_for,
    comparison_windows,
    stretch_pixels,
)
from parcel_intel.domain.convergence import (
    APPEARS_MIN_FRACTION,
    CONVERGENCE_ROW_SOURCES,
    ConvergenceStretch,
    ConvergenceTable,
    PreparedLayer,
    StretchNotComputableError,
    build_convergence_table,
    image_axis,
    stretch_from_candidate,
    stretch_from_discrepancy,
)
from parcel_intel.domain.cut_candidate import CutCandidate
from parcel_intel.domain.discrepancy import SourceRef
from parcel_intel.domain.discrepancy_report import MunicipalDiscrepancyReport, TrailLayer
from parcel_intel.domain.geometry import Geometry, LineGeometry, MultiLineGeometry, Point
from parcel_intel.domain.historic_coverage import HistoricCoverage, HistoricUnitCoverage
from parcel_intel.domain.historic_trail import HistoricDocumentKind, HistoricTrail
from parcel_intel.domain.orthophoto import HistoricalVintage
from parcel_intel.domain.provenance import Provenance, Redistribution
from parcel_intel.domain.trail import SourceKind
from parcel_intel.ports.errors import TrailLayerUnavailableError
from tests.trail_factories import make_layer, make_provenance, make_trail

X0 = 450000.0
Y0 = 4435000.0
INE = "28043"
STRETCH_ID = "0123456789abcdef"
IMAGE_DATE = date(2026, 11, 27)
HISTORIC_PROVENANCE = Provenance(
    source="Planimetría sintética, IGN",
    url="https://example.invalid/planimetrias",
    retrieved_at=date(2026, 11, 20),
    license="CC-BY 4.0 © Instituto Geográfico Nacional de España",
    redistribution=Redistribution.PUBLIC,
)


def p(x: float, y: float) -> Point:
    return (X0 + x, Y0 + y)


def line(*points: tuple[float, float]) -> MultiLineGeometry:
    return MultiLineGeometry(lines=(tuple(p(x, y) for x, y in points),), srid=25830)


def lines(*parts: Sequence[tuple[float, float]]) -> MultiLineGeometry:
    return MultiLineGeometry(
        lines=tuple(tuple(p(x, y) for x, y in part) for part in parts), srid=25830
    )


def strip(x0: float, y0: float, x1: float, y1: float) -> Geometry:
    """Franja rectangular (antihoraria) en coordenadas relativas."""
    ring = (p(x0, y0), p(x1, y0), p(x1, y1), p(x0, y1), p(x0, y0))
    return Geometry(rings=(ring,), srid=25830)


def origin(kind: SourceKind = "ign_red_transporte") -> SourceRef:
    return SourceRef(kind=kind, provenance=make_provenance(kind, "tramo"), data_date=None)


def stretch(
    shape: MultiLineGeometry | Geometry,
    kind: SourceKind = "ign_red_transporte",
    stretch_id: str = STRETCH_ID,
) -> ConvergenceStretch:
    return ConvergenceStretch(
        stretch_id=stretch_id, municipality_code=INE, origin=origin(kind), shape=shape
    )


def layer(
    kind: SourceKind,
    shapes: Mapping[str, MultiLineGeometry | Geometry],
    data_dates: Mapping[str, date] | None = None,
) -> TrailLayer:
    dates = data_dates or {}
    return make_layer(
        kind,
        [
            make_trail(identifier, kind, shape, data_date=dates.get(identifier))
            for identifier, shape in shapes.items()
        ],
    )


def unit(
    unit_id: str,
    footprint: Geometry,
    document_kind: HistoricDocumentKind = "planimetria",
    document_date: date = date(1875, 8, 6),
    retrieved_at: date = date(2026, 11, 20),
) -> HistoricUnitCoverage:
    return HistoricUnitCoverage(
        unit_id=unit_id,
        document_kind=document_kind,
        document_date=document_date,
        footprint=footprint,
        retrieved_at=retrieved_at,
    )


def historic_trail(
    identifier: str,
    points: Sequence[tuple[float, float]],
    document_kind: HistoricDocumentKind = "planimetria",
    status: str = "approved",
) -> HistoricTrail:
    reviewed = status != "proposed"
    return HistoricTrail(
        identifier=identifier,
        municipality_code=INE,
        document_kind=document_kind,
        document_id=identifier.split("-")[0],
        document_date=date(1875, 8, 6),
        document_date_precision="day",
        geometry=LineGeometry(points=tuple(p(x, y) for x, y in points), srid=25830),
        georeferencing_error_m=10.0,
        provenance=HISTORIC_PROVENANCE,
        status=status,  # type: ignore[arg-type]
        reviewer="usuario-sintetico" if reviewed else None,
        reviewed_at=datetime(2026, 11, 21, tzinfo=UTC) if reviewed else None,
    )


def coverage(
    document_kind: HistoricDocumentKind = "planimetria",
    units: Sequence[HistoricUnitCoverage] = (),
    trails: Sequence[HistoricTrail] = (),
) -> HistoricCoverage:
    return HistoricCoverage(
        document_kind=document_kind,
        provenance=HISTORIC_PROVENANCE,
        units=tuple(units),
        trails=tuple(trails),
    )


def empty_coverages() -> dict[HistoricDocumentKind, HistoricCoverage]:
    return {"planimetria": coverage("planimetria"), "mtn50": coverage("mtn50")}


# Lo que da cada franja en una comparación sintética: vuelo con imagen o None (omitida).
DEFAULT_SLOT_VINTAGES: Mapping[str, HistoricalVintage | None] = {
    "1956-1957": "AMS_1956-1957",
    "1975": None,
    "1991": "CM_1991",
    "1997-2003": "OLISTAT",
    "2006-2011": 2009,
    "2014-2020": None,
    "reciente": 2023,
}
NO_IMAGES_SLOT_VINTAGES: Mapping[str, HistoricalVintage | None] = {
    slot.name: None for slot in COMPARISON_SLOTS
}


def _image_url(window_index: int, vintage: HistoricalVintage) -> str:
    return f"https://example.invalid/wms?ventana={window_index}&vuelo={vintage}"


def comparison_for(
    axis: MultiLineGeometry,
    stretch_id: str = STRETCH_ID,
    slot_vintages: Mapping[str, HistoricalVintage | None] = DEFAULT_SLOT_VINTAGES,
    omission: str = "sin_cobertura",
) -> StretchComparison:
    """Comparación coherente con `axis` (las ventanas y píxeles que da el dominio)."""
    windows = comparison_windows(axis)
    assert windows is not None
    built: list[ComparisonWindow] = []
    for index, window in enumerate(windows):
        images: list[ComparisonImage] = []
        omitted: list[OmittedSlot] = []
        for slot in COMPARISON_SLOTS:
            vintage = slot_vintages[slot.name]
            if vintage is None:
                omitted.append(
                    OmittedSlot(
                        slot.name,
                        omission,  # type: ignore[arg-type]
                        slot.vintages,
                        "ConnectionError" if omission == "fallo" else None,
                    )
                )
                continue
            flight = FLIGHTS[vintage]
            images.append(
                ComparisonImage(
                    slot=slot.name,
                    vintage=vintage,
                    period=flight.period,
                    resolution_m=flight.resolution_m,
                    attribution=attribution_for(vintage),
                    sha256="4" * 64,
                    byte_size=1000,
                    provenance=Provenance(
                        flight.source,
                        _image_url(index, vintage),
                        IMAGE_DATE,
                        "CC BY 4.0",
                        Redistribution.PUBLIC,
                    ),
                )
            )
        built.append(
            ComparisonWindow(
                window=window,
                stretch_px=stretch_pixels(axis, window),
                images=tuple(images),
                omitted=tuple(omitted),
            )
        )
    return StretchComparison(
        stretch_id=stretch_id,
        municipality_code=INE,
        policy=COMPARISON_POLICY,
        status="generada",
        windows=tuple(built),
        gaps=DECLARED_GAPS,
    )


def manifest(*comparisons: StretchComparison, municipality_code: str = INE) -> ComparisonManifest:
    return ComparisonManifest(
        municipality_code=municipality_code,
        policy=COMPARISON_POLICY,
        gaps=DECLARED_GAPS,
        stretches=tuple(sorted(comparisons, key=lambda c: c.stretch_id)),
    )


def candidate_tables(
    candidates: Sequence[CutCandidate],
    comparisons: ComparisonManifest,
    layers: Mapping[SourceKind, PreparedLayer] | None = None,
    appears_min_fraction: float = APPEARS_MIN_FRACTION,
) -> tuple[dict[str, ConvergenceTable], dict[str, str]]:
    """Tablas de convergencia (historia #315) de `candidates` por `candidate_id`, como las
    calcula `BuildConvergenceTablesUseCase`: sin capas (salvo `layers`), sin cobertura
    histórica y con las imágenes de `comparisons`. Un tramo sin tabla va a los motivos."""
    tables: dict[str, ConvergenceTable] = {}
    failures: dict[str, str] = {}
    for candidate in candidates:
        try:
            tables[candidate.candidate_id] = build_convergence_table(
                stretch_from_candidate(candidate),
                CONVERGENCE_ROW_SOURCES,
                layers or {},
                empty_coverages(),
                comparisons.stretch(candidate.candidate_id),
                appears_min_fraction,
            )
        except StretchNotComputableError as error:
            failures[candidate.candidate_id] = error.reason
    return tables, failures


def indicio_tables(
    report: MunicipalDiscrepancyReport,
    appears_min_fraction: float = APPEARS_MIN_FRACTION,
) -> dict[str, ConvergenceTable]:
    """Tablas de convergencia (historia #316) de los registros con zona de `report`, por
    `record_id`: sin capas ni cobertura histórica y con imágenes coherentes con el trazo de
    cada zona (`comparison_for(image_axis(zona))`). Vale para el informe publicable: sus
    registros sin redactar llevan la misma zona que el interno."""
    tables: dict[str, ConvergenceTable] = {}
    for record in report.records:
        if record.zone_redacted:
            continue
        zone = record.discrepancy.zone
        if not any(zone.rings if isinstance(zone, Geometry) else zone.lines):
            continue  # zona vacía: el dominio la rechaza antes de mirar la tabla
        tramo = stretch_from_discrepancy(record, report.municipality_code)
        axis = image_axis(tramo.shape)
        tables[record.record_id] = build_convergence_table(
            tramo,
            CONVERGENCE_ROW_SOURCES,
            {},
            empty_coverages(),
            comparison_for(axis, stretch_id=record.record_id),
            appears_min_fraction,
        )
    return tables


# --- Dobles de los ports. ---------------------------------------------------------------
@dataclass
class FakeLayers:
    layers: dict[SourceKind, TrailLayer] = field(default_factory=dict)
    calls: list[tuple[SourceKind, str]] = field(default_factory=list)

    def get_layer(self, source: SourceKind, municipality_code: str) -> TrailLayer:
        self.calls.append((source, municipality_code))
        if source not in self.layers:
            raise TrailLayerUnavailableError(source, municipality_code, "sin capa sintética")
        return self.layers[source]


@dataclass
class FakeHistoric:
    coverages: dict[HistoricDocumentKind, HistoricCoverage] = field(default_factory=empty_coverages)
    calls: list[HistoricDocumentKind] = field(default_factory=list)

    def get_coverage(self, document_kind: HistoricDocumentKind) -> HistoricCoverage:
        self.calls.append(document_kind)
        return self.coverages[document_kind]


@dataclass
class FakeComparisons:
    manifest: ComparisonManifest | None = None
    calls: list[str] = field(default_factory=list)

    def get_manifest(self, municipality_code: str) -> ComparisonManifest | None:
        self.calls.append(municipality_code)
        return self.manifest
