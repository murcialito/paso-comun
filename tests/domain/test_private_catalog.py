"""Tests unitarios del catálogo de datos privados (historia #295). Fixtures sintéticas."""

import json
from datetime import date

import pytest

from parcel_intel.domain.private_catalog import (
    AssetPart,
    CatalogDocument,
    EntryLayout,
    EntrySyncResult,
    GitFile,
    PrivateCatalogError,
    ReleaseAsset,
    SyncOutcome,
    SyncSummary,
    find_ownership_markers,
    local_record,
    parse_catalog,
    parse_provenance,
    record_matches,
    safe_destination,
    safe_repo_path,
    validate_catalog_set,
)
from tests.fixtures.private_data import generar
from tests.fixtures.private_data.generar import (
    AC_ID,
    CACHE_ID,
    HISTORICO,
    OLD_AC_ID,
    SNAPSHOT_ID,
    TP_GIT_PATH,
    TP_ID,
)


def _document(name: str, data: object) -> CatalogDocument:
    return CatalogDocument(name=name, content=json.dumps(data).encode())


def _fixture_catalogs() -> list[CatalogDocument]:
    return [
        CatalogDocument(name=path.name, content=path.read_bytes())
        for path in sorted((generar.REPO_DIR / "catalogo").glob("*.json"))
    ]


def _historico() -> dict[str, object]:
    return generar.catalogs()[f"{HISTORICO}.json"]


@pytest.mark.parametrize(
    "text",
    ["raw/a", "raw/catastro/28043/x.7z", "derived/candidatos/interno/28043.geojson"],
)
def test_safe_destination_acepta_rutas_bajo_raw_y_derived(text: str) -> None:
    assert safe_destination(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "/raw/a",
        "raw",
        "raw/",
        "raw//a",
        "raw/./a",
        "raw/../a",
        "raw/a/..",
        "raw\\a",
        "C:/raw/a",
        "raw/a\x00b",
        "raw/a\nb",
        "otros/a",
        "privados/a",
    ],
)
def test_safe_destination_rechaza(text: str) -> None:
    with pytest.raises(ValueError) as raised:
        safe_destination(text)
    assert text not in str(raised.value) or text == ""


def test_safe_repo_path_exige_la_raiz() -> None:
    assert safe_repo_path("derivados/a/b.txt", "derivados") == "derivados/a/b.txt"
    with pytest.raises(ValueError):
        safe_repo_path("procedencia/a.json", "derivados")
    with pytest.raises(ValueError):
        safe_repo_path("derivados/../catalogo/x.json", "derivados")


def test_find_ownership_markers_recorre_todo_sin_nombrar_lo_desconocido() -> None:
    data = {
        "id": "x",
        "Dueño": "a",
        "lista": [{"NIF_persona": "b"}, "texto 99999A00109101"],
        "anidado": {"profundo": {"refcat": 1}},
        "99999A00109101": "clave con forma de referencia",
    }
    markers = find_ownership_markers(data, "")
    assert len(markers) == 5
    joined = "\n".join(markers)
    for leaked in ("Dueño", "NIF_persona", "refcat", "99999A00109101", "profundo", "lista"):
        assert leaked not in joined
    assert "<clave>[1]: valor con forma de referencia catastral" in joined


def test_find_ownership_markers_vacio_si_no_hay_nada() -> None:
    assert find_ownership_markers(_historico(), "") == ()


def test_parse_catalog_de_la_fixture() -> None:
    catalogs = [parse_catalog(document) for document in _fixture_catalogs()]
    assert [c.source for c in catalogs] == ["catastro_historico", "catastro_inspire"]
    historico = catalogs[0]
    assert [e.entry_id for e in historico.entries] == [OLD_AC_ID, AC_ID, TP_ID]
    old, ac, tp = historico.entries
    assert old.replaced_by == AC_ID
    assert (
        ac.storage == ReleaseAsset(tag=generar.HISTORICO_TAG, asset=f"{AC_ID}.7z")
        and ac.layout is EntryLayout.FILE
    )
    assert tp.storage == GitFile(path=TP_GIT_PATH)
    assert tp.size_bytes == len(generar.PAYLOADS[TP_ID])
    assert tp.downloaded_on == date(2099, 1, 1)
    assert tp.municipality_code == "28043"
    assert tp.provenance.path == f"procedencia/{HISTORICO}/{TP_ID}.procedencia.json"


def test_validate_catalog_set_ordena_por_fuente_e_id() -> None:
    entries = validate_catalog_set([parse_catalog(d) for d in reversed(_fixture_catalogs())])
    assert [e.entry_id for e in entries] == [OLD_AC_ID, AC_ID, TP_ID, CACHE_ID, SNAPSHOT_ID]


def test_parse_catalog_con_partes_y_tar() -> None:
    data = _historico()
    entries = data["entradas"]
    assert isinstance(entries, list)
    entry = dict(entries[1])
    entry["formato"] = "tar"
    entry["partes"] = [
        {"adjunto": f"{AC_ID}.7z.001", "sha256": "a" * 64, "tamano_bytes": 3},
        {"adjunto": f"{AC_ID}.7z.002", "sha256": "b" * 64, "tamano_bytes": 2},
    ]
    entries[1] = entry
    catalog = parse_catalog(_document(f"{HISTORICO}.json", data))
    parsed = catalog.entries[1]
    assert parsed.layout is EntryLayout.TAR
    assert parsed.parts == (
        AssetPart(asset=f"{AC_ID}.7z.001", sha256="a" * 64, size_bytes=3),
        AssetPart(asset=f"{AC_ID}.7z.002", sha256="b" * 64, size_bytes=2),
    )


@pytest.mark.parametrize(
    "partes",
    [
        [],
        [{"adjunto": "otro.7z.001", "sha256": "a" * 64, "tamano_bytes": 1}],
        [{"adjunto": f"{AC_ID}.7z.001", "sha256": "a" * 64, "tamano_bytes": True}],
        [{"adjunto": f"{AC_ID}.7z.001", "sha256": "a" * 64}],
        [{"adjunto": f"{AC_ID}.*", "sha256": "a" * 64, "tamano_bytes": 1}],
    ],
)
def test_parse_catalog_rechaza_partes_mal_formadas(partes: object) -> None:
    data = _historico()
    entries = data["entradas"]
    assert isinstance(entries, list)
    entries[1]["partes"] = partes
    with pytest.raises(PrivateCatalogError):
        parse_catalog(_document(f"{HISTORICO}.json", data))


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("Mal.json", b"{}"),
        (f"{HISTORICO}.json", b"\xff\xfe"),
        (f"{HISTORICO}.json", b"[1, 2]"),
        (f"{HISTORICO}.json", b"no es json"),
    ],
)
def test_parse_catalog_rechaza_documentos_ilegibles(name: str, content: bytes) -> None:
    with pytest.raises(PrivateCatalogError) as raised:
        parse_catalog(CatalogDocument(name=name, content=content))
    assert "Mal.json" not in str(raised.value)


def test_un_tar_vigente_no_puede_contener_un_fichero_vigente() -> None:
    data = _historico()
    entries = data["entradas"]
    assert isinstance(entries, list)
    entries[2]["formato"] = "tar"
    entries[2]["destino"] = "raw/catastro_historico"
    catalog = parse_catalog(_document(f"{HISTORICO}.json", data))
    with pytest.raises(PrivateCatalogError, match=r"entradas\[1\]\.destino: dentro del destino"):
        validate_catalog_set([catalog])


def test_la_entrada_sustituida_puede_compartir_destino() -> None:
    entries = validate_catalog_set([parse_catalog(d) for d in _fixture_catalogs()])
    destinations = [e.destination for e in entries if e.entry_id in (AC_ID, OLD_AC_ID)]
    assert destinations[0] == destinations[1]


def test_parse_provenance() -> None:
    assert parse_provenance("x", b'{"fuente": "a"}') == {"fuente": "a"}
    for bad in (b"[]", b"{}", b"no", b'{"titular": "a"}', b'{"nota": "99999A00109101"}'):
        with pytest.raises(PrivateCatalogError) as raised:
            parse_provenance("x", bad)
        assert "99999A00109101" not in str(raised.value)


def test_local_record_es_canonico_y_sin_fecha_de_sincronizacion() -> None:
    entry = parse_catalog(_fixture_catalogs()[0]).entries[1]
    provenance = {"z": 1, "a": "ñ"}
    first = local_record(entry, provenance)
    assert first == local_record(entry, dict(reversed(list(provenance.items()))))
    assert first.endswith(b"}\n")
    data = json.loads(first)
    assert set(data) == {"entrada", "procedencia"}
    assert data["entrada"]["id"] == AC_ID
    assert "sustituido_por" not in data["entrada"]
    assert "ñ" in first.decode()
    assert record_matches(entry, first)
    other = parse_catalog(_fixture_catalogs()[0]).entries[2]
    assert not record_matches(other, first)
    assert not record_matches(entry, b"no es json")


def test_el_registro_reproduce_la_entrada_del_catalogo() -> None:
    raw_entries = _historico()["entradas"]
    assert isinstance(raw_entries, list)
    entry = parse_catalog(_fixture_catalogs()[0]).entries[2]
    data = json.loads(local_record(entry, {"a": 1}))
    expected = {k: v for k, v in raw_entries[2].items() if v is not None}
    assert data["entrada"] == expected


def test_sync_summary_cuenta_por_resultado() -> None:
    summary = SyncSummary(
        results=(
            EntrySyncResult("a", SyncOutcome.DOWNLOADED),
            EntrySyncResult("b", SyncOutcome.DOWNLOADED),
            EntrySyncResult("c", SyncOutcome.PRESENT),
            EntrySyncResult("d", SyncOutcome.SKIPPED, "x"),
            EntrySyncResult("e", SyncOutcome.FAILED, "y"),
        )
    )
    assert (summary.downloaded, summary.present, summary.skipped, summary.failed) == (2, 1, 1, 1)
