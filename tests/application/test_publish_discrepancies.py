"""Caso de uso `PublishDiscrepanciesUseCase` (historias #94 y #316) con dobles. Datos
SINTÉTICOS: coordenadas inventadas cerca de `X0, Y0` de `tests/convergence_support.py`,
`record_id` de 16 hexadecimales inventados."""

import dataclasses
from collections.abc import Sequence
from datetime import date

import pytest

from parcel_intel.application.build_convergence_tables import (
    NO_MANIFEST,
    BuildConvergenceTablesUseCase,
)
from parcel_intel.application.publish_discrepancies import (
    INDICIO_NO_MANIFEST,
    PublishDiscrepanciesUseCase,
    _for_indicios,
)
from parcel_intel.domain.comparison_image import ComparisonManifest
from parcel_intel.domain.convergence import image_axis, stretch_from_discrepancy
from parcel_intel.domain.discrepancy import Discrepancy, Measurement, SourceRef, TrailRef
from parcel_intel.domain.discrepancy_criterion import CRITERION
from parcel_intel.domain.discrepancy_policy import redact_for_publication
from parcel_intel.domain.discrepancy_report import DiscrepancyRecord, MunicipalDiscrepancyReport
from parcel_intel.domain.geometry import MultiLineGeometry
from parcel_intel.domain.historic_coverage import HistoricCoverage
from parcel_intel.domain.historic_trail import HistoricDocumentKind
from parcel_intel.domain.provenance import Provenance, Redistribution
from parcel_intel.domain.published_comparison import (
    INCOHERENT,
    NO_IMAGES,
    NOT_IN_MANIFEST,
    RERUN,
    TOO_LONG,
)
from parcel_intel.domain.published_discrepancy import (
    INTERNAL_MISMATCH,
    INTERNAL_MISSING,
    INTERNAL_REDACTED,
    DiscrepancyLayerPublication,
    published_indicio_ids,
)
from parcel_intel.domain.published_layers import PublishedFile
from parcel_intel.domain.trail import SourceKind
from parcel_intel.ports.errors import (
    ComparisonManifestFileError,
    DiscrepancyReportUnavailableError,
    HistoricCoverageError,
    TrailLayerFileError,
)
from tests.convergence_support import (
    X0,
    Y0,
    FakeComparisons,
    FakeHistoric,
    FakeLayers,
    comparison_for,
    manifest,
)

ID_A = "00000000000000a1"
ID_B = "00000000000000b2"
ID_C = "00000000000000c3"


def _source(kind: SourceKind, redistribution: Redistribution) -> SourceRef:
    return SourceRef(
        kind,
        Provenance(
            "SINTÉTICO", "https://example.invalid", date(2026, 11, 20), "SINT", redistribution
        ),
        date(2014, 1, 1),
    )


def _zone(offset: float = 0.0, length: float = 100.0) -> MultiLineGeometry:
    return MultiLineGeometry(lines=(((X0, Y0 + offset), (X0 + length, Y0 + offset)),), srid=25830)


def _record(
    record_id: str = ID_A,
    a: SourceKind = "via_pecuaria_oficial",
    *,
    offset: float = 0.0,
    length: float = 100.0,
    redistribution: Redistribution = Redistribution.PUBLIC,
) -> DiscrepancyRecord:
    """Registro del informe INTERNO (sin redactar)."""
    discrepancy = Discrepancy(
        kind="AUSENTE_EN_FUENTE",
        zone=_zone(offset, length),
        subject=TrailRef(f"99999-{record_id}", _source(a, redistribution)),
        counterpart_source=_source("ign_red_transporte", Redistribution.PUBLIC),
        counterpart_trails=(),
        confidence=0.5,
        evidence=(Measurement("radio_busqueda_m", 33.5, "m"),),
        criterion=CRITERION,
    )
    return DiscrepancyRecord(record_id, discrepancy, (), False)


def _internal(code: str, *records: DiscrepancyRecord) -> MunicipalDiscrepancyReport:
    return MunicipalDiscrepancyReport(
        municipality_code=code,
        criterion_id="discrepancia_trazados@1",
        layers=(),
        records=records,
        redacted=False,
        municipality_name="Municipio sintético",
    )


def _pair(
    code: str, *records: DiscrepancyRecord
) -> tuple[MunicipalDiscrepancyReport, MunicipalDiscrepancyReport]:
    """(publicable, interno), como los escribe `discrepancias`."""
    internal = _internal(code, *records)
    return redact_for_publication(internal), internal


def _manifest_for(internal: MunicipalDiscrepancyReport) -> ComparisonManifest:
    """Manifiesto coherente con los indicios publicables del interno (como #314)."""
    comparisons = []
    publishable = redact_for_publication(internal)
    for record_id, _ in published_indicio_ids(publishable):
        record = next(r for r in internal.records if r.record_id == record_id)
        axis = image_axis(stretch_from_discrepancy(record, internal.municipality_code).shape)
        comparisons.append(comparison_for(axis, stretch_id=record_id))
    return manifest(*comparisons, municipality_code=internal.municipality_code)


class _Reports:
    def __init__(self, reports: dict[str, MunicipalDiscrepancyReport | Exception]) -> None:
        self._reports = reports
        self.calls: list[str] = []

    def get_report(self, municipality_code: str) -> MunicipalDiscrepancyReport:
        self.calls.append(municipality_code)
        value = self._reports.get(municipality_code)
        if value is None:
            raise DiscrepancyReportUnavailableError(municipality_code, "no existe")
        if isinstance(value, Exception):
            raise value
        return value


class _Sink:
    def __init__(self) -> None:
        self.calls: list[list[DiscrepancyLayerPublication]] = []

    def publish(
        self, publications: Sequence[DiscrepancyLayerPublication]
    ) -> tuple[PublishedFile, ...]:
        self.calls.append(list(publications))
        return tuple(
            PublishedFile(
                "discrepancias",
                p.municipality_code,
                f"discrepancias/{p.municipality_code}.geojson",
                1,
                None,
                False,
            )
            for p in publications
        )


class _FailingHistoric(FakeHistoric):
    def get_coverage(self, document_kind: HistoricDocumentKind) -> HistoricCoverage:
        raise HistoricCoverageError("280075-w1", "aprobaciones caducadas: T001")


class _BrokenLayer(TrailLayerFileError):
    def __init__(self) -> None:
        super().__init__("Capa no válida en /ruta/secreta: valor-secreto")
        self.source = "via_pecuaria_oficial"


class _BrokenLayers(FakeLayers):
    def get_layer(self, source: SourceKind, municipality_code: str) -> object:  # type: ignore[override]
        raise _BrokenLayer()


class _Setup:
    """Caso de uso con dobles; registra las lecturas del interno, capas, histórico y
    manifiesto."""

    def __init__(
        self,
        pairs: Sequence[tuple[MunicipalDiscrepancyReport, MunicipalDiscrepancyReport]],
        *,
        internal: dict[str, MunicipalDiscrepancyReport | Exception] | None = None,
        comparisons: FakeComparisons | None = None,
        layers: FakeLayers | None = None,
        historic: FakeHistoric | None = None,
    ) -> None:
        self.reports = _Reports({p.municipality_code: p for p, _ in pairs})
        self.internal = _Reports(
            internal if internal is not None else {i.municipality_code: i for _, i in pairs}
        )
        first = pairs[0][1] if pairs else None
        self.comparisons = comparisons or FakeComparisons(
            _manifest_for(first) if first is not None else None
        )
        self.layers = layers or FakeLayers()
        self.historic = historic or FakeHistoric()
        self.sink = _Sink()
        self.use_case = PublishDiscrepanciesUseCase(
            self.reports,
            self.sink,
            internal_reports=self.internal,
            convergence=BuildConvergenceTablesUseCase(
                layers=self.layers,
                historic=self.historic,
                comparisons=self.comparisons,
                without_images_if_too_long=True,
            ),
        )

    def reads(self) -> int:
        return (
            len(self.internal.calls)
            + len(self.layers.calls)
            + len(self.historic.calls)
            + len(self.comparisons.calls)
        )


def _failure(setup: _Setup, codes: Sequence[str] = ("28043",)) -> str:
    result = setup.use_case.execute(codes)
    assert setup.sink.calls == []
    assert result.published == ()
    (failure,) = result.failures
    return failure.reason


# --- Lo de #94 -------------------------------------------------------------------------


def test_publica_y_cuenta_vacios_y_omitidos() -> None:
    colmenar = _pair("28043", _record(), _record(ID_B, "catastro_vt", offset=300.0))
    vacio = _pair("28045", _record(ID_C, "catastro_vt"))
    setup = _Setup([colmenar, vacio])
    result = setup.use_case.execute(["28043", "28045"])
    assert result.failures == ()
    assert [f.municipality_code for f in result.published] == ["28043"]
    assert result.empty == ("28045",)
    assert [(c, o.subject, o.counterpart) for c, o in result.omitted] == [
        ("28043", 1, 0),
        ("28045", 1, 0),
    ]
    assert result.kinds == (("28043", ("AUSENTE_EN_FUENTE",)),)
    assert len(setup.sink.calls) == 1 and len(setup.sink.calls[0]) == 1
    # El municipio vacío no lee su interno (CA12).
    assert setup.internal.calls == ["28043"]


@pytest.mark.parametrize(
    ("report", "prefix"),
    [
        (None, "sin fichero: no existe"),
        (
            dataclasses.replace(_pair("28043", _record())[0], redacted=False),
            "no publicable: no es la variante",
        ),
        (_pair("28999", _record())[0], "el fichero es del municipio 28999"),
        (
            dataclasses.replace(_pair("28043", _record())[0], municipality_name=None),
            "no publicable: el informe no trae el nombre del municipio",
        ),
    ],
)
def test_un_fallo_de_94_no_escribe_ni_lee_nada_mas(
    report: MunicipalDiscrepancyReport | None, prefix: str
) -> None:
    pairs = [_pair("28045", _record())]
    setup = _Setup(pairs)
    if report is not None:
        setup.reports._reports["28043"] = report
    result = setup.use_case.execute(["28043"])
    assert setup.sink.calls == [] and result.published == ()
    (failure,) = result.failures
    assert failure.municipality_code == "28043"
    assert failure.reason.startswith(prefix), failure.reason
    assert setup.reads() == 0


def test_fuente_a_no_redistribuible_sin_redactar_no_lee_nada_mas() -> None:
    publishable, internal = _pair("28043", _record())
    record = internal.records[0]
    unredacted = dataclasses.replace(
        record,
        discrepancy=dataclasses.replace(
            record.discrepancy,
            subject=TrailRef("99999-x", _source("catastro_vt", Redistribution.INTERNAL_ONLY)),
        ),
    )
    setup = _Setup([(dataclasses.replace(publishable, records=(unredacted,)), internal)])
    reason = _failure(setup)
    assert reason.startswith("no publicable: registro sin redactar con fuente A 'catastro_vt'")
    assert setup.reads() == 0


def test_procedencia_interna_del_lado_a_sigue_como_hoy() -> None:
    setup = _Setup([_pair("28043", _record(redistribution=Redistribution.INTERNAL_ONLY))])
    assert _failure(setup) == (
        "no redistribuible: procedencia 'SINTÉTICO' marcada 'solo_uso_interno'; "
        "regenera con `discrepancias`"
    )


def test_un_fichero_corrupto_se_propaga() -> None:
    setup = _Setup([])
    setup.reports._reports["28043"] = RuntimeError("corrupto")
    with pytest.raises(RuntimeError, match="corrupto"):
        setup.use_case.execute(["28043"])
    assert setup.sink.calls == []


def test_un_interno_corrupto_se_propaga() -> None:
    pair = _pair("28043", _record())
    setup = _Setup([pair], internal={"28043": RuntimeError("interno corrupto")})
    with pytest.raises(RuntimeError, match="interno corrupto"):
        setup.use_case.execute(["28043"])
    assert setup.sink.calls == []


def test_sin_publicaciones_no_llama_al_sink() -> None:
    setup = _Setup([_pair("28045")])
    result = setup.use_case.execute(["28045"])
    assert setup.sink.calls == []
    assert result.empty == ("28045",)
    assert setup.reads() == 0


# --- Tabla de convergencia (#316) ------------------------------------------------------


def test_cada_indicio_lleva_su_tabla() -> None:
    setup = _Setup([_pair("28043", _record(), _record(ID_B, offset=300.0))])
    result = setup.use_case.execute(["28043"])
    assert result.failures == ()
    (publication,) = setup.sink.calls[0]
    assert len(publication.content.items) == 2
    for item in publication.content.items:
        assert [row.source_key for row in item.convergence.rows][:3] == [
            "via_pecuaria_oficial",
            "ign_red_transporte",
            "catastro_vt",
        ]
        assert item.convergence.images_note is None
    assert publication.convergence_notes.appears_min_fraction == 0.5
    assert setup.layers.calls and setup.historic.calls and setup.comparisons.calls


def test_sin_informe_interno() -> None:
    setup = _Setup([_pair("28043", _record())], internal={})
    assert _failure(setup) == INTERNAL_MISSING
    assert setup.layers.calls == [] and setup.comparisons.calls == []


def test_interno_redactado_o_que_no_casa_sin_prefijo_ni_ids() -> None:
    publishable, internal = _pair("28043", _record(), _record(ID_B, offset=300.0))
    setup = _Setup([(publishable, internal)], internal={"28043": publishable})
    assert _failure(setup) == INTERNAL_REDACTED
    swapped = dataclasses.replace(internal, records=internal.records[::-1])
    setup = _Setup([(publishable, internal)], internal={"28043": swapped})
    reason = _failure(setup)
    assert reason == INTERNAL_MISMATCH
    assert ID_A not in reason and ID_B not in reason
    assert setup.layers.calls == [] and setup.comparisons.calls == []


def test_sin_manifiesto_falla_el_municipio() -> None:
    setup = _Setup([_pair("28043", _record())], comparisons=FakeComparisons(None))
    assert _failure(setup) == INDICIO_NO_MANIFEST
    assert INDICIO_NO_MANIFEST.endswith("ejecuta imagenes-comparacion-indicios")


def test_indicio_ausente_del_manifiesto_con_su_public_id_y_el_comando_de_indicios() -> None:
    publishable, internal = _pair("28043", _record(), _record(ID_B, offset=300.0))
    only_b = _manifest_for(_internal("28043", internal.records[1]))
    setup = _Setup([(publishable, internal)], comparisons=FakeComparisons(only_b))
    reason = _failure(setup)
    ((_, public_a), _) = published_indicio_ids(publishable)
    assert reason == f"indicio {public_a}: tabla de convergencia: {NOT_IN_MANIFEST}-indicios"
    assert ID_A not in reason


def test_se_informa_el_primer_fallo_en_el_orden_de_los_indicios() -> None:
    # ID_B < ID_C por stretch_id, pero en el informe va primero ID_C: manda el informe.
    publishable, internal = _pair("28043", _record(ID_C), _record(ID_B, offset=300.0))
    setup = _Setup([(publishable, internal)], comparisons=FakeComparisons(manifest()))
    ((_, first_public), _) = published_indicio_ids(publishable)
    assert _failure(setup).startswith(f"indicio {first_public}: ")


def test_manifiesto_de_otra_politica_manda_a_imagenes_comparacion_indicios() -> None:
    pair = _pair("28043", _record())
    other = dataclasses.replace(_manifest_for(pair[1]), gaps=())
    setup = _Setup([pair], comparisons=FakeComparisons(other))
    assert _failure(setup) == (
        "tabla de convergencia: el manifiesto declara otros huecos que la política vigente; "
        f"{RERUN}-indicios"
    )


def test_manifiesto_ilegible_da_su_ruta() -> None:
    class _Unreadable(FakeComparisons):
        def get_manifest(self, municipality_code: str) -> ComparisonManifest | None:
            raise ComparisonManifestFileError("data/x/28043.json", "no es JSON")

    setup = _Setup([_pair("28043", _record())], comparisons=_Unreadable())
    assert _failure(setup) == (
        "Manifiesto de imágenes de comparación no válido data/x/28043.json: no es JSON"
    )


def test_capa_ilegible_e_historico_con_motivo_fijo() -> None:
    setup = _Setup([_pair("28043", _record())], layers=_BrokenLayers())
    reason = _failure(setup)
    assert reason == (
        "tabla de convergencia: capa via_pecuaria_oficial no válida; regenera las capas derivadas"
    )
    assert "secreta" not in reason
    setup = _Setup([_pair("28043", _record())], historic=_FailingHistoric())
    assert _failure(setup) == (
        "tabla de convergencia: cobertura histórica: la unidad 280075-w1 no es utilizable: "
        "aprobaciones caducadas: T001"
    )


def test_indicio_demasiado_largo_sale_sin_imagenes_y_no_falla() -> None:
    long_record = _record(length=3000.0)
    pair = _pair("28043", long_record)
    too_long = dataclasses.replace(
        comparison_for(_zone(0.0, 100.0), stretch_id=ID_A),
        status="tramo_demasiado_largo",
        windows=(),
    )
    setup = _Setup([pair], comparisons=FakeComparisons(manifest(too_long)))
    result = setup.use_case.execute(["28043"])
    assert result.failures == ()
    ((item,),) = [p.content.items for p in setup.sink.calls[0]]
    assert item.convergence.images_note == "Imagen no disponible"
    assert all(row.evidence_kind == "trazado" for row in item.convergence.rows)


def test_otro_municipio_falla_y_no_escribe_ninguno() -> None:
    good = _pair("28043", _record())
    bad = _pair("28045", _record(ID_B))
    setup = _Setup([good, bad], internal={"28043": good[1]})
    result = setup.use_case.execute(["28043", "28045"])
    assert setup.sink.calls == []
    assert [(f.municipality_code, f.reason) for f in result.failures] == [
        ("28045", INTERNAL_MISSING)
    ]


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (NOT_IN_MANIFEST, f"{NOT_IN_MANIFEST}-indicios"),
        (INCOHERENT, f"{INCOHERENT}-indicios"),
        (f"una franja se omitió por un fallo de descarga; {RERUN}", None),
        (NO_MANIFEST, INDICIO_NO_MANIFEST),
        (TOO_LONG, TOO_LONG),
        (NO_IMAGES, NO_IMAGES),
    ],
)
def test_for_indicios_reescribe_solo_los_que_mandan_a_imagenes_comparacion(
    reason: str, expected: str | None
) -> None:
    rewritten = _for_indicios(reason)
    assert rewritten == (expected or f"{reason}-indicios")
    if "imagenes-comparacion" in reason:
        assert rewritten.endswith("imagenes-comparacion-indicios")
        assert rewritten.count("imagenes-comparacion") == 1
