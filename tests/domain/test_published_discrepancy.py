"""Dominio de publicación de indicios (historia #94, ADR 0044). Datos SINTÉTICOS."""

import dataclasses
import hashlib
from datetime import date
from typing import get_args

import pytest

from parcel_intel.domain.discrepancy import (
    Discrepancy,
    DiscrepancyKind,
    Measurement,
    SourceRef,
    TrailRef,
)
from parcel_intel.domain.discrepancy_criterion import CRITERION
from parcel_intel.domain.discrepancy_policy import REDISTRIBUTABLE_SOURCES, redact_for_publication
from parcel_intel.domain.discrepancy_report import DiscrepancyRecord, MunicipalDiscrepancyReport
from parcel_intel.domain.geometry import Geometry, MultiLineGeometry, TrailShape
from parcel_intel.domain.provenance import Provenance, Redistribution
from parcel_intel.domain.public_card import build_card
from parcel_intel.domain.publication import NotRedistributableError
from parcel_intel.domain.published_convergence import convergence_notes, publish_convergence
from parcel_intel.domain.published_discrepancy import (
    CADASTRAL_REFERENCE_RE,
    INTERNAL_MISMATCH,
    INTERNAL_REDACTED,
    NO_CONVERGENCE_TABLE,
    PARCEL_LOCATOR_RE,
    PUBLISHABLE_COUNTERPART_SOURCES,
    DiscrepancyLayerBuild,
    NotPublishableReportError,
    build_discrepancy_layer,
    check_internal_report,
    indicio_stretches,
    public_indicio_id,
    published_indicio_ids,
)
from parcel_intel.domain.trail import SourceKind
from tests.convergence_support import indicio_tables

LINE = MultiLineGeometry(lines=(((465000.0, 4440000.0), (465200.0, 4440000.0)),), srid=25830)


def _source(kind: SourceKind, public: bool = True) -> SourceRef:
    return SourceRef(
        kind=kind,
        provenance=Provenance(
            source=f"SINTÉTICO {kind}",
            url=f"https://example.invalid/{kind}",
            retrieved_at=date(2026, 11, 20),
            license="SINTÉTICA",
            redistribution=Redistribution.PUBLIC if public else Redistribution.INTERNAL_ONLY,
        ),
        data_date=date(2014, 1, 1),
    )


def _record(
    a: SourceKind = "via_pecuaria_oficial",
    b: SourceKind = "ign_red_transporte",
    *,
    trail_id: str = "VP-1",
    zone: TrailShape = LINE,
    redacted: bool = False,
    kind: DiscrepancyKind = "AUSENTE_EN_FUENTE",
    public_subject: bool = True,
) -> DiscrepancyRecord:
    discrepancy = Discrepancy(
        kind=kind,
        zone=zone,
        subject=TrailRef(trail_id, _source(a, public_subject)),
        counterpart_source=_source(b, b in REDISTRIBUTABLE_SOURCES),
        counterpart_trails=(),
        confidence=0.6,
        evidence=(Measurement("desplazamiento_mediano_m", 20.0, "m"),),
        criterion=CRITERION,
    )
    # Como los reales, 16 hexadecimales (los exige el tramo de la tabla, #316).
    record_id = hashlib.sha256(trail_id.encode("utf-8")).hexdigest()[:16]
    return DiscrepancyRecord(
        record_id=record_id, discrepancy=discrepancy, trails=(), zone_redacted=redacted
    )


def _report(*records: DiscrepancyRecord, **changes: object) -> MunicipalDiscrepancyReport:
    report = MunicipalDiscrepancyReport(
        municipality_code="28043",
        criterion_id="discrepancia_trazados@1",
        layers=(),
        records=records,
        redacted=True,
        municipality_name="Colmenar de Oreja",
    )
    return dataclasses.replace(report, **changes)  # type: ignore[arg-type]


def _build(report: MunicipalDiscrepancyReport) -> DiscrepancyLayerBuild:
    """`build_discrepancy_layer` con la tabla de convergencia de cada indicio (#316)."""
    return build_discrepancy_layer(report, indicio_tables(report))


# --- Patrones de última barrera ---------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    [
        "99043A00109001",  # rústica, 14
        "99043A001090010001AB",  # rústica, 20
        "9904301VK4590S",  # urbana, 14
        "9904301VK4590S0001WX",  # urbana, 20
        "ref 99043A00109001.",
    ],
)
def test_la_forma_de_referencia_catastral_casa_rustica_y_urbana(reference: str) -> None:
    assert CADASTRAL_REFERENCE_RE.search(reference) is not None


@pytest.mark.parametrize(
    "text",
    [
        # URLs reales de bbox de las capas publicadas (vías pecuarias e IGN).
        "https://idem.comunidad.madrid/geoidem/Zonas/wfs?bbox=470173.1391980562%2C"
        "4433515.89948871%2C483164.45611997315%2C4451633.093908208%2CEPSG%3A25830"
        "&request=GetFeature&service=WFS&typenames=Zonas%3AIDEM_MA_VIAS_PECUARIAS",
        "https://api-features.ign.es/collections/roadlink/items?bbox=-3.62%2C39.98%2C"
        "-3.15%2C40.22&filter=surfacecategory%3D%27unpaved%27&limit=500&offset=1000",
        "Obra derivada de IGR-RT 2026 CC-BY 4.0 Instituto Geográfico Nacional",
        "2026-11-26",
        "99043a00109001",  # en minúsculas no es una referencia
    ],
)
def test_textos_publicados_no_casan_con_la_forma_de_referencia(text: str) -> None:
    assert CADASTRAL_REFERENCE_RE.search(text) is None
    assert PARCEL_LOCATOR_RE.search(text) is None


def test_localizador_de_parcela_en_url() -> None:
    url = "https://ovc.catastro.meh.es/x?Provincia=28&Municipio=43&Poligono=1&Parcela=9001"
    assert PARCEL_LOCATOR_RE.search(url) is not None
    assert PARCEL_LOCATOR_RE.search("https://x.invalid/?refcat=1") is not None


# --- Construcción de la capa -------------------------------------------------------------


def test_contrapartes_publicables_son_las_redistribuibles_y_catastro() -> None:
    assert PUBLISHABLE_COUNTERPART_SOURCES == REDISTRIBUTABLE_SOURCES | {"catastro_vt"}
    assert set(get_args(SourceKind)) == PUBLISHABLE_COUNTERPART_SOURCES


def test_publica_la_ficha_y_la_zona_y_cuenta_el_lado_a_redactado() -> None:
    publishable = _record()
    catastro_b = _record(
        b="catastro_vt",
        trail_id="VP-2",
        zone=MultiLineGeometry(lines=(((465000.0, 4440300.0), (465100.0, 4440300.0)),), srid=25830),
    )
    redacted = _record(
        a="catastro_vt",
        trail_id="99043A00109002",
        zone=Geometry(rings=(), srid=25830),
        redacted=True,
    )
    build = _build(_report(redacted, publishable, catastro_b))
    assert (build.omitted.subject, build.omitted.counterpart, build.omitted.total) == (1, 0, 1)
    assert build.publication is not None
    items = build.publication.content.items
    assert [i.card for i in items] == [
        build_card(publishable.discrepancy),
        build_card(catastro_b.discrepancy),
    ]
    assert items[0].zone == LINE
    assert items[0].provenance == publishable.discrepancy.subject.source.provenance
    assert build.publication.municipality_name == "Colmenar de Oreja"
    assert build.publication.criterion_id == "discrepancia_trazados@1"
    assert build.publication.content.name == "discrepancias/28043"


def test_sin_indicios_publicables_no_hay_publicacion() -> None:
    redacted = _record(a="catastro_vt", zone=Geometry(rings=(), srid=25830), redacted=True)
    build = _build(_report(redacted))
    assert build.publication is None
    assert build.omitted.subject == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"redacted": False}, "variante publicable"),
        ({"municipality_name": None}, "nombre del municipio"),
        ({"municipality_name": ""}, "nombre del municipio"),
    ],
)
def test_informe_no_publicable(changes: dict[str, object], message: str) -> None:
    with pytest.raises(NotPublishableReportError) as error:
        _build(_report(_record(), **changes))
    assert message in error.value.reason
    assert error.value.municipality_code == "28043"


def test_registro_sin_redactar_con_lado_a_no_redistribuible_falla() -> None:
    record = _record(a="catastro_vt", public_subject=False)
    with pytest.raises(NotPublishableReportError, match="sin redactar"):
        _build(_report(record))


def test_registro_sin_redactar_con_zona_vacia_falla() -> None:
    record = _record(zone=Geometry(rings=(), srid=25830))
    with pytest.raises(NotPublishableReportError, match="zona vacía"):
        _build(_report(record))
    empty_line = _record(zone=MultiLineGeometry(lines=((),), srid=25830))
    with pytest.raises(NotPublishableReportError, match="zona vacía"):
        _build(_report(empty_line))


def test_trazado_a_con_procedencia_interna_no_pasa_la_puerta() -> None:
    record = _record(public_subject=False)
    with pytest.raises(NotRedistributableError):
        _build(_report(record))


# --- Identificador público --------------------------------------------------------------


def test_el_id_publico_no_depende_de_los_ids_de_trazado() -> None:
    a = _record(trail_id="VP-1").discrepancy
    b = _record(trail_id="99043A00109001").discrepancy
    assert public_indicio_id(a) == public_indicio_id(b)
    assert len(public_indicio_id(a)) == 16
    int(public_indicio_id(a), 16)


def test_el_id_publico_cambia_con_la_zona_el_tipo_y_las_fuentes() -> None:
    base = _record().discrepancy
    other_zone = dataclasses.replace(
        base,
        zone=MultiLineGeometry(lines=(((465000.0, 4440001.0), (465200.0, 4440000.0)),), srid=25830),
    )
    other_kind = dataclasses.replace(base, kind="DESPLAZAMIENTO")
    other_b = _record(b="catastro_vt").discrepancy
    ids = {public_indicio_id(d) for d in (base, other_zone, other_kind, other_b)}
    assert len(ids) == 4


def test_colision_de_id_lleva_sufijo_en_orden_canonico() -> None:
    # Dos trazados idénticos de la misma fuente con distinto id: mismo id público.
    first, second, third = (_record(trail_id=f"VP-{n}") for n in (1, 2, 3))
    build = _build(_report(first, second, third))
    assert build.publication is not None
    ids = [item.public_id for item in build.publication.content.items]
    base = public_indicio_id(first.discrepancy)
    assert ids == [base, f"{base}-2", f"{base}-3"]


def test_is_publishable_indicio_fuente_a_y_contraparte() -> None:
    """#314: la regla de publicación de un indicio es una función propia."""
    from parcel_intel.domain.published_discrepancy import is_publishable_indicio
    from tests.indicio_comparison_support import line, record

    zone = line(0.0, 0.0)
    assert is_publishable_indicio(record("0" * 16, zone).discrepancy)
    assert is_publishable_indicio(
        record("1" * 16, zone, counterpart_kind="catastro_vt").discrepancy
    )
    assert not is_publishable_indicio(
        record("2" * 16, zone, subject_kind="catastro_vt").discrepancy
    )


# --- Tabla de convergencia y casado con el informe interno (#316) -----------------------

OTHER_LINE = MultiLineGeometry(lines=(((465000.0, 4440300.0), (465100.0, 4440300.0)),), srid=25830)


def _internal_pair() -> tuple[MunicipalDiscrepancyReport, MunicipalDiscrepancyReport]:
    """(publicable, interno): un indicio vía → IGN, uno vía → Catastro y uno con Catastro en
    el lado A (la zona se redacta en el publicable)."""
    internal = _report(
        _record(),
        _record(b="catastro_vt", trail_id="VP-2", zone=OTHER_LINE),
        _record(
            a="catastro_vt",
            trail_id="99999A00109002",
            zone=MultiLineGeometry(
                lines=(((465000.0, 4440600.0), (465100.0, 4440600.0)),), srid=25830
            ),
            public_subject=False,
        ),
        redacted=False,
    )
    return redact_for_publication(internal), internal


def test_cada_indicio_lleva_su_tabla_y_la_publicacion_sus_notas() -> None:
    report, _ = _internal_pair()
    tables = indicio_tables(report)
    build = build_discrepancy_layer(report, tables)
    assert build.publication is not None
    items = build.publication.content.items
    ids = published_indicio_ids(report)
    assert [item.public_id for item in items] == [public for _, public in ids]
    for (record_id, _), item in zip(ids, items, strict=True):
        assert item.convergence == publish_convergence(tables[record_id])
    used = [tables[record_id] for record_id, _ in ids]
    assert build.publication.convergence_notes == convergence_notes(used)
    assert (build.omitted.subject, build.omitted.counterpart) == (1, 0)


def test_published_indicio_ids_es_el_recorrido_de_build() -> None:
    report, _ = _internal_pair()
    ids = published_indicio_ids(report)
    assert [record_id for record_id, _ in ids] == [r.record_id for r in report.records[:2]]
    with pytest.raises(NotPublishableReportError, match="variante publicable"):
        published_indicio_ids(dataclasses.replace(report, redacted=False))
    # Las colisiones llevan el mismo sufijo que en la capa.
    first, second = (_record(trail_id=f"VP-{n}") for n in (1, 2))
    collided = _report(first, second)
    base = public_indicio_id(first.discrepancy)
    assert [p for _, p in published_indicio_ids(collided)] == [base, f"{base}-2"]


def test_falta_la_tabla_de_un_indicio() -> None:
    report, _ = _internal_pair()
    tables = indicio_tables(report)
    (record_id, public_id), _ = published_indicio_ids(report)
    del tables[record_id]
    with pytest.raises(NotPublishableReportError) as error:
        build_discrepancy_layer(report, tables)
    assert error.value.reason == f"indicio {public_id}: {NO_CONVERGENCE_TABLE}"
    assert record_id not in error.value.reason


def test_sin_indicios_no_mira_las_tablas() -> None:
    redacted = _record(a="catastro_vt", zone=Geometry(rings=(), srid=25830), redacted=True)
    build = build_discrepancy_layer(_report(redacted), {})
    assert build.publication is None


def test_el_interno_casado_pasa_y_da_los_tramos_del_interno() -> None:
    report, internal = _internal_pair()
    check_internal_report(report, internal)
    stretches = indicio_stretches(report, internal)
    ids = published_indicio_ids(report)
    assert [s.stretch_id for s in stretches] == [record_id for record_id, _ in ids]
    assert [s.shape for s in stretches] == [LINE, OTHER_LINE]
    assert all(s.municipality_code == "28043" for s in stretches)


def _replace_record(
    report: MunicipalDiscrepancyReport, index: int, record: DiscrepancyRecord
) -> MunicipalDiscrepancyReport:
    records = list(report.records)
    records[index] = record
    return dataclasses.replace(report, records=tuple(records))


def _mismatches() -> list[tuple[str, MunicipalDiscrepancyReport]]:
    report, internal = _internal_pair()
    first = internal.records[0]
    moved = dataclasses.replace(
        first, discrepancy=dataclasses.replace(first.discrepancy, zone=OTHER_LINE)
    )
    other_b = dataclasses.replace(
        first,
        discrepancy=dataclasses.replace(
            first.discrepancy, counterpart_source=_source("catastro_vt", False)
        ),
    )
    other_a = dataclasses.replace(
        first,
        discrepancy=dataclasses.replace(
            first.discrepancy,
            subject=TrailRef("VP-1", _source("ign_red_transporte")),
        ),
    )
    renamed = dataclasses.replace(internal.records[2], record_id="f" * 16)
    return [
        ("municipio", dataclasses.replace(internal, municipality_code="28045")),
        ("criterio", dataclasses.replace(internal, criterion_id="discrepancia_trazados@2")),
        ("otra secuencia", _replace_record(internal, 2, renamed)),
        ("menos registros", dataclasses.replace(internal, records=internal.records[:2])),
        ("otra zona", _replace_record(internal, 0, moved)),
        ("otra fuente B", _replace_record(internal, 0, other_b)),
        ("otra fuente A", _replace_record(internal, 0, other_a)),
        (
            "zona redactada",
            _replace_record(internal, 0, dataclasses.replace(first, zone_redacted=True)),
        ),
    ]


@pytest.mark.parametrize(("case", "internal"), _mismatches(), ids=lambda v: str(v)[:20])
def test_un_interno_que_no_casa_da_un_motivo_fijo(
    case: str, internal: MunicipalDiscrepancyReport
) -> None:
    report, _ = _internal_pair()
    with pytest.raises(NotPublishableReportError) as error:
        check_internal_report(report, internal)
    assert error.value.reason == INTERNAL_MISMATCH, case
    for record in (*report.records, *internal.records):
        assert record.record_id not in str(error.value)


def test_un_interno_redactado_se_rechaza() -> None:
    report, _ = _internal_pair()
    with pytest.raises(NotPublishableReportError) as error:
        check_internal_report(report, report)
    assert error.value.reason == INTERNAL_REDACTED


def test_un_indicio_no_publicado_puede_diferir() -> None:
    """Solo se comparan zona y fuentes de los indicios que se publican: el de Catastro en el
    lado A (redactado en el publicable) no se mira."""
    report, internal = _internal_pair()
    third = internal.records[2]
    moved = dataclasses.replace(
        third, discrepancy=dataclasses.replace(third.discrepancy, zone=OTHER_LINE)
    )
    check_internal_report(report, _replace_record(internal, 2, moved))
