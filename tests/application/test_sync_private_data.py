"""Tests unitarios del caso de uso de sincronización de datos privados (historia #295).
Almacén falso sobre la fixture sintética y zona local real sobre `tmp_path`."""

import json
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from parcel_intel.adapters.private_data import LocalPrivateDataArea
from parcel_intel.application.sync_private_data import (
    REASON_CONFLICT,
    REASON_LOCAL_RECORD_DIFFERS,
    REASON_PROVENANCE,
    REASON_STORE,
    SyncPrivateDataUseCase,
)
from parcel_intel.domain.private_catalog import (
    LocalFileState,
    PrivateCatalogError,
    SyncOutcome,
)
from parcel_intel.ports.errors import PrivateDataStoreError, PrivateDataUnavailable
from tests.application.fake_private_data_store import FakePrivateDataStore, asset_key
from tests.fixtures.private_data import generar
from tests.fixtures.private_data.generar import (
    AC_ID,
    CACHE_ID,
    HISTORICO,
    HISTORICO_TAG,
    PAYLOADS,
    SNAPSHOT_ID,
    TP_ID,
)

AC_ASSET = asset_key(HISTORICO_TAG, f"{AC_ID}.7z")
AC_PROVENANCE = f"procedencia/{HISTORICO}/{AC_ID}.procedencia.json"
AC_DESTINATION = "raw/catastro_historico/28043/AC.7z"


class SpyArea:
    """Zona local que falla si se usa: para probar que nada se toca."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _fail(self, name: str) -> AssertionError:
        self.calls.append(name)
        return AssertionError(f"no se debía tocar la zona local: {name}")

    def staging(self) -> AbstractContextManager[Path]:
        raise self._fail("staging")

    def file_state(self, path: Path) -> LocalFileState:
        raise self._fail("file_state")

    def read_small(self, path: Path) -> bytes:
        raise self._fail("read_small")

    def existing_state(self, destination: str) -> LocalFileState | None:
        raise self._fail("existing_state")

    def place(self, staged: Path, destination: str) -> None:
        raise self._fail("place")

    def read_record(self, entry_id: str) -> bytes | None:
        raise self._fail("read_record")

    def write_record(self, entry_id: str, content: bytes) -> None:
        raise self._fail("write_record")


def _outcomes(store: FakePrivateDataStore, root: Path) -> dict[str, tuple[SyncOutcome, str | None]]:
    summary = SyncPrivateDataUseCase(store, LocalPrivateDataArea(root)).execute()
    return {r.entry_id: (r.outcome, r.reason) for r in summary.results}


def test_catalogo_invalido_sale_antes_de_tocar_la_zona_local() -> None:
    store = FakePrivateDataStore(catalog_overrides={f"{HISTORICO}.json": b"{}"})
    area = SpyArea()
    with pytest.raises(PrivateCatalogError):
        SyncPrivateDataUseCase(store, area).execute()
    assert area.calls == []
    assert store.downloads == []


@pytest.mark.parametrize(
    "error", [PrivateDataUnavailable(), PrivateDataStoreError("x")], ids=["sin_acceso", "fallo"]
)
def test_errores_al_leer_los_catalogos_salen_tal_cual(error: Exception) -> None:
    store = FakePrivateDataStore(catalog_error=error)
    area = SpyArea()
    with pytest.raises(type(error)):
        SyncPrivateDataUseCase(store, area).execute()
    assert area.calls == []


def test_fallo_al_bajar_la_procedencia_no_coloca_el_fichero(tmp_path: Path) -> None:
    store = FakePrivateDataStore(failing={AC_PROVENANCE})
    outcomes = _outcomes(store, tmp_path)
    assert outcomes[AC_ID] == (SyncOutcome.FAILED, REASON_STORE)
    assert not (tmp_path / AC_DESTINATION).exists()
    assert outcomes[TP_ID] == (SyncOutcome.DOWNLOADED, None)


def test_procedencia_con_referencia_catastral_es_fallo_de_la_entrada(tmp_path: Path) -> None:
    store = FakePrivateDataStore(replaced_bytes={AC_PROVENANCE: b'{"nota": "99999A00109101"}'})
    outcomes = _outcomes(store, tmp_path)
    assert outcomes[AC_ID] == (SyncOutcome.FAILED, REASON_PROVENANCE)
    assert not (tmp_path / AC_DESTINATION).exists()
    assert not (tmp_path / "privados" / f"{AC_ID}.json").exists()


def test_sesion_caducada_a_mitad_es_fallo_de_la_entrada(tmp_path: Path) -> None:
    class Expiring(FakePrivateDataStore):
        def download(self, location: object, target: Path) -> None:
            raise PrivateDataUnavailable()

    outcomes = _outcomes(Expiring(), tmp_path)
    assert {o for o, _ in outcomes.values() if o is not SyncOutcome.SKIPPED} == {SyncOutcome.FAILED}
    assert {r for o, r in outcomes.values() if o is SyncOutcome.FAILED} == {REASON_STORE}


def test_registro_local_distinto_es_fallo(tmp_path: Path) -> None:
    record = tmp_path / "privados" / f"{AC_ID}.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"entrada": {"id": "otra"}}))
    store = FakePrivateDataStore()
    outcomes = _outcomes(store, tmp_path)
    assert outcomes[AC_ID] == (SyncOutcome.FAILED, REASON_LOCAL_RECORD_DIFFERS)
    assert AC_ASSET not in store.downloads
    assert not (tmp_path / AC_DESTINATION).exists()


def test_registro_presente_y_fichero_borrado_se_vuelve_a_bajar(tmp_path: Path) -> None:
    store = FakePrivateDataStore()
    _outcomes(store, tmp_path)
    record_before = (tmp_path / "privados" / f"{AC_ID}.json").read_bytes()
    (tmp_path / AC_DESTINATION).unlink()
    store.downloads.clear()
    outcomes = _outcomes(store, tmp_path)
    assert outcomes[AC_ID] == (SyncOutcome.DOWNLOADED, None)
    assert (tmp_path / AC_DESTINATION).read_bytes() == PAYLOADS[AC_ID]
    assert (tmp_path / "privados" / f"{AC_ID}.json").read_bytes() == record_before
    assert store.downloads == [AC_ASSET, AC_PROVENANCE]


def test_destino_ocupado_por_un_directorio_es_conflicto(tmp_path: Path) -> None:
    (tmp_path / AC_DESTINATION).mkdir(parents=True)
    outcomes = _outcomes(FakePrivateDataStore(), tmp_path)
    assert outcomes[AC_ID] == (SyncOutcome.FAILED, REASON_CONFLICT)


def test_filtro_por_fuente(tmp_path: Path) -> None:
    store = FakePrivateDataStore()
    summary = SyncPrivateDataUseCase(store, LocalPrivateDataArea(tmp_path)).execute(
        frozenset({"catastro_inspire"})
    )
    outcomes = {r.entry_id: r.outcome for r in summary.results}
    assert outcomes[CACHE_ID] is SyncOutcome.DOWNLOADED
    assert outcomes[SNAPSHOT_ID] is SyncOutcome.DOWNLOADED
    assert outcomes[AC_ID] is SyncOutcome.SKIPPED
    assert outcomes[TP_ID] is SyncOutcome.SKIPPED
    assert summary.downloaded == 2


def test_dos_catalogos_y_dos_entradas_por_catalogo(tmp_path: Path) -> None:
    """Lección de 0002 (N ≥ 2): todas las entradas vigentes de los dos catálogos."""
    outcomes = _outcomes(FakePrivateDataStore(), tmp_path)
    downloaded = sorted(k for k, (o, _) in outcomes.items() if o is SyncOutcome.DOWNLOADED)
    assert downloaded == sorted([AC_ID, TP_ID, CACHE_ID, SNAPSHOT_ID])
    assert len(generar.catalogs()) == 2
