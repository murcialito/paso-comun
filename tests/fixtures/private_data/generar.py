"""Generador de las fixtures SINTÉTICAS del repo privado de datos (historia #295).

Escribe, bajo `tests/fixtures/private_data/`:
- `repo/catalogo/<fuente>.json`: dos catálogos con dos entradas vigentes cada uno (una
  `release` y una `git`) y, en `catastro_historico`, una entrada sustituida;
- `repo/procedencia/<fuente>/<id>.procedencia.json` y `repo/derivados/...`: los ficheros
  que en el repo privado viven en git;
- `adjuntos/<etiqueta>/<adjunto>`: los bytes de los adjuntos de release.

Los sha256 y tamaños del catálogo se **calculan** aquí a partir de los bytes; nada se copia a
mano. Todo es inventado: año 2099, sin referencias catastrales ni nombres. Para regenerar:
`uv run python -m tests.fixtures.private_data.generar` (un test comprueba que lo versionado
coincide con lo generado).
"""

import hashlib
import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent
REPO_DIR = FIXTURE_DIR / "repo"
ASSETS_DIR = FIXTURE_DIR / "adjuntos"

HISTORICO = "catastro_historico"
INSPIRE = "catastro_inspire"

AC_ID = "catastro-historico-28043-ac-20990101"
TP_ID = "catastro-historico-28043-tp-20990101"
OLD_AC_ID = "catastro-historico-28043-ac-20980101"
CACHE_ID = "catastro-inspire-28043-cache-20990101"
SNAPSHOT_ID = "catastro-inspire-28043-candidatos-20990102"

HISTORICO_TAG = "catastro-historico-28043-20990101"
OLD_HISTORICO_TAG = "catastro-historico-28043-20980101"
INSPIRE_TAG = "catastro-inspire-28043-20990101"

RESTRICTIONS = ["solo uso interno", "fixture sintética: no es un dato real"]
LICENSE = "Licencia sintética de prueba (historia #295)"

PAYLOADS: dict[str, bytes] = {
    AC_ID: b"7z\xbc\xaf\x27\x1c sintetico avance catastral 2099\x00\x01\x02",
    OLD_AC_ID: b"7z\xbc\xaf\x27\x1c sintetico avance catastral 2098 (sustituido)",
    TP_ID: b"texto sintetico de la entrada tp de 2099\n",
    CACHE_ID: b"\x1f\x8b sintetico cache de catastro inspire 2099",
    SNAPSHOT_ID: b'{"type": "FeatureCollection", "features": []}\n',
}

TP_GIT_PATH = "derivados/catastro_historico/28043/2099-01-01-tp.txt"
SNAPSHOT_GIT_PATH = "derivados/candidatos/28043/2099-01-02-candidatos.geojson"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def provenance_path(source: str, entry_id: str) -> str:
    return f"procedencia/{source}/{entry_id}.procedencia.json"


def _entry(
    entry_id: str,
    source: str,
    storage: dict[str, str],
    destination: str,
    *,
    replaced_by: str | None = None,
) -> dict[str, Any]:
    payload = PAYLOADS[entry_id]
    entry: dict[str, Any] = {
        "id": entry_id,
        "fuente": source,
        "producto": "Producto sintético",
        "municipio_ine": "28043",
        "url": "https://example.invalid/sintetico",
        "fecha_descarga": "2099-01-01",
        "licencia": LICENSE,
        "publicable": False,
        "restricciones_uso": list(RESTRICTIONS),
        "sha256": sha256(payload),
        "tamano_bytes": len(payload),
        "almacen": storage,
        "formato": "fichero",
        "destino": destination,
        "procedencia": provenance_path(source, entry_id),
        "sustituido_por": replaced_by,
    }
    return entry


def catalogs() -> dict[str, dict[str, Any]]:
    """Los dos catálogos, por nombre de fichero."""
    return {
        f"{HISTORICO}.json": {
            "esquema": 1,
            "fuente": HISTORICO,
            "entradas": [
                _entry(
                    OLD_AC_ID,
                    HISTORICO,
                    {
                        "tipo": "release",
                        "etiqueta": OLD_HISTORICO_TAG,
                        "adjunto": f"{OLD_AC_ID}.7z",
                    },
                    "raw/catastro_historico/28043/AC.7z",
                    replaced_by=AC_ID,
                ),
                _entry(
                    AC_ID,
                    HISTORICO,
                    {"tipo": "release", "etiqueta": HISTORICO_TAG, "adjunto": f"{AC_ID}.7z"},
                    "raw/catastro_historico/28043/AC.7z",
                ),
                _entry(
                    TP_ID,
                    HISTORICO,
                    {"tipo": "git", "ruta": TP_GIT_PATH},
                    "raw/catastro_historico/28043/TP.txt",
                ),
            ],
        },
        f"{INSPIRE}.json": {
            "esquema": 1,
            "fuente": INSPIRE,
            "entradas": [
                _entry(
                    CACHE_ID,
                    INSPIRE,
                    {"tipo": "release", "etiqueta": INSPIRE_TAG, "adjunto": f"{CACHE_ID}.tar.gz"},
                    "raw/catastro/28043/cache.tar.gz",
                ),
                _entry(
                    SNAPSHOT_ID,
                    INSPIRE,
                    {"tipo": "git", "ruta": SNAPSHOT_GIT_PATH},
                    "derived/candidatos/interno/28043.geojson",
                ),
            ],
        },
    }


def provenance(entry_id: str, source: str) -> dict[str, Any]:
    return {
        "fuente": source,
        "url": "https://example.invalid/sintetico",
        "fecha_descarga": "2099-01-01",
        "licencia": LICENSE,
        "nota": f"procedencia sintética de {entry_id}",
    }


def to_json(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def files() -> dict[Path, bytes]:
    """Todos los ficheros de la fixture, con su ruta absoluta."""
    out: dict[Path, bytes] = {}
    for name, catalog in catalogs().items():
        out[REPO_DIR / "catalogo" / name] = to_json(catalog)
        entries = catalog["entradas"]
        for entry in entries:
            entry_id = str(entry["id"])
            source = str(entry["fuente"])
            out[REPO_DIR / provenance_path(source, entry_id)] = to_json(
                provenance(entry_id, source)
            )
            storage = entry["almacen"]
            if storage["tipo"] == "release":
                out[ASSETS_DIR / storage["etiqueta"] / storage["adjunto"]] = PAYLOADS[entry_id]
            else:
                out[REPO_DIR / storage["ruta"]] = PAYLOADS[entry_id]
    return out


def main() -> None:
    for path, content in files().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


if __name__ == "__main__":
    main()
