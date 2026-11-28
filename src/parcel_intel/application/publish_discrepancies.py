"""Caso de uso: publicar los indicios de discrepancia de uno o varios municipios
(historias #94 y #316, ADR 0044 y 0057).

Lee el informe **publicable** que escribió `discrepancias` (#32), deja solo los
indicios publicables con su ficha y su tabla de convergencia de fuentes
(`build_discrepancy_layer`) y se los pasa al sink. Todo o nada: si un municipio falla, no
se llama al sink y no se escribe nada. Un fichero corrupto (cualquier otro error del
adapter) se propaga.

Orden por municipio (CA12 de #316): primero las reglas de #94 sobre el publicable, con
sus mensajes de siempre y sin lecturas caras; un municipio sin indicios publicables sale
«vacío» sin leer nada más. Solo entonces se lee el informe **interno** (casado con el
publicable), el manifiesto de imágenes de los indicios (#314), las capas derivadas y
`curated/historico/` para la tabla.

Ningún mensaje lleva un `record_id`: solo el `public_id`, que ya es público.
"""

from collections.abc import Sequence

from parcel_intel.application.build_convergence_tables import (
    NO_MANIFEST,
    BuildConvergenceTablesUseCase,
)
from parcel_intel.domain.convergence import (
    ConvergenceNotComputableError,
    ConvergenceTable,
    InvalidConvergenceRowError,
    InvalidConvergenceStretchError,
    InvalidConvergenceTableError,
)
from parcel_intel.domain.discrepancy_report import MunicipalDiscrepancyReport
from parcel_intel.domain.publication import NotRedistributableError
from parcel_intel.domain.published_convergence import ConvergenceNotPublishableError
from parcel_intel.domain.published_discrepancy import (
    INTERNAL_MISSING,
    DiscrepancyLayerBuild,
    DiscrepancyLayerPublication,
    DiscrepancyPublicationFailure,
    DiscrepancyPublicationResult,
    NotPublishableReportError,
    OmittedByLicense,
    build_discrepancy_layer,
    check_internal_report,
    indicio_stretches,
    published_indicio_ids,
)
from parcel_intel.ports.discrepancy_report_source import DiscrepancyReportSource
from parcel_intel.ports.errors import (
    ComparisonManifestFileError,
    DiscrepancyReportUnavailableError,
    HistoricCoverageError,
    TrailLayerFileError,
)
from parcel_intel.ports.published_discrepancy_sink import PublishedDiscrepancySink

INDICIO_COMPARISON_COMMAND = "imagenes-comparacion-indicios"
INDICIO_NO_MANIFEST = (
    f"sin manifiesto de imágenes de comparación; ejecuta {INDICIO_COMPARISON_COMMAND}"
)
_TABLE = "tabla de convergencia"
_INVALID_ROW = "fila no válida"
_RERUN_SUFFIXES = ("ejecuta imagenes-comparacion", "ejecutar imagenes-comparacion")


def _for_indicios(reason: str) -> str:
    """Los motivos heredados de #172/#240 que mandan a `imagenes-comparacion` se reescriben
    a `imagenes-comparacion-indicios`; los demás, tal cual."""
    if reason.endswith(_RERUN_SUFFIXES):
        return f"{reason}-indicios"
    return reason


class _MunicipalityFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PublishDiscrepanciesUseCase:
    def __init__(
        self,
        reports: DiscrepancyReportSource,
        sink: PublishedDiscrepancySink,
        internal_reports: DiscrepancyReportSource,
        convergence: BuildConvergenceTablesUseCase,
    ) -> None:
        self._reports = reports
        self._sink = sink
        self._internal_reports = internal_reports
        self._convergence = convergence

    def execute(self, municipality_codes: Sequence[str]) -> DiscrepancyPublicationResult:
        publications: list[DiscrepancyLayerPublication] = []
        empty: list[str] = []
        omitted: list[tuple[str, OmittedByLicense]] = []
        failures: list[DiscrepancyPublicationFailure] = []
        for code in municipality_codes:
            try:
                build = self._build(code)
            except _MunicipalityFailed as error:
                failures.append(DiscrepancyPublicationFailure(code, error.reason))
                continue
            omitted.append((code, build.omitted))
            if build.publication is None:
                empty.append(code)
            else:
                publications.append(build.publication)
        if failures:
            return DiscrepancyPublicationResult(
                published=(), empty=tuple(empty), omitted=tuple(omitted), failures=tuple(failures)
            )
        published = self._sink.publish(publications) if publications else ()
        return DiscrepancyPublicationResult(
            published=published,
            empty=tuple(empty),
            omitted=tuple(omitted),
            failures=(),
            kinds=tuple(
                (p.municipality_code, tuple(item.card.kind for item in p.content.items))
                for p in publications
            ),
        )

    def _build(self, code: str) -> DiscrepancyLayerBuild:
        try:
            report = self._reports.get_report(code)
        except DiscrepancyReportUnavailableError as error:
            raise _MunicipalityFailed(f"sin fichero: {error.reason}") from error
        if report.municipality_code != code:
            raise _MunicipalityFailed(f"el fichero es del municipio {report.municipality_code}")
        try:
            # Reglas de #94 primero, sin leer el interno, el manifiesto ni las capas (CA12).
            ids = published_indicio_ids(report)
            if not ids:
                return build_discrepancy_layer(report, {})
        except NotPublishableReportError as error:
            raise _MunicipalityFailed(f"no publicable: {error.reason}") from error
        except NotRedistributableError as error:
            raise _MunicipalityFailed(_not_redistributable(error)) from error
        try:
            internal = self._internal_reports.get_report(code)
        except DiscrepancyReportUnavailableError as error:
            raise _MunicipalityFailed(INTERNAL_MISSING) from error
        try:
            check_internal_report(report, internal)
        except NotPublishableReportError as error:
            raise _MunicipalityFailed(error.reason) from error  # sin «no publicable:»
        tables = self._tables(code, report, internal, dict(ids))
        try:
            return build_discrepancy_layer(report, tables)
        except NotPublishableReportError as error:
            raise _MunicipalityFailed(f"no publicable: {error.reason}") from error
        except NotRedistributableError as error:
            raise _MunicipalityFailed(_not_redistributable(error)) from error
        except ConvergenceNotPublishableError as error:
            raise _MunicipalityFailed(f"{_TABLE}: {error.reason}") from error

    def _tables(
        self,
        code: str,
        report: MunicipalDiscrepancyReport,
        internal: MunicipalDiscrepancyReport,
        public: dict[str, str],
    ) -> dict[str, ConvergenceTable]:
        try:
            result = self._convergence.execute(code, indicio_stretches(report, internal))
        except InvalidConvergenceStretchError as error:
            raise _MunicipalityFailed(f"{_TABLE}: tramo no válido") from error
        except TrailLayerFileError as error:
            # Motivo fijo: ni la ruta ni el mensaje de pydantic (como en #315).
            raise _MunicipalityFailed(
                f"{_TABLE}: capa {error.source} no válida; regenera las capas derivadas"
            ) from error
        except HistoricCoverageError as error:
            raise _MunicipalityFailed(
                f"{_TABLE}: cobertura histórica: la unidad {error.unit_id} no es utilizable: "
                f"{error.reason}"
            ) from error
        except ComparisonManifestFileError as error:
            raise _MunicipalityFailed(str(error)) from error  # la ruta, sin ids
        except ConvergenceNotComputableError as error:
            raise _MunicipalityFailed(f"{_TABLE}: {_for_indicios(error.reason)}") from error
        except (InvalidConvergenceRowError, InvalidConvergenceTableError) as error:
            raise _MunicipalityFailed(f"{_TABLE}: {_INVALID_ROW}") from error
        failed = {r.stretch_id: r.failure for r in result.results if r.failure is not None}
        for record_id, public_id in public.items():  # orden de `ids`: determinista
            failure = failed.get(record_id)
            if failure is None:
                continue
            if failure == NO_MANIFEST:
                raise _MunicipalityFailed(INDICIO_NO_MANIFEST)
            raise _MunicipalityFailed(f"indicio {public_id}: {_TABLE}: {_for_indicios(failure)}")
        return {r.stretch_id: r.table for r in result.results if r.table is not None}


def _not_redistributable(error: NotRedistributableError) -> str:
    provenance = error.provenance
    return (
        f"no redistribuible: procedencia '{provenance.source}' marcada "
        f"'{provenance.redistribution.value}'; regenera con `discrepancias`"
    )
