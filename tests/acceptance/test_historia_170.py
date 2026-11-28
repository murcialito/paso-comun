"""Historia #170: publicar los tramos a verificar aprobados (`publicar-candidatos`, ADR 0048).

Las entradas son SINTÉTICAS (`tests/fixtures/candidatos_publicar/`, ver su LEEME.md): los
5 candidatos de #168 con 3 aprobados vigentes (`aprobado`, `propuesto` de dos piezas y
`caducado`, aprobado aquí con su huella vigente), 1 rechazado (sujeto de Catastro con
`trail_id` con forma de referencia sintética, prefijo 99999) y 1 sin revisión. Sin red ni
PostGIS, salvo CA15 (`fuera_de_ci`: lo ejecuta el fundador en local).

Oráculos del Bloque 0, calculados pasando la fixture por `public_candidate_id` (Diseño, §8):
`aprobado` → `980ea673dbc2917f`, `propuesto` → `fe4fe6c2754e854f`, `caducado` →
`9cdd209df9143b26`; los no publicados serían `rechazado` → `56b281f54f05bbd0` y
`sin_revision` → `f122ad6ba1eac3be`. Las features van en orden de `candidate_id`
(`caducado`, `propuesto`, `aprobado`).

Adaptación anotada: un tramo de una sola pieza sale como `LineString` (lo que da
`shape_to_geojson`, igual que los indicios de #94); el de dos piezas, como
`MultiLineString` (CA17).

Actualizado por #172 (CA15, ADR 0049): la feature gana `imagenes_comparacion` y la
colección `comparacion`; `_publicar` pasa `--comparacion` con el manifiesto de la fixture
(generado con el productor real), `_write_inputs` escribe el manifiesto de sus candidatos
con el mismo productor y CA15 se salta también si falta `data/derived/comparacion/28043.json`.

Actualizado por #315 (CA10, ADR 0057): la feature gana `convergencia`, con su propia lista
blanca (`TABLE_KEYS`/`ROW_KEYS`); la búsqueda de fugas de CA7 se acota al resto de la feature,
que es lo que este criterio protegía (lección de #172). `_publicar` pasa `--capas` con las
capas sintéticas de la fixture y un `--curated-historico`/`--cache-historico` inexistentes
(sin cobertura histórica), para no leer `data/` ni `curated/` del repositorio; CA9 construye
la publicación con las tablas de convergencia de los aprobados.
"""

import dataclasses
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from parcel_intel.adapters.comparison_files import JsonComparisonManifestReader
from parcel_intel.adapters.cut_candidate_files import GeoJsonCutCandidateWriter
from parcel_intel.adapters.published_layers import (
    GeoJsonPublishedCandidateSink,
    PublishedLayerError,
)
from parcel_intel.adapters.published_layers.convergence import ROW_KEYS, TABLE_KEYS
from parcel_intel.adapters.published_layers.projection import to_lonlat
from parcel_intel.adapters.validation_cases.congelacion import congelar
from parcel_intel.cli import main
from parcel_intel.domain.candidate_review import (
    CandidateReview,
    candidate_fingerprint,
    reconcile,
)
from parcel_intel.domain.cut_candidate import CutCandidate
from parcel_intel.domain.provenance import Redistribution
from parcel_intel.domain.public_candidate_card import CANDIDATE_LAYER_NAME
from parcel_intel.domain.public_card import CATASTRO_ATTRIBUTION, WARNING, banned_words_in
from parcel_intel.domain.published_candidate import (
    REVIEWED_BY_HAND,
    CandidateLayerPublication,
    MeasurementReport,
    build_candidate_layer,
    public_candidate_id,
    summarize_measurement,
)
from parcel_intel.domain.published_discrepancy import CADASTRAL_REFERENCE_RE, PARCEL_LOCATOR_RE
from tests.convergence_support import candidate_tables
from tests.fixtures.candidatos_publicar import generar
from tests.fixtures.candidatos_revision import generar as revision
from tests.indicio_publication_support import indicio_publication_options
from tests.medir_candidatos_support import caso

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "candidatos_publicar"
CANDIDATES_DIR = FIXTURE / "candidatos"
CURATED_DIR = FIXTURE / "curated"
COMPARISON_DIR = FIXTURE / "comparacion"
MEASUREMENT = FIXTURE / "medicion.json"
MEASUREMENT_COMPUTABLE = FIXTURE / "medicion_calculable.json"
CAPAS_DIR = FIXTURE / "capas"  # capas derivadas sintéticas de la tabla de convergencia (#315)
DISCREPANCY_FIXTURES = ROOT / "tests" / "fixtures" / "discrepancias_publicar" / "entrada"
NOTICE = ROOT / "NOTICE"
INE = "28043"

PUBLIC_IDS = {
    "aprobado": "980ea673dbc2917f",
    "propuesto": "fe4fe6c2754e854f",
    "caducado": "9cdd209df9143b26",
}
NOT_PUBLISHED_IDS = {"rechazado": "56b281f54f05bbd0", "sin_revision": "f122ad6ba1eac3be"}
FEATURE_KEYS = {
    "etiqueta",
    "texto",
    "aviso",
    "confianza",
    "evidencia",
    "aviso_omision",
    "imagenes_comparacion",
    "convergencia",  # #315
}
COLLECTION_KEYS = {
    "capa",
    "nombre_capa",
    "municipio",
    "nombre_municipio",
    "metodo",
    "aviso",
    "revisado_a_mano",
    "medicion",
    "comparacion",
    "atribucion",
    "procedencia",
}
APROBADO_TEXT = (
    "Indicio a verificar: este tramo de 200 m figura como vías pecuarias (IDEM), año no "
    "declarado, y no aparece en el camino actual del IGN. Al cruzarlo con datos de la "
    "Dirección General del Catastro (consultados el 26/11/2026), el 80 % del tramo cae "
    "dentro de parcelas que no figuran como vía de comunicación. Conviene comprobarlo "
    "sobre el terreno."
)


# --- Utilidades -----------------------------------------------------------------------


def _run(args: list[str]) -> int:
    parsed = main._build_parser().parse_args(args)
    code: int = parsed.func(parsed)
    return code


def _publicar(
    salida: Path,
    *codes: str,
    medicion: Path = MEASUREMENT,
    entrada: Path = CANDIDATES_DIR,
    curated: Path = CURATED_DIR,
    comparacion: Path = COMPARISON_DIR,
    capas: Path = CAPAS_DIR,
) -> int:
    municipalities = [arg for code in codes for arg in ("--municipio", code)]
    return _run(
        [
            "publicar-candidatos",
            *municipalities,
            "--medicion",
            str(medicion),
            "--entrada",
            str(entrada),
            "--curated",
            str(curated),
            "--comparacion",
            str(comparacion),
            "--salida",
            str(salida),
            # #315: capas de la fixture y sin cobertura histórica (directorios que no existen).
            "--capas",
            str(capas),
            "--curated-historico",
            str(salida.parent / "sin_curated_historico"),
            "--cache-historico",
            str(salida.parent / "sin_cache_historico"),
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


def _written_files(site: Path) -> list[Path]:
    return [
        site / "candidatos" / f"{INE}.geojson",
        site / "candidatos" / f"{INE}.geojson.procedencia.json",
        site / "indice.json",
        site / "indice.json.procedencia.json",
    ]


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


def _review_entry(candidate: CutCandidate, status: str = "aprobado") -> dict[str, object]:
    entry: dict[str, object] = {
        "candidate_id": candidate.candidate_id,
        "review": status,
        "reviewed_by": revision.REVIEWER,
        "reviewed_at": revision.REVIEWED_AT,
    }
    if status == "aprobado":
        entry["candidate_sha256"] = candidate_fingerprint(candidate)
    else:
        entry["review_note"] = "Revisado en la ortofoto."
    return entry


def _write_inputs(
    base: Path, candidates: list[CutCandidate], reviews: list[dict[str, object]]
) -> tuple[Path, Path]:
    """Fichero de candidatos y revisión sintéticos en `base`, y el manifiesto de imágenes
    de esos candidatos en `base / "comparacion"` (productor real, #172); devuelve
    (entrada, curated)."""
    entrada = base / "candidatos"
    curated = base / "curated"
    report = dataclasses.replace(
        revision.report(), candidates=tuple(sorted(candidates, key=lambda c: c.candidate_id))
    )
    GeoJsonCutCandidateWriter(entrada).write(report)
    comparacion = base / "comparacion"
    comparacion.mkdir()
    (comparacion / f"{INE}.json").write_text(
        generar.manifest_text_for(entrada / f"{INE}.geojson"), encoding="utf-8"
    )
    (curated / INE).mkdir(parents=True)
    (curated / INE / "revision.json").write_text(
        json.dumps({"municipio": INE, "revisiones": reviews}, ensure_ascii=False),
        encoding="utf-8",
    )
    return entrada, curated


def _write_measurement(path: Path, **changes: object) -> Path:
    document = _load(MEASUREMENT)
    document.update(changes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def published(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    site = tmp_path / "site" / "datos"
    code = _publicar(site, INE)
    out = capsys.readouterr()
    return {
        "code": code,
        "out": out.out,
        "err": out.err,
        "site": site,
        "file": site / "candidatos" / f"{INE}.geojson",
        "sidecar": site / "candidatos" / f"{INE}.geojson.procedencia.json",
    }


# --- Criterios ------------------------------------------------------------------------


def test_ca1_publica_geojson_en_4326_con_procedencia(published: dict[str, Any]) -> None:
    """CA1. Dado un municipio con candidatos aprobados vigentes, un informe de medición de la
    misma versión del método y todo válido, cuando se ejecuta `publicar-candidatos --municipio
    <ine>`, entonces se escribe `site/datos/candidatos/<ine>.geojson` (EPSG:4326) y su
    `.procedencia.json`, y el comando sale con 0."""
    assert published["code"] == 0, published["err"]
    document = _load(published["file"])
    assert document["type"] == "FeatureCollection"
    sidecar = _load(published["sidecar"])
    assert sidecar["transformacion"]["crs_destino"] == "EPSG:4326"
    assert sidecar["transformacion"]["crs_origen"] == "EPSG:25830"
    for key in ("fuente", "url", "fecha_descarga", "licencia", "redistribucion"):
        assert key in sidecar
    for feature in document["features"]:
        coordinates = feature["geometry"]["coordinates"]
        flat = coordinates if feature["geometry"]["type"] == "LineString" else sum(coordinates, [])
        for lon, lat in flat:
            assert -4.0 < lon < -3.0 and 39.5 < lat < 40.5
    assert f"publicado {published['site'] / 'candidatos' / '28043.geojson'}" in published["out"]
    assert "3 tramos aprobados (sin publicar: 1 propuestos, 1 rechazados)" in published["out"]


def test_ca2_sin_informe_de_medicion_sale_con_1_y_nombra_el_fichero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA2. Dado que no existe el informe de medición o no se puede leer, cuando se ejecuta el
    comando, entonces sale con 1, nombra el fichero que falta y no escribe nada."""
    site = tmp_path / "site"
    missing = tmp_path / "no-existe" / "medicion.json"
    assert _publicar(site, INE, medicion=missing) == 1
    err = capsys.readouterr().err
    assert str(missing) in err
    assert "no existe" in err
    assert _snapshot(site) == {}

    broken = tmp_path / "roto.json"
    broken.write_text("{no es json", encoding="utf-8")
    assert _publicar(site, INE, medicion=broken) == 1
    assert str(broken) in capsys.readouterr().err
    assert _snapshot(site) == {}


def test_ca3_informe_de_otro_metodo_o_no_congelado_sale_con_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA3. Dado un informe de medición cuyo `metodo_candidatos` no contiene el método y la
    versión de los candidatos del municipio, o cuyo `congelado` es falso, cuando se ejecuta el
    comando, entonces sale con 1, indica el motivo y no escribe nada."""
    site = tmp_path / "site"
    other = _write_measurement(tmp_path / "otro.json", metodo_candidatos=["candidato_corte@2"])
    assert _publicar(site, INE, medicion=other) == 1
    assert "el informe mide candidato_corte@2 y los candidatos son candidato_corte@1" in (
        capsys.readouterr().err
    )
    empty = _write_measurement(tmp_path / "vacio.json", metodo_candidatos=[])
    assert _publicar(site, INE, medicion=empty) == 1
    assert "ningún método" in capsys.readouterr().err
    not_frozen = _write_measurement(tmp_path / "no-congelado.json", congelado=False)
    assert _publicar(site, INE, medicion=not_frozen) == 1
    assert "no es de un conjunto congelado" in capsys.readouterr().err
    assert _snapshot(site) == {}


def test_ca4_sin_ningun_aprobado_o_sin_fichero_sale_con_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA4. Dado un municipio sin ningún candidato aprobado (todos propuestos o rechazados, o
    sin fichero de candidatos), cuando se ejecuta el comando, entonces sale con 1 y no escribe
    nada."""
    found = revision.candidates()
    entrada, curated = _write_inputs(
        tmp_path / "entradas",
        list(found.values()),
        [_review_entry(found["rechazado"], "rechazado")],
    )
    site = tmp_path / "site"
    comparacion = entrada.parent / "comparacion"
    assert _publicar(site, INE, entrada=entrada, curated=curated, comparacion=comparacion) == 1
    assert "ningún candidato aprobado" in capsys.readouterr().err
    assert _publicar(site, INE, entrada=tmp_path / "sin-candidatos") == 1
    assert "sin fichero de candidatos" in capsys.readouterr().err
    assert _snapshot(site) == {}


def test_ca5_aprobacion_caducada_o_huerfana_sale_con_1_y_lista_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA5. Dado un municipio con alguna aprobación caducada o revisión huérfana
    (`check_candidate_approvals_current`), cuando se ejecuta el comando, entonces sale con 1,
    lista los ids afectados y no escribe nada."""
    site = tmp_path / "site"
    # La revisión de #168 tiene un aprobado caducado y una huérfana.
    curated = ROOT / "tests" / "fixtures" / "candidatos_revision" / "curated"
    assert _publicar(site, INE, curated=curated) == 1
    err = capsys.readouterr().err
    assert revision.candidates()["caducado"].candidate_id in err
    assert revision.orphan_id() in err
    assert "caducadas" in err and "huérfanas" in err
    assert _snapshot(site) == {}


def test_ca6_solo_los_aprobados_y_nada_de_los_demas(published: dict[str, Any]) -> None:
    """CA6. Dado un municipio con candidatos aprobados, propuestos y rechazados, cuando se
    publica, entonces el fichero contiene exactamente los aprobados: ninguna feature ni texto de
    un propuesto o rechazado, y ni su identificador público ni su huella aparecen en ningún
    fichero."""
    assert published["code"] == 0, published["err"]
    found = revision.candidates()
    features = _load(published["file"])["features"]
    assert [f["id"] for f in features] == [
        PUBLIC_IDS["caducado"],
        PUBLIC_IDS["propuesto"],
        PUBLIC_IDS["aprobado"],
    ]
    for role, public_id in NOT_PUBLISHED_IDS.items():
        assert public_candidate_id(found[role]) == public_id
    forbidden = [
        *NOT_PUBLISHED_IDS.values(),
        candidate_fingerprint(found["rechazado"]),
        candidate_fingerprint(found["sin_revision"]),
        revision.CATASTRO_TRAIL_ID,
    ]
    for candidate in found.values():
        forbidden += [candidate.candidate_id, candidate_fingerprint(candidate)]
    for path in _written_files(published["site"]):
        text = path.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in text, (path.name, value)
    # El rechazado tiene un tramo de 120 m: ningún texto publicado lo cita.
    texts = [f["properties"]["texto"] for f in features]
    assert not any("120 m" in text for text in texts)


def test_ca7_geometria_del_tramo_ficha_y_lista_blanca_cerrada(published: dict[str, Any]) -> None:
    """CA7. Dado un candidato aprobado, cuando se publica, entonces su feature lleva la geometría
    del tramo del trazado (no la de parcelas) reproyectada a EPSG:4326, la ficha de
    `build_candidate_card` y un identificador público propio que no es el `candidate_id` ni
    contiene referencias, y solo estas propiedades (lista blanca cerrada): identificador
    público, etiqueta, texto, aviso, confianza en palabras, fuentes con su atribución y
    procedencia y, si la hay, la nota de omisión. No salen la huella, el `candidate_id`, la
    confianza numérica, la fracción de apoyo, el número de parcelas, `reviewed_by` ni la nota de
    revisión."""
    assert published["code"] == 0, published["err"]
    found = revision.candidates()
    document = _load(published["file"])
    assert set(document["properties"]) == COLLECTION_KEYS
    by_id = {f["id"]: f for f in document["features"]}
    aprobado = by_id[PUBLIC_IDS["aprobado"]]
    assert set(aprobado) == {"type", "id", "geometry", "properties"}
    assert set(aprobado["properties"]) == FEATURE_KEYS
    assert aprobado["geometry"] == {
        "type": "LineString",
        "coordinates": to_lonlat(found["aprobado"].stretch.lines[0]),
    }
    props = aprobado["properties"]
    assert props["etiqueta"] == "Tramo a verificar: trazado sin equivalente en el camino actual"
    assert props["texto"] == APROBADO_TEXT
    assert props["aviso"].startswith(WARNING)
    assert props["confianza"] == "alta"
    assert props["aviso_omision"] is None
    assert [e["papel"] for e in props["evidencia"]] == ["sujeto", "contraparte"]
    for public_id in by_id:
        assert re.fullmatch(r"[0-9a-f]{16}(-\d+)?", public_id)
        assert public_id not in {c.candidate_id for c in found.values()}
        assert CADASTRAL_REFERENCE_RE.search(public_id) is None
    # #172: `resolucion_m` (0.5, 0.25) es dato del vuelo, no la confianza ni el apoyo; las
    # imágenes tienen su propia lista blanca cerrada (#172 CA1). Se buscan las fugas en el
    # resto del fichero, que es lo que este criterio cubre. Lo mismo con la tabla de
    # convergencia de #315: su propia lista blanca (ADR 0057 §8) y fuera de la búsqueda.
    for feature in document["features"]:
        images = feature["properties"].pop("imagenes_comparacion")
        assert {key for window in images for key in window} <= {
            "ancho_px",
            "alto_px",
            "trazo_px",
            "imagenes",
            "omitidas",
        }
        table = feature["properties"].pop("convergencia")
        assert set(table) == set(TABLE_KEYS)
        assert all(set(row) == set(ROW_KEYS) for row in table["filas"])
    text = json.dumps(document, ensure_ascii=False)
    for leaked in (
        revision.REVIEWER,
        "Comparado con la ortofoto",
        "0.8",
        "0.5",
        "support",
        "apoyo",
        "parcelas_",
        "candidate",
        "huella",
    ):
        assert leaked not in text, leaked


def test_ca8_resumen_literal_de_la_medicion_y_revisado_a_mano(
    published: dict[str, Any], tmp_path: Path
) -> None:
    """CA8. Dado el informe de medición válido, cuando se publica, entonces la colección lleva el
    resumen de la medición literal (casos señalados de los evaluables, candidatos que casan de
    los evaluables, «precisión como cota inferior», «solo N casos en el ámbito, no es una
    estadística», fecha y versión del método) y la marca «revisado a mano». Una medición con un
    cociente no calculable se publica diciendo «no calculable», nunca como 0 %."""
    assert published["code"] == 0, published["err"]
    props = _load(published["file"])["properties"]
    assert props["revisado_a_mano"] == REVIEWED_BY_HAND
    assert props["medicion"] == {
        "fecha": "2026-11-27",
        "conjunto": "validacion-v1",
        "metodo": "candidato_corte v1",
        "casos_en_ambito": 2,
        "casos_evaluables": 0,
        "casos_senalados": 0,
        "candidatos_evaluables": 0,
        "candidatos_que_casan": 0,
        "cobertura": "no calculable",
        "precision_cota_inferior": "no calculable",
        "texto": [
            "Casos de validación señalados: 0 de 0 evaluables (no calculable).",
            "Tramos que casan con un caso: 0 de 0 evaluables; precisión como cota inferior: "
            "no calculable.",
            "Solo 2 casos en el ámbito, no es una estadística.",
            "Medición del 27/11/2026 con el conjunto validacion-v1 y el método candidato_corte v1.",
        ],
    }
    # Ningún número del resumen se puede leer como 0 % (el «80 %» de la ficha es otra cosa).
    for value in _strings(props["medicion"]):
        assert not re.search(r"(?<!\d)0 %", value), value
        assert not re.search(r"\d[.,]\d", value), value

    site = tmp_path / "calculable"
    assert _publicar(site, INE, medicion=MEASUREMENT_COMPUTABLE) == 0
    medicion = _load(site / "candidatos" / f"{INE}.geojson")["properties"]["medicion"]
    assert medicion["cobertura"] == "50 %"
    assert medicion["precision_cota_inferior"] == "33 %"
    assert medicion["texto"][:2] == [
        "Casos de validación señalados: 1 de 2 evaluables (50 %).",
        "Tramos que casan con un caso: 1 de 3 evaluables; precisión como cota inferior: 33 %.",
    ]


def test_ca9_sin_referencias_ni_parcelas_ni_palabras_vetadas_y_barrera_del_sink(
    published: dict[str, Any], tmp_path: Path
) -> None:
    """CA9. Dado cualquier fichero escrito (incluidos procedencia e índice), cuando un test los
    recorre, entonces ninguna cadena casa con `CADASTRAL_REFERENCE_RE` o `PARCEL_LOCATOR_RE`, no
    hay geometría ni atributos de parcela, ni `BANNED_WORDS` (`banned_words_in`), y la
    atribución de la Dirección General del Catastro figura en la procedencia de un candidato con
    esa señal. Si el sink recibe un texto con una referencia o un localizador, lanza el error y
    no escribe nada (última barrera)."""
    assert published["code"] == 0, published["err"]
    for path in _written_files(published["site"]):
        document = json.loads(path.read_text(encoding="utf-8"))
        for value in _strings(document):
            assert CADASTRAL_REFERENCE_RE.search(value) is None, (path.name, value)
            assert PARCEL_LOCATOR_RE.search(value) is None, (path.name, value)
            assert banned_words_in(value) == (), (path.name, value)
            assert "parcela" not in value.casefold() or "parcelas que no figuran" in value
    for feature in _load(published["file"])["features"]:
        assert feature["geometry"]["type"] in {"LineString", "MultiLineString"}
    sidecar = _load(published["sidecar"])
    assert CATASTRO_ATTRIBUTION in [source["atribucion"] for source in sidecar["fuentes"]]
    assert CATASTRO_ATTRIBUTION in sidecar["atribucion"]

    # Última barrera: un texto con una referencia o un localizador no se escribe.
    state = reconcile(revision.report(), _fixture_reviews())
    summary = summarize_measurement(_not_computable_report(), revision.report().method)
    manifest = JsonComparisonManifestReader(COMPARISON_DIR).get_manifest(INE)
    assert manifest is not None
    tables, failures = candidate_tables(state.approved(), manifest)
    publication = build_candidate_layer(
        state, summary, "Colmenar de Oreja", manifest, tables, failures
    ).publication
    site = tmp_path / "barrera"
    GeoJsonPublishedCandidateSink(site, main.REPOSITORY_URL).publish([publication])
    before = _snapshot(site)
    for leak in (f"tramo {revision.CATASTRO_TRAIL_ID}", "https://example.invalid/x?parcela=1"):
        tampered = _with_text(publication, leak)
        with pytest.raises(PublishedLayerError):
            GeoJsonPublishedCandidateSink(site, main.REPOSITORY_URL).publish([tampered])
        assert _snapshot(site) == before


def _fixture_reviews() -> tuple[CandidateReview, ...]:
    from parcel_intel.adapters.curated_candidates import CuratedCandidateReviewFiles

    return CuratedCandidateReviewFiles(CURATED_DIR).reviews(INE)


def _not_computable_report() -> MeasurementReport:
    from parcel_intel.adapters.published_layers import JsonMeasurementReportReader

    return JsonMeasurementReportReader(MEASUREMENT).get_measurement()


def _with_text(publication: CandidateLayerPublication, text: str) -> CandidateLayerPublication:
    first, *rest = publication.content.items
    card = dataclasses.replace(first.card, text=text)
    items = (dataclasses.replace(first, card=card), *rest)
    content = dataclasses.replace(publication.content, items=items)
    return dataclasses.replace(publication, content=content)


def test_ca10_trazado_no_redistribuible_o_sin_fecha_de_catastro_falla_el_municipio(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA10. Dado un candidato aprobado cuya fuente del sujeto no es redistribuible (o cuya capa
    no pasa `PublishableLayer`), cuando se publica, entonces el comando sale con 1 y no escribe
    nada. Un candidato con Catastro en la señal pero sin fecha de consulta
    (`MissingCatastroDateError`) hace fallar igual el municipio."""
    found = revision.candidates()
    aprobado = found["aprobado"]
    internal = dataclasses.replace(
        aprobado.subject.source.provenance, redistribution=Redistribution.INTERNAL_ONLY
    )
    internal_subject = dataclasses.replace(
        aprobado.subject, source=dataclasses.replace(aprobado.subject.source, provenance=internal)
    )
    without_catastro = dataclasses.replace(
        aprobado.signals[0], provenance=(aprobado.subject.source.provenance,)
    )
    variants = {
        "fuente no redistribuible": found["rechazado"],  # sujeto catastro_vt
        "no redistribuible": dataclasses.replace(aprobado, subject=internal_subject),
        "ninguna procedencia de Catastro": dataclasses.replace(
            aprobado, signals=(without_catastro,)
        ),
    }
    for index, (reason, candidate) in enumerate(variants.items()):
        entrada, curated = _write_inputs(
            tmp_path / f"variante-{index}", [candidate], [_review_entry(candidate)]
        )
        site = tmp_path / f"site-{index}"
        comparacion = entrada.parent / "comparacion"
        assert (
            _publicar(site, INE, entrada=entrada, curated=curated, comparacion=comparacion) == 1
        ), reason
        err = capsys.readouterr().err
        assert reason in err, err
        assert revision.CATASTRO_TRAIL_ID not in err
        assert _snapshot(site) == {}


def test_ca11_indice_conserva_las_cuatro_capas_en_cualquier_orden(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA11. Dado un directorio `site/datos/` con las capas `vias_pecuarias`, `camino_actual` y
    `discrepancias` ya publicadas, cuando se publican candidatos, entonces el índice incluye la
    capa `candidatos` y conserva las demás entradas; y publicar cualquiera de las otras capas
    después no borra `candidatos` (`INDEXED_LAYER_IDS`)."""
    derived = tmp_path / "discrepancias"
    site = tmp_path / "site" / "datos"
    assert (
        _run(
            [
                "discrepancias",
                "--entrada",
                str(DISCREPANCY_FIXTURES),
                "--salida",
                str(derived),
                "--municipio",
                INE,
            ]
        )
        == 0
    )
    capas = ["publicar-capas", "--entrada", str(DISCREPANCY_FIXTURES), "--municipio", INE]
    indicios = ["publicar-discrepancias", "--entrada", str(derived / "publicable")]
    indicios += ["--municipio", INE, "--salida", str(site)]
    # Desde #316: interno, manifiesto de indicios (productor real), capas y histórico vacío.
    indicios += indicio_publication_options(derived / "publicable", DISCREPANCY_FIXTURES)
    assert _run([*capas, "--salida", str(site)]) == 0
    assert _run(indicios) == 0
    expected = {"camino_actual", "candidatos", "discrepancias", "vias_pecuarias"}

    assert _publicar(site, INE) == 0, capsys.readouterr().err
    (entry,) = _load(site / "indice.json")["municipios"]
    assert set(entry["capas"]) == expected
    for later in (indicios, [*capas, "--salida", str(site)]):
        assert _run(later) == 0, capsys.readouterr().err
        (entry,) = _load(site / "indice.json")["municipios"]
        assert set(entry["capas"]) == expected
        assert entry["capas"]["candidatos"] == f"candidatos/{INE}.geojson"


def test_ca12_todo_o_nada_y_solo_municipios_verificados(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA12. Dado `--municipio` con un municipio que falla y otro válido, cuando se ejecuta,
    entonces no se escribe ninguno de los dos (todo o nada) y el comando sale con 1; sin
    `--municipio` sale con 1. No hay `--ambito`: solo se acepta un municipio con código de
    Catastro verificado (hoy, 28043), y otro código sale con 1 sin escribir."""
    site = tmp_path / "site"
    assert _publicar(site, INE, "28013") == 1
    err = capsys.readouterr().err
    assert "ERROR 28013: solo se publican candidatos de municipios con código de Catastro" in err
    assert f"No se ha escrito nada en {site}." in err
    assert _snapshot(site) == {}

    assert _publicar(site) == 1
    assert "--municipio" in capsys.readouterr().err
    assert _publicar(site, "9999x") == 1
    err = capsys.readouterr().err
    assert "9999x" not in err and "(no válido)" in err
    assert _snapshot(site) == {}
    with pytest.raises(SystemExit) as exit_info:
        _run(["publicar-candidatos", "--ambito"])
    assert exit_info.value.code == 2
    capsys.readouterr()


def test_ca13_dos_publicaciones_dan_los_mismos_bytes(tmp_path: Path) -> None:
    """CA13. Dado el mismo conjunto de entradas, cuando se publica dos veces, entonces los
    ficheros son idénticos byte a byte (escritura atómica, sin fecha del reloj en el contenido
    salvo la ya declarada de la medición y de las consultas)."""
    first = tmp_path / "primera"
    second = tmp_path / "segunda"
    assert _publicar(first, INE) == 0
    assert _publicar(second, INE) == 0
    snapshot = _snapshot(first)
    assert snapshot == _snapshot(second)
    assert _publicar(first, INE) == 0
    assert _snapshot(first) == snapshot
    assert not any(name.endswith(".tmp") for name in snapshot)


def test_ca14_notice_con_la_capa_y_sin_duplicar_catastro() -> None:
    """CA14. Dado el fichero `NOTICE`, cuando falta la atribución de Catastro para esta capa,
    entonces la historia la añade; con ella presente, no se duplica."""
    notice = NOTICE.read_text(encoding="utf-8")
    assert notice.count(CATASTRO_ATTRIBUTION) == 1
    assert "### Paso Común — tramos a verificar (ADR 0048, historia #170)" in notice
    section = notice.split("### Paso Común — tramos a verificar (ADR 0048, historia #170)")[1]
    section = section.split("\n## ")[0]
    assert "CC BY 4.0" in section
    assert f"`{WARNING}`" in section
    assert (
        "Cada tramo con la señal de Catastro cita en su ficha a la Dirección General del "
        "Catastro con la fecha de consulta y la atribución de la sección anterior."
    ) in section.replace("\n  ", " ")
    catastro = notice.split("### Dirección General del Catastro — indicios derivados")[1]
    catastro = catastro.split("\n### ")[0].replace("\n  ", " ")
    assert "Se publican indicios y tramos a verificar sobre un trazado" in catastro
    assert CANDIDATE_LAYER_NAME  # el nombre de la capa viaja en el fichero (CA7)


@pytest.mark.integration
def test_ca15_flujo_real_en_local(tmp_path: Path) -> None:
    """CA15. [integración] Dado el flujo real en local con Catastro (`candidatos-corte`,
    `medir-candidatos`, revisión aprobada en `curated/candidatos/28043/revision.json`), cuando
    se ejecuta `publicar-candidatos --municipio 28043`, entonces se obtienen los ficheros
    anteriores. La historia no commitea datos reales, así que en CI se salta: faltan `data/derived`
    y `revision.json`. Decisión del fundador en #170: no bloquea el merge y se ejecuta en la
    primera publicación real, cuando haya candidatos aprobados. El resultado se anota en
    `REGISTRY.md`. Desde #172 hace falta además el manifiesto de `imagenes-comparacion`."""
    medicion = ROOT / "data" / "derived" / "validacion" / "medicion.json"
    entrada = ROOT / "data" / "derived" / "candidatos" / "interno"
    curated = ROOT / "curated" / "candidatos"
    comparacion = ROOT / "data" / "derived" / "comparacion"
    if not (medicion.is_file() and (entrada / f"{INE}.geojson").is_file()):
        pytest.skip("faltan medicion.json o el fichero interno de 28043 en data/derived")
    if not (comparacion / f"{INE}.json").is_file():
        pytest.skip("falta data/derived/comparacion/28043.json (imagenes-comparacion)")
    if not (curated / INE / "revision.json").is_file():
        pytest.skip("falta curated/candidatos/28043/revision.json")
    site = tmp_path / "site"
    code = _publicar(
        site,
        INE,
        medicion=medicion,
        entrada=entrada,
        curated=curated,
        comparacion=comparacion,
        capas=ROOT / "data" / "derived",  # #315: las capas derivadas reales
    )
    assert code == 0
    for path in _written_files(site):
        assert path.is_file(), path


def test_ca16_medir_sin_casos_en_colmenar_y_publicar_no_calculable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA16. Dado un conjunto de validación **sin ningún caso en 28043** (como el real: los dos
    casos del ámbito son de Aranjuez) y un fichero de candidatos de 28043, cuando se ejecuta
    `medir-candidatos` y después `publicar-candidatos --municipio 28043` sobre el informe que
    acaba de escribir, entonces el informe recoge `metodo_candidatos == ["candidato_corte@1"]`,
    cuenta los candidatos de 28043 en `candidatos.no_evaluables` sin cambiar cobertura ni
    precisión, y la publicación sale con 0 con la medición «no calculable»."""
    validation = tmp_path / "validacion"
    validation.mkdir()
    casos = validation / "casos.json"
    line = [[440000.0, 4420000.0], [440200.0, 4420000.0]]
    documento = {
        "casos": [
            caso("aranjuez-con-linea", "Aranjuez", "hito", tramo_linea=line),
            caso("aranjuez-sin-linea", "Aranjuez", "hito"),
            caso("fuera-uno", "El Escorial", "comunidad"),
            caso("fuera-dos", "Segovia", "comunidad"),
            caso("fuera-tres", "Toledo", "comunidad"),
            caso("fuera-cuatro", "Guadalajara", "comunidad"),
        ]
    }
    casos.write_text(json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8")
    congelar(casos, generar.RUN_DATE, validation)

    def medir(entrada: Path, salida: Path) -> dict[str, Any]:
        args = ["medir-candidatos", "--casos", str(casos), "--entrada", str(entrada)]
        assert _run([*args, "--salida", str(salida)]) == 0
        return _load(salida)

    without = medir(tmp_path / "sin-candidatos", tmp_path / "sin.json")
    report_path = tmp_path / "derived" / "medicion.json"
    report = medir(CANDIDATES_DIR, report_path)
    assert without["metodo_candidatos"] == []
    assert report["metodo_candidatos"] == ["candidato_corte@1"]
    assert report["candidatos"]["no_evaluables"] == 5
    for key in ("cobertura", "precision", "evaluables", "no_evaluables"):
        assert report[key] == without[key], key
    capsys.readouterr()

    site = tmp_path / "site"
    assert _publicar(site, INE, medicion=report_path) == 0, capsys.readouterr().err
    medicion = _load(site / "candidatos" / f"{INE}.geojson")["properties"]["medicion"]
    assert medicion["cobertura"] == "no calculable"
    assert medicion["precision_cota_inferior"] == "no calculable"
    assert medicion["casos_en_ambito"] == 2


def test_ca17_colision_de_id_publico_y_tramo_de_dos_piezas(tmp_path: Path) -> None:
    """CA17. Dados dos candidatos aprobados con el mismo método, fuente y tramo a 0,1 m pero
    distinto trazado, cuando se publica, entonces el segundo identificador público (por orden de
    `candidate_id`) lleva el sufijo `-2`; y un candidato aprobado con tramo de **dos piezas**
    sale como `MultiLineString` de dos líneas, con todas sus coordenadas en el `bbox` del
    índice."""
    found = revision.candidates()
    aprobado = found["aprobado"]
    twin = revision.make_candidate(
        "via_pecuaria_oficial",
        "VP-SINT-GEMELO",
        tuple(tuple((x + 0.02, y - 0.03) for x, y in line) for line in aprobado.stretch.lines),
        0.8,
        0.5,
    )
    assert twin.candidate_id != aprobado.candidate_id
    assert public_candidate_id(twin) == public_candidate_id(aprobado)
    two_pieces = found["propuesto"]
    candidates = [aprobado, twin, two_pieces]
    entrada, curated = _write_inputs(
        tmp_path / "entradas", candidates, [_review_entry(c) for c in candidates]
    )
    site = tmp_path / "site"
    comparacion = entrada.parent / "comparacion"
    assert _publicar(site, INE, entrada=entrada, curated=curated, comparacion=comparacion) == 0
    features = _load(site / "candidatos" / f"{INE}.geojson")["features"]
    first, second = sorted([aprobado, twin], key=lambda c: c.candidate_id)
    ids = [f["id"] for f in features]
    base = PUBLIC_IDS["aprobado"]
    order = sorted(candidates, key=lambda c: c.candidate_id)
    expected = []
    for candidate in order:
        if candidate is first:
            expected.append(base)
        elif candidate is second:
            expected.append(f"{base}-2")
        else:
            expected.append(PUBLIC_IDS["propuesto"])
    assert ids == expected

    multi = next(f for f in features if f["id"] == PUBLIC_IDS["propuesto"])
    assert multi["geometry"]["type"] == "MultiLineString"
    assert len(multi["geometry"]["coordinates"]) == 2
    assert multi["geometry"]["coordinates"] == [
        to_lonlat(line) for line in two_pieces.stretch.lines
    ]
    (entry,) = _load(site / "indice.json")["municipios"]
    west, south, east, north = entry["bbox"]
    for feature in features:
        geometry = feature["geometry"]
        lines = (
            [geometry["coordinates"]]
            if geometry["type"] == "LineString"
            else geometry["coordinates"]
        )
        for lon, lat in (point for line in lines for point in line):
            assert west <= lon <= east and south <= lat <= north
