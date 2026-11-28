"""CA10 de la historia #295: descarga real de la entrada de prueba del repo privado.

**Solo lo pasa el fundador, a mano, desde su terminal** (ADR 0055 §5: ningún agente usa
`gh` contra el repo privado; ADR 0059). Marcado `integration` y `fuera_de_ci`, y además se
salta si está definida cualquier variable de la guarda (`CI`, `GITHUB_ACTIONS`,
`CLAUDECODE`) o si falta `PASO_COMUN_DATOS_PRIVADOS=1`:

    PASO_COMUN_DATOS_PRIVADOS=1 uv run pytest -m integration \
        tests/adapters/private_data/test_gh_store_integration.py

Necesita en el repo privado una entrada `prueba-sincronizacion` de pocos bytes (#296).
"""

import hashlib
import os
from pathlib import Path

import pytest

from parcel_intel.adapters.private_data import GhCliPrivateDataStore
from parcel_intel.cli.main import PRIVATE_DATA_ENV_GUARD, PRIVATE_DATA_REPO
from parcel_intel.domain.private_catalog import parse_catalog, validate_catalog_set

TEST_ENTRY_ID = "prueba-sincronizacion"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.fuera_de_ci,
    pytest.mark.skipif(
        any(name in os.environ for name in PRIVATE_DATA_ENV_GUARD)
        or os.environ.get("PASO_COMUN_DATOS_PRIVADOS") != "1",
        reason="solo a mano, por el fundador: PASO_COMUN_DATOS_PRIVADOS=1 y fuera de CI",
    ),
]


def test_descarga_la_entrada_de_prueba_y_comprueba_su_sha256(tmp_path: Path) -> None:
    store = GhCliPrivateDataStore(PRIVATE_DATA_REPO)
    entries = validate_catalog_set([parse_catalog(doc) for doc in store.read_catalogs()])
    entry = next(entry for entry in entries if entry.entry_id == TEST_ENTRY_ID)
    target = tmp_path / "prueba"
    store.download(entry.storage, target)
    data = target.read_bytes()
    assert len(data) == entry.size_bytes
    assert hashlib.sha256(data).hexdigest() == entry.sha256
