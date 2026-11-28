"""Tests de aceptación de la historia #314: manifiesto de imágenes de comparación de los
indicios (`imagenes-comparacion-indicios`).

Fixtures SINTÉTICAS (`tests/indicio_comparison_support.py`): coordenadas inventadas e
identificadores con el prefijo `99999`. El `http_get` falso responde un JPEG «con contenido»
al IGN y la tesela real de la fixture de #207 a la IDEM, según la URL, y cuenta las llamadas.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from parcel_intel.adapters.comparison_files import JsonComparisonManifestReader
from parcel_intel.application.build_indicio_comparisons import BuildIndicioComparisonsUseCase
from parcel_intel.cli import main
from parcel_intel.domain import published_discrepancy
from parcel_intel.domain.comparison_image import StretchComparison, comparison_windows
from parcel_intel.domain.convergence import image_axis, stretch_from_discrepancy
from parcel_intel.domain.discrepancy_policy import redact_for_publication
from parcel_intel.domain.discrepancy_report import MunicipalDiscrepancyReport
from parcel_intel.domain.geometry import Geometry, MultiLineGeometry
from parcel_intel.domain.published_comparison import (
    ComparisonNotPublishableError,
    build_published_comparison,
)
from tests.convergence_support import indicio_tables
from tests.fixtures.candidatos_publicar import generar_idem
from tests.indicio_comparison_support import (
    INE,
    X0,
    Y0,
    line,
    record,
    report,
    strip,
    two_part_line,
    write_internal,
    write_publishable,
)

pytestmark = pytest.mark.acceptance

DAY = date(2026, 11, 27)
STRIP_ID = "00000000000000a1"
MULTI_ID = "00000000000000a2"
CATASTRO_ID = "00000000000000a3"
LONG_ID = "00000000000000a4"


@pytest.fixture
def http() -> generar_idem.FakeServers:
    return generar_idem.FakeServers()


@pytest.fixture(autouse=True)
def _fake_network(monkeypatch: pytest.MonkeyPatch, http: generar_idem.FakeServers) -> None:
    real = main.build_indicio_comparison_use_case

    def build(
        input_dir: Path, cache_dir: Path, output_dir: Path, offline: bool
    ) -> BuildIndicioComparisonsUseCase:
        return real(
            input_dir,
            cache_dir,
            output_dir,
            offline,
            http_get=http,
            today=lambda: DAY,
            sleep=lambda seconds: None,
        )

    monkeypatch.setattr(main, "build_indicio_comparison_use_case", build)


def _run(base: Path, *extra: str, municipios: tuple[str, ...] = (INE,)) -> int:
    args = ["imagenes-comparacion-indicios"]
    for code in municipios:
        args += ["--municipio", code]
    args += [
        "--entrada",
        str(base / "interno"),
        "--cache",
        str(base / "raw"),
        "--salida",
        str(base / "comparacion_indicios"),
        *extra,
    ]
    parsed = main._build_parser().parse_args(args)
    exit_code: int = parsed.func(parsed)
    return exit_code


def _snapshot(directory: Path) -> dict[str, bytes]:
    if not directory.exists():
        return {}
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


def _requested_min_ys(http: generar_idem.FakeServers) -> list[float]:
    """`min_y` de la ventana de cada petición WMS (el `bbox` empieza por `minx,miny`)."""
    return [float(call[1]["bbox"].split(",")[1]) for call in http.calls]


def _mixed_report() -> MunicipalDiscrepancyReport:
    return report(
        record(STRIP_ID, strip(X0, Y0)),
        record(
            MULTI_ID,
            two_part_line(X0, Y0 + 1000),
            subject_kind="ign_red_transporte",
            counterpart_kind="via_pecuaria_oficial",
        ),
        record(CATASTRO_ID, line(X0, Y0 + 2000), subject_kind="catastro_vt"),
    )


def test_ca1_manifiesto_con_una_entrada_por_indicio_publicable(
    tmp_path: Path, http: generar_idem.FakeServers
) -> None:
    """CA1. Dado un informe interno con indicios publicables, cuando se ejecuta
    `imagenes-comparacion-indicios --municipio <ine>`, entonces se escribe
    `data/derived/comparacion_indicios/<ine>.json` con una entrada por indicio publicable,
    de id `record_id` y trazo `image_axis(zona)`. La fixture incluye al menos una franja
    (`Polygon`) y una multilínea de dos partes."""
    rep = _mixed_report()
    write_internal(tmp_path / "interno", rep)
    assert _run(tmp_path) == 0
    document = json.loads(
        (tmp_path / "comparacion_indicios" / f"{INE}.json").read_text(encoding="utf-8")
    )
    assert [t["id"] for t in document["tramos"]] == [STRIP_ID, MULTI_ID]
    by_id = {r.record_id: r for r in rep.records}
    assert isinstance(by_id[STRIP_ID].discrepancy.zone, Geometry)
    zone = by_id[MULTI_ID].discrepancy.zone
    assert isinstance(zone, MultiLineGeometry) and len(zone.lines) == 2
    for tramo in document["tramos"]:
        axis = image_axis(by_id[tramo["id"]].discrepancy.zone)
        expected = comparison_windows(axis)
        assert expected is not None
        assert [v["bbox"] for v in tramo["ventanas"]] == [
            [w.min_x, w.min_y, w.max_x, w.max_y] for w in expected
        ]
    assert http.calls


def test_ca2_el_manifiesto_de_los_candidatos_no_se_toca(tmp_path: Path) -> None:
    """CA2. El manifiesto de los candidatos (`--salida` de `imagenes-comparacion`, por
    defecto `data/derived/comparacion/`) no se toca: el test lo deja escrito antes y
    comprueba que queda igual byte a byte."""
    candidates = tmp_path / "comparacion"
    candidates.mkdir()
    (candidates / f"{INE}.json").write_bytes(b'{"municipio": "28043", "tramos": []}\n')
    before = _snapshot(candidates)
    write_internal(tmp_path / "interno", _mixed_report())
    assert _run(tmp_path) == 0
    assert _snapshot(candidates) == before
    assert (tmp_path / "comparacion_indicios" / f"{INE}.json").is_file()
    # El comando no tiene por defecto el directorio de los candidatos.
    parsed = main._build_parser().parse_args(["imagenes-comparacion-indicios", "--municipio", INE])
    assert parsed.salida == Path("data/derived/comparacion_indicios")
    assert parsed.entrada == Path("data/derived/discrepancias/interno")


def test_ca3_indicio_no_publicable_no_entra_ni_genera_peticiones(
    tmp_path: Path, http: generar_idem.FakeServers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CA3. Un indicio no publicable (fuente A no redistribuible, o contraparte fuera de
    `PUBLISHABLE_COUNTERPART_SOURCES`) no entra en el manifiesto ni genera peticiones. La
    regla es `is_publishable_indicio`, la misma función que usa `build_discrepancy_layer`."""
    write_internal(tmp_path / "interno", _mixed_report())
    assert _run(tmp_path) == 0
    ids = [
        t["id"]
        for t in json.loads(
            (tmp_path / "comparacion_indicios" / f"{INE}.json").read_text(encoding="utf-8")
        )["tramos"]
    ]
    assert CATASTRO_ID not in ids
    # Ninguna petición cae en la franja del indicio de Catastro (Y0 + 2000)...
    assert http.calls
    assert all(min_y < Y0 + 1500 for min_y in _requested_min_ys(http))
    # ...y son exactamente las de un informe sin ese indicio (caché distinta, mismas peticiones).
    first_run_calls = len(http.calls)
    write_internal(tmp_path / "solo" / "interno", report(*_mixed_report().records[:2]))
    assert _run(tmp_path / "solo") == 0
    assert len(http.calls) - first_run_calls == first_run_calls

    # Contraparte fuera de la lista: con la lista recortada, el indicio de la franja sale.
    monkeypatch.setattr(
        published_discrepancy,
        "PUBLISHABLE_COUNTERPART_SOURCES",
        frozenset({"via_pecuaria_oficial"}),
    )
    write_internal(tmp_path / "otro" / "interno", _mixed_report())
    restricted_start = len(http.calls)
    assert _run(tmp_path / "otro") == 0
    ids = [
        t["id"]
        for t in json.loads(
            (tmp_path / "otro" / "comparacion_indicios" / f"{INE}.json").read_text(encoding="utf-8")
        )["tramos"]
    ]
    assert ids == [MULTI_ID]  # la franja tenía como contraparte la red del IGN
    # La franja excluida (en Y0) tampoco genera peticiones: solo las de Y0 + 1000.
    assert min(_requested_min_ys(http)[restricted_start:]) >= Y0 + 500
    # La misma función decide en la capa publicada.
    publishable = redact_for_publication(_mixed_report())
    # Desde #316 la capa lleva la tabla de convergencia de cada indicio publicado.
    layer = published_discrepancy.build_discrepancy_layer(publishable, indicio_tables(publishable))
    assert layer.omitted.counterpart == 1 and layer.omitted.subject == 1


def test_ca4_mas_de_400_ventanas_no_pide_ni_escribe(
    tmp_path: Path, http: generar_idem.FakeServers, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA4. Si los indicios de un municipio suman más de 400 ventanas, el comando sale con
    1, no hace ninguna petición (el `http_get` falso cuenta cero llamadas) y no escribe
    nada. El mensaje dice el municipio y el recuento."""
    records = [
        record(f"{i:016x}", line(X0 + 1000.0 * (i % 40), Y0 + 1000.0 * (i // 40)))
        for i in range(401)
    ]
    write_internal(tmp_path / "interno", report(*records))
    assert _run(tmp_path) == 1
    assert http.calls == []
    assert not (tmp_path / "comparacion_indicios").exists()
    assert not (tmp_path / "raw").exists()
    error = capsys.readouterr().err
    assert INE in error and "401" in error


def test_ca5_indicio_demasiado_largo_queda_marcado_sin_peticiones(
    tmp_path: Path, http: generar_idem.FakeServers
) -> None:
    """CA5. Un indicio demasiado largo queda en el manifiesto como `tramo_demasiado_largo`,
    sin peticiones, y no es un fallo."""
    write_internal(tmp_path / "interno", report(record(LONG_ID, line(X0, Y0, length=3000.0))))
    assert _run(tmp_path) == 0
    (tramo,) = json.loads(
        (tmp_path / "comparacion_indicios" / f"{INE}.json").read_text(encoding="utf-8")
    )["tramos"]
    assert tramo["id"] == LONG_ID and tramo["estado"] == "tramo_demasiado_largo"
    assert http.calls == []


def test_ca6_informe_publicable_redactado_falla_sin_escribir(
    tmp_path: Path, http: generar_idem.FakeServers, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA6. Si `--entrada` apunta al informe publicable (redactado), sale con 1 y no
    escribe nada."""
    write_publishable(tmp_path / "interno", _mixed_report())
    assert _run(tmp_path) == 1
    assert http.calls == []
    assert not (tmp_path / "comparacion_indicios").exists()
    assert "publicable" in capsys.readouterr().err


def test_ca7_ida_y_vuelta_productor_y_consumidor_usan_el_mismo_trazo(tmp_path: Path) -> None:
    """CA7. Ida y vuelta: para cada indicio del manifiesto escrito,
    `build_published_comparison(image_axis(stretch_from_discrepancy(registro).shape),
    manifiesto.stretch(record_id))` no lanza, salvo los `tramo_demasiado_largo` y los «sin
    imágenes»."""
    rep = report(
        record(STRIP_ID, strip(X0, Y0)),
        record(MULTI_ID, two_part_line(X0, Y0 + 1000), subject_kind="ign_red_transporte"),
        record(LONG_ID, line(X0, Y0 + 3000, length=3000.0)),
    )
    write_internal(tmp_path / "interno", rep)
    assert _run(tmp_path) == 0
    manifest = JsonComparisonManifestReader(tmp_path / "comparacion_indicios").get_manifest(INE)
    assert manifest is not None
    checked = 0
    for item in rep.records:
        comparison = manifest.stretch(item.record_id)
        assert isinstance(comparison, StretchComparison)
        shape = image_axis(stretch_from_discrepancy(item, INE).shape)
        if comparison.status == "tramo_demasiado_largo":
            with pytest.raises(ComparisonNotPublishableError):
                build_published_comparison(shape, comparison)
            continue
        build_published_comparison(shape, comparison)
        checked += 1
    assert checked == 2


def test_ca8_varios_municipios_el_fallo_de_uno_no_impide_los_demas(
    tmp_path: Path, http: generar_idem.FakeServers
) -> None:
    """CA8. Con `--municipio` repetido o con `--ambito`, cada municipio escribe su
    manifiesto y el fallo de uno no impide los demás. Si alguno falla, sale con 1.
    `--sin-red` solo usa la caché: si falta una imagen, ese municipio falla sin escribir."""
    write_internal(tmp_path / "interno", report(record(STRIP_ID, strip(X0, Y0))))
    # 28044 no tiene informe: falla; 28045 sí.
    write_internal(
        tmp_path / "interno",
        report(record(MULTI_ID, two_part_line(X0, Y0)), municipality_code="28045"),
    )
    assert _run(tmp_path, municipios=(INE, "28044", "28045")) == 1
    out = tmp_path / "comparacion_indicios"
    assert (out / f"{INE}.json").is_file()
    assert (out / "28045.json").is_file()
    assert not (out / "28044.json").exists()

    # --sin-red con la caché vacía: falla sin escribir; con la caché llena, funciona.
    fresh = tmp_path / "frio"
    write_internal(fresh / "interno", report(record(STRIP_ID, strip(X0, Y0))))
    assert _run(fresh, "--sin-red") == 1
    assert not (fresh / "comparacion_indicios").exists()
    assert http.calls  # solo las del primer lote, ninguna del lote en frío
    calls = len(http.calls)
    assert _run(tmp_path, "--sin-red") == 0  # la caché de `tmp_path/raw` ya está llena
    assert len(http.calls) == calls

    parsed = main._build_parser().parse_args(["imagenes-comparacion-indicios", "--ambito"])
    assert parsed.ambito is True


def test_el_informe_interno_no_cambia_de_huella(tmp_path: Path) -> None:
    """El comando solo lee: el informe interno queda igual."""
    write_internal(tmp_path / "interno", _mixed_report())
    before = hashlib.sha256((tmp_path / "interno" / f"{INE}.geojson").read_bytes()).hexdigest()
    assert _run(tmp_path) == 0
    after = hashlib.sha256((tmp_path / "interno" / f"{INE}.geojson").read_bytes()).hexdigest()
    assert before == after
