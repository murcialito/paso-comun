"""La fixture sintética del repo privado (historia #295) es exactamente la que escribe su
generador: los sha256 del catálogo salen de los bytes, no se copian a mano."""

import hashlib

from tests.fixtures.private_data import generar


def test_lo_versionado_coincide_con_el_generador() -> None:
    expected = generar.files()
    on_disk = {
        path: path.read_bytes()
        for directory in (generar.REPO_DIR, generar.ASSETS_DIR)
        for path in directory.rglob("*")
        if path.is_file()
    }
    assert on_disk == expected


def test_cada_sha256_del_catalogo_es_el_de_sus_bytes() -> None:
    for catalog in generar.catalogs().values():
        for entry in catalog["entradas"]:
            payload = generar.PAYLOADS[entry["id"]]
            assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
            assert entry["tamano_bytes"] == len(payload)
