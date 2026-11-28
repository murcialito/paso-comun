"""Criterios de aceptación de la historia #183: la red asfaltada del IGN cuenta como
contraparte en `discrepancias` y nunca como sujeto (ADR 0050).

Fixtures:

- `tests/fixtures/asfaltado/entrada/` — SINTÉTICAS (ver su LEEME.md): municipio 28997,
  vías VP-A…VP-E, para CA1–CA4, CA7, CA10 y CA11.
- `tests/fixtures/road_network/roadlink_paved_recorte_municipio_<ine>.json` — RECORTES
  reales de la consulta `paved` (ver `tests/road_layer_fixtures.py`), para CA5, CA6 y CA12.

Adaptación del diseño, anotada en la PR: el diseño esperaba que el `AUSENTE`/`SOLAPE` de
VP-D (asfaltado paralelo a 20 m) se mantuviera. Al pasar la fixture por el pipeline
(Bloque 0), VP-D no da ningún registro: la contraparte a 20 m está dentro del radio de
búsqueda (33,54 m) y fuera de la tolerancia (11,18 m), así que `compare_trail` da un
`DESPLAZAMIENTO`, que el par vía → IGN no conserva (`kinds_kept`), y el inverso (IGN →
vía) no se evalúa porque el asfaltado es solo contraparte. Es el mismo trato que un tramo
sin asfaltar a 20 m, salvo que este no genera el `DESPLAZAMIENTO` con sujeto asfaltado.
CA10 se comprueba tal como está escrito y con VP-E (sin asfaltar a 20 m), que conserva su
`DESPLAZAMIENTO` con o sin red asfaltada.
"""

import json
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import LineString

from parcel_intel.adapters.discrepancy_files import GeoJsonDiscrepancyReportReader
from parcel_intel.adapters.discrepancy_files.geojson_reader import read_report
from parcel_intel.adapters.raw_cache import CachedRawFetcher
from parcel_intel.adapters.road_network.adapter import LICENSE, SOURCE, RoadNetworkAdapter
from parcel_intel.application.cut_signals import CadastralParcelSignalProvider
from parcel_intel.application.detect_cut_candidates import DetectCutCandidatesUseCase
from parcel_intel.cli import main
from parcel_intel.cli.main import AMBITO_HITO_INE
from parcel_intel.domain.cut_method import BASE_COUNTERPARTS, CUT_METHOD
from parcel_intel.domain.geometry import Geometry
from parcel_intel.domain.published_discrepancy import public_indicio_id
from parcel_intel.domain.road_segment import RoadSurface
from tests.cut_signal_support import ORDINARY, Boundaries, ParcelsPort, Sink, parcel, rect
from tests.indicio_publication_support import indicio_publication_options
from tests.road_layer_fixtures import prepare_road_batches, recorte_for

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).parents[2]
D1 = date(2026, 11, 26)
ASFALTADO = ROOT / "tests" / "fixtures" / "asfaltado" / "entrada"
INE = "28997"
PAVED_IDS = {"900000000001", "900000000003", "900000000004"}
UNPAVED_IDS = {"900000000002", "900000000005"}
TOLERANCE_VIA_IGN_M = 11.18


def _run_cli(argv: list[str]) -> int:
    old = sys.argv
    sys.argv = ["parcel_intel.cli.main", *argv]
    try:
        with pytest.raises(SystemExit) as exit_info:
            main.main()
    finally:
        sys.argv = old
    code = exit_info.value.code
    assert isinstance(code, int)
    return code


def _discrepancias(entrada: Path, salida: Path, ine: str = INE) -> int:
    return _run_cli(
        ["discrepancias", "--municipio", ine, "--entrada", str(entrada), "--salida", str(salida)]
    )


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _records(path: Path) -> list[tuple[str, str, str, str]]:
    """(tipo, fuente_a, fuente_b, id del sujeto) de cada registro del fichero."""
    return [
        (
            feature["properties"]["tipo"],
            feature["properties"]["fuente_a"],
            feature["properties"]["fuente_b"],
            feature["properties"]["trazados"][0]["id"],
        )
        for feature in _load(path)["features"]
    ]


def _copy_input(tmp_path: Path) -> Path:
    entrada = tmp_path / "entrada"
    shutil.copytree(ASFALTADO, entrada)
    return entrada


def _empty_paved(entrada: Path) -> None:
    path = entrada / "camino_asfaltado" / f"{INE}.geojson"
    document = _load(path)
    document["features"] = []
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def salida(tmp_path: Path) -> Path:
    out = tmp_path / "salida"
    assert _discrepancias(ASFALTADO, out) == 0
    return out


def test_ca1_trazado_sobre_asfalto_no_genera_indicio(salida: Path) -> None:
    """CA1. Dado un trazado de vía pecuaria cuyo recorrido coincide (≤ el umbral de solape
    vigente) con un tramo `paved` de la red del IGN y sin tramo `unpaved`, cuando se ejecuta
    `discrepancias`, entonces no genera registro publicable por ausencia/solape contra
    `ign_red_transporte`."""
    for folder in ("publicable", "interno"):
        records = _records(salida / folder / f"{INE}.geojson")
        assert not [
            r
            for r in records
            if r[3] == "VP-A"
            and r[2] == "ign_red_transporte"
            and r[0] in {"AUSENTE_EN_FUENTE", "SOLAPE"}
        ]
        assert all(r[3] != "VP-A" for r in records)


def test_ca2_trazado_sobre_camino_sin_asfaltar_sin_regresion(tmp_path: Path, salida: Path) -> None:
    """CA2. Dado un trazado que coincide solo con un tramo `unpaved`, cuando se ejecuta
    `discrepancias`, entonces el resultado es el mismo que antes (sin regresión)."""
    entrada = _copy_input(tmp_path)
    _empty_paved(entrada)
    before = tmp_path / "antes"
    assert _discrepancias(entrada, before) == 0

    def about_b(path: Path) -> list[tuple[str, str, str, str]]:
        return [r for r in _records(path) if "VP-B" in r or r[3] == "900000000002"]

    assert about_b(salida / "interno" / f"{INE}.geojson") == []
    assert about_b(before / "interno" / f"{INE}.geojson") == []
    # Y las fixtures de #32 (con la red asfaltada vacía) dan los mismos ficheros que su oráculo:
    # lo comprueban sin cambios los tests de #32, #94 y #96.


def test_ca3_solo_se_senala_la_parte_sin_contraparte(salida: Path) -> None:
    """CA3. Dado un trazado cuya parte bajo asfalto es parcial, cuando se ejecuta
    `discrepancias`, entonces solo se señala la parte sin contraparte (asfaltada o no), con
    el mismo criterio de longitud mínima que ya usa el ADR 0030."""
    features = [
        f
        for f in _load(salida / "publicable" / f"{INE}.geojson")["features"]
        if f["properties"]["trazados"][0]["id"] == "VP-C"
    ]
    assert [f["properties"]["tipo"] for f in features] == ["SOLAPE"]
    zone_x = [x for x, _ in features[0]["geometry"]["coordinates"]]
    # Oráculo del pipeline (Bloque 0): zona de x = 450610 a 451000.
    assert (min(zone_x), max(zone_x)) == (450610.0, 451000.0)
    # Contraste con shapely: primer punto del eje a más de la tolerancia del par del tramo
    # asfaltado (de 450000 a 450600, a 2 m del eje). La zona empieza en la muestra anterior.
    paved = LineString([(450000.0, 4437002.0), (450600.0, 4437002.0)])
    first_uncovered = next(
        x / 10 for x in range(4500000, 4510001) if paved.distance(_point(x / 10)) > 11.18
    )
    assert first_uncovered == pytest.approx(450611.0, abs=0.1)
    assert min(zone_x) == pytest.approx(first_uncovered, abs=5.0)  # paso de muestreo: 5 m
    medidas = features[0]["properties"]["medidas"]
    assert medidas["tolerancia_par_m"] == TOLERANCE_VIA_IGN_M
    assert medidas["paso_muestreo_m"] == 5.0


def _point(x: float) -> Any:
    from shapely.geometry import Point

    return Point(x, 4437000.0)


def test_ca4_candidatos_con_la_misma_contraparte(tmp_path: Path, salida: Path) -> None:
    """CA4. Dado el método de candidatos a corte (ADR 0039), cuando se calcula la
    discrepancia base, entonces usa la misma contraparte (asfaltada + sin asfaltar) y los
    candidatos de Colmenar no cambian salvo por la parte bajo asfalto.

    Encadena `discrepancias` → `DetectCutCandidatesUseCase` con la señal de Catastro sobre
    un doble (una parcela ordinaria SINTÉTICA que cubre todo): el único candidato es la
    parte de VP-C sin contraparte; VP-A (bajo asfalto) no da ninguno. Los candidatos reales
    de Colmenar se miden en local (CA13)."""
    assert BASE_COUNTERPARTS == {"ign_red_transporte"}
    assert CUT_METHOD.identifier == "candidato_corte"
    assert CUT_METHOD.version == 1

    def candidates(discrepancy_dir: Path) -> list[Any]:
        port = ParcelsPort([parcel(ORDINARY, rect(449000, 4434000, 452000, 4440000))])
        provider = CadastralParcelSignalProvider(
            lambda _code: port, Boundaries((449000, 4434000, 452000, 4440000)), 900.0
        )
        use_case = DetectCutCandidatesUseCase(
            GeoJsonDiscrepancyReportReader(discrepancy_dir / "interno"), [provider], Sink()
        )
        return list(use_case.execute(INE).candidates)

    with_paved = candidates(salida)
    assert [c.subject.trail_id for c in with_paved] == ["VP-C"]
    xs = [x for part in with_paved[0].stretch.lines for x, _ in part]
    assert min(xs) >= 450600.0  # solo la parte sin contraparte
    assert max(xs) == pytest.approx(451000.0, abs=5.0)

    entrada = _copy_input(tmp_path)
    _empty_paved(entrada)
    sin_asfalto = tmp_path / "sin_asfalto"
    assert _discrepancias(entrada, sin_asfalto) == 0
    without_paved = candidates(sin_asfalto)
    assert sorted(c.subject.trail_id for c in without_paved) == ["VP-A", "VP-C", "VP-D"]


def test_ca7_ficheros_anteriores_sin_asfaltado_fallan_con_mensaje(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA7. Dados los ficheros derivados anteriores (sin asfaltado), cuando se ejecuta
    `discrepancias`, entonces falla con un mensaje claro que pide regenerar la capa, en vez
    de dar resultados silenciosamente distintos."""
    entrada = _copy_input(tmp_path)
    shutil.rmtree(entrada / "camino_asfaltado")
    out = tmp_path / "salida"

    assert _discrepancias(entrada, out) == 1

    err = capsys.readouterr().err
    assert f"FALLO {INE}" in err
    assert str(entrada / "camino_asfaltado" / f"{INE}.geojson") in err
    assert "regenera con `camino-actual-municipios`" in err
    assert not (out / "interno" / f"{INE}.geojson").exists()
    assert not (out / "publicable" / f"{INE}.geojson").exists()


def test_ca10_un_tramo_asfaltado_nunca_es_sujeto(tmp_path: Path, salida: Path) -> None:
    """CA10. Dado un tramo `paved` del IGN paralelo a una vía pecuaria a 20 m (fuera de la
    tolerancia del par, 11,18 m, y dentro de su radio de búsqueda, 33,54 m), cuando se
    ejecuta `discrepancias`, entonces ningún registro tiene como sujeto (fuente A) un tramo
    asfaltado —ni `DESPLAZAMIENTO` ni otro tipo— y los registros cuyo sujeto es un tramo sin
    asfaltar son los mismos que con la capa asfaltada vacía. Los tramos asfaltados solo
    sirven de contraparte."""
    records = _records(salida / "interno" / f"{INE}.geojson")
    assert all(r[3] not in PAVED_IDS for r in records)

    entrada = _copy_input(tmp_path)
    _empty_paved(entrada)
    empty = tmp_path / "vacia"
    assert _discrepancias(entrada, empty) == 0

    def unpaved_subject(path: Path) -> list[dict[str, Any]]:
        return [
            f
            for f in _load(path)["features"]
            if f["properties"]["trazados"][0]["id"] in UNPAVED_IDS
        ]

    for folder in ("interno", "publicable"):
        with_paved = unpaved_subject(salida / folder / f"{INE}.geojson")
        without = unpaved_subject(empty / folder / f"{INE}.geojson")
        assert with_paved == without
        # VP-E: el tramo sin asfaltar a 20 m sigue dando su DESPLAZAMIENTO.
        assert [
            (f["properties"]["tipo"], f["properties"]["trazados"][1]["id"]) for f in with_paved
        ] == [("DESPLAZAMIENTO", "VP-E")]


def _mutate(entrada: Path, folder: str, change: Any) -> Path:
    path = entrada / folder / f"{INE}.geojson"
    document = _load(path)
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _second_municipality(entrada: Path) -> None:
    """Copia 28997 como 28996 (sin cambios) para comprobar que el lote sigue."""
    for folder in ("vias_pecuarias", "camino_actual", "camino_asfaltado"):
        document = _load(entrada / folder / f"{INE}.geojson")
        document["properties"]["municipio"] = "28996"
        (entrada / folder / "28996.geojson").write_text(json.dumps(document), encoding="utf-8")


def _set_surface(value: str) -> Any:
    def change(document: dict[str, Any]) -> None:
        document["properties"]["superficie"] = value
        for feature in document["features"]:
            feature["properties"]["superficie"] = value

    return change


def _swap(entrada: Path) -> Path:
    """Intercambia los dos ficheros de 28997 (28996 queda bien)."""
    actual = entrada / "camino_actual" / f"{INE}.geojson"
    asfaltado = entrada / "camino_asfaltado" / f"{INE}.geojson"
    text_actual, text_asfaltado = actual.read_bytes(), asfaltado.read_bytes()
    actual.write_bytes(text_asfaltado)
    asfaltado.write_bytes(text_actual)
    return actual


def _duplicate_id(entrada: Path) -> Path:
    def change(document: dict[str, Any]) -> None:
        document["features"][0]["id"] = "900000000002"  # el id de un tramo sin asfaltar

    return _mutate(entrada, "camino_asfaltado", change)


def _paved_without_unpaved(entrada: Path) -> Path:
    path = entrada / "camino_actual" / f"{INE}.geojson"
    path.unlink()
    return path


@pytest.mark.parametrize(
    "case",
    [
        lambda e: _mutate(e, "camino_actual", _set_surface("paved")),
        lambda e: _mutate(e, "camino_asfaltado", _set_surface("unpaved")),
        _swap,
        _duplicate_id,
        _paved_without_unpaved,
    ],
    ids=[
        "paved-en-camino_actual",
        "unpaved-en-camino_asfaltado",
        "intercambiados",
        "id-repetido",
        "asfaltado-sin-camino_actual",
    ],
)
def test_ca11_ficheros_incoherentes_fallan_nombrando_el_fichero_y_el_lote_sigue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: Any
) -> None:
    """CA11. Dados los directorios intercambiados (un fichero con `superficie: "paved"` en
    `camino_actual/` o uno con `superficie: "unpaved"` en `camino_asfaltado/`), un mismo
    `id_tramo` en los dos ficheros, o el fichero asfaltado sin el fichero sin asfaltar,
    cuando se ejecuta `discrepancias`, entonces ese municipio falla con un mensaje que nombra
    el fichero y el lote sigue (como el CA8 de #32). Sin ninguno de los dos ficheros, la
    fuente consta como `AUSENTE`, como hoy."""
    entrada = _copy_input(tmp_path)
    _second_municipality(entrada)
    bad_path = case(entrada)
    out = tmp_path / "salida"

    code = _run_cli(
        [
            "discrepancias",
            "--municipio",
            INE,
            "--municipio",
            "28996",
            "--entrada",
            str(entrada),
            "--salida",
            str(out),
        ]
    )

    assert code == 1
    err = capsys.readouterr().err
    assert f"FALLO {INE}" in err
    assert str(bad_path) in err
    assert not (out / "interno" / f"{INE}.geojson").exists()
    assert (out / "interno" / "28996.geojson").is_file()  # el lote sigue


def test_ca11_sin_ninguno_de_los_dos_ficheros_la_fuente_consta_ausente(tmp_path: Path) -> None:
    """CA11 (última frase). Sin ninguno de los dos ficheros, la fuente consta como
    `AUSENTE`, como hoy."""
    entrada = _copy_input(tmp_path)
    shutil.rmtree(entrada / "camino_actual")
    shutil.rmtree(entrada / "camino_asfaltado")
    out = tmp_path / "salida"

    assert _discrepancias(entrada, out) == 0

    layers = {
        layer["fuente"]: layer
        for layer in _load(out / "interno" / f"{INE}.geojson")["properties"]["capas"]
    }
    assert layers["ign_red_transporte"]["estado"] == "ausente"


# --- CA5, CA6 y CA12: recortes reales `paved` (tests/road_layer_fixtures.py) ---------------


def _camino_actual(cache: Path, salida: Path, asfaltado: Path) -> int:
    return _run_cli(
        [
            "camino-actual-municipios",
            "--sin-red",
            "--cache",
            str(cache),
            "--salida",
            str(salida),
            "--salida-asfaltado",
            str(asfaltado),
        ]
    )


def _paved_ids() -> set[int]:
    return {
        feature["properties"]["id_tramo"]
        for ine in AMBITO_HITO_INE
        for feature in _load(recorte_for(ine, "paved"))["features"]
    }


def test_ca5_la_capa_publicada_sigue_sin_asfaltado(tmp_path: Path) -> None:
    """CA5. Dada la capa publicada «camino actual» del visor (#84), cuando se publica,
    entonces sigue conteniendo solo tramos sin asfaltar (mismos 893 de Colmenar) y ningún
    tramo asfaltado entra en `site/`.

    Con los recortes reales: se regeneran las dos capas, se publica el ámbito y ningún
    `id_tramo` de los recortes `paved` aparece en ningún fichero de `site/`. Un
    `camino_asfaltado/` corrupto no afecta a `publicar-capas`, que no lo lee. Los 893 de
    Colmenar son los del fichero publicado en `main`, que esta historia no cambia."""
    cache, derived, site = tmp_path / "raw", tmp_path / "derived", tmp_path / "site"
    batches = prepare_road_batches(cache, D1)
    assert _camino_actual(cache, derived / "camino_actual", derived / "camino_asfaltado") == 0
    (derived / "camino_asfaltado" / "28043.geojson").write_text("{roto", encoding="utf-8")

    code = _run_cli(
        [
            "publicar-capas",
            "--ambito",
            "--capa",
            "camino_actual",
            "--entrada",
            str(derived),
            "--salida",
            str(site),
        ]
    )

    assert code == 0
    published = sorted(site.rglob("*.json*"))
    assert published, "se publica algo"
    paved_ids = _paved_ids()
    assert len(paved_ids) >= 40  # 8 por municipio; un vecino puede repetirse
    for path in published:
        text = path.read_text(encoding="utf-8")
        assert "camino_asfaltado" not in text, path
        assert "surfacecategory%3D%27paved%27" not in text, path
        if path.suffix == ".geojson":
            ids = {f["properties"].get("id_tramo") for f in json.loads(text)["features"]}
            assert not (ids & paved_ids), path
    for layer in batches["unpaved"].layers:
        path = site / "camino_actual" / f"{layer.municipality.ine_code}.geojson"
        assert len(_load(path)["features"]) == len(layer.segments)
    colmenar = _load(ROOT / "site" / "datos" / "camino_actual" / "28043.geojson")
    assert len(colmenar["features"]) == 893


def test_ca6_contraparte_con_superficie_paginado_cache_y_procedencia(tmp_path: Path) -> None:
    """CA6. Dado el adapter, cuando se consulta una capa de contraparte, entonces cada tramo
    conserva su `surface_category`, pagina hasta `numberMatched`, usa la caché cruda
    inmutable y la procedencia (fuente, URL, fecha, licencia CC BY 4.0) queda registrada."""
    cache = tmp_path / "raw"
    batches = prepare_road_batches(cache, D1)
    first = (tmp_path / "a" / "actual", tmp_path / "a" / "asfaltado")
    second = (tmp_path / "b" / "actual", tmp_path / "b" / "asfaltado")
    assert _camino_actual(cache, *first) == 0
    assert _camino_actual(cache, *second) == 0  # sin red: solo la caché

    for ine in AMBITO_HITO_INE:
        for a, b in zip(first, second, strict=True):
            assert (a / f"{ine}.geojson").read_bytes() == (b / f"{ine}.geojson").read_bytes()
        paved = _load(first[1] / f"{ine}.geojson")
        unpaved = _load(first[0] / f"{ine}.geojson")
        assert paved["properties"]["superficie"] == "paved"
        assert unpaved["properties"]["superficie"] == "unpaved"
        assert {f["properties"]["superficie"] for f in paved["features"]} <= {"paved"}
        assert {f["properties"]["superficie"] for f in unpaved["features"]} <= {"unpaved"}
        for provenance in [paved["properties"]["procedencia"]] + [
            f["properties"]["procedencia"] for f in paved["features"]
        ]:
            assert provenance["fuente"] == SOURCE
            assert "surfacecategory%3D%27paved%27" in provenance["url"]
            assert provenance["fecha_descarga"] == D1.isoformat()
            assert provenance["licencia"] == LICENSE
            assert "CC BY 4.0" in provenance["licencia"]
            assert provenance["redistribucion"] == "publicable"
    assert all(layer.surface == "paved" for layer in batches["paved"].layers)
    assert {
        piece.source.surface_category
        for layer in batches["paved"].layers
        for piece in layer.segments
    } == {"paved"}

    # Paginado hasta numberMatched, cacheado: respuestas SINTÉTICAS en tres páginas.
    def page(ids: list[int], matched: int) -> bytes:
        features = [
            {
                "type": "Feature",
                "properties": {
                    "surfacecategory": "paved",
                    "fecha_alta": None,
                    "fictitious": "false",
                    "id_tramo": i,
                },
                "geometry": {"type": "LineString", "coordinates": [[1.0, 2.0], [3.0, 4.0]]},
            }
            for i in ids
        ]
        return json.dumps(
            {"features": features, "numberMatched": matched, "numberReturned": len(ids)}
        ).encode()

    pages = {"0": page([1, 2], 5), "2": page([3, 4], 5), "4": page([5], 5)}
    calls: list[dict[str, str]] = []

    def http_get(url: str, params: dict[str, str]) -> bytes:
        calls.append(params)
        return pages[params.get("offset", "0")]

    window = Geometry(rings=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)),), srid=25830)
    synthetic_cache = tmp_path / "sintetica"
    online = RoadNetworkAdapter(
        fetcher=CachedRawFetcher(http_get, synthetic_cache, lambda: D1), today=lambda: D1
    ).query_roads(window, 2.0, "paved")

    def no_network(url: str, params: dict[str, str]) -> bytes:
        raise AssertionError("sin red")

    offline = RoadNetworkAdapter(
        fetcher=CachedRawFetcher(
            no_network, synthetic_cache, lambda: date(2027, 1, 1), offline=True
        ),
        today=lambda: date(2027, 1, 1),
    ).query_roads(window, 2.0, "paved")
    assert [s.segment_id for s in online.segments] == [1, 2, 3, 4, 5]
    assert len(calls) == 3
    assert offline == online
    assert offline.provenance.retrieved_at == D1


def test_ca12_un_municipio_sin_alguna_de_las_dos_consultas_no_escribe_nada(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA12. Dado `camino-actual-municipios`, cuando un municipio falla en una de las dos
    consultas (sin asfaltar o asfaltada, p. ej. `--sin-red` con una caché anterior a esta
    historia), entonces no escribe ninguno de sus dos ficheros, lo informa con `FALLO` y sale
    con 1; los municipios que sí completan las dos consultas escriben los dos ficheros."""
    cache = tmp_path / "raw"
    # Caché «anterior a #183» para Chinchón: tiene su consulta sin asfaltar, no la asfaltada.
    batches = prepare_road_batches(cache, D1, fail_for=("28052",), fail_surfaces=("paved",))
    assert [f.ine_code for f in batches["paved"].failures] == ["28052"]
    assert batches["unpaved"].failures == ()
    actual, asfaltado = tmp_path / "actual", tmp_path / "asfaltado"

    assert _camino_actual(cache, actual, asfaltado) == 1

    captured = capsys.readouterr()
    assert "FALLO 28052 Chinchón: paved:" in captured.err
    assert "28052" not in captured.out
    expected = sorted(f"{ine}.geojson" for ine in AMBITO_HITO_INE if ine != "28052")
    assert sorted(p.name for p in actual.iterdir()) == expected
    assert sorted(p.name for p in asfaltado.iterdir()) == expected
    assert "28043 Colmenar de Oreja: " in captured.out
    assert " tramos asfaltados -> " in captured.out


# --- CA8: integración con red real (IDEM e IDEE), sin Catastro -----------------------------

INDICIOS_SOBRE_ASFALTO_28043 = ("228d09f82d066f2e", "b1f47704c7f34945")
"""`public_id` de los 2 indicios `SOLAPE` vía → IGN publicados en `main`
(site/datos/discrepancias/28043.geojson, 2026-11-27) que iban ≥ 57 % por asfalto (82,2 % y
57,0 % de su zona a ≤ 15 m de un tramo `paved`). Medidos en el Bloque 0 con shapely
(REGISTRY.md, sección de #183); literales, no leídos."""

INDICIO_MANTENIDO_28043 = ("415956c18feed794", "280430215621")
"""`public_id` del `DESPLAZAMIENTO` IGN → vía que se mantiene a propósito (CA8 vigente,
opción 1 del fundador en #183) y id de su sujeto: un camino sin asfaltar del IGN en paralelo
a una carretera (64,5 % de su zona a ≤ 15 m del asfalto, pero solo el 1,9 % de su vía a
≤ 11,18 m de un tramo asfaltado)."""


@pytest.mark.integration
@pytest.mark.red
def test_ca8_colmenar_con_red_los_solapes_sobre_asfalto_desaparecen_y_el_desplazamiento_sigue(
    tmp_path: Path,
) -> None:
    """CA8. [integración] [red] Dado Colmenar con red, cuando se regeneran la capa y las
    discrepancias, entonces:
    - los dos indicios `SOLAPE` vía → IGN que iban ≥ 57 % por asfalto (`228d09f82d066f2e`,
      82,2 %; `b1f47704c7f34945`, 57,0 %) dejan de publicarse con esa zona, o quedan reducidos
      a su parte sin contraparte;
    - el indicio `DESPLAZAMIENTO` IGN → vía `415956c18feed794` (64,5 % de su zona a ≤ 15 m de
      una carretera, pero solo el 1,9 % de su vía a ≤ 11,18 m de un tramo asfaltado) se
      mantiene: su sujeto es un camino sin asfaltar y la vía no va bajo asfalto;
    - ningún indicio publicado tiene como sujeto un tramo asfaltado;
    - el recuento antes/después queda anotado en REGISTRY.md, con el motivo de por qué
      `415956c18feed794` sigue.

    Regenera vías pecuarias y las dos capas del IGN de 28043 en `tmp_path` con red, ejecuta
    `discrepancias` (Catastro consta como ausente: no altera los registros vía → IGN) y
    `publicar-discrepancias`. Un indicio reducido cambia de zona y por tanto de id, así que
    «dejan de publicarse o quedan reducidos» se comprueba como «su id ya no está». El sujeto
    de cada indicio publicado se busca en el fichero publicable de `discrepancias` por su id
    público."""
    from parcel_intel.adapters.ign_admin_units import IgnAdministrativeUnitsAdapter
    from parcel_intel.adapters.ign_admin_units import client as ign_client
    from parcel_intel.adapters.livestock_trails import client as livestock_client
    from parcel_intel.adapters.livestock_trails.adapter import LivestockTrailAdapter
    from parcel_intel.adapters.road_layer_files import (
        GeoJsonLivestockTrailLayerWriter,
        GeoJsonRoadLayerWriter,
    )
    from parcel_intel.adapters.road_network import client as roadlink_client
    from parcel_intel.adapters.shapely_geometry import ShapelyLineClipper, ShapelyPolygonClipper
    from parcel_intel.application.build_municipal_livestock_trail_layers import (
        BuildMunicipalLivestockTrailLayersUseCase,
    )
    from parcel_intel.application.build_municipal_road_layers import (
        BuildMunicipalRoadLayersUseCase,
    )
    from parcel_intel.cli.main import AMBITO_HITO_BBOX_ETRS89

    cache, derived = tmp_path / "raw", tmp_path / "derived"
    today = date.today
    fetcher = CachedRawFetcher(roadlink_client.default_http_getter, cache, today)
    ign = CachedRawFetcher(ign_client.default_http_getter, cache, today)
    idem = CachedRawFetcher(livestock_client.default_http_getter, cache, today)
    boundaries = IgnAdministrativeUnitsAdapter(AMBITO_HITO_BBOX_ETRS89, fetcher=ign, today=today)
    [vias] = BuildMunicipalLivestockTrailLayersUseCase(
        boundaries, LivestockTrailAdapter(fetcher=idem, today=today), ShapelyPolygonClipper()
    ).execute(["28043"])
    GeoJsonLivestockTrailLayerWriter(derived / "vias_pecuarias").write(vias)
    surfaces: tuple[tuple[RoadSurface, str], ...] = (
        ("unpaved", "camino_actual"),
        ("paved", "camino_asfaltado"),
    )
    for surface, folder in surfaces:
        batch = BuildMunicipalRoadLayersUseCase(
            boundaries,
            RoadNetworkAdapter(fetcher=fetcher, today=today),
            ShapelyLineClipper(),
            surface=surface,
        ).execute(["28043"])
        assert batch.failures == ()
        GeoJsonRoadLayerWriter(derived / folder).write(batch.layers[0])

    assert _discrepancias(derived, tmp_path / "discrepancias", "28043") == 0
    site = tmp_path / "site"
    assert (
        _run_cli(
            [
                "publicar-discrepancias",
                "--municipio",
                "28043",
                "--entrada",
                str(tmp_path / "discrepancias" / "publicable"),
                "--salida",
                str(site),
                # Desde #316: interno, manifiesto (productor real, red falsa), capas e histórico.
                *indicio_publication_options(tmp_path / "discrepancias" / "publicable", derived),
            ]
        )
        == 0
    )

    published = {f["id"] for f in _load(site / "discrepancias" / "28043.geojson")["features"]}
    assert published  # sigue habiendo indicios
    assert not published & set(INDICIOS_SOBRE_ASFALTO_28043)

    # Sujeto de cada indicio publicado, por su id público (ADR 0044), desde el publicable.
    report = read_report(tmp_path / "discrepancias" / "publicable" / "28043.geojson")
    subjects = {
        public_indicio_id(record.discrepancy): (
            record.discrepancy.kind,
            record.discrepancy.subject.trail_id,
        )
        for record in report.records
    }
    assert published <= subjects.keys()
    kept_id, kept_subject = INDICIO_MANTENIDO_28043
    assert kept_id in published
    assert subjects[kept_id] == ("DESPLAZAMIENTO", kept_subject)

    paved = {
        f["id"] for f in _load(derived / "camino_asfaltado" / "28043.geojson")["features"]
    }
    assert paved
    assert not {subjects[public_id][1] for public_id in published} & paved

    section = _registry_section()
    assert kept_id in section
    assert "a propósito" in section


# --- CA9 y CA13: paso local con Catastro (`fuera_de_ci`, PR con `pendiente-local`) ---------

REGISTRY = ROOT / "src" / "parcel_intel" / "sources" / "REGISTRY.md"


def _registry_section() -> str:
    text = REGISTRY.read_text(encoding="utf-8")
    start = text.index("### Red asfaltada del IGN como contraparte (historia #183)")
    end = text.find("\n### ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _table_rows(section: str, header: str) -> list[list[str]]:
    lines = section[section.index(header) :].splitlines()
    rows: list[list[str]] = []
    for line in lines[2:]:  # cabecera y separador
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _local_data_or_skip() -> Path:
    derived = ROOT / "data" / "derived"
    needed = [
        derived / "catastro_caminos" / "28043.geojson",
        *(
            derived / folder / f"{ine}.geojson"
            for ine in AMBITO_HITO_INE
            for folder in ("vias_pecuarias", "camino_actual", "camino_asfaltado")
        ),
    ]
    missing = [str(p) for p in needed if not p.is_file()]
    if missing:
        pytest.skip(f"paso local de #183: faltan capas en data/derived ({missing[0]}...)")
    return derived


@pytest.mark.integration
@pytest.mark.fuera_de_ci
def test_ca9_recuento_de_indicios_del_ambito_anotado(tmp_path: Path) -> None:
    """CA9. Cuando se regeneran y republican los indicios de los 7 municipios del ámbito,
    entonces el recuento resultante se anota en REGISTRY.md (la ejecución con Catastro es en
    local).

    Con `data/derived` regenerado en local (paso 11 del diseño), repite `discrepancias
    --ambito` y `publicar-discrepancias --ambito` en `tmp_path`, comprueba que los ficheros
    publicados en `site/datos/discrepancias/` son los mismos y que la columna «Después» de
    la tabla de REGISTRY.md coincide con los indicios publicados de cada municipio."""
    derived = _local_data_or_skip()
    out, site = tmp_path / "discrepancias", tmp_path / "site"
    assert (
        _run_cli(["discrepancias", "--ambito", "--entrada", str(derived), "--salida", str(out)])
        == 0
    )
    assert (
        _run_cli(
            [
                "publicar-discrepancias",
                "--ambito",
                "--entrada",
                str(out / "publicable"),
                "--salida",
                str(site),
                # Desde #316: interno, manifiesto (productor real, red falsa), capas e histórico.
                *indicio_publication_options(out / "publicable", derived, work=tmp_path),
            ]
        )
        == 0
    )
    rows = _table_rows(_registry_section(), "| INE | Municipio | Indicios publicados antes |")
    assert sorted(row[0] for row in rows) == sorted(AMBITO_HITO_INE)
    for row in rows:
        ine, after = row[0], row[3]
        path = site / "discrepancias" / f"{ine}.geojson"
        count = len(_load(path)["features"]) if path.is_file() else 0
        assert after == str(count), (ine, after, count)
        committed = ROOT / "site" / "datos" / "discrepancias" / f"{ine}.geojson"
        assert committed.is_file() == path.is_file(), ine
        if path.is_file():
            ids = {f["id"] for f in _load(path)["features"]}
            assert ids == {f["id"] for f in _load(committed)["features"]}, ine


@pytest.mark.integration
@pytest.mark.fuera_de_ci
def test_ca13_candidatos_y_aprobaciones_de_colmenar_anotados(tmp_path: Path) -> None:
    """CA13. [integración, fuera de CI, local] Dado Colmenar regenerado con Catastro, cuando
    se ejecutan `candidatos-corte --municipio 28043` y `revision-candidatos --municipio
    28043`, entonces en REGISTRY.md quedan anotados el recuento de candidatos antes/después y
    cuántas de las 40 aprobaciones siguen vigentes, cuántas caducan y cuántas quedan
    huérfanas. Ningún agente toca `curated/candidatos/`. Si alguna caduca o queda huérfana,
    `site/datos/candidatos/` no se republica en esta PR y se comenta en la issue mencionando
    al fundador para que las revise (ADR 0047)."""
    derived = _local_data_or_skip()
    revision = ROOT / "curated" / "candidatos" / "28043" / "revision.json"
    before = revision.read_bytes()
    out, candidates = tmp_path / "discrepancias", tmp_path / "candidatos"
    assert (
        _run_cli(
            [
                "discrepancias",
                "--municipio",
                "28043",
                "--entrada",
                str(derived),
                "--salida",
                str(out),
            ]
        )
        == 0
    )
    assert (
        _run_cli(
            [
                "candidatos-corte",
                "--municipio",
                "28043",
                "--sin-red",
                "--entrada",
                str(tmp_path),  # raíz con discrepancias/interno/, no la carpeta de discrepancias
                "--cache",
                str(ROOT / "data" / "raw"),
                "--salida",
                str(candidates),
            ]
        )
        == 0
    )
    old = sys.stdout
    from io import StringIO

    buffer = StringIO()
    sys.stdout = buffer
    try:
        code = _run_cli(
            [
                "revision-candidatos",
                "--municipio",
                "28043",
                "--entrada",
                str(candidates),
                "--comparacion",
                str(derived / "comparacion"),
            ]
        )
    finally:
        sys.stdout = old
    summary = next(line for line in buffer.getvalue().splitlines() if line.startswith("resumen"))
    values = dict(item.split("=") for item in summary.split(": ", 1)[1].split())
    count = len(_load(candidates / "28043.geojson")["features"])
    rows = {
        row[0]: row[2] for row in _table_rows(_registry_section(), "| Medida | Antes | Después |")
    }
    assert rows["candidatos de 28043"] == str(count)
    assert rows["aprobaciones vigentes"] == values["aprobados"]
    assert rows["aprobaciones caducadas"] == values["caducados"]
    assert rows["revisiones huérfanas"] == values["huérfanas"]
    assert code == (1 if values["caducados"] != "0" or values["huérfanas"] != "0" else 0)
    assert revision.read_bytes() == before  # nadie toca curated/candidatos/
