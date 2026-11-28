"""Historia #96: publicar los indicios de discrepancia de los municipios del ámbito y
comprobar peso y lenguaje del sitio real.

Los ficheros de `site/datos/discrepancias/` son los generados con `publicar-discrepancias
--ambito` sobre `data/derived/` regenerado con red el 2026-11-26. Los criterios de lenguaje y
peso se prueban con ficheros SINTÉTICOS (que deben detectarse) y con el sitio real. Los
docstrings copian el criterio literal de la issue.
"""

import importlib.util
import json
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from parcel_intel.cli import main as cli
from parcel_intel.cli.main import AMBITO_HITO_INE
from parcel_intel.domain.public_card import BANNED_WORDS, WARNING, banned_words_in
from parcel_intel.domain.published_discrepancy import CADASTRAL_REFERENCE_RE
from tests.acceptance.test_historia_94 import EVIDENCE_KEYS, FEATURE_KEYS
from tests.indicio_publication_support import indicio_publication_options

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
DATOS = SITE / "datos"
DISCREPANCIAS = DATOS / "discrepancias"
DERIVED_PUBLICABLE = ROOT / "data" / "derived" / "discrepancias" / "publicable"

# Municipios del ámbito con al menos un indicio publicable (salida real de la regeneración);
# los otros dos (Belmonte de Tajo, Valdelaguna) no tienen indicios entre las capas disponibles.
CON_INDICIOS = ("28013", "28043", "28052", "28170", "28180")
SIN_INDICIOS = ("28019", "28157")

# Lista blanca de #94 §4: propiedades de la colección, de cada feature y de la evidencia.
DOCUMENT_KEYS = {
    "capa",
    "municipio",
    "nombre_municipio",
    "criterio",
    "aviso",
    "atribucion",
    "procedencia",
}
FEATURE_TOP_KEYS = {"type", "id", "geometry", "properties"}
OWNERSHIP_KEY_RE = re.compile(
    r"(?i)propietari|titular|nif|owner|dueñ|sociedad|persona|razon_social|refcat|catastral"
)


def _load_script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


peso = _load_script("comprobar_peso_sitio")
construir_sitio = _load_script("construir_sitio")


def _load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _run(argv: list[str]) -> int:
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


def auditar_discrepancias(directory: Path) -> list[str]:
    """Recorre `*.geojson` y `*.procedencia.json` y devuelve los hallazgos de lenguaje,
    referencias catastrales, claves de titularidad y propiedades fuera de la lista blanca,
    cada uno con el nombre del fichero."""
    findings: list[str] = []
    for path in sorted([*directory.glob("*.geojson"), *directory.glob("*.procedencia.json")]):
        document = _load(path)
        for kind, value in _walk(document):
            if kind == "clave" and isinstance(value, str) and OWNERSHIP_KEY_RE.search(value):
                if value != "nombre_municipio":
                    findings.append(f"{path.name}: clave de titularidad {value!r}")
            if isinstance(value, str):
                for word in banned_words_in(value):
                    findings.append(f"{path.name}: palabra vetada {word!r}")
                if CADASTRAL_REFERENCE_RE.search(value):
                    findings.append(f"{path.name}: referencia catastral en {value!r}")
        if path.name.endswith(".geojson"):
            _audit_whitelist(path.name, document, findings)
    return findings


def _audit_whitelist(name: str, document: dict[str, Any], findings: list[str]) -> None:
    for key in sorted(set(document["properties"]) - DOCUMENT_KEYS):
        findings.append(f"{name}: propiedad de colección fuera de la lista blanca {key!r}")
    for feature in document["features"]:
        for key in sorted(set(feature) - FEATURE_TOP_KEYS):
            findings.append(f"{name}: clave de feature fuera de la lista blanca {key!r}")
        props = feature["properties"]
        for key in sorted(set(props) - FEATURE_KEYS):
            findings.append(f"{name}: propiedad fuera de la lista blanca {key!r}")
        for item in props.get("evidencia", []):
            for key in sorted(set(item) - EVIDENCE_KEYS):
                findings.append(f"{name}: clave de evidencia fuera de la lista blanca {key!r}")


def _synthetic_collection(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    props: dict[str, Any] = {
        "tipo": "SOLAPE",
        "etiqueta": "x",
        "texto": "Indicio a verificar: algo.",
        "aviso": WARNING,
        "confianza": "baja",
        "evidencia": [],
        "criterio": "c",
        "descripcion_criterio": "d",
        "aviso_omision": None,
    }
    props.update(properties or {})
    return {
        "type": "FeatureCollection",
        "properties": {"capa": "discrepancias", "municipio": "28999"},
        "features": [
            {"type": "Feature", "id": "a", "geometry": None, "properties": props},
        ],
    }


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


# --- Criterios ------------------------------------------------------------------------


@pytest.mark.integration
def test_ca1_ambito_publica_un_fichero_por_municipio_con_indicios(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA1. [integración] Dados los 7 municipios del ámbito, cuando se ejecuta
    `publicar-discrepancias --ambito` sobre `data/derived/discrepancias/publicable/`
    regenerado, entonces `site/datos/discrepancias/` contiene un `<ine>.geojson` y su
    `.procedencia.json` por cada municipio con al menos un indicio publicable, ningún fichero
    vacío, y la salida lista por municipio los indicios publicados por tipo y los omitidos
    (por licencia: lado A o contraparte no redistribuible; por Catastro)."""
    assert set(AMBITO_HITO_INE) == set(CON_INDICIOS) | set(SIN_INDICIOS)
    # Lo desplegado: un fichero y su procedencia por municipio con indicios, nada más.
    assert sorted(p.name for p in DISCREPANCIAS.glob("*.geojson")) == [
        f"{ine}.geojson" for ine in sorted(CON_INDICIOS)
    ]
    for ine in CON_INDICIOS:
        assert (DISCREPANCIAS / f"{ine}.geojson.procedencia.json").is_file()
        assert _load(DISCREPANCIAS / f"{ine}.geojson")["features"], ine

    if not DERIVED_PUBLICABLE.is_dir():
        pytest.skip("data/derived/ no existe en este entorno; se regenera con `discrepancias`")
    # Reproducible desde `data/derived/`: los bytes publicados son los del repo.
    destino = tmp_path / "datos"
    # Desde #316: el interno real, el manifiesto de indicios (productor real con red falsa,
    # en `tmp_path`), las capas reales y un `curated/historico/` vacío (CA10 de #316).
    opciones = indicio_publication_options(
        DERIVED_PUBLICABLE, DERIVED_PUBLICABLE.parents[1], work=tmp_path
    )
    assert _run(
        ["publicar-discrepancias", "--ambito", "--entrada", str(DERIVED_PUBLICABLE)]
        + ["--salida", str(destino), *opciones]
    ) == 0
    out = capsys.readouterr().out
    for ine in CON_INDICIOS:
        assert f"publicado {destino / 'discrepancias' / f'{ine}.geojson'} " in out
        assert (destino / "discrepancias" / f"{ine}.geojson").read_bytes() == (
            DISCREPANCIAS / f"{ine}.geojson"
        ).read_bytes()
    assert "omitidos por licencia" in out and "AUSENTE_EN_FUENTE=" in out
    for ine in SIN_INDICIOS:
        assert f"sin indicios publicables, no se publica: {ine}" in out


def test_ca2_indice_con_discrepancias_exactamente_donde_hay_fichero() -> None:
    """CA2. Dado `site/datos/indice.json` tras CA1, cuando se lee, entonces la capa
    `discrepancias` aparece exactamente en los municipios con fichero publicado, apuntando a un
    fichero que existe, y las capas `vias_pecuarias` y `camino_actual` quedan idénticas a las de
    antes (regenerar el índice con el comando, nunca a mano)."""
    index = _load(DATOS / "indice.json")
    assert {m["ine"] for m in index["municipios"]} == set(AMBITO_HITO_INE)
    for entry in index["municipios"]:
        ine = entry["ine"]
        con_fichero = (DISCREPANCIAS / f"{ine}.geojson").is_file()
        assert ("discrepancias" in entry["capas"]) == con_fichero, ine
        assert con_fichero == (ine in CON_INDICIOS), ine
        if con_fichero:
            assert entry["capas"]["discrepancias"] == f"discrepancias/{ine}.geojson"
        # Las capas de #86, idénticas: mismas rutas y con su fichero.
        assert entry["capas"]["camino_actual"] == f"camino_actual/{ine}.geojson"
        assert (DATOS / entry["capas"]["camino_actual"]).is_file()
        if "vias_pecuarias" in entry["capas"]:
            assert entry["capas"]["vias_pecuarias"] == f"vias_pecuarias/{ine}.geojson"
            assert (DATOS / entry["capas"]["vias_pecuarias"]).is_file()
    assert _load(DATOS / "indice.json.procedencia.json")["redistribucion"] == "publicable"


def test_ca3_municipios_sin_indicios_no_dejan_fichero_ni_entrada() -> None:
    """CA3. Dado un municipio del ámbito sin indicios publicables, cuando se publica, entonces
    no queda fichero vacío ni entrada en el índice para él, y la PR dice cuáles son y por qué
    (sin inventar una fuente: si no hay capa de históricas o de Catastro, se documenta como no
    disponible)."""
    index = {m["ine"]: m for m in _load(DATOS / "indice.json")["municipios"]}
    for ine in SIN_INDICIOS:
        assert not (DISCREPANCIAS / f"{ine}.geojson").exists()
        assert not (DISCREPANCIAS / f"{ine}.geojson.procedencia.json").exists()
        assert "discrepancias" not in index[ine]["capas"]
    for path in DISCREPANCIAS.glob("*.geojson"):
        assert _load(path)["features"], f"{path.name} vacío"


def test_ca4_peso_de_discrepancias_se_cuenta_y_falla_nombrando_fichero(tmp_path: Path) -> None:
    """CA4. Dado `site/datos/discrepancias/` con ficheros sintéticos de tamaño controlado (sin
    red), cuando se ejecuta `comprobar(sitio)` de `scripts/comprobar_peso_sitio.py`, entonces
    falla nombrando el fichero y su tamaño si un fichero de `discrepancias/` supera 3 MB, y
    cuenta ese fichero en la suma por pantalla del municipio (8 MB) y en el total del sitio
    (40 MB); pasa si no se supera ninguno."""

    def write(site: Path, relative: str, size: int) -> None:
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    ok = tmp_path / "ok"
    write(ok, "datos/discrepancias/28043.geojson", 3_000_000)
    write(ok, "datos/vias_pecuarias/28043.geojson", 2_000_000)
    assert peso.comprobar(ok) == []

    grande = tmp_path / "grande"
    write(grande, "datos/discrepancias/28043.geojson", 3_000_001)
    errors = peso.comprobar(grande)
    assert any("datos/discrepancias/28043.geojson" in e and "3000001" in e for e in errors)

    pantalla = tmp_path / "pantalla"
    for layer, size in (("discrepancias", 2_900_000), ("camino_actual", 2_900_000)):
        write(pantalla, f"datos/{layer}/28013.geojson", size)
    write(pantalla, "datos/vias_pecuarias/28013.geojson", 2_100_000)
    assert peso.comprobar(pantalla) == []  # 7.9 MB: bajo el umbral de pantalla
    write(pantalla, "datos/vias_pecuarias/28013.geojson", 2_300_000)
    # 2.9 + 2.9 + 2.3 = 8.1 MB > 8 MB: el fichero de discrepancias cuenta en la pantalla.
    errors = peso.comprobar(pantalla)
    assert len(errors) == 1 and "municipio 28013" in errors[0] and "8100000" in errors[0]
    solo_otras = tmp_path / "sin_discrepancias"
    write(solo_otras, "datos/camino_actual/28013.geojson", 2_900_000)
    write(solo_otras, "datos/vias_pecuarias/28013.geojson", 2_300_000)
    assert peso.comprobar(solo_otras) == []

    total = tmp_path / "total"
    for i in range(13):
        write(total, f"datos/discrepancias/280{i:02d}.geojson", 2_900_000)
    assert peso.comprobar(total) == []  # 37.7 MB: bajo el umbral del sitio
    write(total, "datos/discrepancias/28099.geojson", 2_900_000)
    errors = peso.comprobar(total)  # 40.6 MB: el fichero de discrepancias cuenta en el total
    assert len(errors) == 1 and "en total" in errors[0] and "40600000" in errors[0]


def test_ca5_el_sitio_real_cumple_los_umbrales(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA5. Dado el `site/` real tras CA1, cuando se ejecuta `python3
    scripts/comprobar_peso_sitio.py site`, entonces sale con 0, y el informe de la PR trae una
    tabla con el peso por municipio y capa (incluida `discrepancias`) y el total de `site/`.
    Si algún umbral se superara, la PR no fuerza la simplificación (las fichas no se
    simplifican, ADR 0044 §7): documenta el disparador de teselas vectoriales del ADR 0037 y
    deja la historia a la espera de decisión."""
    assert peso.comprobar(SITE) == []
    assert peso.main(str(SITE)) == 0
    assert capsys.readouterr().err == ""
    # Un fichero real de discrepancias sigue contando: inflado, el sitio falla nombrándolo.
    copia = tmp_path / "site"
    (copia / "datos" / "discrepancias").mkdir(parents=True)
    (copia / "datos" / "discrepancias" / "28013.geojson").write_bytes(b"x" * 3_000_001)
    assert peso.main(str(copia)) == 1
    assert "28013.geojson" in capsys.readouterr().err


def test_ca6_lenguaje_referencias_y_titularidad_en_los_ficheros_reales(tmp_path: Path) -> None:
    """CA6. Dado todo `site/datos/discrepancias/` real, cuando un test lo recorre (`*.geojson`
    y `*.procedencia.json`), entonces ningún texto contiene una palabra de `BANNED_WORDS`,
    ningún texto casa con el patrón de referencia catastral, y ninguna propiedad fuera de la
    lista blanca de #94 §4 ni clave de titularidad (propietario, titular, nif, nombre de
    persona o sociedad) aparece; el test falla nombrando el fichero y el hallazgo. Se prueba
    que detecta, con ficheros sintéticos que lo incumplen (palabra vetada, referencia, clave
    extra)."""
    assert len(BANNED_WORDS) == 10
    ficheros = sorted(DISCREPANCIAS.glob("*.geojson")) + sorted(
        DISCREPANCIAS.glob("*.procedencia.json")
    )
    assert len(ficheros) == 2 * len(CON_INDICIOS)
    assert auditar_discrepancias(DISCREPANCIAS) == []

    # El detector detecta, nombrando el fichero y el hallazgo.
    palabra = tmp_path / "palabra"
    _write_json(
        palabra / "28999.geojson",
        _synthetic_collection({"texto": "Indicio: una ILEGALIDAD en el tramo."}),
    )
    assert any("28999.geojson" in f and "ilegal" in f for f in auditar_discrepancias(palabra))

    referencia = tmp_path / "referencia"
    _write_json(
        referencia / "28999.geojson",
        _synthetic_collection({"texto": "Indicio: parcela 28043A00900001 del tramo."}),
    )
    assert any(
        "28999.geojson" in f and "referencia catastral" in f
        for f in auditar_discrepancias(referencia)
    )

    extra = tmp_path / "extra"
    _write_json(extra / "28999.geojson", _synthetic_collection({"parcela": "x"}))
    assert any(
        "28999.geojson" in f and "fuera de la lista blanca 'parcela'" in f
        for f in auditar_discrepancias(extra)
    )

    titular = tmp_path / "titular"
    _write_json(titular / "28999.geojson", _synthetic_collection({"propietario": "x"}))
    findings = auditar_discrepancias(titular)
    assert any("28999.geojson" in f and "clave de titularidad 'propietario'" in f for f in findings)

    sidecar = tmp_path / "sidecar"
    _write_json(sidecar / "28999.geojson.procedencia.json", {"titular": "alguien"})
    assert any(
        "28999.geojson.procedencia.json" in f and "titular" in f
        for f in auditar_discrepancias(sidecar)
    )


def test_ca7_procedencia_y_formato_de_cada_fichero_real() -> None:
    """CA7. Dado cada fichero real de `discrepancias/`, cuando se lee su `.procedencia.json`,
    entonces trae `redistribucion: publicable`, fuentes con URL, fecha de descarga, licencia y
    atribución, y `transformacion.simplificacion` es `null`; el GeoJSON está en EPSG:4326 y
    cada feature trae el aviso de «indicio, no dictamen» de la ficha de #16."""
    for ine in CON_INDICIOS:
        path = DISCREPANCIAS / f"{ine}.geojson"
        sidecar = _load(DISCREPANCIAS / f"{ine}.geojson.procedencia.json")
        assert sidecar["redistribucion"] == "publicable", ine
        assert sidecar["capa"] == "discrepancias" and sidecar["municipio"] == ine
        assert sidecar["fuentes"], ine
        for fuente in sidecar["fuentes"]:
            for field in ("url", "fecha_descarga", "licencia", "atribucion"):
                assert isinstance(fuente[field], str) and fuente[field], (ine, field)
            assert fuente["url"].startswith("https://")
        assert sidecar["transformacion"]["simplificacion"] is None, ine
        assert sidecar["transformacion"]["crs_destino"] == "EPSG:4326"

        document = _load(path)
        assert "crs" not in document  # RFC 7946: EPSG:4326 implícito
        for feature in document["features"]:
            assert feature["properties"]["aviso"] == WARNING
            assert "no es un dictamen" in feature["properties"]["aviso"]
            assert feature["properties"]["texto"].startswith("Indicio a verificar:")
            positions = re.findall(
                r"\[(-?\d+\.\d+),(-?\d+\.\d+)\]",
                json.dumps(feature["geometry"], separators=(",", ":")),
            )
            assert positions
            for lon, lat in positions:
                assert -3.9 < float(lon) < -3.1 and 39.8 < float(lat) < 40.3, (ine, lon, lat)


def test_ca8_fechas_de_descarga_de_las_capas_de_origen_coinciden() -> None:
    """CA8. Dado el informe de la PR, cuando se revisa, entonces incluye por municipio los
    indicios publicados y los omitidos (por licencia), las `fecha_descarga` de las capas de
    origen (y avisa si difieren entre sí), y lo que no tiene capa todavía (históricas,
    Catastro) como «no disponible», no como «sin indicios». (El informe vive en la PR; aquí se
    comprueba el dato que lo alimenta: las fechas de descarga publicadas.)"""
    # Antes se fijaba el literal «2026-11-26»: deja de valer en cuanto se republica otro día
    # (#183 regeneró la red del IGN el 2026-11-27). Lo que se comprueba es que la fecha de cada
    # fuente del indicio coincide con la de su capa de origen publicada en `site/datos/`.
    capas = {"ign-roadlink": "camino_actual", "idem": "vias_pecuarias"}
    for ine in CON_INDICIOS:
        sidecar = _load(DISCREPANCIAS / f"{ine}.geojson.procedencia.json")
        for fuente in sidecar["fuentes"]:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fuente["fecha_descarga"]), (ine, fuente)
            capa = capas.get(fuente["origen"])
            if capa is not None:  # Catastro no tiene capa publicada (uso interno)
                origen = _load(DATOS / capa / f"{ine}.geojson.procedencia.json")
                assert fuente["fecha_descarga"] == origen["fecha_descarga"], (ine, capa)
        for feature in _load(DISCREPANCIAS / f"{ine}.geojson")["features"]:
            assert feature["properties"]["evidencia"], ine


@pytest.mark.integration
def test_ca9_el_sitio_construido_no_referencia_ficheros_inexistentes(tmp_path: Path) -> None:
    """CA9. [integración] Dado el sitio desplegado (workflow de #41), cuando se abren los
    ficheros de `discrepancias/` listados en el índice, entonces responden 200 sin 404. (Sin
    la red del despliegue: se construye el sitio como el workflow y se comprueba que cada ruta
    del índice, y su procedencia, existe en el resultado.)"""
    destino = tmp_path / "_site"
    assert construir_sitio.main(str(SITE), str(destino)) == 0
    listados = 0
    for entry in _load(destino / "datos" / "indice.json")["municipios"]:
        relative = entry["capas"].get("discrepancias")
        if relative is None:
            continue
        listados += 1
        assert (destino / "datos" / relative).is_file(), relative
        assert (destino / "datos" / f"{relative}.procedencia.json").is_file(), relative
    assert listados == len(CON_INDICIOS)
    assert shutil.which("python3") is not None
    assert peso.comprobar(destino) == []
