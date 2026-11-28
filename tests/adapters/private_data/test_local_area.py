"""Tests de la zona local `data/` de los datos privados (historia #295)."""

import hashlib
import os
from pathlib import Path

import pytest

from parcel_intel.adapters.private_data import LocalPrivateDataArea
from parcel_intel.adapters.private_data import local_area as module
from parcel_intel.domain.private_catalog import LocalFileState
from parcel_intel.ports.errors import PrivateDataConflictError, PrivateDataStoreError


def _staged(area: LocalPrivateDataArea, content: bytes, destination: str) -> None:
    with area.staging() as staging:
        path = staging / "objeto"
        path.write_bytes(content)
        area.place(path, destination)


def test_coloca_sin_sobrescribir(tmp_path: Path) -> None:
    area = LocalPrivateDataArea(tmp_path)
    _staged(area, b"uno", "raw/a/b.bin")
    assert (tmp_path / "raw/a/b.bin").read_bytes() == b"uno"
    with pytest.raises(PrivateDataConflictError):
        _staged(area, b"dos", "raw/a/b.bin")
    assert (tmp_path / "raw/a/b.bin").read_bytes() == b"uno"
    assert not (tmp_path / "privados" / ".preparacion").exists()


def test_sin_enlaces_fisicos_copia_y_renombra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_hardlinks(source: object, target: object) -> None:
        raise OSError("sistema de ficheros sin enlaces físicos")

    monkeypatch.setattr(os, "link", no_hardlinks)
    area = LocalPrivateDataArea(tmp_path)
    _staged(area, b"uno", "raw/a/b.bin")
    assert (tmp_path / "raw/a/b.bin").read_bytes() == b"uno"
    with pytest.raises(PrivateDataConflictError):
        _staged(area, b"dos", "raw/a/b.bin")
    assert (tmp_path / "raw/a/b.bin").read_bytes() == b"uno"
    assert sorted(p.name for p in (tmp_path / "raw/a").iterdir()) == ["b.bin"]
    area.write_record("x", b"registro")
    assert (tmp_path / "privados" / "x.json").read_bytes() == b"registro"


def test_la_zona_de_preparacion_se_borra_aunque_falle(tmp_path: Path) -> None:
    area = LocalPrivateDataArea(tmp_path)
    with pytest.raises(RuntimeError), area.staging() as staging:
        (staging / "a").write_bytes(b"x")
        raise RuntimeError("fallo")
    assert not (tmp_path / "privados" / ".preparacion").exists()


def test_estado_de_un_fichero(tmp_path: Path) -> None:
    area = LocalPrivateDataArea(tmp_path)
    assert area.existing_state("raw/a/b.bin") is None
    (tmp_path / "raw/a").mkdir(parents=True)
    (tmp_path / "raw/a/b.bin").write_bytes(b"hola")
    assert area.existing_state("raw/a/b.bin") == LocalFileState(
        sha256=hashlib.sha256(b"hola").hexdigest(), size_bytes=4
    )


def test_enlace_simbolico_en_el_camino_es_conflicto(tmp_path: Path) -> None:
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "b.bin").write_bytes(b"x")
    data = tmp_path / "data"
    (data / "raw").mkdir(parents=True)
    os.symlink(outside, data / "raw" / "a")
    area = LocalPrivateDataArea(data)
    with pytest.raises(PrivateDataConflictError):
        area.existing_state("raw/a/b.bin")
    os.symlink(outside / "b.bin", data / "raw" / "c.bin")
    with pytest.raises(PrivateDataConflictError):
        area.existing_state("raw/c.bin")


def test_registro_idempotente_y_sin_sobrescribir(tmp_path: Path) -> None:
    area = LocalPrivateDataArea(tmp_path)
    assert area.read_record("x") is None
    area.write_record("x", b"uno")
    area.write_record("x", b"uno")
    with pytest.raises(PrivateDataConflictError):
        area.write_record("x", b"dos")
    assert area.read_record("x") == b"uno"
    assert sorted(p.name for p in (tmp_path / "privados").iterdir()) == ["x.json"]


def test_read_small_con_tope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = LocalPrivateDataArea(tmp_path)
    path = tmp_path / "p.json"
    path.write_bytes(b"12345")
    assert area.read_small(path) == b"12345"
    monkeypatch.setattr(module, "SMALL_FILE_LIMIT_BYTES", 4)
    with pytest.raises(PrivateDataStoreError):
        area.read_small(path)


def test_el_constructor_no_crea_nada(tmp_path: Path) -> None:
    LocalPrivateDataArea(tmp_path / "data")
    assert not (tmp_path / "data").exists()
