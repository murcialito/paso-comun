"""Tests de aceptación de la historia #295: `datos-privados sincronizar`, la sincronización
de bajada del repo privado de datos (ADR 0055 §3 y §5, ADR 0059).

Fixtures SINTÉTICAS (`tests/fixtures/private_data/`, ver su `LEEME.md`): año 2099, sin
referencias catastrales ni nombres. Ningún test lanza `gh` ni toca la red: el almacén es
`FakePrivateDataStore` o el adapter real con el ejecutor falso `FakeGhRunner`, y la zona
local es la real (`LocalPrivateDataArea`) sobre `tmp_path`.

Adaptaciones respecto a la issue (anotadas en la PR):
- El ADR que propone el diseño como «ADR 0057» es el **ADR 0059**: el 0057 lo ocupó #313
  mientras esta historia esperaba y el 0058, #102, con la PR abierta (lección de #313).
- CA10 no es un test de aceptación ejecutable (CI corre `-m "acceptance and …"`, que anula
  el `not integration`): aquí solo se comprueba estáticamente el test de integración, que
  pasa el fundador a mano.
- Las tres variables de la guarda (CA11) se borran en todos los tests salvo los de CA11: en
  CI `CI` está definida y todo saldría por la guarda.
"""

import ast
import copy
import hashlib
import inspect
import json
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from parcel_intel.adapters.private_data import (
    CommandResult,
    GhCliPrivateDataStore,
    LocalPrivateDataArea,
)
from parcel_intel.adapters.private_data.gh_store import RAW_ACCEPT
from parcel_intel.application.sync_private_data import SyncPrivateDataUseCase
from parcel_intel.cli import main
from parcel_intel.domain.private_catalog import (
    CatalogDocument,
    EntryLayout,
    GitFile,
    PrivateCatalogError,
    ReleaseAsset,
    parse_catalog,
)
from parcel_intel.domain.published_discrepancy import CADASTRAL_REFERENCE_RE
from parcel_intel.ports.errors import PrivateDataStoreError, PrivateDataUnavailable
from tests.application.fake_private_data_store import (
    FakeGhRunner,
    FakePrivateDataStore,
    asset_key,
    catalog_documents,
)
from tests.fixtures.private_data import generar
from tests.fixtures.private_data.generar import (
    AC_ID,
    CACHE_ID,
    HISTORICO,
    HISTORICO_TAG,
    INSPIRE,
    OLD_AC_ID,
    PAYLOADS,
    SNAPSHOT_ID,
    TP_GIT_PATH,
    TP_ID,
)

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[2]
REPO = main.PRIVATE_DATA_REPO
SENTINEL = "valor-secreto-rechazado"
FAKE_REFCAT = "99999A00109101"
CURRENT_IDS = (AC_ID, TP_ID, CACHE_ID, SNAPSHOT_ID)
AC_ASSET = asset_key(HISTORICO_TAG, f"{AC_ID}.7z")
HISTORICO_FILE = f"{HISTORICO}.json"
INSPIRE_FILE = f"{INSPIRE}.json"


def _destinations() -> dict[str, str]:
    out: dict[str, str] = {}
    for catalog in generar.catalogs().values():
        for entry in catalog["entradas"]:
            if entry["sustituido_por"] is None:
                out[str(entry["id"])] = str(entry["destino"])
    return out


DESTINATIONS = _destinations()


@pytest.fixture(autouse=True)
def _sin_guarda(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in main.PRIVATE_DATA_ENV_GUARD:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def store() -> FakePrivateDataStore:
    return FakePrivateDataStore()


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch, store: FakePrivateDataStore) -> list[Path]:
    """Sustituye el composition root por uno con el almacén falso; registra cada uso."""
    made: list[Path] = []

    def build(data_root: Path) -> SyncPrivateDataUseCase:
        made.append(data_root)
        return SyncPrivateDataUseCase(store=store, area=LocalPrivateDataArea(data_root))

    monkeypatch.setattr(main, "build_private_data_sync", build)
    return made


def _use_gh_store(
    monkeypatch: pytest.MonkeyPatch, runner: FakeGhRunner, which: bool = True
) -> None:
    def build(data_root: Path) -> SyncPrivateDataUseCase:
        gh = GhCliPrivateDataStore(
            REPO, runner=runner, which=lambda name: "/usr/bin/gh" if which else None
        )
        return SyncPrivateDataUseCase(store=gh, area=LocalPrivateDataArea(data_root))

    monkeypatch.setattr(main, "build_private_data_sync", build)


def _run(datos: Path, *extra: str) -> int:
    parsed = main._build_parser().parse_args(
        ["datos-privados", "sincronizar", "--datos", str(datos), *extra]
    )
    exit_code: int = parsed.func(parsed)
    return exit_code


def _tree(directory: Path) -> dict[str, str]:
    if not directory.exists():
        return {}
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(": ")
        out[key] = value
    return out


def _catalogs() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(generar.catalogs())


def _override(store: FakePrivateDataStore, catalogs: dict[str, dict[str, Any]]) -> None:
    store.catalog_overrides = {
        name: json.dumps(catalog, ensure_ascii=False).encode() for name, catalog in catalogs.items()
    }


def _entry(catalogs: dict[str, dict[str, Any]], entry_id: str) -> dict[str, Any]:
    for catalog in catalogs.values():
        entries: list[dict[str, Any]] = catalog["entradas"]
        for entry in entries:
            if entry["id"] == entry_id:
                return entry
    raise KeyError(entry_id)


# --- CA1 ---------------------------------------------------------------------------------


def test_ca1_descarga_cada_fichero_con_su_procedencia_y_resumen(
    tmp_path: Path, builds: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """CA1. Dado un catálogo válido (fixture) y un acceso simulado con ficheros cuyo sha256
    coincide, cuando se ejecuta el comando, entonces cada fichero queda en `data/raw/` con su
    procedencia y el resumen dice cuántos descargó, cuántos ya estaban y cuántos saltó."""
    datos = tmp_path / "data"
    assert _run(datos) == 0
    out = capsys.readouterr().out
    lines = _lines(out)
    for entry_id in CURRENT_IDS:
        assert (datos / DESTINATIONS[entry_id]).read_bytes() == PAYLOADS[entry_id]
        assert lines[entry_id] == "descargado"
        record = json.loads((datos / "privados" / f"{entry_id}.json").read_text("utf-8"))
        assert record["entrada"]["id"] == entry_id
        assert record["entrada"]["sha256"] == hashlib.sha256(PAYLOADS[entry_id]).hexdigest()
        assert record["procedencia"]["nota"] == f"procedencia sintética de {entry_id}"
        assert record["procedencia"]["licencia"]
    assert (datos / "raw" / "catastro_historico" / "28043" / "AC.7z").is_file()
    assert lines[OLD_AC_ID] == f"saltado (sustituida por {AC_ID})"
    assert lines["resumen"] == "4 descargados, 0 ya estaban, 1 saltados, 0 fallidos"
    assert not (datos / "privados" / ".preparacion").exists()
    assert builds == [datos]


# --- CA2 ---------------------------------------------------------------------------------


def test_ca2_segunda_ejecucion_idempotente(
    tmp_path: Path,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA2. Dado un fichero ya cacheado con el mismo sha256, cuando se ejecuta de nuevo,
    entonces no lo descarga y el resultado es idéntico (idempotente)."""
    datos = tmp_path / "data"
    assert _run(datos) == 0
    before = _tree(datos)
    capsys.readouterr()
    store.downloads.clear()
    assert _run(datos) == 0
    out = capsys.readouterr().out
    assert store.downloads == []
    assert _tree(datos) == before
    lines = _lines(out)
    for entry_id in CURRENT_IDS:
        assert lines[entry_id] == "ya estaba"
    assert lines["resumen"] == "0 descargados, 4 ya estaban, 1 saltados, 0 fallidos"


# --- CA3 ---------------------------------------------------------------------------------


def test_ca3_fichero_local_distinto_no_se_sustituye(
    tmp_path: Path,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA3. Dado un fichero ya cacheado con un sha256 distinto al del catálogo, cuando se
    ejecuta, entonces falla con error claro, no lo sustituye y sale con 1 (inmutabilidad)."""
    datos = tmp_path / "data"
    local = datos / DESTINATIONS[AC_ID]
    local.parent.mkdir(parents=True)
    local.write_bytes(b"otro contenido local")
    assert _run(datos) == 1
    out = capsys.readouterr().out
    assert _lines(out)[AC_ID] == "fallo (el fichero local no es el del catálogo)"
    assert local.read_bytes() == b"otro contenido local"
    assert AC_ASSET not in store.downloads
    assert not (datos / "privados" / f"{AC_ID}.json").exists()
    assert _lines(out)["resumen"] == "3 descargados, 0 ya estaban, 1 saltados, 1 fallidos"


# --- CA4 ---------------------------------------------------------------------------------


def test_ca4_descarga_con_sha256_distinto_no_queda_en_la_cache(
    tmp_path: Path,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA4. Dado un fichero descargado cuyo sha256 no coincide con el del catálogo, cuando se
    ejecuta, entonces no lo deja en la caché, falla y sale con 1."""
    datos = tmp_path / "data"
    store.replaced_bytes[AC_ASSET] = b"bytes que no son los del catalogo"
    assert _run(datos) == 1
    out = capsys.readouterr().out
    assert _lines(out)[AC_ID] == "fallo (el fichero descargado no es el del catálogo)"
    assert not (datos / DESTINATIONS[AC_ID]).exists()
    assert not (datos / "privados" / f"{AC_ID}.json").exists()
    assert not (datos / "privados" / ".preparacion").exists()
    for path in datos.rglob("*"):
        if path.is_file():
            assert path.read_bytes() != b"bytes que no son los del catalogo"


# --- CA5 ---------------------------------------------------------------------------------


def _no_access_runner(stderr: bytes) -> FakeGhRunner:
    return FakeGhRunner(REPO, failures={"catalogo": CommandResult(1, b"", stderr)})


@pytest.mark.parametrize(
    "case",
    ["doble", "sin_gh", "gh_no_se_lanza", "sin_sesion", "sin_permisos", "no_existe"],
)
def test_ca5_sin_acceso_sale_con_0_y_un_mensaje_generico(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA5. Dado que no hay acceso al repo privado (sin `gh`, sin sesión o sin permisos),
    cuando se ejecuta, entonces sale con 0, escribe un único mensaje genérico («sin acceso al
    repo de datos privados; nada que hacer») y no escribe en logs ni stdout el nombre del
    repo, ni rutas, ni nombres de ficheros del catálogo."""
    leak = f"{REPO} catalogo/{HISTORICO_FILE} {SENTINEL}".encode()
    if case == "doble":
        store.unavailable = True
    elif case == "sin_gh":
        _use_gh_store(monkeypatch, FakeGhRunner(REPO), which=False)
    elif case == "gh_no_se_lanza":
        _use_gh_store(monkeypatch, FakeGhRunner(REPO, raises={"catalogo": FileNotFoundError("gh")}))
    elif case == "sin_sesion":
        _use_gh_store(
            monkeypatch,
            _no_access_runner(b"To get started with GitHub CLI, run: gh auth login " + leak),
        )
    elif case == "sin_permisos":
        _use_gh_store(monkeypatch, _no_access_runner(b"HTTP 403: Forbidden " + leak))
    else:
        _use_gh_store(monkeypatch, _no_access_runner(b"gh: Not Found (HTTP 404) " + leak))
    datos = tmp_path / "data"
    assert _run(datos) == 0
    captured = capsys.readouterr()
    assert captured.out == "sin acceso al repo de datos privados; nada que hacer\n"
    assert captured.err == ""
    for forbidden in (REPO, "paso-comun-datos", HISTORICO_FILE, "catalogo", SENTINEL, str(datos)):
        assert forbidden not in captured.out + captured.err
    assert not datos.exists()


# --- CA6 / CA12 --------------------------------------------------------------------------

Mutation = Callable[[dict[str, dict[str, Any]]], None]


def _set(entry_id: str, key: str, value: object) -> Mutation:
    def mutate(catalogs: dict[str, dict[str, Any]]) -> None:
        _entry(catalogs, entry_id)[key] = value

    return mutate


def _set_storage(entry_id: str, key: str, value: object) -> Mutation:
    def mutate(catalogs: dict[str, dict[str, Any]]) -> None:
        _entry(catalogs, entry_id)["almacen"][key] = value

    return mutate


def _delete(entry_id: str, key: str) -> Mutation:
    def mutate(catalogs: dict[str, dict[str, Any]]) -> None:
        del _entry(catalogs, entry_id)[key]

    return mutate


def _set_document(name: str, key: str, value: object) -> Mutation:
    def mutate(catalogs: dict[str, dict[str, Any]]) -> None:
        catalogs[name][key] = value

    return mutate


def _repeated_id(catalogs: dict[str, dict[str, Any]]) -> None:
    entry = _entry(catalogs, CACHE_ID)
    entry["id"] = AC_ID
    entry["almacen"]["adjunto"] = f"{AC_ID}.tar.gz"
    entry["procedencia"] = f"procedencia/{INSPIRE}/{AC_ID}.procedencia.json"


def _same_destination(catalogs: dict[str, dict[str, Any]]) -> None:
    _entry(catalogs, TP_ID)["destino"] = DESTINATIONS[AC_ID]


CA6_CASES: dict[str, Mutation] = {
    "campo_obligatorio_ausente": _delete(AC_ID, "licencia"),
    "sha256_mal_formado": _set(AC_ID, "sha256", SENTINEL),
    "ruta_fuera_de_data_raw": _set(AC_ID, "destino", f"raw/../../{SENTINEL}"),
    "propiedad_de_titularidad": _set(AC_ID, "titular", SENTINEL),
    "referencia_catastral": _set(AC_ID, "producto", f"{SENTINEL} {FAKE_REFCAT}"),
}

CA12_CASES: dict[str, Mutation] = {
    **CA6_CASES,
    "esquema_distinto_de_1": _set_document(HISTORICO_FILE, "esquema", 2),
    "esquema_true": _set_document(HISTORICO_FILE, "esquema", True),
    "clave_desconocida": _set(AC_ID, SENTINEL, SENTINEL),
    "clave_desconocida_en_el_documento": _set_document(HISTORICO_FILE, SENTINEL, SENTINEL),
    "publicable_true": _set(AC_ID, "publicable", True),
    "restricciones_uso_vacia": _set(AC_ID, "restricciones_uso", []),
    "fuente_de_la_entrada_distinta": _set(AC_ID, "fuente", SENTINEL),
    "fuente_del_catalogo_distinta_del_nombre": _set_document(HISTORICO_FILE, "fuente", SENTINEL),
    "id_repetido_entre_catalogos": _repeated_id,
    "sustituido_por_inexistente": _set(TP_ID, "sustituido_por", f"{SENTINEL}-x"),
    "sustituido_por_a_si_misma": _set(TP_ID, "sustituido_por", TP_ID),
    "dos_vigentes_mismo_destino": _same_destination,
    "destino_absoluto": _set(AC_ID, "destino", f"/{SENTINEL}/AC.7z"),
    "destino_con_puntos": _set(AC_ID, "destino", f"raw/../{SENTINEL}"),
    "destino_con_barra_invertida": _set(AC_ID, "destino", f"raw\\{SENTINEL}"),
    "destino_vacio": _set(AC_ID, "destino", ""),
    "destino_fuera_de_raw_y_derived": _set(AC_ID, "destino", f"otros/{SENTINEL}"),
    "procedencia_fuera_de_procedencia": _set(AC_ID, "procedencia", f"otros/{SENTINEL}.json"),
    "git_fuera_de_derivados": _set_storage(TP_ID, "ruta", f"otros/{SENTINEL}"),
    "adjunto_que_no_es_el_id": _set_storage(AC_ID, "adjunto", f"{SENTINEL}.7z"),
    "tamano_negativo": _set(AC_ID, "tamano_bytes", -1),
    "tamano_no_entero": _set(AC_ID, "tamano_bytes", 1.5),
    "tamano_true": _set(AC_ID, "tamano_bytes", True),
    "referencia_catastral_en_lista": _set(AC_ID, "restricciones_uso", [FAKE_REFCAT]),
    "referencia_catastral_en_url": _set(AC_ID, "url", f"https://x.invalid/{FAKE_REFCAT}"),
    "referencia_catastral_en_licencia": _set(AC_ID, "licencia", f"{SENTINEL} {FAKE_REFCAT}"),
}

_DOCUMENT_LEVEL_CASES = {
    "esquema_distinto_de_1",
    "esquema_true",
    "clave_desconocida_en_el_documento",
    "fuente_del_catalogo_distinta_del_nombre",
}


def _assert_rejected_before_anything(
    name: str,
    mutation: Mutation,
    tmp_path: Path,
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> str:
    catalogs = _catalogs()
    mutation(catalogs)
    _override(store, catalogs)
    datos = tmp_path / "data"
    assert _run(datos) == 1, name
    captured = capsys.readouterr()
    assert store.downloads == [], name
    assert store.calls == ["read_catalogs"], name
    assert not datos.exists(), name
    assert captured.out == "", name
    assert captured.err.startswith("catálogo no válido: "), name
    assert SENTINEL not in captured.out + captured.err, name
    assert not CADASTRAL_REFERENCE_RE.search(captured.out + captured.err), name
    return captured.err


@pytest.mark.parametrize("name", sorted(CA6_CASES))
def test_ca6_entrada_invalida_falla_antes_de_descargar(
    name: str,
    tmp_path: Path,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA6. Dado un catálogo con una entrada inválida (campo obligatorio ausente, sha256 mal
    formado, ruta que sale de `data/raw/`, o propiedad de titularidad o referencia
    catastral) , cuando se ejecuta, entonces falla antes de descargar nada, con todo o
    nada."""
    err = _assert_rejected_before_anything(name, CA6_CASES[name], tmp_path, store, capsys)
    assert HISTORICO_FILE in err


@pytest.mark.parametrize("name", sorted(CA12_CASES))
def test_ca12_cada_caso_invalido_sin_valores_en_el_mensaje(
    name: str,
    tmp_path: Path,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA12. Dado cada caso inválido de CA6 y además: `esquema` distinto de 1, clave
    desconocida, `publicable: true`, `restricciones_uso` vacía, `fuente` de la entrada
    distinta de la del catálogo o del nombre del fichero, `id` repetido entre dos catálogos,
    `sustituido_por` que no existe o que apunta a sí mismo, dos entradas vigentes con el mismo
    `destino`, `destino` absoluto, con `..`, con `\\`, vacío o fuera de `raw/` y `derived/`,
    `procedencia` fuera de `procedencia/`, `almacen` de tipo `git` con `ruta` fuera de
    `derivados/`, `adjunto` que no es el `id` más una extensión, `tamano_bytes` negativo o no
    entero (`true` no vale como entero), y una referencia catastral (forma de
    `CADASTRAL_REFERENCE_RE`) en cualquier valor, cuando se ejecuta, entonces sale con 1 sin
    llamar a `download` ni crear nada en `--datos`, y el mensaje nombra solo el fichero de
    catálogo, la posición y el campo: un valor centinela (`valor-secreto-rechazado`) puesto en
    el campo rechazado no aparece en stdout ni en stderr (lección de #168). Las claves
    desconocidas se cuentan sin nombrarlas."""
    err = _assert_rejected_before_anything(name, CA12_CASES[name], tmp_path, store, capsys)
    assert HISTORICO_FILE in err or INSPIRE_FILE in err
    if name.startswith("clave_desconocida"):
        assert "1 clave(s) desconocida(s)" in err
    elif name not in _DOCUMENT_LEVEL_CASES:
        assert re.search(r"entradas\[\d+\]\.[a-z_<]", err), err


# --- CA7 ---------------------------------------------------------------------------------

_FORBIDDEN_CODE = ("os.environ[", "os.getenv", "shell=True", "env=")


def test_ca7_sin_credenciales_y_sin_workflows(builds: list[Path]) -> None:
    """CA7. Dado el código, cuando se revisa, entonces no lee, guarda ni imprime credenciales
    o tokens (la autenticación la hace `gh` del usuario) y el comando no se invoca desde
    ningún workflow (test que lo comprueba sobre `.github/workflows/`)."""
    github = [ROOT / ".github" / "workflows", ROOT / ".github" / "scripts"]
    scanned = 0
    for directory in github:
        for path in directory.rglob("*"):
            if path.is_file():
                text = path.read_text("utf-8", errors="replace")
                scanned += 1
                assert "datos-privados" not in text, path
                assert "paso-comun-datos" not in text, path
    assert scanned > 5
    adapters = sorted((ROOT / "src/parcel_intel/adapters/private_data").glob("*.py"))
    assert len(adapters) == 3
    for path in adapters:
        text = path.read_text("utf-8")
        assert "token" not in text.lower(), path
        for forbidden in _FORBIDDEN_CODE:
            assert forbidden not in text, (path, forbidden)
    handler = "".join(
        inspect.getsource(function)
        for function in (
            main._datos_privados_sincronizar,
            main._private_data_guard_active,
            main._print_sync_summary,
        )
    )
    assert "token" not in handler.lower()
    for forbidden in _FORBIDDEN_CODE:
        assert forbidden not in handler
    # El composition root real tampoco: se lee del fichero, no de `main` (sustituido aquí).
    tree = ast.parse((ROOT / "src/parcel_intel/cli/main.py").read_text("utf-8"))
    build = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_private_data_sync"
    )
    build_text = ast.unparse(build)
    assert "token" not in build_text.lower()
    for forbidden in _FORBIDDEN_CODE:
        assert forbidden not in build_text
    application = (ROOT / "src/parcel_intel/application/sync_private_data.py").read_text("utf-8")
    assert "token" not in application.lower()


# --- CA8 ---------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_ca8_capas_hexagonales() -> None:
    """CA8. Dado `lint-imports`, `mypy` y `ruff`, cuando se ejecutan, entonces pasan: el port
    solo importa `domain`, el caso de uso solo `ports` y `domain`, y el adapter se instancia
    solo en `cli/`."""
    src = ROOT / "src/parcel_intel"
    domain = _imports(src / "domain/private_catalog.py")
    assert {m for m in domain if m.startswith("parcel_intel")} <= {
        "parcel_intel.domain.published_discrepancy"
    }
    for port in ("ports/private_data_store.py", "ports/private_data_area.py"):
        own = {m for m in _imports(src / port) if m.startswith("parcel_intel")}
        assert own and all(m.startswith("parcel_intel.domain") for m in own), port
    use_case = {m for m in _imports(src / "application/sync_private_data.py")}
    own = {m for m in use_case if m.startswith("parcel_intel")}
    assert all(m.startswith(("parcel_intel.domain", "parcel_intel.ports")) for m in own)
    assert not any(m.startswith("parcel_intel.adapters") for m in use_case)
    users = sorted(
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if not str(path.relative_to(src)).startswith("adapters/private_data")
        and re.search(r"\b(GhCliPrivateDataStore|LocalPrivateDataArea)\(", path.read_text("utf-8"))
    )
    assert users == ["cli/main.py"]
    result = subprocess.run(
        ["uv", "run", "lint-imports"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- CA9 ---------------------------------------------------------------------------------


def test_ca9_claude_md_documenta_el_comando() -> None:
    """CA9. Dado `CLAUDE.md`, cuando se lee, entonces documenta el comando en «Comandos» con
    sus opciones y las garantías de arriba."""
    text = (ROOT / "CLAUDE.md").read_text("utf-8")
    commands = text[text.index("## Comandos") : text.index("## Contexto de negocio")]
    assert "uv run python -m parcel_intel.cli.main datos-privados sincronizar" in commands
    for fragment in (
        "--datos data",
        "--fuente",
        "sin acceso al repo de datos privados; nada que hacer",
        "`CI`",
        "`GITHUB_ACTIONS`",
        "`CLAUDECODE`",
        "sha256",
        "data/privados/<id>.json",
        "no sobrescribe",
        "ningún agente",
        "ningún workflow",
        "2.28",
        "#320",
    ):
        assert fragment in commands, fragment


# --- CA10 --------------------------------------------------------------------------------


def test_ca10_test_de_integracion_solo_a_mano() -> None:
    """CA10. [integración] Dado el repo privado real y el `gh auth` del fundador, cuando se
    ejecuta, entonces descarga un fichero pequeño de prueba y verifica su sha256. Se corre
    solo a mano, marcado `integration`."""
    path = ROOT / "tests/adapters/private_data/test_gh_store_integration.py"
    text = path.read_text("utf-8")
    assert "pytest.mark.integration" in text
    assert "pytest.mark.fuera_de_ci" in text
    assert "pytest.mark.skipif" in text
    assert "PASO_COMUN_DATOS_PRIVADOS" in text
    assert "PRIVATE_DATA_ENV_GUARD" in text
    assert '"prueba-sincronizacion"' in text
    assert "sha256" in text


# --- CA11 --------------------------------------------------------------------------------


@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS", "CLAUDECODE"])
@pytest.mark.parametrize("value", ["", "true"])
def test_ca11_guarda_de_entorno(
    variable: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA11. Dado que `CI`, `GITHUB_ACTIONS` o `CLAUDECODE` están definidos en el entorno
    (cualquier valor, también vacío), cuando se ejecuta el comando, entonces no llama a `gh`
    ni a ningún método del store (el doble lo registra), no crea nada en `--datos`, escribe
    solo «la sincronización de datos privados solo la lanza el fundador desde su terminal» y
    sale con 1. Un test por variable."""
    for name in main.PRIVATE_DATA_ENV_GUARD:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, value)
    datos = tmp_path / "data"
    assert _run(datos) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "la sincronización de datos privados solo la lanza el fundador desde su terminal\n"
    )
    assert builds == []
    assert store.calls == []
    assert not datos.exists()


# --- CA13 --------------------------------------------------------------------------------


def _adr_0055_example() -> str:
    adr = next((ROOT / "docs/adr").glob("0055-*.md")).read_text("utf-8")
    section = adr[adr.index("### 3.") : adr.index("### 4.")]
    match = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_ca13_el_ejemplo_del_adr_0055_valida() -> None:
    """CA13. Dado el bloque JSON de ejemplo del ADR 0055 §3, con `<64 hexadecimales>`
    sustituido por un sha256 válido, cuando se valida con `parse_catalog`, entonces es válido
    y da una entrada con todos sus campos (test que lee el ADR; si alguien cambia el esquema
    en un lado, falla)."""
    example = _adr_0055_example()
    assert "<64 hexadecimales>" in example
    content = example.replace("<64 hexadecimales>", "ab" * 32).encode()
    catalog = parse_catalog(CatalogDocument(name="catastro_historico.json", content=content))
    assert catalog.source == "catastro_historico"
    assert len(catalog.entries) == 1
    entry = catalog.entries[0]
    raw = json.loads(content)["entradas"][0]
    assert len(raw) == 16  # las 13 obligatorias más producto, municipio_ine y sustituido_por
    assert entry.entry_id == "catastro-historico-28043-ac-20261127"
    assert entry.source == "catastro_historico"
    assert entry.product == "Avance catastral"
    assert entry.municipality_code == "28043"
    assert entry.url == raw["url"]
    assert entry.downloaded_on == date(2026, 11, 27)
    assert entry.license == raw["licencia"]
    assert entry.use_restrictions == tuple(raw["restricciones_uso"])
    assert len(entry.use_restrictions) == 3
    assert entry.sha256 == "ab" * 32
    assert entry.size_bytes == 123456789
    assert entry.storage == ReleaseAsset(
        tag="catastro-historico-28043-20261127", asset="catastro-historico-28043-ac-20261127.7z"
    )
    assert entry.layout is EntryLayout.FILE
    assert entry.destination == "raw/catastro_historico/28043/AC.7z"
    assert entry.provenance == GitFile(
        path="procedencia/catastro_historico/catastro-historico-28043-ac-20261127.procedencia.json"
    )
    assert entry.parts == ()
    assert entry.replaced_by is None


# --- CA14 --------------------------------------------------------------------------------


def test_ca14_ficheros_que_ya_tiene_el_fundador(
    tmp_path: Path,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA14. Dado un fichero ya presente en `data/` con el sha256 y el tamaño del catálogo
    pero **sin** registro local (el portátil del fundador, que ya tiene los datos), cuando se
    ejecuta, entonces no descarga el fichero, descarga solo la procedencia, escribe
    `data/privados/<id>.json` y la entrada sale `ya estaba`; ejecutado otra vez, no llama a
    `download` ninguna vez y `data/` queda idéntico byte a byte (sha256 de cada fichero del
    árbol antes y después)."""
    datos = tmp_path / "data"
    for entry_id in CURRENT_IDS:
        path = datos / DESTINATIONS[entry_id]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PAYLOADS[entry_id])
    assert _run(datos) == 0
    lines = _lines(capsys.readouterr().out)
    expected_provenance = {
        f"procedencia/{HISTORICO if 'historico' in entry_id else INSPIRE}/{entry_id}"
        ".procedencia.json"
        for entry_id in CURRENT_IDS
    }
    assert set(store.downloads) == expected_provenance
    assert len(store.downloads) == len(CURRENT_IDS)
    for entry_id in CURRENT_IDS:
        assert lines[entry_id] == "ya estaba"
        assert (datos / "privados" / f"{entry_id}.json").is_file()
        assert (datos / DESTINATIONS[entry_id]).read_bytes() == PAYLOADS[entry_id]
    assert lines["resumen"] == "0 descargados, 4 ya estaban, 1 saltados, 0 fallidos"
    before = _tree(datos)
    store.downloads.clear()
    assert _run(datos) == 0
    assert store.downloads == []
    assert _tree(datos) == before


# --- CA15 --------------------------------------------------------------------------------


TAR_ID = "catastro-historico-28043-tar-20990101"
PARTS_ID = "catastro-historico-28043-partes-20990101"


def _with_tar_and_parts(catalogs: dict[str, dict[str, Any]]) -> None:
    base = _entry(catalogs, AC_ID)
    tar = copy.deepcopy(base)
    tar.update(
        id=TAR_ID,
        formato="tar",
        destino="raw/catastro_historico/28043/teselas",
        procedencia=f"procedencia/{HISTORICO}/{TAR_ID}.procedencia.json",
    )
    tar["almacen"]["adjunto"] = f"{TAR_ID}.tar.gz"
    parts = copy.deepcopy(base)
    parts.update(
        id=PARTS_ID,
        destino="raw/catastro_historico/28043/grande.7z",
        procedencia=f"procedencia/{HISTORICO}/{PARTS_ID}.procedencia.json",
        partes=[
            {"adjunto": f"{PARTS_ID}.7z.001", "sha256": "0" * 64, "tamano_bytes": 10},
            {"adjunto": f"{PARTS_ID}.7z.002", "sha256": "1" * 64, "tamano_bytes": 5},
        ],
    )
    parts["almacen"]["adjunto"] = f"{PARTS_ID}.7z"
    catalogs[HISTORICO_FILE]["entradas"].extend([tar, parts])


def test_ca15_saltadas_con_su_motivo(
    tmp_path: Path,
    builds: list[Path],
    store: FakePrivateDataStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA15. Dada una entrada con `sustituido_por` relleno, una de una fuente no pedida con
    `--fuente`, una con `formato: tar` y una con `partes`, cuando se ejecuta, entonces las
    cuatro salen `saltado` con su motivo («sustituida por <id>», «fuente no pedida», «formato
    tar: pendiente de #320», «en partes: pendiente de #320») y ninguna se descarga."""
    catalogs = _catalogs()
    _with_tar_and_parts(catalogs)
    _override(store, catalogs)
    datos = tmp_path / "data"
    assert _run(datos, "--fuente", HISTORICO) == 0
    lines = _lines(capsys.readouterr().out)
    assert lines[OLD_AC_ID] == f"saltado (sustituida por {AC_ID})"
    assert lines[CACHE_ID] == "saltado (fuente no pedida)"
    assert lines[SNAPSHOT_ID] == "saltado (fuente no pedida)"
    assert lines[TAR_ID] == "saltado (formato tar: pendiente de #320)"
    assert lines[PARTS_ID] == "saltado (en partes: pendiente de #320)"
    for key in store.downloads:
        for skipped in (OLD_AC_ID, CACHE_ID, SNAPSHOT_ID, TAR_ID, PARTS_ID):
            assert skipped not in key
    assert lines[AC_ID] == "descargado"
    assert lines[TP_ID] == "descargado"
    assert lines["resumen"] == "2 descargados, 0 ya estaban, 5 saltados, 0 fallidos"


# --- CA16 --------------------------------------------------------------------------------


def test_ca16_fallo_del_store_en_una_entrada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA16. Dado un fallo del store en una entrada (código de salida de `gh` distinto de 0
    con un `stderr` que contiene el nombre del repo y un valor centinela), cuando se ejecuta,
    entonces esa entrada sale `fallo` con un motivo genérico (sin el `stderr` de `gh`), las
    demás se procesan, no queda nada de la fallida en `data/` (ni en la zona de preparación)
    y el comando sale con 1."""
    stderr = f"HTTP 500: error en {REPO} {SENTINEL}".encode()
    runner = FakeGhRunner(REPO, failures={AC_ASSET: CommandResult(1, b"", stderr)})
    _use_gh_store(monkeypatch, runner)
    datos = tmp_path / "data"
    assert _run(datos) == 1
    captured = capsys.readouterr()
    lines = _lines(captured.out)
    assert lines[AC_ID] == "fallo (no se pudo descargar del almacén)"
    for entry_id in (TP_ID, CACHE_ID, SNAPSHOT_ID):
        assert lines[entry_id] == "descargado"
        assert (datos / DESTINATIONS[entry_id]).read_bytes() == PAYLOADS[entry_id]
    assert not (datos / DESTINATIONS[AC_ID]).exists()
    assert not (datos / "privados" / f"{AC_ID}.json").exists()
    assert not (datos / "privados" / ".preparacion").exists()
    for forbidden in (REPO, SENTINEL, "HTTP 500"):
        assert forbidden not in captured.out + captured.err
    assert lines["resumen"] == "3 descargados, 0 ya estaban, 1 saltados, 1 fallidos"


# --- CA17 --------------------------------------------------------------------------------


@pytest.fixture
def runner() -> FakeGhRunner:
    return FakeGhRunner(REPO)


def _gh(runner: FakeGhRunner, which: bool = True) -> GhCliPrivateDataStore:
    return GhCliPrivateDataStore(
        REPO, runner=runner, which=lambda name: "/usr/bin/gh" if which else None
    )


def test_ca17_argumentos_exactos_de_gh(runner: FakeGhRunner, tmp_path: Path) -> None:
    """CA17. Dado el adapter de `gh` con un ejecutor falso, cuando se leen los catálogos y se
    descarga un adjunto y un fichero de git, entonces los argumentos son exactamente los del
    ADR 0057 §3, ninguno contiene `token` (sin distinguir mayúsculas), el ejecutor no recibe
    `env`, y: `gh` no encontrado, `HTTP 401`, `HTTP 403`, `HTTP 404` o «gh auth login» en
    `stderr` al leer los catálogos dan `PrivateDataUnavailable`; `HTTP 500` o un tiempo
    agotado dan `PrivateDataStoreError`. Un nombre de catálogo que no case con
    `^[a-z0-9_]{1,64}\\.json$` hace fallar la lectura (`PrivateCatalogError`)."""
    store = _gh(runner)
    documents = store.read_catalogs()
    assert [d.name for d in documents] == [HISTORICO_FILE, INSPIRE_FILE]
    assert {d.name: d.content for d in documents} == catalog_documents()
    asset_target = tmp_path / "adjunto"
    git_target = tmp_path / "git"
    store.download(ReleaseAsset(tag=HISTORICO_TAG, asset=f"{AC_ID}.7z"), asset_target)
    store.download(GitFile(path=TP_GIT_PATH), git_target)
    assert asset_target.read_bytes() == PAYLOADS[AC_ID]
    assert git_target.read_bytes() == PAYLOADS[TP_ID]
    contents = f"repos/{REPO}/contents"
    assert [call.args for call in runner.calls] == [
        ("gh", "api", "--method", "GET", f"{contents}/catalogo"),
        ("gh", "api", "--method", "GET", "-H", RAW_ACCEPT, f"{contents}/catalogo/{HISTORICO_FILE}"),
        ("gh", "api", "--method", "GET", "-H", RAW_ACCEPT, f"{contents}/catalogo/{INSPIRE_FILE}"),
        (
            "gh",
            "release",
            "download",
            HISTORICO_TAG,
            "--repo",
            REPO,
            "--pattern",
            f"{AC_ID}.7z",
            "--output",
            str(asset_target),
        ),
        ("gh", "api", "--method", "GET", "-H", RAW_ACCEPT, f"{contents}/{TP_GIT_PATH}"),
    ]
    assert RAW_ACCEPT == "Accept: application/vnd.github.raw+json"
    adr = next((ROOT / "docs/adr").glob("0059-*.md")).read_text("utf-8")
    for form in (
        "`gh api --method GET repos/<repo>/contents/catalogo`",
        '`-H "Accept: application/vnd.github.raw+json"`',
        "`gh release download <etiqueta> --repo <repo> --pattern <adjunto> --output <ruta>`",
    ):
        assert form in adr, form
    for call in runner.calls:
        assert not any("token" in arg.lower() for arg in call.args)
    assert [c.timeout for c in runner.calls] == [120.0, 120.0, 120.0, None, 120.0]
    assert [c.has_stdout for c in runner.calls] == [False, False, False, False, True]
    parameters = inspect.signature(type(runner).__call__).parameters
    assert "env" not in parameters
    assert "env" not in inspect.signature(GhCliPrivateDataStore).parameters


_UNAVAILABLE_STDERR = (
    b"gh: Bad credentials (HTTP 401)",
    b"gh: Resource not accessible (HTTP 403)",
    b"gh: Not Found (HTTP 404)",
    b"To get started with GitHub CLI, please run:  gh auth login",
)


@pytest.mark.parametrize("stderr", _UNAVAILABLE_STDERR)
@pytest.mark.parametrize("where", ["catalogo", f"catalogo/{HISTORICO_FILE}"])
def test_ca17_sin_acceso_al_leer_los_catalogos(stderr: bytes, where: str) -> None:
    """CA17 (clasificación «sin acceso»: HTTP 401, 403, 404 y «gh auth login»)."""
    runner = FakeGhRunner(REPO, failures={where: CommandResult(1, b"", stderr)})
    with pytest.raises(PrivateDataUnavailable):
        _gh(runner).read_catalogs()


def test_ca17_gh_no_encontrado() -> None:
    """CA17 (`gh` no encontrado: ni en el PATH ni al lanzarlo)."""
    runner = FakeGhRunner(REPO)
    with pytest.raises(PrivateDataUnavailable):
        _gh(runner, which=False).read_catalogs()
    assert runner.calls == []
    launch = FakeGhRunner(REPO, raises={"catalogo": FileNotFoundError("gh")})
    with pytest.raises(PrivateDataUnavailable):
        _gh(launch).read_catalogs()


@pytest.mark.parametrize(
    "failure",
    [
        CommandResult(1, b"", b"gh: Server Error (HTTP 500)"),
        subprocess.TimeoutExpired(["gh"], 120),
        CommandResult(0, b"no es json", b""),
    ],
    ids=["http_500", "tiempo_agotado", "json_ilegible"],
)
def test_ca17_otros_fallos_son_del_almacen(failure: object) -> None:
    """CA17 (`HTTP 500` o un tiempo agotado dan `PrivateDataStoreError`)."""
    if isinstance(failure, CommandResult):
        runner = FakeGhRunner(REPO, failures={"catalogo": failure})
    else:
        assert isinstance(failure, BaseException)
        runner = FakeGhRunner(REPO, raises={"catalogo": failure})
    with pytest.raises(PrivateDataStoreError) as raised:
        _gh(runner).read_catalogs()
    assert REPO not in str(raised.value)


@pytest.mark.parametrize("name", ["Mayusculas.json", "con-guion.json", "README.md", ".json"])
def test_ca17_nombre_de_catalogo_no_valido(name: str) -> None:
    """CA17 (un nombre de catálogo que no case con `^[a-z0-9_]{1,64}\\.json$`)."""
    runner = FakeGhRunner(
        REPO, extra_listing=[{"name": name, "path": f"catalogo/{name}", "type": "file"}]
    )
    with pytest.raises(PrivateCatalogError) as raised:
        _gh(runner).read_catalogs()
    assert name not in str(raised.value)


def test_cada_criterio_tiene_su_test() -> None:
    """Un test (al menos) por criterio, de CA1 a CA17."""
    names = {n.split("_")[1] for n in globals() if re.match(r"test_ca\d+_", n)}
    assert names == {f"ca{k}" for k in range(1, 18)}
