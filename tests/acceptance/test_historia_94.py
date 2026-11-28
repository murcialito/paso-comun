"""Historia #94: `publicar-discrepancias` publica los indicios de un municipio en
`site/datos/discrepancias/` con la ficha pública de #16.

Las entradas son SINTÉTICAS (`tests/fixtures/discrepancias_publicar/`, ver su LEEME.md):
el test ejecuta `discrepancias` sobre esas capas y después `publicar-discrepancias`.

Ajuste por el ADR 0041 (comentario del fundador en la issue antes de desarrollar): los
indicios con Catastro como **contraparte** sí se publican, sin geometría de Catastro, con
la cita de la Dirección General del Catastro, la fecha de consulta y el aviso de no
fehaciencia. Por eso el oráculo de 28043 publica 4 indicios (no 2) y omite 1 (lado A), y
CA10 comprueba la publicación en lugar de la omisión. Los docstrings copian el criterio
literal de la issue.
"""

import ast
import json
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from parcel_intel.adapters.discrepancy_files import (
    GeoJsonDiscrepancyReportReader,
    read_report,
    report_to_geojson_text,
)
from parcel_intel.cli import main as cli
from parcel_intel.domain.discrepancy import Discrepancy, Measurement, SourceRef, TrailRef
from parcel_intel.domain.discrepancy_criterion import CRITERION
from parcel_intel.domain.discrepancy_policy import redact_for_publication
from parcel_intel.domain.discrepancy_report import (
    DiscrepancyRecord,
    MunicipalDiscrepancyReport,
    TrailEvidence,
)
from parcel_intel.domain.geometry import Geometry, MultiLineGeometry
from parcel_intel.domain.provenance import Provenance, Redistribution
from parcel_intel.domain.public_card import (
    BANNED_WORDS,
    CATASTRO_ATTRIBUTION,
    VALIDATION,
    WARNING,
    banned_words_in,
    build_card,
    compute_text_hash,
)
from parcel_intel.domain.published_discrepancy import (
    CADASTRAL_REFERENCE_RE,
    build_discrepancy_layer,
)
from parcel_intel.domain.published_layers import PUBLISHED_LAYERS
from tests.convergence_support import indicio_tables
from tests.fixtures.published_layers import write_real_derived_layers
from tests.indicio_publication_support import indicio_publication_options
from tests.network_guard import no_network

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "discrepancias_publicar" / "entrada"
NOTICE = ROOT / "NOTICE"
SITE_DATOS = ROOT / "site" / "datos"
AMBITO = ("28043", "28013", "28052", "28019", "28170", "28180", "28157")
IGN_ATTRIBUTION = "Obra derivada de IGR-RT 2026 CC-BY 4.0 Instituto Geográfico Nacional"

FEATURE_KEYS = {
    "tipo",
    "etiqueta",
    "texto",
    "aviso",
    "confianza",
    "evidencia",
    "criterio",
    "descripcion_criterio",
    "aviso_omision",
    "convergencia",  # #316: la lista blanca solo crece en la tabla de convergencia
}
EVIDENCE_KEYS = {
    "papel",
    "fuente",
    "anio",
    "origen",
    "url",
    "fecha_descarga",
    "licencia",
    "atribucion",
}
CA11_TEXT = (
    "Indicio a verificar: según vías pecuarias (IDEM), 2014 existe un trazado en este tramo "
    "que no aparece en red de transporte del IGN, 2025."
)
DESPLAZAMIENTO_TEXT = (
    "Indicio a verificar: el trazado de red de transporte del IGN y el de vías pecuarias "
    "(IDEM) difieren unos 12 m en este tramo."
)


# --- Utilidades -----------------------------------------------------------------------


def _run(argv: list[str], capsys: pytest.CaptureFixture[str] | None = None) -> int:
    old = sys.argv
    sys.argv = ["parcel_intel.cli.main", *argv]
    try:
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
    finally:
        sys.argv = old
    code = exit_info.value.code
    assert isinstance(code, int)
    return code


def _codes(*codes: str) -> list[str]:
    return [arg for code in codes for arg in ("--municipio", code)]


def _discrepancias(entrada: Path, salida: Path, *codes: str) -> int:
    return _run(
        ["discrepancias", "--entrada", str(entrada), "--salida", str(salida), *_codes(*codes)]
    )


def _publicar(entrada: Path, salida: Path, *args: str, capas: Path = FIXTURES) -> int:
    """Desde #316 también pasa el interno, el manifiesto de indicios (generado con el
    productor real), las capas y `curated/historico/` a `tmp_path` (CA10 de #316)."""
    return _run(
        [
            "publicar-discrepancias",
            "--entrada",
            str(entrada),
            "--salida",
            str(salida),
            *indicio_publication_options(entrada, capas),
            *args,
        ]
    )


def _load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _snapshot(directory: Path) -> dict[str, bytes]:
    if not directory.exists():
        return {}
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _walk(value: object) -> Iterator[tuple[str, object]]:
    """(«clave» o «valor», dato) de todo el JSON."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield "clave", key
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    else:
        yield "valor", value


def _features(path: Path) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = _load(path)["features"]
    return features


def _by_text(features: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        grouped.setdefault(feature["properties"]["texto"], []).append(feature)
    return grouped


@pytest.fixture
def pipeline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """`discrepancias` + `publicar-discrepancias --municipio 28043` sobre las fixtures."""
    derived = tmp_path / "discrepancias"
    site = tmp_path / "site" / "datos"
    assert _discrepancias(FIXTURES, derived, "28043", "28045") == 0
    capsys.readouterr()
    code = _publicar(derived / "publicable", site, *_codes("28043"))
    out = capsys.readouterr()
    return {
        "code": code,
        "out": out.out,
        "err": out.err,
        "derived": derived,
        "site": site,
        "file": site / "discrepancias" / "28043.geojson",
    }


# --- Criterios ------------------------------------------------------------------------


def test_ca1_publica_en_4326_determinista_una_feature_por_indicio(
    pipeline: dict[str, Any], tmp_path: Path
) -> None:
    """CA1. Dado el fichero `data/derived/discrepancias/publicable/<ine>.geojson` de un
    municipio con indicios redistribuibles, cuando se ejecuta `publicar-discrepancias
    --municipio <ine>`, entonces se escribe `site/datos/discrepancias/<ine>.geojson` en
    EPSG:4326, determinista (dos ejecuciones dan bytes idénticos), con una feature por
    indicio."""
    assert pipeline["code"] == 0, pipeline["err"]
    path: Path = pipeline["file"]
    document = _load(path)
    assert document["type"] == "FeatureCollection"
    assert "crs" not in document  # RFC 7946: EPSG:4326 implícito
    # Oráculo (LEEME.md, ajustado por el ADR 0041): 4 indicios publicados.
    features = document["features"]
    assert len(features) == 4
    assert len({f["id"] for f in features}) == 4
    for feature in features:
        for kind, value in _walk(feature["geometry"]["coordinates"]):
            assert kind == "valor" and isinstance(value, float)
        positions = re.findall(
            r"\[(-?\d+\.\d+),(-?\d+\.\d+)\]", json.dumps(feature["geometry"], separators=(",", ":"))
        )
        assert positions
        for lon, lat in positions:
            assert -3.62 < float(lon) < -3.15 and 39.98 < float(lat) < 40.22  # Colmenar
            assert len(lon.split(".")[1]) <= 6 and len(lat.split(".")[1]) <= 6

    first = _snapshot(pipeline["site"])
    assert _publicar(pipeline["derived"] / "publicable", pipeline["site"], *_codes("28043")) == 0
    assert _snapshot(pipeline["site"]) == first
    other = tmp_path / "otra"
    assert _publicar(pipeline["derived"] / "publicable", other, *_codes("28043")) == 0
    assert _snapshot(other) == first


def test_ca2_zona_del_tramo_y_propiedades_de_la_ficha(pipeline: dict[str, Any]) -> None:
    """CA2. Dado un indicio publicado, cuando se lee su feature, entonces la geometría es
    solo la zona del tramo y las propiedades son las de la ficha de #16 (`build_card`): tipo,
    etiqueta, texto de indicio, aviso, confianza en palabras (`baja`/`media`/`alta`),
    evidencia (fuente, año, URL, fecha de descarga, licencia, atribución), criterio con
    versión, y aviso de omisión si procede."""
    features = _features(pipeline["file"])
    report = GeoJsonDiscrepancyReportReader(pipeline["derived"] / "publicable").get_report("28043")
    published = [r for r in report.records if not r.zone_redacted]
    assert len(published) == len(features)
    for feature, record in zip(features, published, strict=True):
        card = build_card(record.discrepancy)
        props = feature["properties"]
        assert set(props) == FEATURE_KEYS
        assert props["tipo"] == card.kind
        assert props["etiqueta"] == card.label
        assert props["texto"] == card.text and props["texto"].startswith("Indicio a verificar:")
        assert props["aviso"] == card.warning == WARNING
        assert props["confianza"] == card.confidence_level in {"baja", "media", "alta"}
        assert props["criterio"] == card.criterion == "discrepancia_trazados v1"
        assert props["descripcion_criterio"] == card.criterion_description
        assert props["aviso_omision"] == card.omission_notice
        assert len(props["evidencia"]) == len(card.sources)
        for item, source in zip(props["evidencia"], card.sources, strict=True):
            assert set(item) == EVIDENCE_KEYS
            assert item["papel"] == source.role
            assert item["fuente"] == source.label
            assert item["anio"] == source.data_year
            assert item["origen"] == source.source
            assert item["url"] == source.url
            assert item["fecha_descarga"] == source.retrieved_at.isoformat()
            assert item["licencia"] == source.license
            assert item["atribucion"] == source.attribution
        # La geometría es la zona del lado A, con su tipo y sus partes (lección 0001).
        zone = record.discrepancy.zone
        geometry = feature["geometry"]
        if isinstance(zone, Geometry):
            expected = "Polygon" if len(zone.rings) == 1 else "MultiPolygon"
            assert geometry["type"] == expected
            if expected == "MultiPolygon":
                assert len(geometry["coordinates"]) == len(zone.rings) == 2
        else:
            expected = "LineString" if len(zone.lines) == 1 else "MultiLineString"
            assert geometry["type"] == expected
    assert {f["geometry"]["type"] for f in features} == {"MultiPolygon", "LineString"}


def test_ca3_ni_confianza_numerica_ni_referencias_ni_ids_ni_catastro(
    pipeline: dict[str, Any],
) -> None:
    """CA3. Dado cualquier indicio publicado, entonces no aparece el valor numérico de
    confianza, ninguna referencia catastral, ningún identificador de parcela ni de trazado,
    ninguna geometría de Catastro ni dato de titularidad (se comprueba recorriendo todo el
    fichero publicado)."""
    path: Path = pipeline["file"]
    document = _load(path)
    text = path.read_text(encoding="utf-8")
    entrada = _load(pipeline["derived"] / "publicable" / "28043.geojson")

    # Ningún número en las propiedades salvo enteros (el año).
    for feature in document["features"]:
        for kind, value in _walk(feature["properties"]):
            if kind == "valor" and not isinstance(value, str | type(None)):
                assert isinstance(value, int) and not isinstance(value, bool), value
    for kind, value in _walk(document["properties"]):
        assert kind == "clave" or isinstance(value, str), value

    # Ni el id de ningún trazado de la entrada, ni el record_id, ni referencias.
    trail_ids = {t["id"] for f in entrada["features"] for t in f["properties"]["trazados"]}
    assert "99043A00109001" in trail_ids or "99043A00109002" in trail_ids
    for trail_id in trail_ids:
        assert trail_id not in text, trail_id
    for feature in entrada["features"]:
        assert feature["id"] not in text
    assert CADASTRAL_REFERENCE_RE.search(text) is None
    assert re.search(r"(?i)[?&](poligono|polígono|parcela|refcat)=", text) is None

    # Claves sin titularidad ni Catastro; valores sin titularidad.
    for kind, value in _walk(document):
        if kind == "clave":
            assert isinstance(value, str)
            assert not re.search(r"(?i)catastr|titular|propietari|refcat|nombre", value) or (
                value == "nombre_municipio"
            ), value
        elif isinstance(value, str):
            assert banned_words_in(value) == (), value
            lowered = value.casefold()
            assert "cartografía catastral" not in lowered
            assert "información catastral" not in lowered

    # «Catastro» solo puede aparecer en la evidencia de la contraparte Catastro (ADR 0041).
    for feature in document["features"]:
        for item in feature["properties"]["evidencia"]:
            mentions = any("catastr" in str(v).casefold() for v in item.values())
            if mentions:
                assert item["papel"] == "contraparte"
                assert item["atribucion"] == CATASTRO_ATTRIBUTION
        # Fuera de la evidencia y de la tabla, solo el rótulo público de la fuente en el texto
        # aprobado. La tabla (#316) se acota aparte: Catastro solo en su propia fila.
        rest = {
            k: v for k, v in feature["properties"].items() if k not in {"evidencia", "convergencia"}
        }
        rest_text = json.dumps(rest, ensure_ascii=False).replace(
            "trazado derivado de datos del Catastro", ""
        )
        assert "catastr" not in rest_text.casefold()
        for row in feature["properties"]["convergencia"]["filas"]:
            if row["fuente"] != "catastro_vt":
                assert "catastr" not in json.dumps(row, ensure_ascii=False).casefold(), row
            elif row["valor"] != "sin_cobertura":
                assert row["atribucion"] == [CATASTRO_ATTRIBUTION]

    # Ninguna geometría de Catastro: ningún vértice publicado es de C-2 (solo Catastro).
    from parcel_intel.adapters.published_layers.projection import to_lonlat

    catastro_only = to_lonlat(((465000.0, 4442000.0), (465200.0, 4442010.0)))
    for lon, lat in catastro_only:
        assert f"[{lon},{lat}]" not in text


def test_ca4_zona_redactada_no_entra_y_se_cuenta(
    pipeline: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA4. Dado un indicio de zona redactada (`zona_omitida_por_licencia`, lado A de
    Catastro), cuando se publica, entonces no entra en el fichero y la salida del comando
    indica cuántos se han dejado fuera por municipio."""
    entrada = _load(pipeline["derived"] / "publicable" / "28043.geojson")
    redacted = [f for f in entrada["features"] if f["properties"]["zona_omitida_por_licencia"]]
    assert len(redacted) == 1 and redacted[0]["properties"]["fuente_a"] == "catastro_vt"
    published = _features(pipeline["file"])
    assert all(
        not any(
            e["papel"] == "sujeto" and "Catastro" in e["fuente"]
            for e in f["properties"]["evidencia"]
        )
        for f in published
    )
    assert "28043" in pipeline["out"]
    assert "omitidos por licencia 1 (lado A: 1, contraparte: 0)" in pipeline["out"]

    # 28045: todo indicio tiene a Catastro en el lado A.
    assert _publicar(pipeline["derived"] / "publicable", tmp_path / "s", *_codes("28045")) == 0
    out = capsys.readouterr().out
    assert "sin indicios publicables, no se publica: 28045" in out
    assert "omitidos por licencia 1 (lado A: 1, contraparte: 0)" in out


def test_ca5_procedencia_publicable_con_atribuciones_e_indice(pipeline: dict[str, Any]) -> None:
    """CA5. Dado cada municipio publicado, entonces existe
    `site/datos/discrepancias/<ine>.procedencia.json` con `redistribucion: publicable` y las
    atribuciones de las fuentes de la evidencia, y el índice `site/datos/indice.json` lista
    la capa `discrepancias` (misma convención que ADR 0036)."""
    site: Path = pipeline["site"]
    # Convención de ADR 0036: `<fichero>.procedencia.json` junto a cada fichero de datos.
    sidecar = _load(site / "discrepancias" / "28043.geojson.procedencia.json")
    assert sidecar["redistribucion"] == "publicable"
    for field in ("fuente", "url", "fecha_descarga", "licencia"):
        assert isinstance(sidecar[field], str) and sidecar[field]
    assert sidecar["licencia"] == "CC BY 4.0"
    assert sidecar["capa"] == "discrepancias" and sidecar["municipio"] == "28043"
    features = _features(site / "discrepancias" / "28043.geojson")
    cited = {e["atribucion"] for f in features for e in f["properties"]["evidencia"]}
    # Desde #316 se suman las atribuciones de las filas de la tabla de convergencia.
    rows = {
        a
        for f in features
        for r in f["properties"]["convergencia"]["filas"]
        for a in r["atribucion"]
    }
    assert rows
    assert set(sidecar["atribucion"]) == cited | rows
    assert IGN_ATTRIBUTION in cited and CATASTRO_ATTRIBUTION in cited
    assert {f["origen"] for f in sidecar["fuentes"]} == {
        "SINTÉTICO capa de vías pecuarias",
        "SINTÉTICO capa de camino actual",
        "SINTÉTICO capa de caminos de Catastro",
    }
    index = _load(site / "indice.json")
    (entry,) = [m for m in index["municipios"] if m["ine"] == "28043"]
    assert entry["capas"]["discrepancias"] == "discrepancias/28043.geojson"
    assert entry["nombre"] == "Colmenar de Oreja"
    assert _load(site / "indice.json.procedencia.json")["redistribucion"] == "publicable"


def test_ca6_guarda_de_lenguaje_y_marca_de_textos(pipeline: dict[str, Any]) -> None:
    """CA6. Guarda de lenguaje: un test recorre todo el texto del fichero publicado y falla
    si aparece una palabra de `BANNED_WORDS` o si `compute_text_hash()` no coincide con
    `VALIDATION.text_hash`. Los textos son exactamente los aprobados en #16; no se redactan
    nuevos."""
    assert compute_text_hash() == VALIDATION.text_hash
    assert len(BANNED_WORDS) == 10
    for path in sorted(pipeline["site"].rglob("*.json*")):
        for kind, value in _walk(_load(path)):
            if isinstance(value, str):
                assert banned_words_in(value) == (), (path, kind, value)
    texts = {f["properties"]["texto"] for f in _features(pipeline["file"])}
    assert CA11_TEXT in texts and DESPLAZAMIENTO_TEXT in texts


def test_ca7_ambito_publica_los_7_sin_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA7. Dado `--ambito`, cuando se ejecuta, entonces se publican los 7 municipios del
    primer hito; el comando no usa red."""
    real = tmp_path / "derived"
    write_real_derived_layers(real, tmp_path / "raw")
    derived = tmp_path / "discrepancias"
    site = tmp_path / "site"
    with no_network():
        assert (
            _run(["discrepancias", "--ambito", "--entrada", str(real), "--salida", str(derived)])
            == 0
        )
        capsys.readouterr()
        assert _publicar(derived / "publicable", site, "--ambito", capas=real) == 0
    out = capsys.readouterr().out
    for code in AMBITO:
        published = f"publicado {site / 'discrepancias' / f'{code}.geojson'} " in out
        empty = f"sin indicios publicables, no se publica: {code}" in out
        assert published != empty, code  # cada municipio, o publicado o declarado vacío
        assert (site / "discrepancias" / f"{code}.geojson").is_file() == published


def test_ca8_todo_o_nada(
    pipeline: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA8. Todo o nada: dado un municipio sin fichero de entrada, con entrada no publicable
    o corrupta, cuando se ejecuta, entonces sale con código 1, explica por qué y no escribe
    ningún fichero (como `publicar-capas`). Un municipio sin indicios publicables no genera
    fichero y se informa como vacío."""
    derived: Path = pipeline["derived"]
    site = tmp_path / "copia"
    shutil.copytree(pipeline["site"], site)
    before = _snapshot(site)
    capsys.readouterr()

    # Sin fichero (28044), junto a uno válido.
    assert _publicar(derived / "publicable", site, *_codes("28043", "28044")) == 1
    err = capsys.readouterr().err
    assert "ERROR 28044: sin fichero" in err and "No se ha escrito nada" in err
    assert _snapshot(site) == before

    # Variante interna: no es publicable.
    assert _publicar(derived / "interno", site, *_codes("28043")) == 1
    err = capsys.readouterr().err
    assert "ERROR 28043: no publicable" in err and "variante publicable" in err
    assert _snapshot(site) == before

    # Corrupta.
    broken = tmp_path / "roto"
    broken.mkdir()
    (broken / "28043.geojson").write_text("{no es json", encoding="utf-8")
    assert _publicar(broken, site, *_codes("28043")) == 1
    err = capsys.readouterr().err
    assert "corrupto" in err and "JSON ilegible" in err
    assert _snapshot(site) == before

    # Vacío: 28045 no genera fichero y se informa.
    empty_site = tmp_path / "vacio"
    assert _publicar(derived / "publicable", empty_site, *_codes("28045")) == 0
    assert "sin indicios publicables, no se publica: 28045" in capsys.readouterr().out
    assert _snapshot(empty_site) == {}


def test_ca9_lint_mypy_ruff_y_dominio_sin_epsg4326() -> None:
    """CA9. `uv run lint-imports`, `uv run mypy` y `uv run ruff check .` en verde; el
    dominio sigue sin conocer EPSG:4326 (la reproyección es del adapter)."""
    # Los tres comandos los ejecuta CI; aquí se fija lo comprobable sin ellos.
    domain = ROOT / "src" / "parcel_intel" / "domain"
    for path in sorted(domain.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            # Ningún 4326 en el código (los docstrings pueden decir que no lo conoce).
            if isinstance(node, ast.Constant) and node.value == 4326:
                raise AssertionError(f"{path.name}: usa 4326")
            if isinstance(node, ast.Import | ast.ImportFrom):
                modules = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                )
                for module in modules:
                    assert module is None or not module.startswith(("pyproj", "shapely")), path
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    for k in range(1, 16):
        assert sum(name.startswith(f"test_ca{k}_") for name in names) == 1, k


def test_ca10_catastro_como_contraparte_se_publica_segun_adr_0041(
    pipeline: dict[str, Any],
) -> None:
    """CA10. Catastro como contraparte: dado un indicio cuyo lado B es Catastro (p. ej. vía
    pecuaria → `catastro_vt`), cuando se publica, entonces no entra en el fichero (ADR 0035
    §3: nada derivado de Catastro se publica sin consulta jurídica) y la salida del comando lo
    cuenta como omitido por licencia, separado de los de CA4: `omitidos por licencia: N (lado
    A: x, contraparte: y)`.

    Ajustado por el ADR 0041 (comentario del fundador en #94): el indicio **sí** se publica,
    sin geometría de Catastro, citando a la Dirección General del Catastro con la fecha de
    consulta, con el aviso de no fehaciencia y sin llamarlo «cartografía catastral»."""
    entrada = _load(pipeline["derived"] / "publicable" / "28043.geojson")
    with_catastro_b = [
        f
        for f in entrada["features"]
        if f["properties"]["fuente_b"] == "catastro_vt"
        and not f["properties"]["zona_omitida_por_licencia"]
    ]
    assert len(with_catastro_b) == 2  # VP-2 y VP-3 frente a Catastro
    features = _features(pipeline["file"])
    catastro = [
        f
        for f in features
        if any(e["atribucion"] == CATASTRO_ATTRIBUTION for e in f["properties"]["evidencia"])
    ]
    assert len(catastro) == 2
    for feature in catastro:
        props = feature["properties"]
        (subject, counterpart) = props["evidencia"]
        assert subject["fuente"] == "vías pecuarias (IDEM)"  # la zona es del lado A
        assert counterpart["papel"] == "contraparte"
        assert counterpart["fecha_descarga"] == "2026-11-24"  # fecha de consulta
        attribution = counterpart["atribucion"]
        assert "Dirección General del Catastro" in attribution
        assert "carece de fehaciencia" in attribution
        assert "puede haber cambiado desde la consulta" in attribution
        assert "cartografía catastral" not in json.dumps(props, ensure_ascii=False).casefold()
        assert props["aviso_omision"] is None
        assert props["texto"].endswith("trazado derivado de datos del Catastro, 2026.")
    assert "omitidos por licencia 1 (lado A: 1, contraparte: 0)" in pipeline["out"]
    # NOTICE lleva la atribución de Catastro (ADR 0041 §5).
    notice = NOTICE.read_text(encoding="utf-8")
    assert CATASTRO_ATTRIBUTION in notice.replace("\n", " ")


def test_ca11_anio_de_la_contraparte(pipeline: dict[str, Any]) -> None:
    """CA11. Año de la contraparte: dado un `AUSENTE_EN_FUENTE` de vías pecuarias (fecha del
    dato 2014-01-01) frente a la red del IGN cuya capa tiene una fecha común 2025-03-15,
    cuando se publica, entonces el texto es exactamente «Indicio a verificar: según vías
    pecuarias (IDEM), 2014 existe un trazado en este tramo que no aparece en red de
    transporte del IGN, 2025.», no «año no declarado»."""
    grouped = _by_text(_features(pipeline["file"]))
    assert len(grouped[CA11_TEXT]) == 1
    feature = grouped[CA11_TEXT][0]
    assert feature["geometry"]["type"] == "MultiPolygon"  # VP-2, dos partes
    assert [e["anio"] for e in feature["properties"]["evidencia"]] == [2014, 2025]
    assert "año no declarado" not in pipeline["file"].read_text(encoding="utf-8")


def test_ca12_evidencia_del_ign_con_atribucion_de_notice(pipeline: dict[str, Any]) -> None:
    """CA12. Evidencia del IGN: dado un indicio con la red del IGN (lado A o B), cuando se
    publica, entonces su evidencia aparece con la etiqueta «red de transporte del IGN», la
    atribución literal de `NOTICE` («Obra derivada de IGR-RT 2026 CC-BY 4.0 Instituto
    Geográfico Nacional») y sin aviso de omisión."""
    assert IGN_ATTRIBUTION in NOTICE.read_text(encoding="utf-8")
    assert PUBLISHED_LAYERS["camino_actual"].attribution == IGN_ATTRIBUTION
    sides = set()
    for feature in _features(pipeline["file"]):
        props = feature["properties"]
        for item in props["evidencia"]:
            if item["origen"] == "SINTÉTICO capa de camino actual":
                assert item["fuente"] == "red de transporte del IGN"
                assert item["atribucion"] == IGN_ATTRIBUTION
                assert props["aviso_omision"] is None
                sides.add(item["papel"])
    assert sides == {"sujeto", "contraparte"}


def test_ca13_indice_compartido_en_los_dos_ordenes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA13. Índice compartido: dado `site/datos/` con `vias_pecuarias` y `camino_actual`
    publicados, cuando se ejecuta `publicar-discrepancias` y después `publicar-capas` (y
    también en el orden inverso), entonces el índice final lista las tres capas del
    municipio; ningún comando borra del índice la capa del otro."""
    derived = tmp_path / "discrepancias"
    assert _discrepancias(FIXTURES, derived, "28043") == 0
    capas = ["publicar-capas", "--entrada", str(FIXTURES), *_codes("28043")]
    expected = {"camino_actual", "discrepancias", "vias_pecuarias"}
    for order in ("indicios_primero", "capas_primero"):
        site = tmp_path / order
        # Partimos de `site/datos/` con las dos capas de trazados ya publicadas.
        assert _run([*capas, "--salida", str(site)]) == 0
        steps = ["indicios", "capas"] if order == "indicios_primero" else ["capas", "indicios"]
        for step in steps:
            if step == "capas":
                code = _run([*capas, "--salida", str(site)])
            else:
                code = _publicar(derived / "publicable", site, *_codes("28043"))
            assert code == 0, capsys.readouterr().err
        (entry,) = _load(site / "indice.json")["municipios"]
        assert set(entry["capas"]) == expected, order


def test_ca14_ida_y_vuelta_y_formato_anterior_rechazado(
    pipeline: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA14. Formato autosuficiente y de ida y vuelta: dado un informe escrito por
    `discrepancias`, cuando se lee con el lector nuevo y se vuelve a escribir, entonces los
    bytes son idénticos (interno y publicable). Un fichero publicable escrito antes de esta
    historia (sin `nombre_municipio`, `procedencia_b`, `fecha_dato_b` ni `redistribucion`) se
    rechaza con un mensaje que pide regenerarlo con `discrepancias`, sin escribir nada."""
    derived: Path = pipeline["derived"]
    for variant in ("interno", "publicable"):
        for code in ("28043", "28045"):
            path = derived / variant / f"{code}.geojson"
            assert report_to_geojson_text(read_report(path)) == path.read_text(encoding="utf-8")

    # Informe en memoria con Polygon, MultiPolygon de 2 partes, MultiLineString de SOLAPE,
    # zona redactada y contraparte de Catastro.
    report = _synthetic_report()
    for variant_report in (report, redact_for_publication(report)):
        written = tmp_path / "ida" / "x.geojson"
        written.parent.mkdir(exist_ok=True)
        text = report_to_geojson_text(variant_report)
        written.write_text(text, encoding="utf-8")
        assert read_report(written) == variant_report
        assert report_to_geojson_text(read_report(written)) == text

    # Formato anterior a #94: se quitan las claves nuevas.
    old = _load(derived / "publicable" / "28043.geojson")
    del old["properties"]["nombre_municipio"]
    for feature in old["features"]:
        props = feature["properties"]
        del props["procedencia_b"], props["fecha_dato_b"]
        for trail in props["trazados"]:
            del trail["procedencia"]["redistribucion"]
    legacy = tmp_path / "anterior"
    legacy.mkdir()
    (legacy / "28043.geojson").write_text(json.dumps(old), encoding="utf-8")
    site = tmp_path / "sin_escribir"
    capsys.readouterr()
    assert _publicar(legacy, site, *_codes("28043")) == 1
    err = capsys.readouterr().err
    assert "discrepancias --municipio" in err and "#94" in err
    assert _snapshot(site) == {}


def test_ca15_datos_reales_de_colmenar_en_el_repo() -> None:
    """CA15. Datos reales: la PR incluye `site/datos/discrepancias/28043.geojson`, su
    `.procedencia.json` y el índice regenerado, obtenidos con `discrepancias --municipio
    28043` y `publicar-discrepancias --municipio 28043` sobre las capas derivadas reales de
    Colmenar de Oreja. La PR anota los indicios publicados por tipo, los omitidos, el peso
    del fichero y la `fecha_descarga` de las capas usadas. Además ejecuta
    `publicar-discrepancias --ambito` y anota lo que falle o salga vacío (lección #84), sin
    subir esos ficheros (son de #96)."""
    path = SITE_DATOS / "discrepancias" / "28043.geojson"
    sidecar = _load(SITE_DATOS / "discrepancias" / "28043.geojson.procedencia.json")
    assert sidecar["redistribucion"] == "publicable" and sidecar["municipio"] == "28043"
    document = _load(path)
    assert document["properties"]["nombre_municipio"] == "Colmenar de Oreja"
    assert document["features"]
    text = path.read_text(encoding="utf-8")
    assert CADASTRAL_REFERENCE_RE.search(text) is None
    for feature in document["features"]:
        # Los ficheros de `site/datos/` se regeneran con la tabla después de fusionar #316
        # (fuera de su alcance): valen con y sin `convergencia`, y nada más.
        assert FEATURE_KEYS - {"convergencia"} <= set(feature["properties"]) <= FEATURE_KEYS
        assert feature["properties"]["texto"].startswith("Indicio a verificar:")
        assert banned_words_in(json.dumps(feature, ensure_ascii=False)) == ()
    (entry,) = [m for m in _load(SITE_DATOS / "indice.json")["municipios"] if m["ine"] == "28043"]
    assert entry["capas"]["discrepancias"] == "discrepancias/28043.geojson"
    # Colmenar sigue publicado; el resto del ámbito lo publica #96 (su test lo comprueba).
    assert "28043.geojson" in {p.name for p in (SITE_DATOS / "discrepancias").glob("*.geojson")}


# --- Informe sintético para la ida y vuelta (CA14) -----------------------------------------


def _synthetic_report() -> MunicipalDiscrepancyReport:
    from datetime import date

    def prov(source: str, redistribution: Redistribution) -> Provenance:
        return Provenance(
            source=f"SINTÉTICO {source}",
            url=f"https://example.invalid/{source}",
            retrieved_at=date(2026, 11, 20),
            license="SINTÉTICA",
            redistribution=redistribution,
        )

    via = prov("vias", Redistribution.PUBLIC)
    ign = prov("ign", Redistribution.PUBLIC)
    cat = prov("catastro", Redistribution.INTERNAL_ONLY)
    via_ref = SourceRef("via_pecuaria_oficial", via, date(2014, 1, 1))
    ign_ref = SourceRef("ign_red_transporte", ign, date(2025, 3, 15))
    cat_ref = SourceRef("catastro_vt", cat, None)
    polygon = Geometry(rings=(((0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 0.0)),), srid=25830)
    multipolygon = Geometry(
        rings=(
            ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),
            ((5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 5.0)),
        ),
        srid=25830,
    )
    solape = MultiLineGeometry(
        lines=(((0.0, 0.0), (5.0, 0.0)), ((7.5, 0.0), (9.25, 0.0))), srid=25830
    )

    def record(
        record_id: str,
        kind: Any,
        zone: Any,
        subject: TrailRef,
        b: SourceRef,
        b_trails: tuple[tuple[str, Any], ...] = (),
    ) -> DiscrepancyRecord:
        discrepancy = Discrepancy(
            kind=kind,
            zone=zone,
            subject=subject,
            counterpart_source=b,
            counterpart_trails=tuple(TrailRef(i, b) for i, _ in b_trails),
            confidence=0.123,
            evidence=(Measurement("tolerancia_par_m", 11.18, "m"),),
            criterion=CRITERION,
        )
        return DiscrepancyRecord(
            record_id=record_id,
            discrepancy=discrepancy,
            trails=(
                TrailEvidence(
                    subject.trail_id,
                    subject.source.kind,
                    subject.source.provenance,
                    subject.source.data_date,
                    zone,
                ),
                *(
                    TrailEvidence(i, b.kind, b.provenance, b.data_date, shape)
                    for i, shape in b_trails
                ),
            ),
            zone_redacted=False,
        )

    records = (
        record("a1", "AUSENTE_EN_FUENTE", multipolygon, TrailRef("VP-2", via_ref), cat_ref),
        record("a2", "AUSENTE_EN_FUENTE", polygon, TrailRef("99043A00109002", cat_ref), ign_ref),
        record(
            "s1",
            "SOLAPE",
            solape,
            TrailRef("VP-9", via_ref),
            ign_ref,
            (("R-9", MultiLineGeometry(lines=(((0.0, 1.0), (5.0, 1.0)),), srid=25830)),),
        ),
    )
    return MunicipalDiscrepancyReport(
        municipality_code="28043",
        criterion_id="discrepancia_trazados@1",
        layers=(),
        records=records,
        redacted=False,
        municipality_name="Colmenar de Oreja",
    )


def test_oraculo_de_publicacion_coincide_con_el_dominio(pipeline: dict[str, Any]) -> None:
    """Apoyo de CA1/CA4/CA10: el oráculo fijado a mano (LEEME.md) es lo que da el dominio."""
    report = GeoJsonDiscrepancyReportReader(pipeline["derived"] / "publicable").get_report("28043")
    build = build_discrepancy_layer(report, indicio_tables(report))
    assert build.publication is not None
    kinds = sorted(item.card.kind for item in build.publication.content.items)
    assert kinds == ["AUSENTE_EN_FUENTE"] * 3 + ["DESPLAZAMIENTO"]
    assert (build.omitted.subject, build.omitted.counterpart) == (1, 0)
