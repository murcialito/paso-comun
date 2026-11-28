"""Tests del almacén sobre `gh` (historia #295) con el ejecutor falso. Ninguno lanza `gh`."""

import subprocess
import sys
from pathlib import Path

import pytest

from parcel_intel.adapters.private_data import CommandResult, GhCliPrivateDataStore, run_command
from parcel_intel.domain.private_catalog import GitFile, ReleaseAsset
from parcel_intel.ports.errors import PrivateDataStoreError
from tests.application.fake_private_data_store import FakeGhRunner
from tests.fixtures.private_data.generar import AC_ID, HISTORICO_TAG, TP_GIT_PATH

REPO = "propietario-sintetico/repo-sintetico"


def _store(runner: FakeGhRunner) -> GhCliPrivateDataStore:
    return GhCliPrivateDataStore(REPO, runner=runner, which=lambda name: "/usr/bin/gh")


@pytest.mark.parametrize(
    ("location", "key"),
    [
        (ReleaseAsset(tag=HISTORICO_TAG, asset=f"{AC_ID}.7z"), f"{HISTORICO_TAG}/{AC_ID}.7z"),
        (GitFile(path=TP_GIT_PATH), TP_GIT_PATH),
    ],
)
def test_fallo_de_descarga_no_deja_nada_a_medias(
    tmp_path: Path, location: ReleaseAsset | GitFile, key: str
) -> None:
    class Partial(FakeGhRunner):
        def _release(self, argv: tuple[str, ...]) -> CommandResult:
            Path(argv[9]).write_bytes(b"a medias")
            return CommandResult(1, b"", f"error en {REPO}".encode())

    runner = Partial(REPO, failures={TP_GIT_PATH: CommandResult(1, b"", b"HTTP 500")})
    target = tmp_path / "objeto"
    with pytest.raises(PrivateDataStoreError) as raised:
        _store(runner).download(location, target)
    assert not target.exists()
    assert REPO not in str(raised.value)


def test_tiempo_agotado_en_una_descarga(tmp_path: Path) -> None:
    runner = FakeGhRunner(REPO, raises={TP_GIT_PATH: subprocess.TimeoutExpired(["gh"], 120)})
    target = tmp_path / "objeto"
    with pytest.raises(PrivateDataStoreError):
        _store(runner).download(GitFile(path=TP_GIT_PATH), target)
    assert not target.exists()


def test_ruta_de_git_con_caracteres_especiales_va_codificada(tmp_path: Path) -> None:
    runner = FakeGhRunner(REPO)
    with pytest.raises(PrivateDataStoreError):
        _store(runner).download(GitFile(path="derivados/a b/c#d.txt"), tmp_path / "x")
    assert runner.calls[0].args[-1] == f"repos/{REPO}/contents/derivados/a%20b/c%23d.txt"


def test_run_command_sin_shell_captura_la_salida_de_error(tmp_path: Path) -> None:
    script = "import sys; sys.stdout.write('salida'); sys.stderr.write('error'); sys.exit(3)"
    result = run_command([sys.executable, "-I", "-c", script], timeout=30, stdout=None)
    assert result == CommandResult(3, b"salida", b"error")
    target = tmp_path / "volcado"
    with target.open("xb") as handle:
        result = run_command([sys.executable, "-I", "-c", script], timeout=30, stdout=handle)
    assert target.read_bytes() == b"salida"
    assert result.stdout == b"" and result.stderr == b"error"
