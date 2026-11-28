"""Historia #85: visor estático con Leaflet, atribución y mapa base IGN.

Un test por criterio. Los de comportamiento del visor lanzan `tests/site/visor.test.mjs` con
`node --test` (filtrado por el prefijo `CA<n>:`, ver `tests/site/nodo.py`); los estáticos leen
`site/` con la stdlib. Las fixtures del visor (`tests/fixtures/visor/`) son sintéticas.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
import requests

from tests.site.nodo import FIXTURES, SITE, assert_node_tests_pass

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[2]
NOTICE = ROOT / "NOTICE"
REGISTRY = ROOT / "src" / "parcel_intel" / "sources" / "REGISTRY.md"
ADR_DIR = ROOT / "docs" / "adr"
BUILD_SCRIPT = ROOT / "scripts" / "construir_sitio.py"
PNOA_FIXTURES = ROOT / "tests" / "fixtures" / "pnoa"
VENDOR = SITE / "vendor"

WMTS_PERMITIDO = "https://www.ign.es/wmts/pnoa-ma"
# Historia #185 (ADR 0051): segundo fondo, el mapa gris de IGN-Base (REGISTRY.md).
WMTS_IGN_BASE = "https://www.ign.es/wmts/ign-base"
USER_AGENT = "parcel-intel-verificacion/0.1"
LEAFLET_SHA1 = "23fae724e282fa25745aff82ca4d394748db7d8d"


# --- utilidades -----------------------------------------------------------------------------


class _Html(HTMLParser):
    """Recoge etiquetas con atributos y el texto visible (sin <script> ni <style>)."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.texto: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag in ("script", "style"):
            self._ignorar += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignorar:
            self.texto.append(data)


def _html(path: Path = SITE / "index.html") -> _Html:
    parser = _Html()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def _texto_visible(parser: _Html) -> str:
    return re.sub(r"\s+", " ", " ".join(parser.texto))


def _modulos_propios(base: Path = SITE) -> list[Path]:
    return sorted(base.glob("*.js"))


def _construir(origen: Path, destino: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), str(origen), str(destino)],
        capture_output=True,
        text=True,
        check=False,
    )


def _sitio_construido(tmp_path: Path) -> Path:
    destino = tmp_path / "_site"
    resultado = _construir(SITE, destino)
    assert resultado.returncode == 0, resultado.stderr
    return destino


def _literales_config() -> tuple[list[str], list[str]]:
    """(atribuciones de capa, atribuciones base) tal como están escritas en config.js.

    Desde #185 la base son las atribuciones de los fondos (`atribuciones` de cada fondo: la
    ortofoto primero si se buscan las del PNOA) y las de capa incluyen la de la cobertura."""
    texto = (SITE / "config.js").read_text(encoding="utf-8")
    capas = re.findall(r'\batribucion:\s*"([^"]+)"', texto)
    bloques = re.findall(r"atribuciones:\s*Object\.freeze\(\[(.*?)\]\)", texto, re.DOTALL)
    assert bloques, "config.js sin atribuciones de los fondos"
    base = [t for b in bloques for t in re.findall(r'"([^"]+)"', b)]
    # La ortofoto (PNOA) va primero: CA10 [integración] compara su año.
    base.sort(key=lambda t: not t.startswith("PNOA "))
    return capas, base


def _url_teselas() -> str:
    """URL del fondo de ortofoto PNOA (el del mapa base de #85)."""
    texto = (SITE / "config.js").read_text(encoding="utf-8")
    urls = re.findall(r'\burl:\s*"([^"]+)"', texto)
    pnoa: list[str] = [u for u in urls if u.startswith(WMTS_PERMITIDO + "?")]
    assert len(pnoa) == 1, urls
    return pnoa[0]


def _aviso_vias_pecuarias_notice() -> str:
    match = re.search(r"Aviso obligatorio: es (.+?)\.\n", NOTICE.read_text(encoding="utf-8"))
    assert match is not None, "NOTICE sin aviso obligatorio de vías pecuarias"
    return match.group(1)


def _adr() -> Path:
    candidatos = sorted(ADR_DIR.glob("*-visor-estatico-leaflet-aislado-en-un-modulo.md"))
    assert len(candidatos) == 1, candidatos
    return candidatos[0]


def _get(url: str, **kwargs: Any) -> requests.Response:
    ultimo: Exception | None = None
    for intento in range(4):
        try:
            respuesta = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=60, **kwargs
            )
            if respuesta.status_code < 500:
                return respuesta
            ultimo = RuntimeError(f"HTTP {respuesta.status_code}")
        except requests.RequestException as exc:
            ultimo = exc
        time.sleep(2 * (intento + 1))
    raise AssertionError(f"{url}: {ultimo}")


# --- criterios -------------------------------------------------------------------------------


def test_ca1_recursos_locales_y_solo_teselas_ign_como_host_externo(tmp_path: Path) -> None:
    """CA1. Dado el sitio construido con `scripts/construir_sitio.py` cuando se abre `index.html`
    entonces carga Leaflet, `mapa.js` y los demás scripts y hojas de estilo desde rutas
    relativas dentro de `site/`, y ningún recurso cargado (`<script src>`, `<link href>`,
    `<img src>`, `url(...)` en CSS, `fetch`/URL en JS) apunta a un CDN ni a un host externo salvo
    `https://www.ign.es/wmts/pnoa-ma` (teselas del mapa base). Los enlaces de navegación
    (`<a href>`) al repositorio y a las licencias están permitidos."""
    sitio = _sitio_construido(tmp_path)
    html = _html(sitio / "index.html")

    def local(ref: str) -> Path:
        assert not re.match(r"^([a-z]+:)?//", ref, re.IGNORECASE), f"recurso externo: {ref}"
        assert not ref.startswith("/"), f"ruta absoluta: {ref}"
        destino = (sitio / ref).resolve()
        assert destino.is_relative_to(sitio.resolve()), ref
        assert destino.is_file(), f"no existe en el sitio: {ref}"
        return destino

    scripts: list[str] = []
    hojas: list[str] = []
    for tag, attrs in html.tags:
        for atributo in ("src", "href", "srcset", "data", "poster"):
            valor = attrs.get(atributo)
            if valor is None or (tag == "a" and atributo == "href"):
                continue
            local(valor)
            if tag == "script":
                scripts.append(valor)
            # Solo hojas de estilo: los favicons y el manifest (#115) también son <link>.
            if tag == "link" and attrs.get("rel") == "stylesheet":
                hojas.append(valor)
    # Leaflet (script clásico) antes que la aplicación (módulo).
    assert scripts == ["vendor/leaflet/leaflet.js", "app.js"], scripts
    tipos = {attrs.get("src"): attrs.get("type") for tag, attrs in html.tags if tag == "script"}
    assert tipos["app.js"] == "module"
    # #114 añade los tokens de diseño antes de estilo.css.
    assert hojas == ["vendor/leaflet/leaflet.css", "css/tokens.css", "estilo.css"], hojas
    # Los <a href> externos son solo al repositorio y a las licencias.
    enlaces = [a.get("href") or "" for t, a in html.tags if t == "a"]
    for enlace in enlaces:
        assert enlace.startswith(
            ("https://github.com/murcialito/paso-comun", "https://creativecommons.org/")
        ), enlace

    # url(...) en todas las hojas de estilo del sitio (propias y de vendor).
    hojas_css = sorted(sitio.rglob("*.css"))
    assert len(hojas_css) >= 2
    for hoja in hojas_css:
        for ref in re.findall(r"url\(\s*['\"]?([^'\")]+)", hoja.read_text(encoding="utf-8")):
            if ref.startswith(("data:", "#")):
                continue  # URI de datos o `behavior: url(#default#VML)` de IE, no un fichero
            assert not re.match(r"^([a-z]+:)?//", ref, re.IGNORECASE), f"{hoja.name}: {ref}"
            assert (hoja.parent / ref).resolve().is_file(), f"{hoja.name}: {ref}"

    # JS propio: imports relativos que existen (app.js carga mapa.js y los demás) y ninguna URL
    # absoluta salvo la del WMTS del IGN.
    propios = _modulos_propios(sitio)
    assert {p.name for p in propios} >= {"app.js", "mapa.js", "controlador.js", "config.js"}
    importados: set[str] = set()
    for modulo in propios:
        texto = modulo.read_text(encoding="utf-8")
        for ref in re.findall(r"""\bimport\s[^;]*?from\s+["']([^"']+)["']""", texto):
            assert ref.startswith("./"), f"{modulo.name}: import no relativo {ref}"
            importados.add(local(ref).name)
        assert not re.search(r"\bimport\s*\(", texto), f"{modulo.name}: import dinámico"
        for url in re.findall(r"""(?:[a-z]+:)?//[^\s"'`)]+""", texto, re.IGNORECASE):
            if url.startswith("//"):
                continue  # comentario de línea
            assert url.startswith((WMTS_PERMITIDO, WMTS_IGN_BASE)), f"{modulo.name}: {url}"
    # Historia #165: mapa.js y app.js importan además estilos-capas.js (módulo puro).
    # Historia #185: app.js importa además enlace.js (estado en la URL, módulo puro).
    assert importados == {
        "config.js",
        "controlador.js",
        "enlace.js",
        "estilos-capas.js",
        "mapa.js",
        "vista.js",
    }, importados


def test_ca2_selector_con_los_municipios_del_indice_y_encuadre(tmp_path: Path) -> None:
    """CA2. Dado `site/datos/indice.json` con uno o varios municipios cuando se carga la página
    entonces hay un selector de municipio con exactamente los municipios del índice, en el orden
    del índice, y al elegir uno el mapa encuadra su `bbox`."""
    # Historia #185: el selector es un buscador accesible (combobox) con las sugerencias del
    # índice, en su orden; un solo control para elegir municipio.
    combos = [a for t, a in _html().tags if a.get("role") == "combobox"]
    assert [a.get("id") for a in combos] == ["buscar"]
    assert combos[0].get("aria-controls") == "sugerencias"
    # El índice real que publica #84 cumple la forma que valida el visor.
    indice = json.loads((SITE / "datos" / "indice.json").read_text(encoding="utf-8"))
    assert indice["municipios"], "el índice publicado no tiene municipios"
    for municipio in indice["municipios"]:
        assert re.fullmatch(r"\d{5}", municipio["ine"])
        assert len(municipio["bbox"]) == 4
    assert_node_tests_pass("^CA2:", tmp_path, minimum=4)


def test_ca3_interruptor_por_capa_sin_recargar(tmp_path: Path) -> None:
    """CA3. Dadas las dos capas publicadas (vías pecuarias y camino actual) cuando se abre el
    visor entonces cada capa tiene su propio interruptor (casilla con `<label>`, activa por
    defecto), y activarlo o desactivarlo la muestra u oculta sin recargar la página ni perder la
    otra."""
    tags = _html().tags
    etiquetas = {a.get("for") for t, a in tags if t == "label"}
    for capa in ("vias_pecuarias", "camino_actual"):
        casillas = [a for t, a in tags if t == "input" and a.get("id") == f"capa-{capa}"]
        assert len(casillas) == 1, capa
        assert casillas[0].get("type") == "checkbox"
        assert "checked" in casillas[0], f"{capa} no está activa por defecto"
        assert f"capa-{capa}" in etiquetas, f"{capa} sin <label>"
    assert_node_tests_pass("^CA3:", tmp_path, minimum=3)


def test_ca4_atribuciones_literales_de_notice_y_visibles_con_su_capa(tmp_path: Path) -> None:
    """CA4. Dada una capa activa cuando se ve el mapa entonces su atribución, **copiada
    literalmente de `NOTICE`**, es visible junto al mapa; al desactivar la capa su atribución
    desaparece, y las del mapa base están siempre visibles. Un test compara los textos de
    `site/config.js` con los de `NOTICE` para que no puedan divergir."""
    notice = NOTICE.read_text(encoding="utf-8")
    registry = REGISTRY.read_text(encoding="utf-8")
    capas, base = _literales_config()
    # Cuatro capas desde #171 (vías pecuarias, camino actual, indicios de discrepancia y tramos a
    # verificar) y, desde #185, la cobertura; la base son los dos fondos (gris y ortofoto, con
    # los rótulos del IGN encima de la ortofoto).
    assert len(capas) == 5 and len(base) == 4, (capas, base)
    for texto in capas + base:
        assert f"`{texto}`" in notice, f"no consta literalmente en NOTICE: {texto}"
    for texto in base:
        assert texto in registry, f"no consta en REGISTRY.md: {texto}"
    # Y al revés: la atribución de cada capa publicada en site/datos está en config.js.
    sidecars = sorted((SITE / "datos").glob("*/*.geojson.procedencia.json"))
    assert sidecars
    for sidecar in sidecars:
        atribucion = json.loads(sidecar.read_text(encoding="utf-8"))["atribucion"]
        if sidecar.parent.name in {"discrepancias", "candidatos"}:  # candidatos: #170/#171
            # #94 (ADR 0044): la capa de indicios cita la atribución de cada fuente de su
            # evidencia (lista). Cada una contiene un literal de capa de config.js o consta
            # literalmente en NOTICE (Catastro, ADR 0041); la muestra el visor de #95.
            assert isinstance(atribucion, list) and atribucion, sidecar.name
            for texto in atribucion:
                assert any(c in texto for c in capas) or f"`{texto}`" in notice, texto
            continue
        assert atribucion in capas, f"{sidecar.name}: {atribucion}"
    # Las atribuciones no están escritas en el HTML (si no, no desaparecerían con su capa).
    html = (SITE / "index.html").read_text(encoding="utf-8")
    for texto in capas + base:
        assert texto not in html, texto
    assert_node_tests_pass("^CA4:", tmp_path, minimum=4)


def test_ca5_aviso_de_vias_pecuarias_e_indicios_en_html_estatico() -> None:
    """CA5. Dado cualquier estado del visor cuando se carga la página entonces se ve, escrito en
    el HTML estático (no inyectado por JS), el aviso de `NOTICE` para vías pecuarias
    («cartografía de carácter informativo, no un deslinde»), además de la frase de principios
    «indicios a verificar» que ya tiene la portada."""
    aviso = _aviso_vias_pecuarias_notice()
    assert aviso == "cartografía de carácter informativo, no un deslinde"
    texto = _texto_visible(_html())
    assert aviso in texto
    assert "indicios a verificar" in texto
    # No depende de ningún estado: no está en ningún elemento que el JS reescriba.
    # #185: el selector pasa a buscador y la ficha a un panel; ninguno lleva el aviso.
    reescritos = {"mensaje", "aviso-base", "sugerencias", "ambito-nombre", "ficha-cuerpo",
                  "estado-vias_pecuarias", "estado-camino_actual", "estado-discrepancias", "mapa"}
    html = (SITE / "index.html").read_text(encoding="utf-8")
    for ident in reescritos:
        bloque = re.search(rf'<[^>]*id="{ident}"[^>]*>(.*?)</', html, re.DOTALL)
        assert bloque is not None, ident
        assert aviso not in bloque.group(1)


def test_ca6_popup_con_lista_cerrada_de_campos_y_como_texto(tmp_path: Path) -> None:
    """CA6. Dado un trazado cuando se pulsa entonces un popup muestra solo el título de la capa,
    su fuente (texto de `config.js`) y, en vías pecuarias, la propiedad `nombre` del trazado si
    existe; el texto se inserta como texto, no como HTML. Nunca contiene propietarios,
    referencias catastrales ni textos que afirmen un corte o una ilegalidad (un test con un
    feature sintético que trae propiedades trampa —`titular`, `refcat`, `<b>`— comprueba que no
    salen)."""
    for modulo in _modulos_propios():
        texto = modulo.read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML", texto), modulo.name
    assert_node_tests_pass("^CA6:", tmp_path, minimum=4)


def test_ca7_solo_mapa_js_usa_leaflet() -> None:
    """CA7. Dado `mapa.js` cuando se revisan el HTML y los demás scripts de `site/` (fuera de
    `site/vendor/`) entonces ningún otro fichero referencia la API de Leaflet (`L.`, `L[`,
    `window.L`): toda llamada pasa por la interfaz del módulo."""
    patron = re.compile(r"\bL\s*[.\[]|window\.L\b|globalThis\.L\b")
    revisados = [
        p
        for p in sorted(SITE.rglob("*"))
        if p.is_file()
        and p.suffix in {".html", ".js", ".mjs", ".css"}
        and VENDOR not in p.parents
        and p.name != "mapa.js"
    ]
    assert len(revisados) >= 5, revisados  # index.html, estilo.css, app, config, controlador, vista
    for fichero in revisados:
        assert not patron.search(fichero.read_text(encoding="utf-8")), fichero.name
    # Y mapa.js sí la usa (el test no pasa en vacío por un patrón mal escrito).
    assert patron.search((SITE / "mapa.js").read_text(encoding="utf-8"))


_FEATURE_PROPS = {"nombre", "tipo", "fecha_dato", "procedencia"}
# Lista blanca de la capa de indicios (#94, ADR 0044 §2): la ficha de #16, sin nombres.
_INDICIO_PROPS = {
    "tipo",
    "etiqueta",
    "texto",
    "aviso",
    "confianza",
    "evidencia",
    "criterio",
    "descripcion_criterio",
    "aviso_omision",
    "convergencia",  # #316: tabla de convergencia; nada más nuevo (CA13)
}
_PROHIBIDAS = re.compile(r"catastr|titular|propietari|refcat|referencia_catastral", re.IGNORECASE)


def _comprobar_datos(datos: Path) -> None:
    indice = json.loads((datos / "indice.json").read_text(encoding="utf-8"))
    cargados = [datos / "indice.json"] + [
        datos / ruta for m in indice["municipios"] for ruta in m["capas"].values()
    ]
    for fichero in cargados:
        if not fichero.exists():
            continue  # el 404 sintético de CA9: no se carga nada
        sidecar = fichero.with_name(fichero.name + ".procedencia.json")
        assert sidecar.is_file(), f"sin procedencia: {fichero.name}"
        procedencia = json.loads(sidecar.read_text(encoding="utf-8"))
        assert procedencia["redistribucion"] == "publicable", fichero
        assert not _PROHIBIDAS.search(str(procedencia.get("fuente"))), fichero
        assert "catastro" not in str(procedencia.get("url")).lower(), fichero
    for geojson in sorted(datos.rglob("*.geojson")):
        try:
            coleccion = json.loads(geojson.read_text(encoding="utf-8"))
        except ValueError:
            continue  # el JSON roto sintético de CA9
        permitidas = _INDICIO_PROPS if geojson.parent.name == "discrepancias" else _FEATURE_PROPS
        if geojson.name == "cobertura.geojson":
            permitidas = {"ine", "nombre"}  # historia #185: contorno de cada municipio cubierto
        for feature in coleccion["features"]:
            assert set(feature["properties"]) <= permitidas, geojson
        for clave in re.findall(r'"([^"]+)"\s*:', geojson.read_text(encoding="utf-8")):
            assert not _PROHIBIDAS.search(clave), f"{geojson.name}: clave {clave}"


def test_ca8_construccion_sin_catastro_y_con_procedencia(tmp_path: Path) -> None:
    """CA8. Dado el contenido de `site/` cuando se construye con `construir_sitio.py` entonces la
    construcción pasa, no hay datos de Catastro ni de titularidad en el sitio, y todo fichero de
    datos cargado por el visor (incluido `indice.json`) tiene su `.procedencia.json` con
    `redistribucion: publicable`."""
    # El site/ real del repo.
    real = _sitio_construido(tmp_path / "real")
    _comprobar_datos(real / "datos")
    # El visor con las fixtures sintéticas como datos.
    origen = tmp_path / "con_fixtures"
    shutil.copytree(SITE, origen, ignore=shutil.ignore_patterns("datos"))
    shutil.copytree(FIXTURES, origen / "datos")
    resultado = _construir(origen, tmp_path / "con_fixtures_out")
    assert resultado.returncode == 0, resultado.stderr
    _comprobar_datos(tmp_path / "con_fixtures_out" / "datos")
    # Ningún fichero de datos fuera de datos/ (los .js de config no son datos).
    datos_fuera = [
        p
        for p in real.rglob("*")
        if p.suffix in {".json", ".geojson", ".gml", ".xml", ".csv"}
        and (real / "datos") not in p.parents
    ]
    assert datos_fuera == []


def test_ca9_capa_que_falla_da_error_y_la_otra_se_dibuja(tmp_path: Path) -> None:
    """CA9. Dado un municipio cuyo GeoJSON de una capa no se puede cargar (404 o JSON inválido)
    cuando se elige entonces el visor muestra un mensaje de error comprensible que nombra la capa
    y el municipio, la otra capa de ese municipio se dibuja, y elegir otro municipio funciona y
    borra el mensaje."""
    # Las fixtures ejercitan los dos fallos: un fichero que no existe y un JSON roto.
    indice = json.loads((FIXTURES / "indice.json").read_text(encoding="utf-8"))
    rutas = {m["ine"]: m["capas"] for m in indice["municipios"]}
    assert not (FIXTURES / rutas["99003"]["vias_pecuarias"]).exists()
    with pytest.raises(ValueError):
        json.loads((FIXTURES / rutas["99004"]["camino_actual"]).read_text(encoding="utf-8"))
    assert_node_tests_pass("^CA9:", tmp_path, minimum=5)


def test_ca10_fondo_neutro_y_fallo_de_teselas(tmp_path: Path) -> None:
    """CA10. Dado un fallo en las teselas del mapa base cuando se carga el visor entonces las
    capas vectoriales se siguen viendo sobre fondo neutro (el contenedor del mapa tiene color de
    fondo propio y el error de tesela no quita capas)."""
    css = (SITE / "estilo.css").read_text(encoding="utf-8")
    regla = re.search(r"#mapa\s*\{([^}]*)\}", css)
    assert regla is not None and "background-color" in regla.group(1)
    assert any(t == "div" and a.get("id") == "mapa" for t, a in _html().tags)
    # La URL de config.js existe en el capabilities grabado (2026-11-26) y su licencia.
    capabilities = (PNOA_FIXTURES / "wmts_pnoa_ma_getcapabilities.xml").read_text(
        encoding="utf-8"
    )
    url = _url_teselas()
    assert url.startswith(WMTS_PERMITIDO + "?")
    parametros = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
    assert parametros["service"] == "WMTS" and parametros["request"] == "GetTile"
    assert f"<ows:Identifier>{parametros['layer']}</ows:Identifier>" in capabilities
    assert f"<ows:Identifier>{parametros['style']}</ows:Identifier>" in capabilities
    assert f"<TileMatrixSet>{parametros['tilematrixset']}</TileMatrixSet>" in capabilities
    assert f"<Format>{parametros['format']}</Format>" in capabilities
    assert "<ows:AccessConstraints>CC BY 4.0 scne.es</ows:AccessConstraints>" in capabilities
    assert "<ows:Value>KVP</ows:Value>" in capabilities
    # La tesela grabada es un JPEG (documenta la verificación; no se publica en site/).
    tesela = (PNOA_FIXTURES / "wmts_pnoa_ma_colmenar_z15.jpg").read_bytes()
    assert tesela[:2] == b"\xff\xd8"
    assert not list(SITE.rglob("*.jpg"))
    assert_node_tests_pass("^CA10:", tmp_path, minimum=2)


@pytest.mark.integration
@pytest.mark.red
def test_ca10_integracion_servicio_real_del_mapa_base() -> None:
    """CA10. [integración] Contra el servicio real: la URL de `config.js` devuelve
    `200 image/jpeg` para la tesela `z=15, x=16075, y=12392` (casco de Colmenar de Oreja, 28043),
    el `GetCapabilities` WMTS sigue declarando `CC BY 4.0 scne.es`, y el `GetFeatureInfo` de
    `OI.MosaicElement` del WMS `pnoa-ma` en Colmenar devuelve el año que figura en la atribución
    del mapa base (hoy 2023). El resultado está anotado en `REGISTRY.md`."""
    url = _url_teselas().replace("{z}", "15").replace("{x}", "16075").replace("{y}", "12392")
    tesela = _get(url)
    assert tesela.status_code == 200
    assert tesela.headers["content-type"].startswith("image/jpeg")
    assert tesela.content[:2] == b"\xff\xd8"

    capabilities = _get(WMTS_PERMITIDO, params={"service": "WMTS", "request": "GetCapabilities"})
    assert capabilities.status_code == 200
    assert "<ows:AccessConstraints>CC BY 4.0 scne.es</ows:AccessConstraints>" in capabilities.text

    gfi = _get(
        "https://www.ign.es/wms-inspire/pnoa-ma",
        params={
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "LAYERS": "OI.MosaicElement",
            "QUERY_LAYERS": "OI.MosaicElement",
            "STYLES": "",
            "CRS": "EPSG:25830",
            "BBOX": "467400,4441800,467800,4442200",
            "WIDTH": "256",
            "HEIGHT": "256",
            "I": "128",
            "J": "128",
            "INFO_FORMAT": "text/html",
            "FORMAT": "image/png",
        },
    )
    assert gfi.status_code == 200
    fecha = re.search(r"<td[^>]*>(\d{4})-\d{2}</td>", gfi.text)
    assert fecha is not None, gfi.text
    _, base = _literales_config()
    anio_atribuido = re.match(r"PNOA (\d{4}) ", base[0])
    assert anio_atribuido is not None, base[0]
    assert fecha.group(1) == anio_atribuido.group(1), (
        f"El vuelo servido es de {fecha.group(1)} y la atribución dice "
        f"{anio_atribuido.group(1)}: actualizar NOTICE, REGISTRY.md y config.js juntos."
    )
    registry = REGISTRY.read_text(encoding="utf-8")
    assert "### WMTS (visor, historia #85)" in registry
    assert base[0] in registry


def test_ca11_adr_del_visor() -> None:
    """CA11. Dado el ADR de esta historia cuando se lee entonces recoge: la elección de Leaflet y
    su versión (1.9.4, con su sha1 de npm), la verificación de la URL y condiciones de uso del
    mapa base, los disparadores para migrar a MapLibre GL (volumen que obligue a teselas
    vectoriales `.pmtiles`, mucho más de unas decenas de MB de GeoJSON por pantalla, o necesidad
    de estilos por zoom o rotación) y el coste de funcionamiento (cero: ficheros estáticos)."""
    texto = _adr().read_text(encoding="utf-8")
    for fragmento in (
        "Leaflet 1.9.4",
        LEAFLET_SHA1,
        WMTS_PERMITIDO,
        "CC BY 4.0 scne.es",
        "## Disparadores para migrar a MapLibre GL",
        ".pmtiles",
        "decenas de MB",
        "zoom",
        "rotación",
        "## Coste de funcionamiento",
        "Cero",
        "ficheros estáticos",
    ):
        assert fragmento in texto, fragmento


def test_ca12_licencia_de_leaflet_viaja_con_la_copia() -> None:
    """CA12. Dada la licencia de Leaflet (BSD-2) cuando se copia a `site/vendor/leaflet/`
    entonces su fichero `LICENSE` viaja con él y se menciona en `NOTICE`."""
    carpeta = VENDOR / "leaflet"
    licencia = (carpeta / "LICENSE").read_text(encoding="utf-8")
    assert licencia.startswith("BSD 2-Clause License")
    assert "Agafonkin" in licencia
    assert (carpeta / "leaflet.js").read_text(encoding="utf-8").startswith(
        "/* @preserve\n * Leaflet 1.9.4,"
    )
    assert (carpeta / "leaflet.css").is_file()
    # Solo dist/ mínimo: ni fuentes sin minificar ni source maps.
    nombres = {p.name for p in carpeta.rglob("*") if p.is_file()}
    assert not {"leaflet-src.js", "leaflet-src.esm.js"} & nombres
    assert not [n for n in nombres if n.endswith(".map")]
    notice = NOTICE.read_text(encoding="utf-8")
    assert "## Software de terceros incluido en el sitio" in notice
    assert "Leaflet 1.9.4" in notice
    assert "BSD 2-Clause" in notice
    assert "site/vendor/leaflet/LICENSE" in notice


def test_ca13_capa_vacia_no_es_error(tmp_path: Path) -> None:
    """CA13. Dado un municipio cuya capa existe pero tiene `features: []` (p. ej. vías pecuarias
    de Belmonte de Tajo o Valdelaguna, CA5 de #83) cuando se elige entonces no se muestra error,
    el interruptor de la capa sigue funcionando y el visor indica «sin trazados de esta capa en
    el municipio» (capa vacía ≠ capa que falla, CA9)."""
    vacia = json.loads((FIXTURES / "vias_pecuarias" / "99002.geojson").read_text("utf-8"))
    assert vacia["type"] == "FeatureCollection" and vacia["features"] == []
    assert "sin trazados de esta capa en el municipio" in (SITE / "config.js").read_text("utf-8")
    assert_node_tests_pass("^CA13:", tmp_path, minimum=3)


def test_ca14_trazados_multiparte_enteros_y_un_popup(tmp_path: Path) -> None:
    """CA14. Dado un trazado multiparte (`MultiPolygon` en vías pecuarias, `MultiLineString` en
    camino actual) cuando se carga entonces se dibujan todas sus partes y el popup es el mismo en
    cualquiera de ellas (fixture sintética con N≥2 partes, lección de la historia 0001)."""
    for capa, tipo in (("vias_pecuarias", "MultiPolygon"), ("camino_actual", "MultiLineString")):
        datos = json.loads((FIXTURES / capa / "99001.geojson").read_text("utf-8"))
        multi = [f for f in datos["features"] if f["geometry"]["type"] == tipo]
        assert multi and all(len(f["geometry"]["coordinates"]) >= 2 for f in multi), capa
    assert_node_tests_pass("^CA14:", tmp_path, minimum=2)


def test_ca15_accesibilidad_de_index_html() -> None:
    """CA15. Dado `index.html` cuando se lee entonces tiene `lang="es"`, el selector y cada
    interruptor tienen `<label>` asociada, todos los controles son elementos nativos alcanzables
    con teclado (`<select>`, `<input type="checkbox">`), y el mensaje de error está en una región
    `role="status"` / `aria-live="polite"`."""
    tags = _html().tags
    raiz = [a for t, a in tags if t == "html"]
    assert raiz and raiz[0].get("lang") == "es"
    etiquetas = {a.get("for") for t, a in tags if t == "label"}
    # Historia #185: buscador + cuatro capas + dos fondos; los botones (plegar el panel, ver
    # España, ampliar y cerrar la ficha) son <button> nativos con texto o aria-label.
    controles = [(t, a) for t, a in tags if t in ("select", "input", "button", "textarea")]
    entradas = [(t, a) for t, a in controles if t != "button"]
    assert len(entradas) == 7, entradas
    for tag, attrs in entradas:
        assert tag == "input", tag
        assert attrs.get("type") in ("search", "checkbox", "radio"), attrs
        assert attrs.get("id") in etiquetas, f"{tag} sin <label>: {attrs}"
        assert attrs.get("tabindex") in (None, "0"), attrs
    botones = [a for t, a in controles if t == "button"]
    assert len(botones) == 4, botones
    for attrs in botones:
        assert attrs.get("type") == "button", attrs
    html = (SITE / "index.html").read_text(encoding="utf-8")
    for ident in ("ficha-ampliar", "ficha-cerrar"):
        assert re.search(rf'id="{ident}"[^>]*aria-label="[^"]+"', html), ident
    # Ningún control falso (div con onclick o role=button). La única lista de opciones es la
    # del combobox (patrón ARIA), referida por su aria-controls.
    for tag, attrs in tags:
        assert "onclick" not in attrs, (tag, attrs)
        assert attrs.get("role") not in ("button", "checkbox"), (tag, attrs)
        if attrs.get("role") == "listbox":
            assert attrs.get("id") == "sugerencias", attrs
    mensaje = [a for t, a in tags if a.get("id") == "mensaje"]
    assert mensaje and mensaje[0].get("role") == "status"
    assert mensaje[0].get("aria-live") == "polite"
