"""Sink de indicios publicados (historias #94 y #316): lista blanca, tabla de convergencia,
barreras de referencias y palabras vetadas, umbral de peso e índice compartido. Datos
SINTÉTICOS."""

import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest

from parcel_intel.adapters.published_layers import (
    GeoJsonPublishedDiscrepancySink,
    PublishedLayerError,
)
from parcel_intel.adapters.published_layers.convergence import (
    SIDECAR_KEYS,
    TABLE_KEYS,
    convergence_document,
)
from parcel_intel.domain.discrepancy import Discrepancy, Measurement, SourceRef, TrailRef
from parcel_intel.domain.discrepancy_criterion import CRITERION
from parcel_intel.domain.discrepancy_report import DiscrepancyRecord, MunicipalDiscrepancyReport
from parcel_intel.domain.geometry import Geometry
from parcel_intel.domain.provenance import Provenance, Redistribution
from parcel_intel.domain.published_discrepancy import (
    DiscrepancyLayerPublication,
    build_discrepancy_layer,
)
from tests.convergence_support import indicio_tables

REPO = "https://example.invalid/repo"


def _publication(url: str = "https://example.invalid/vias") -> DiscrepancyLayerPublication:
    prov = Provenance("SINTÉTICO vías", url, date(2026, 11, 20), "SINT", Redistribution.PUBLIC)
    other = Provenance(
        "SINTÉTICO ign",
        "https://example.invalid/ign",
        date(2026, 11, 22),
        "SINT",
        Redistribution.PUBLIC,
    )
    discrepancy = Discrepancy(
        kind="AUSENTE_EN_FUENTE",
        zone=Geometry(
            rings=(
                ((465000.0, 4440300.0), (465100.0, 4440300.0), (465100.0, 4440310.0)),
                ((465200.0, 4440300.0), (465300.0, 4440300.0), (465300.0, 4440310.0)),
            ),
            srid=25830,
        ),
        subject=TrailRef("VP-2", SourceRef("via_pecuaria_oficial", prov, date(2014, 1, 1))),
        counterpart_source=SourceRef("ign_red_transporte", other, date(2025, 3, 15)),
        counterpart_trails=(),
        confidence=0.7,
        evidence=(Measurement("radio_busqueda_m", 33.541, "m"),),
        criterion=CRITERION,
    )
    report = MunicipalDiscrepancyReport(
        "28043",
        "discrepancia_trazados@1",
        (),
        (DiscrepancyRecord("00000000000000a1", discrepancy, (), False),),
        True,
        "Colmenar de Oreja",
    )
    publication = build_discrepancy_layer(report, indicio_tables(report)).publication
    assert publication is not None
    return publication


def _load(path: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def test_publica_la_lista_blanca_con_anillos_cerrados_y_procedencia(tmp_path: Path) -> None:
    (published,) = GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish([_publication()])
    assert published.path == "discrepancias/28043.geojson"
    assert published.layer_id == "discrepancias"
    assert not published.over_threshold and published.simplification is None
    document = _load(tmp_path / published.path)
    assert "crs" not in document
    props = document["properties"]
    assert isinstance(props, dict)
    assert props["procedencia"] == {
        "fuente": "Parcel Intel, indicios de discrepancia entre trazados",
        "url": REPO,
        "fecha_descarga": "2026-11-22",
        "licencia": "CC BY 4.0",
        "redistribucion": "publicable",
    }
    assert props["criterio"] == "discrepancia_trazados v1"
    features = document["features"]
    assert isinstance(features, list)
    (feature,) = features
    geometry = feature["geometry"]
    assert geometry["type"] == "MultiPolygon" and len(geometry["coordinates"]) == 2
    for polygon in geometry["coordinates"]:
        assert polygon[0][0] == polygon[0][-1]  # anillo cerrado
    assert set(feature["properties"]) == {
        "tipo",
        "etiqueta",
        "texto",
        "aviso",
        "confianza",
        "evidencia",
        "criterio",
        "descripcion_criterio",
        "aviso_omision",
        "convergencia",  # #316
    }
    publication = _publication()
    (item,) = publication.content.items
    assert feature["properties"]["convergencia"] == convergence_document(item.convergence)
    assert set(feature["properties"]["convergencia"]) == set(TABLE_KEYS)
    text = (tmp_path / published.path).read_text(encoding="utf-8")
    assert "VP-2" not in text and "33.541" not in text and "0.7" not in text
    assert "00000000000000a1" not in text  # el record_id no sale
    sidecar = _load(tmp_path / "discrepancias" / "28043.geojson.procedencia.json")
    assert sidecar["redistribucion"] == "publicable"
    assert len(sidecar["fuentes"]) == 2  # type: ignore[arg-type]
    block = sidecar["convergencia"]
    assert isinstance(block, dict) and set(block) == set(SIDECAR_KEYS)
    assert block["umbral_aparece"] == 0.5
    # La atribución de la capa suma las de las filas de la tabla, ordenada y sin repetir.
    rows = {a for _, attributions, _ in publication.convergence_notes.sources for a in attributions}
    assert rows
    for attributions in (props["atribucion"], sidecar["atribucion"]):
        assert isinstance(attributions, list)
        assert rows <= set(attributions)
        assert attributions == sorted(set(attributions))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/x?ref=99043A00109001",
        "https://example.invalid/x?Poligono=1&Parcela=9001",
    ],
)
def test_barrera_de_referencias_no_escribe_nada(tmp_path: Path, url: str) -> None:
    with pytest.raises(PublishedLayerError, match="referencia o localizador de parcela"):
        GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish([_publication(url)])
    assert list(tmp_path.iterdir()) == []


def test_sin_publicaciones_no_escribe(tmp_path: Path) -> None:
    assert GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish([]) == ()
    assert list(tmp_path.iterdir()) == []


def test_el_indice_conserva_las_capas_de_trazados(tmp_path: Path) -> None:
    existing = {
        "type": "FeatureCollection",
        "properties": {
            "nombre_municipio": "Colmenar de Oreja",
            "procedencia": {"fecha_descarga": "2026-11-26"},
        },
        "features": [
            {"geometry": {"type": "LineString", "coordinates": [[-3.5, 40.1], [-3.4, 40.2]]}}
        ],
    }
    for layer in ("vias_pecuarias", "camino_actual"):
        (tmp_path / layer).mkdir()
        (tmp_path / layer / "28043.geojson").write_text(json.dumps(existing), encoding="utf-8")
    GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish([_publication()])
    index = json.loads((tmp_path / "indice.json").read_text(encoding="utf-8"))
    (entry,) = index["municipios"]
    assert set(entry["capas"]) == {"camino_actual", "discrepancias", "vias_pecuarias"}


def test_fichero_publicado_ilegible_de_otra_capa_no_escribe_nada(tmp_path: Path) -> None:
    (tmp_path / "vias_pecuarias").mkdir()
    (tmp_path / "vias_pecuarias" / "28045.geojson").write_text("{", encoding="utf-8")
    with pytest.raises(PublishedLayerError, match="ilegible"):
        GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish([_publication()])
    assert not (tmp_path / "discrepancias").exists()


def test_por_encima_del_umbral_falla_sin_escribir_nada(tmp_path: Path) -> None:
    """#316 (ADR 0057 §8): ya no se marca `over_threshold`; falla antes de escribir."""
    with pytest.raises(PublishedLayerError, match="por encima del umbral de 10"):
        GeoJsonPublishedDiscrepancySink(tmp_path, REPO, size_threshold_bytes=10).publish(
            [_publication()]
        )
    assert list(tmp_path.iterdir()) == []
    (published,) = GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish([_publication()])
    assert published.over_threshold is False


def test_tabla_no_validada_no_escribe_nada(tmp_path: Path) -> None:
    publication = _publication()
    (item,) = publication.content.items
    broken = dataclasses.replace(
        item, convergence=dataclasses.replace(item.convergence, title="Otro título")
    )
    content = dataclasses.replace(publication.content, items=(broken,))
    with pytest.raises(
        PublishedLayerError,
        match="discrepancias/28043: tabla de convergencia: el título no es el validado",
    ):
        GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish(
            [dataclasses.replace(publication, content=content)]
        )
    assert list(tmp_path.iterdir()) == []


def test_palabra_vetada_en_el_fichero_no_escribe_nada(tmp_path: Path) -> None:
    publication = dataclasses.replace(_publication(), municipality_name="Villa Ilegal")
    with pytest.raises(PublishedLayerError, match="palabras vetadas"):
        GeoJsonPublishedDiscrepancySink(tmp_path, REPO).publish([publication])
    assert list(tmp_path.iterdir()) == []


def test_barreras_tambien_en_el_indice(tmp_path: Path) -> None:
    """Un fichero ya publicado de otra capa con una palabra vetada o una referencia llega al
    índice: no se escribe nada."""
    for name, match in (
        ("Villa Ilegal", "indice.json: contiene palabras vetadas"),
        ("Villa 99043A00109001", "indice.json: contiene algo con forma de referencia"),
    ):
        site = tmp_path / name.replace(" ", "_")
        existing = {
            "type": "FeatureCollection",
            "properties": {
                "nombre_municipio": name,
                "procedencia": {"fecha_descarga": "2026-11-26"},
            },
            "features": [
                {"geometry": {"type": "LineString", "coordinates": [[-3.5, 40.1], [-3.4, 40.2]]}}
            ],
        }
        (site / "vias_pecuarias").mkdir(parents=True)
        (site / "vias_pecuarias" / "28045.geojson").write_text(
            json.dumps(existing), encoding="utf-8"
        )
        with pytest.raises(PublishedLayerError, match=match):
            GeoJsonPublishedDiscrepancySink(site, REPO).publish([_publication()])
        assert not (site / "discrepancias").exists()
        assert not (site / "indice.json").exists()
