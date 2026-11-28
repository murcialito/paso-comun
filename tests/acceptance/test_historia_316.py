"""Historia #316: publicar la tabla de convergencia en los indicios (`publicar-discrepancias`,
ADR 0057 §1, §2, §4, §6 y §8). Los docstrings copian el criterio.

Entradas SINTÉTICAS:
- las capas de #94 (`tests/fixtures/discrepancias_publicar/entrada/`, ver su LEEME.md), sobre
  las que el test ejecuta `discrepancias` (informe interno y publicable) y el productor real
  del manifiesto, `imagenes-comparacion-indicios`, con un `http_get` falso
  (`tests/indicio_publication_support.py`);
- para CA2, CA6 y CA7, informes escritos con `tests/indicio_comparison_support.py` (ids con el
  prefijo `99999`) sobre las capas de #315 (`tests/fixtures/candidatos_publicar/capas/`), cuyo
  camino actual solapa el 60 % del tramo del candidato `aprobado`;
- las unidades históricas sintéticas de #313 (copias en `tmp_path`).

Sin red ni PostGIS, salvo CA11 (datos reales en local; se salta si faltan).
"""

import copy
import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import tests.acceptance.test_historia_85 as historia_85
import tests.acceptance.test_historia_172 as historia_172
import tests.acceptance.test_historia_313 as historia_313
import tests.acceptance.test_historia_315 as historia_315
from parcel_intel.adapters.comparison_files import JsonComparisonManifestReader
from parcel_intel.adapters.discrepancy_files import (
    GeoJsonDiscrepancyReportReader,
    GeoJsonDiscrepancyWriter,
)
from parcel_intel.adapters.published_layers import PublishedLayerError
from parcel_intel.adapters.published_layers.convergence import (
    ROW_KEYS,
    SIDECAR_KEYS,
    TABLE_KEYS,
    convergence_document,
)
from parcel_intel.adapters.published_layers.geojson_sink import dump_compact
from parcel_intel.application.build_convergence_tables import BuildConvergenceTablesUseCase
from parcel_intel.cli import main
from parcel_intel.domain.comparison_image import COMPARISON_SLOTS
from parcel_intel.domain.convergence import (
    CONVERGENCE_TEXT_VALIDATION,
    CONVERGENCE_TITLE,
    CONVERGENCE_WARNING,
    LAYER_ROW_LABEL,
    ORTHOPHOTO_ROW_LABEL,
    VALUE_TEXT,
    compute_convergence_text_hash,
    vetoed_terms_in,
)
from parcel_intel.domain.discrepancy_report import MunicipalDiscrepancyReport
from parcel_intel.domain.public_card import CATASTRO_ATTRIBUTION, banned_words_in
from parcel_intel.domain.published_comparison import NOT_AVAILABLE_TEXT, NOT_IN_MANIFEST
from parcel_intel.domain.published_convergence import (
    ROW_SOURCE_KEYS,
    PublishedConvergence,
    publish_convergence,
)
from parcel_intel.domain.published_discrepancy import (
    CADASTRAL_REFERENCE_RE,
    INTERNAL_MISMATCH,
    INTERNAL_MISSING,
    INTERNAL_REDACTED,
    PARCEL_LOCATOR_RE,
    indicio_stretches,
    published_indicio_ids,
)
from tests.fixtures.candidatos_publicar import generar
from tests.fixtures.candidatos_revision import generar as revision
from tests.indicio_comparison_support import line, record, report, write_internal
from tests.indicio_publication_support import indicio_publication_options

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "discrepancias_publicar" / "entrada"
CAPAS_315 = ROOT / "tests" / "fixtures" / "candidatos_publicar" / "capas"
KIT_FIXTURES = ROOT / "tests" / "fixtures" / "kit_campo" / "datos"
INE = "28043"
FEATURE_KEYS_BEFORE = {
    "tipo",
    "etiqueta",
    "texto",
    "aviso",
    "confianza",
    "evidencia",
    "criterio",
    "descripcion_criterio",
    "aviso_omision",
}
# Lista blanca del ADR 0057 §8, literal.
ADR_TABLE_KEYS = ["titulo", "filas", "imagenes", "aviso", "metodo", "criterio"]
ADR_ROW_KEYS = [
    "fuente",
    "rotulo",
    "tipo_evidencia",
    "valor",
    "texto",
    "anio",
    "fecha_consulta",
    "enlaces",
    "atribucion",
]
UMBRAL_ERROR = "ERROR --umbral-aparece: debe estar en (0, 1]"
APROBADO = revision.candidates()["aprobado"]  # vía pecuaria, 200 m; 60 % de camino actual
INDICIO_ID = "00000000000003a1"
LONG_ID = "00000000000000b4"


# --- Utilidades -----------------------------------------------------------------------


def _run(args: list[str]) -> int:
    parsed = main._build_parser().parse_args(args)
    code: int = parsed.func(parsed)
    return code


def _discrepancias(salida: Path, *codes: str, entrada: Path = FIXTURES) -> int:
    args = ["discrepancias", "--entrada", str(entrada), "--salida", str(salida)]
    for code in codes:
        args += ["--municipio", code]
    return _run(args)


def _publicar(
    derived: Path,
    salida: Path,
    *extra: str,
    capas: Path = FIXTURES,
    codes: tuple[str, ...] = (INE,),
    entrada: Path | None = None,
    options: list[str] | None = None,
) -> int:
    """`publicar-discrepancias` con todas las opciones a `tmp_path` o a la fixture."""
    publicable = entrada or derived / "publicable"
    args = ["publicar-discrepancias", "--entrada", str(publicable), "--salida", str(salida)]
    for code in codes:
        args += ["--municipio", code]
    args += options if options is not None else indicio_publication_options(publicable, capas)
    return _run([*args, *extra])


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot(directory: Path) -> dict[str, bytes]:
    if not directory.exists():
        return {}
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _files(site: Path, code: str = INE) -> tuple[Path, Path]:
    return (
        site / "discrepancias" / f"{code}.geojson",
        site / "discrepancias" / f"{code}.geojson.procedencia.json",
    )


def _row(feature: dict[str, Any], source: str) -> dict[str, Any]:
    found: dict[str, Any] = next(
        row for row in feature["properties"]["convergencia"]["filas"] if row["fuente"] == source
    )
    return found


def _options(derived: Path, **changes: Path) -> list[str]:
    """Opciones de `indicio_publication_options` (genera el manifiesto) con cambios."""
    options = indicio_publication_options(derived / "publicable", changes.pop("capas", FIXTURES))
    for name, value in changes.items():
        flag = "--" + name.replace("_", "-")
        options[options.index(flag) + 1] = str(value)
    return options


def _fails(
    derived: Path,
    site: Path,
    capsys: pytest.CaptureFixture[str],
    expected: str,
    options: list[str],
    **kwargs: Any,
) -> str:
    assert _publicar(derived, site, options=options, **kwargs) == 1
    err = capsys.readouterr().err
    assert expected in err.splitlines(), (expected, err)
    assert f"No se ha escrito nada en {site}." in err
    assert _snapshot(site) == {}
    # Ningún motivo remite a `imagenes-comparacion` a secas (CA5).
    assert re.search(r"imagenes-comparacion(?!-indicios)", err) is None, err
    return err


def _record_ids(derived: Path) -> list[str]:
    internal = GeoJsonDiscrepancyReportReader(derived / "interno").get_report(INE)
    return [r.record_id for r in internal.records]


@pytest.fixture
def derived(tmp_path: Path) -> Path:
    """`discrepancias` sobre las capas de #94 (28043 y 28045)."""
    out = tmp_path / "discrepancias"
    assert _discrepancias(out, INE, "28045") == 0
    return out


@pytest.fixture
def published(derived: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    capsys.readouterr()
    site = tmp_path / "site"
    code = _publicar(derived, site)
    out = capsys.readouterr()
    assert code == 0, out.err
    layer, sidecar = _files(site)
    return {
        "site": site,
        "out": out.out,
        "document": _load(layer),
        "sidecar": _load(sidecar),
        "text": layer.read_text(encoding="utf-8"),
        "sidecar_text": sidecar.read_text(encoding="utf-8"),
    }


def _write_pair(base: Path, rep: MunicipalDiscrepancyReport) -> Path:
    """Interno y publicable, como los escribe `discrepancias`, en `base/{interno,publicable}`."""
    from parcel_intel.domain.discrepancy_policy import redact_for_publication

    write_internal(base / "interno", rep)
    GeoJsonDiscrepancyWriter(base / "publicable", publishable=True).write(
        redact_for_publication(rep)
    )
    return base


def _aprobado_indicio(base: Path) -> Path:
    """Un indicio vía pecuaria → IGN con el mismo tramo (multilínea) que el candidato
    `aprobado` de #315."""
    rep = report(record(INDICIO_ID, APROBADO.stretch, subject_kind=APROBADO.subject.source.kind))
    return _write_pair(base, rep)


# --- CA1 ------------------------------------------------------------------------------


def test_ca1_cada_indicio_lleva_su_tabla_y_la_lista_blanca_crece_solo_en_convergencia(
    derived: Path, published: dict[str, Any]
) -> None:
    """CA1 (CA1 de #246). Dado un municipio con indicios publicables, cuando se ejecuta
    `publicar-discrepancias`, entonces cada feature publicada lleva `convergencia` (título,
    filas en el orden de `CONVERGENCE_ROW_SOURCES`, `imagenes`, aviso, método y criterio). La
    lista blanca de la feature solo crece en `convergencia`."""
    assert list(TABLE_KEYS) == ADR_TABLE_KEYS and list(ROW_KEYS) == ADR_ROW_KEYS
    publicable = GeoJsonDiscrepancyReportReader(derived / "publicable").get_report(INE)
    internal = GeoJsonDiscrepancyReportReader(derived / "interno").get_report(INE)
    use_case = BuildConvergenceTablesUseCase(
        layers=main._trail_layer_reader(FIXTURES),
        historic=main.build_historic_coverage_source(derived / "nada", derived / "nada"),
        comparisons=JsonComparisonManifestReader(derived / "comparacion_indicios"),
        without_images_if_too_long=True,
    )
    result = use_case.execute(INE, indicio_stretches(publicable, internal))
    tables = {r.stretch_id: r.table for r in result.results}
    ids = published_indicio_ids(publicable)
    features = published["document"]["features"]
    assert [f["id"] for f in features] == [public_id for _, public_id in ids]
    assert len(features) == 4  # oráculo de #94
    for (record_id, _), feature in zip(ids, features, strict=True):
        properties = feature["properties"]
        assert set(properties) == FEATURE_KEYS_BEFORE | {"convergencia"}
        table = properties["convergencia"]
        assert set(table) == set(ADR_TABLE_KEYS)  # el JSON publicado ordena las claves
        assert [row["fuente"] for row in table["filas"]] == list(ROW_SOURCE_KEYS)
        for row in table["filas"]:
            assert set(row) == set(ADR_ROW_KEYS)
        assert table["titulo"] == CONVERGENCE_TITLE and table["aviso"] == CONVERGENCE_WARNING
        assert table["metodo"] == "convergencia_fuentes v2"
        assert table["criterio"] == "discrepancia_trazados v1"
        assert table["imagenes"] is None
        expected = tables[record_id]
        assert expected is not None
        assert table == convergence_document(publish_convergence(expected))


# --- CA2 ------------------------------------------------------------------------------


def test_ca2_el_mismo_tramo_da_la_misma_tabla_como_indicio_y_como_tramo_a_verificar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA2 (CA3 de #246). Dado el mismo tramo geométrico (una multilínea) como indicio y como
    tramo a verificar aprobado, con la misma fuente A, las mismas capas y manifiestos generados
    por los dos comandos reales, entonces los dos bloques `convergencia` publicados son
    iguales."""
    # Tramo a verificar: candidatos, revisión y manifiesto de `imagenes-comparacion`
    # (productor real con `generar.fake_wms`), publicado con las capas de #315.
    base = historia_172._inputs(tmp_path / "tramos", [APROBADO], [APROBADO])
    tramos_site = tmp_path / "site_tramos"
    assert (
        historia_315._publicar(
            tramos_site,
            capas=CAPAS_315,
            entrada=base / "candidatos",
            curated=base / "curated",
            comparacion=base / "comparacion",
        )
        == 0
    ), capsys.readouterr().err
    (tramo,) = _load(tramos_site / "candidatos" / f"{INE}.geojson")["features"]

    # Indicio: el mismo tramo, el mismo `http_get` falso y `imagenes-comparacion-indicios`.
    indicios = _aprobado_indicio(tmp_path / "indicios")
    options = indicio_publication_options(indicios / "publicable", CAPAS_315, http=generar.fake_wms)
    site = tmp_path / "site_indicios"
    assert _publicar(indicios, site, options=options) == 0, capsys.readouterr().err
    (indicio,) = _load(_files(site)[0])["features"]

    assert indicio["geometry"] == tramo["geometry"]
    assert indicio["properties"]["convergencia"] == tramo["properties"]["convergencia"]
    # La tabla no es trivial: hay imágenes y una capa que aparece.
    assert _row(indicio, "ign_red_transporte")["valor"] == "aparece"
    assert any(
        row["tipo_evidencia"] == "imagen" and row["valor"] != "sin_cobertura"
        for row in indicio["properties"]["convergencia"]["filas"]
    )


# --- CA3 ------------------------------------------------------------------------------


def test_ca3_sin_catastro_ni_ids_ni_textos_ajenos(derived: Path, published: dict[str, Any]) -> None:
    """CA3 (CA4 de #246, indicios). Es el mismo test de fugas que en #315, sobre
    `discrepancias/<ine>.geojson` y su `.procedencia.json`. Además, ningún `record_id` del
    informe interno de la fixture aparece en ninguno de los dos ficheros."""
    document, sidecar = published["document"], published["sidecar"]
    texts = (published["text"], published["sidecar_text"])
    # Geometría y atributos de Catastro: ni sus ids, ni sus atributos, ni sus vértices.
    for text in texts:
        for leaked in ("99043A00109001", "99043A00109002", '"area_m2"', '"clases"', "(VT)"):
            assert leaked not in text, leaked
    from parcel_intel.adapters.published_layers.projection import to_lonlat

    for lon, lat in to_lonlat(((465000.0, 4442000.0), (465200.0, 4442010.0))):
        assert f"[{lon},{lat}]" not in published["text"]
    for value in [*_strings(document), *_strings(sidecar)]:
        assert CADASTRAL_REFERENCE_RE.search(value) is None, value
        assert PARCEL_LOCATOR_RE.search(value) is None, value
        assert banned_words_in(value) == (), value
    # Ningún record_id del informe interno, ni id de trazado, en ninguno de los dos ficheros.
    record_ids = _record_ids(derived)
    assert len(record_ids) == 5
    internal = _load(derived / "interno" / f"{INE}.geojson")
    trail_ids = {t["id"] for f in internal["features"] for t in f["properties"]["trazados"]}
    for text in texts:
        for identifier in [*record_ids, *trail_ids]:
            assert identifier not in text, identifier
    assert set(sidecar["convergencia"]) == set(SIDECAR_KEYS)
    # Textos validados por su huella y rótulos aprobados.
    assert compute_convergence_text_hash() == CONVERGENCE_TEXT_VALIDATION.text_hash
    labels = set(LAYER_ROW_LABEL.values()) | {
        ORTHOPHOTO_ROW_LABEL.format(franja=slot.name) for slot in COMPARISON_SLOTS
    }
    catastro_seen = False
    for feature in document["features"]:
        table = feature["properties"]["convergencia"]
        assert table["titulo"] == CONVERGENCE_TITLE and table["aviso"] == CONVERGENCE_WARNING
        for row in table["filas"]:
            assert row["rotulo"] in labels
            assert row["texto"] == VALUE_TEXT[row["valor"]]
            for text in (row["rotulo"], row["texto"], *row["atribucion"]):
                assert vetoed_terms_in(text) == (), text
            if row["fuente"] == "catastro_vt" and row["valor"] != "sin_cobertura":
                catastro_seen = True
                assert row["atribucion"] == [CATASTRO_ATTRIBUTION]
                assert row["fecha_consulta"] is not None
    assert catastro_seen
    # Las anotaciones de la entidad publicada no alcanzan nada prohibido (lección de #240).
    historia_315._walk_types(PublishedConvergence, set())


# --- CA4 ------------------------------------------------------------------------------


def test_ca4_interno_ausente_redactado_o_que_no_casa(
    derived: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA4 (CA5 de #246). Sale con 1 y no escribe nada en cuatro casos: un informe interno que
    no existe (`sin informe interno; ejecuta discrepancias`); uno redactado; uno con otra
    secuencia de `record_id` (por ejemplo, regenerado tras cambiar una capa); uno con otra zona
    en un indicio que se publica. El motivo es fijo y no lleva ningún `record_id`."""
    record_ids = _record_ids(derived)
    errors: list[str] = []
    capsys.readouterr()
    # Sin informe interno.
    errors.append(
        _fails(
            derived,
            tmp_path / "s1",
            capsys,
            f"ERROR {INE}: {INTERNAL_MISSING}",
            _options(derived, interno=tmp_path / "no_existe"),
        )
    )
    # Redactado: el publicable como interno.
    errors.append(
        _fails(
            derived,
            tmp_path / "s2",
            capsys,
            f"ERROR {INE}: {INTERNAL_REDACTED}",
            _options(derived, interno=derived / "publicable"),
        )
    )
    # Otra secuencia: `discrepancias` regenerado tras quitar una vía pecuaria de la capa.
    capas = tmp_path / "capas_cambiadas"
    shutil.copytree(FIXTURES, capas)
    path = capas / "vias_pecuarias" / f"{INE}.geojson"
    layer = _load(path)
    layer["features"] = [f for f in layer["features"] if f["id"] != "VP-3"]
    path.write_text(json.dumps(layer, ensure_ascii=False), encoding="utf-8")
    regenerated = tmp_path / "regenerado"
    assert _discrepancias(regenerated, INE, entrada=capas) == 0
    assert _record_ids(regenerated) != record_ids
    errors.append(
        _fails(
            derived,
            tmp_path / "s3",
            capsys,
            f"ERROR {INE}: {INTERNAL_MISMATCH}",
            _options(derived, interno=regenerated / "interno"),
        )
    )
    # Otra zona en un indicio que se publica (mismo record_id).
    publicable = GeoJsonDiscrepancyReportReader(derived / "publicable").get_report(INE)
    ((first_id, _), *_) = published_indicio_ids(publicable)
    moved = tmp_path / "otra_zona"
    moved.mkdir()
    document = _load(derived / "interno" / f"{INE}.geojson")
    feature = next(f for f in document["features"] if f["id"] == first_id)
    coordinates = feature["geometry"]["coordinates"]
    while isinstance(coordinates[0][0], list):
        coordinates = coordinates[0]
    coordinates[0][0] += 5.0
    (moved / f"{INE}.geojson").write_text(json.dumps(document), encoding="utf-8")
    errors.append(
        _fails(
            derived,
            tmp_path / "s4",
            capsys,
            f"ERROR {INE}: {INTERNAL_MISMATCH}",
            _options(derived, interno=moved),
        )
    )
    for err in errors:
        for record_id in record_ids:
            assert record_id not in err, record_id


# --- CA5 ------------------------------------------------------------------------------


def _manifest_variant(derived: Path, base: Path, change: Any) -> Path:
    """Copia del manifiesto de indicios con `change` aplicado, en `base`."""
    document = copy.deepcopy(_load(derived / "comparacion_indicios" / f"{INE}.json"))
    change(document)
    base.mkdir(parents=True)
    (base / f"{INE}.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return base


def test_ca5_todo_o_nada_por_cada_motivo(
    derived: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA5 (CA6 de #246, indicios). Todo o nada: sale con 1 y no escribe nada en estos casos:
    sin manifiesto de indicios (`sin manifiesto de imágenes de comparación; ejecuta
    imagenes-comparacion-indicios`); un indicio ausente o incoherente con él; una franja con
    `fallo`; una capa ilegible; una `HistoricCoverageError`; una fila inválida. El mensaje es
    `ERROR <ine>: indicio <public_id>: tabla de convergencia: <motivo fijo>` o el motivo del
    municipio. Todo motivo que remite a un comando nombra `imagenes-comparacion-indicios`,
    nunca `imagenes-comparacion` a secas."""
    # Manifiesto de indicios con `fake_wms`, que omite tres franjas (para marcar una `fallo`).
    base_options = indicio_publication_options(
        derived / "publicable", FIXTURES, http=generar.fake_wms
    )
    publicable = GeoJsonDiscrepancyReportReader(derived / "publicable").get_report(INE)
    ids = published_indicio_ids(publicable)
    (first_id, first_public) = ids[0]
    record_ids = _record_ids(derived)
    errors: list[str] = []

    def options(**changes: Path) -> list[str]:
        result = list(base_options)
        for name, value in changes.items():
            flag = "--" + name.replace("_", "-")
            result[result.index(flag) + 1] = str(value)
        return result

    capsys.readouterr()
    # Sin manifiesto de indicios.
    empty = tmp_path / "sin_manifiesto"
    empty.mkdir()
    errors.append(
        _fails(
            derived,
            tmp_path / "s1",
            capsys,
            f"ERROR {INE}: sin manifiesto de imágenes de comparación; ejecuta "
            "imagenes-comparacion-indicios",
            options(comparacion=empty),
        )
    )
    # Un indicio ausente del manifiesto.
    absent = _manifest_variant(
        derived,
        tmp_path / "ausente",
        lambda d: d.update(tramos=[t for t in d["tramos"] if t["id"] != first_id]),
    )
    errors.append(
        _fails(
            derived,
            tmp_path / "s2",
            capsys,
            f"ERROR {INE}: indicio {first_public}: tabla de convergencia: {NOT_IN_MANIFEST}"
            "-indicios",
            options(comparacion=absent),
        )
    )

    # Un indicio incoherente con el manifiesto: el trazo en píxeles no es el del tramo.
    def shift(document: dict[str, Any]) -> None:
        tramo = next(t for t in document["tramos"] if t["id"] == first_id)
        tramo["ventanas"][0]["trazo_px"][0][0][0] += 7

    errors.append(
        _fails(
            derived,
            tmp_path / "s3",
            capsys,
            f"ERROR {INE}: indicio {first_public}: tabla de convergencia: el manifiesto no "
            "corresponde al tramo actual; vuelve a ejecutar imagenes-comparacion-indicios",
            options(comparacion=_manifest_variant(derived, tmp_path / "incoherente", shift)),
        )
    )

    # Una franja con `fallo`.
    def with_failure(document: dict[str, Any]) -> None:
        tramo = next(t for t in document["tramos"] if t["id"] == first_id)
        tramo["ventanas"][0]["omitidas"][0].update(motivo="fallo", tipo_error="ConnectionError")

    errors.append(
        _fails(
            derived,
            tmp_path / "s4",
            capsys,
            f"ERROR {INE}: indicio {first_public}: tabla de convergencia: una franja se omitió "
            "por un fallo de descarga; vuelve a ejecutar imagenes-comparacion-indicios",
            options(comparacion=_manifest_variant(derived, tmp_path / "fallo", with_failure)),
        )
    )
    # Una capa ilegible.
    capas = tmp_path / "ilegible"
    shutil.copytree(FIXTURES, capas)
    (capas / "vias_pecuarias" / f"{INE}.geojson").write_text("{no es json", encoding="utf-8")
    errors.append(
        _fails(
            derived,
            tmp_path / "s5",
            capsys,
            f"ERROR {INE}: tabla de convergencia: capa via_pecuaria_oficial no válida; "
            "regenera las capas derivadas",
            options(capas=capas),
        )
    )
    # HistoricCoverageError: aprobación caducada en una unidad aceptada.
    curated, cache = tmp_path / "historico", tmp_path / "cache"
    curated.mkdir()
    historia_313._add_unit(curated, cache, historia_313.UNIT_ID)
    historia_313._edit_csv(curated, historia_313.UNIT_ID, "Plaza en el centro", "Plaza central")
    errors.append(
        _fails(
            derived,
            tmp_path / "s6",
            capsys,
            f"ERROR {INE}: tabla de convergencia: cobertura histórica: la unidad "
            f"{historia_313.UNIT_ID} no es utilizable: aprobaciones caducadas: T001, T002; "
            "vuelve a revisar la unidad",
            options(curated_historico=curated, cache_historico=cache),
        )
    )
    # Fila inválida: un año fuera de rango en la capa del camino actual.
    capas = tmp_path / "anio"
    shutil.copytree(FIXTURES, capas)
    path = capas / "camino_actual" / f"{INE}.geojson"
    document = _load(path)
    for feature in document["features"]:
        feature["properties"]["fecha_dato"] = "1700-01-01"
    path.write_text(json.dumps(document), encoding="utf-8")
    errors.append(
        _fails(
            derived,
            tmp_path / "s7",
            capsys,
            f"ERROR {INE}: tabla de convergencia: fila no válida",
            options(capas=capas),
        )
    )
    for err in errors:
        for record_id in record_ids:
            assert record_id not in err, record_id


# --- CA6 ------------------------------------------------------------------------------


def test_ca6_indicio_demasiado_largo_sin_filas_de_imagen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA6. Un indicio `tramo_demasiado_largo` se publica con la tabla sin filas de ortofoto e
    `imagenes: "Imagen no disponible"` (`NOT_AVAILABLE_TEXT`), y no es un fallo."""
    base = _write_pair(
        tmp_path / "largo", report(record(LONG_ID, line(460000.0, 4438000.0, 3000.0)))
    )
    site = tmp_path / "site"
    assert _publicar(base, site, capas=CAPAS_315) == 0, capsys.readouterr().err
    manifest = _load(base / "comparacion_indicios" / f"{INE}.json")
    assert [t["estado"] for t in manifest["tramos"]] == ["tramo_demasiado_largo"]
    (feature,) = _load(_files(site)[0])["features"]
    table = feature["properties"]["convergencia"]
    assert table["imagenes"] == NOT_AVAILABLE_TEXT == "Imagen no disponible"
    assert [row["fuente"] for row in table["filas"]] == list(ROW_SOURCE_KEYS[:5])
    assert all(row["tipo_evidencia"] == "trazado" for row in table["filas"])


# --- CA7 ------------------------------------------------------------------------------


def test_ca7_umbral_de_aparece_igual_que_en_publicar_candidatos(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA7 (CA8 de #246, indicios). `--umbral-aparece` se comporta igual que en
    `publicar-candidatos` y el `.procedencia.json` lo registra."""
    base = _aprobado_indicio(tmp_path / "indicios")
    options = indicio_publication_options(base / "publicable", CAPAS_315)
    for umbral, valor in (("0.5", "aparece"), ("0.8", "no_aparece")):
        site = tmp_path / f"site-{umbral}"
        extra = () if umbral == "0.5" else ("--umbral-aparece", umbral)
        assert _publicar(base, site, *extra, options=options) == 0, capsys.readouterr().err
        layer, sidecar = _files(site)
        (feature,) = _load(layer)["features"]
        assert _row(feature, "ign_red_transporte")["valor"] == valor  # solape del 60 %
        assert _load(sidecar)["convergencia"]["umbral_aparece"] == float(umbral)

    def no_debe_leerse(*args: object, **kwargs: object) -> object:
        raise AssertionError("no debe leer nada")

    capsys.readouterr()
    for value in ("0", "1.5", "nan"):
        bad = tmp_path / f"malo-{value}"
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(main, "build_historic_coverage_source", no_debe_leerse)
            patch.setattr(main, "build_publish_discrepancies_use_case", no_debe_leerse)
            code = _publicar(base, bad, "--umbral-aparece", value, options=options)
        out = capsys.readouterr()
        assert code == 1
        assert out.err.splitlines() == [UMBRAL_ERROR]
        assert out.out == ""
        assert not bad.exists()


# --- CA8 ------------------------------------------------------------------------------


def test_ca8_por_encima_del_umbral_de_peso_falla_y_no_escribe(
    derived: Path,
    published: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA8 (CA9 de #246). El sink de indicios **falla** por encima de 3.000.000 bytes: sale
    con 1, no escribe nada y deja de imprimir «AVISO: por encima del umbral de peso». Con el
    umbral inyectado bajo."""
    size = len(published["text"].encode("utf-8"))
    without = copy.deepcopy(published["document"])
    for feature in without["features"]:
        del feature["properties"]["convergencia"]
    assert len(dump_compact(without).encode("utf-8")) < size - 1  # la tabla lo pasa
    assert main.PUBLISH_SIZE_THRESHOLD_BYTES == 3_000_000

    use_case = main.build_publish_discrepancies_use_case(
        derived / "publicable",
        derived / "interno",
        derived / "comparacion_indicios",
        tmp_path / "pesado",
        FIXTURES,
        main.build_historic_coverage_source(tmp_path / "nada", tmp_path / "nada"),
        size_threshold_bytes=size - 1,
    )
    with pytest.raises(PublishedLayerError) as error:
        use_case.execute([INE])
    assert f"pesa {size} bytes, por encima del umbral de {size - 1}" in str(error.value)
    assert _snapshot(tmp_path / "pesado") == {}

    real = main.build_publish_discrepancies_use_case

    def small(*args: Any, **kwargs: Any) -> Any:
        kwargs["size_threshold_bytes"] = size - 1
        return real(*args, **kwargs)

    monkeypatch.setattr(main, "build_publish_discrepancies_use_case", small)
    site = tmp_path / "site_cli"
    capsys.readouterr()
    assert _publicar(derived, site) == 1
    out = capsys.readouterr()
    assert f"por encima del umbral de {size - 1}" in out.err
    assert "AVISO: por encima del umbral de peso" not in out.out + out.err
    assert _snapshot(site) == {}
    source = (ROOT / "src" / "parcel_intel" / "cli" / "main.py").read_text(encoding="utf-8")
    assert "AVISO: por encima del umbral de peso" not in source


# --- CA9 ------------------------------------------------------------------------------


def test_ca9_dos_publicaciones_dan_los_mismos_bytes(derived: Path, tmp_path: Path) -> None:
    """CA9 (CA10 de #246). Dos publicaciones con las mismas entradas dan ficheros idénticos
    byte a byte."""
    first, second = tmp_path / "a" / "site", tmp_path / "b" / "site"
    assert _publicar(derived, first) == 0
    assert _publicar(derived, second) == 0
    assert _snapshot(first) == _snapshot(second)
    assert _snapshot(first)


# --- CA10 -----------------------------------------------------------------------------


def test_ca10_los_tests_de_otras_historias_usan_los_productores_reales() -> None:
    """CA10. Los tests de #94, #96, #170 y #183 se actualizan sin relajarlos. Sus ayudantes
    generan el informe interno y el manifiesto de indicios con los productores reales
    (`discrepancias` e `imagenes-comparacion-indicios` con un `http_get` falso). Pasan
    `--interno`, `--comparacion`, `--capas`, `--curated-historico` y `--cache-historico` a
    `tmp_path` (lecciones de #183 y #315)."""
    support = (ROOT / "tests" / "indicio_publication_support.py").read_text(encoding="utf-8")
    for option in ("--interno", "--comparacion", "--capas", "--curated-historico"):
        assert f'"{option}"' in support, option
    assert '"--cache-historico"' in support
    assert '"imagenes-comparacion-indicios"' in support
    assert "http_get=servers" in support
    acceptance = ROOT / "tests" / "acceptance"
    callers = {
        "94": "def _publicar(",
        "96": "def test_ca1_",
        "170": "def test_ca11_",
        "183": "def test_ca8_",
    }
    for story, block in callers.items():
        source = (acceptance / f"test_historia_{story}.py").read_text(encoding="utf-8")
        assert "indicio_publication_options(" in source, story
        helper = source.split(block)[1].split("\ndef ")[0]
        assert "indicio_publication_options(" in helper, story
        # Cada función que invoca el comando por la CLI pasa las opciones nuevas.
        invoking = [c for c in source.split("\ndef ")[1:] if '"publicar-discrepancias",' in c]
        assert invoking, story
        for chunk in invoking:
            assert "indicio_publication_options(" in chunk, (story, chunk[:60])
    # Los datos reales (#96, #183) no escriben en `data/`: el manifiesto va a `tmp_path`.
    for story in ("96", "183"):
        source = (acceptance / f"test_historia_{story}.py").read_text(encoding="utf-8")
        assert "work=tmp_path" in source, story
    # Sin relajar: la lista blanca de la feature de #94 solo gana `convergencia`.
    source_94 = (acceptance / "test_historia_94.py").read_text(encoding="utf-8")
    keys_94 = source_94.split("FEATURE_KEYS = {")[1].split("}")[0]
    assert '"convergencia",  # #316' in keys_94
    assert set(re.findall(r'"([a-z_]+)"', keys_94)) == FEATURE_KEYS_BEFORE | {"convergencia"}


# --- CA11 -----------------------------------------------------------------------------


@pytest.mark.integration
def test_ca11_datos_reales_de_28043(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CA11 [integración]. Con las capas derivadas, el informe interno y la caché real de
    28043, `imagenes-comparacion-indicios --sin-red` (tras una primera ejecución con red) y
    `publicar-discrepancias --municipio 28043` publican todos los indicios con tabla, y el
    fichero pasa el test de CA3. Sin red: si faltan los datos reales en local (`data/`), se
    salta."""
    derived_root = ROOT / "data" / "derived"
    discrepancias = derived_root / "discrepancias"
    needed = [
        discrepancias / "publicable" / f"{INE}.geojson",
        discrepancias / "interno" / f"{INE}.geojson",
        derived_root / "vias_pecuarias" / f"{INE}.geojson",
        derived_root / "camino_actual" / f"{INE}.geojson",
    ]
    missing = [str(path.relative_to(ROOT)) for path in needed if not path.is_file()]
    if missing:
        pytest.skip(f"faltan datos reales: {', '.join(missing)}")
    comparacion = tmp_path / "comparacion_indicios"
    code = _run(
        [
            "imagenes-comparacion-indicios",
            "--municipio",
            INE,
            "--entrada",
            str(discrepancias / "interno"),
            "--cache",
            str(ROOT / "data" / "raw"),
            "--salida",
            str(comparacion),
            "--sin-red",
        ]
    )
    if code != 0:
        pytest.skip("la caché real no tiene todas las imágenes: ejecuta antes con red")
    site = tmp_path / "site"
    options = [
        "--interno",
        str(discrepancias / "interno"),
        "--comparacion",
        str(comparacion),
        "--capas",
        str(derived_root),
        "--curated-historico",
        str(ROOT / "curated" / "historico"),
        "--cache-historico",
        str(ROOT / "data" / "raw" / "ign_planimetrias"),
    ]
    code = _publicar(discrepancias, site, options=options)
    out = capsys.readouterr()
    assert code == 0, out.err
    layer, sidecar = _files(site)
    document = _load(layer)
    publicable = GeoJsonDiscrepancyReportReader(discrepancias / "publicable").get_report(INE)
    assert len(document["features"]) == len(published_indicio_ids(publicable))
    record_ids = [r.record_id for r in publicable.records]
    for feature in document["features"]:
        assert set(feature["properties"]["convergencia"]) == set(ADR_TABLE_KEYS)
    for text in (layer.read_text(encoding="utf-8"), sidecar.read_text(encoding="utf-8")):
        for record_id in record_ids:
            assert record_id not in text
    for value in [*_strings(document), *_strings(_load(sidecar))]:
        assert CADASTRAL_REFERENCE_RE.search(value) is None, value
        assert PARCEL_LOCATOR_RE.search(value) is None, value
        assert banned_words_in(value) == (), value


# --- CA12 -----------------------------------------------------------------------------


class _MustNotRead:
    """Lector que falla si se llama (CA12)."""

    def __init__(self, what: str) -> None:
        self.what = what
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        if name in {"get_report", "get_layer", "get_manifest", "get_coverage"}:
            self.calls += 1
            raise AssertionError(f"no se debe leer {self.what}")
        raise AttributeError(name)

    def skipped_units(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return ()


def test_ca12_las_reglas_de_94_van_primero_y_sin_leer_nada_mas(
    derived: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA12 (añadido en diseño; lección de #315). Dado un informe publicable que falla por una
    regla de #94 (no redactado, sin nombre de municipio, registro sin redactar con fuente A no
    redistribuible), cuando se ejecuta `publicar-discrepancias`, entonces sale con 1 con el
    mensaje de siempre y **no se lee** el informe interno, ni el manifiesto, ni las capas, ni
    `curated/historico/` (lectores falsos que fallan si se llaman). Un municipio sin indicios
    publicables sale «vacío» como hoy sin leer nada de eso."""
    readers = {
        "interno": _MustNotRead("el informe interno"),
        "manifiesto": _MustNotRead("el manifiesto"),
        "capas": _MustNotRead("las capas"),
        "historico": _MustNotRead("curated/historico"),
    }
    real_reader = GeoJsonDiscrepancyReportReader
    internal_dir = tmp_path / "interno_vigilado"

    def reader(directory: Path) -> Any:
        return readers["interno"] if directory == internal_dir else real_reader(directory)

    monkeypatch.setattr(main, "GeoJsonDiscrepancyReportReader", reader)
    monkeypatch.setattr(main, "JsonComparisonManifestReader", lambda d: readers["manifiesto"])
    monkeypatch.setattr(main, "_trail_layer_reader", lambda d: readers["capas"])
    monkeypatch.setattr(main, "build_historic_coverage_source", lambda *a: readers["historico"])
    options = [
        "--interno",
        str(internal_dir),
        "--comparacion",
        str(tmp_path / "comparacion"),
        "--capas",
        str(tmp_path / "capas"),
        "--curated-historico",
        str(tmp_path / "curated"),
        "--cache-historico",
        str(tmp_path / "cache"),
    ]

    # Sin nombre de municipio y con un registro de fuente A no redistribuible sin redactar.
    def variant(name: str, change: Any) -> Path:
        document = _load(derived / "publicable" / f"{INE}.geojson")
        change(document)
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{INE}.geojson").write_text(json.dumps(document), encoding="utf-8")
        return tmp_path / name

    def unredact(document: dict[str, Any]) -> None:
        internal = _load(derived / "interno" / f"{INE}.geojson")
        index = next(
            i
            for i, f in enumerate(document["features"])
            if f["properties"]["zona_omitida_por_licencia"]
        )
        document["features"][index] = internal["features"][index]

    cases = [
        (
            derived / "interno",
            f"ERROR {INE}: no publicable: no es la variante publicable del informe",
        ),
        (
            variant("sin_nombre", lambda d: d["properties"].update(nombre_municipio="")),
            f"ERROR {INE}: no publicable: el informe no trae el nombre del municipio; "
            "regenera las capas derivadas y `discrepancias`",
        ),
        (
            variant("sin_redactar", unredact),
            f"ERROR {INE}: no publicable: registro sin redactar con fuente A 'catastro_vt', "
            "que no es redistribuible",
        ),
    ]
    capsys.readouterr()
    for index, (entrada, expected) in enumerate(cases):
        site = tmp_path / f"site-{index}"
        assert _publicar(derived, site, entrada=entrada, options=options) == 1
        err = capsys.readouterr().err
        assert expected in err.splitlines(), (expected, err)
        assert _snapshot(site) == {}
    # Vacío: 28045 (todo indicio tiene a Catastro en el lado A).
    site = tmp_path / "vacio"
    assert _publicar(derived, site, codes=("28045",), options=options) == 0
    assert "sin indicios publicables, no se publica: 28045" in capsys.readouterr().out
    assert _snapshot(site) == {}
    assert all(r.calls == 0 for r in readers.values())


# --- CA13 -----------------------------------------------------------------------------


def test_ca13_el_kit_de_campo_no_exporta_convergencia(
    published: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA13 (añadido en diseño). Con un fichero de indicios publicado con `convergencia`,
    `publicar-kit-campo` y `publicar-kit-campo-qfield` siguen funcionando y no exportan
    `convergencia`; y el test de CA8 de #85 (lista blanca de propiedades de
    `site/datos/discrepancias/`) admite `convergencia` y nada más nuevo."""
    entrada = tmp_path / "entrada"
    shutil.copytree(KIT_FIXTURES, entrada)
    for path in _files(published["site"]):
        shutil.copy(path, entrada / "discrepancias" / path.name)
    capsys.readouterr()
    salida = tmp_path / "salida"
    common = ["--municipio", INE, "--entrada", str(entrada), "--salida", str(salida)]
    assert _run(["publicar-kit-campo", *common]) == 0, capsys.readouterr().err
    assert _run(["publicar-kit-campo-qfield", *common]) == 0, capsys.readouterr().err
    written = [p for p in (salida / "kit").rglob("*") if p.is_file()]
    assert any(p.suffix == ".gpx" for p in written)
    assert any(p.suffix == ".gpkg" for p in written)
    markers = ["convergencia", CONVERGENCE_TITLE, "Sin cobertura", "Imagen disponible"]
    for file in written:
        if file.suffix == ".zip":
            continue  # comprimido: su contenido es el .gpkg/.qgs ya comprobados
        content = file.read_bytes()
        for marker in markers:
            assert marker.encode("utf-8") not in content, (file.name, marker)
    # #85 CA8: la lista blanca de indicios gana `convergencia` y nada más.
    assert historia_85._INDICIO_PROPS == FEATURE_KEYS_BEFORE | {"convergencia"}
    # Y el fichero de este sink la cumple.
    for feature in published["document"]["features"]:
        assert set(feature["properties"]) <= historia_85._INDICIO_PROPS
