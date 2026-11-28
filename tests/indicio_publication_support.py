"""Entradas de `publicar-discrepancias` desde la historia #316, para los tests que lo invocan
por la CLI (#94, #96, #170, #183 y #316).

Desde #316 el comando lee, además del informe publicable, el informe INTERNO casado, el
manifiesto de imágenes de los indicios, las capas derivadas y `curated/historico/`. Para no
pasar por accidente con los valores por defecto (lección de #315), cada test pasa todas las
opciones nuevas a `tmp_path` o a la fixture, de forma explícita:

- el informe interno es el que escribió `discrepancias` junto al publicable
  (`<salida de discrepancias>/interno`);
- el manifiesto lo genera el productor real, `imagenes-comparacion-indicios`, con un
  `http_get` falso (`FakeServers` de #208: JPEG «con contenido» del IGN y la tesela real de
  la IDEM) y una caché propia en `tmp_path`;
- `--curated-historico` y `--cache-historico` apuntan a directorios que no existen: la
  cobertura histórica sale vacía («Sin cobertura»).

Nada es real: las imágenes son las respuestas falsas de `FakeServers`.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from parcel_intel.adapters.raw_cache import HttpGet
from parcel_intel.cli import main
from tests.fixtures.candidatos_publicar.generar_idem import FakeServers

DAY = date(2026, 11, 27)


def write_indicio_manifests(
    internal: Path,
    comparison: Path,
    cache: Path,
    codes: Sequence[str],
    http: HttpGet | None = None,
) -> None:
    """Ejecuta `imagenes-comparacion-indicios` (CLI) sobre `internal` con un `http_get`
    falso. Falla si el comando no sale con 0."""
    if not codes:
        return
    servers: HttpGet = http or FakeServers()
    real = main.build_indicio_comparison_use_case

    def build(input_dir: Path, cache_dir: Path, output_dir: Path, offline: bool) -> object:
        return real(
            input_dir,
            cache_dir,
            output_dir,
            offline,
            http_get=servers,
            today=lambda: DAY,
            sleep=lambda seconds: None,
        )

    args = ["imagenes-comparacion-indicios"]
    for code in codes:
        args += ["--municipio", code]
    args += ["--entrada", str(internal), "--cache", str(cache), "--salida", str(comparison)]
    parsed = main._build_parser().parse_args(args)
    main.build_indicio_comparison_use_case = build  # type: ignore[assignment]
    try:
        exit_code = parsed.func(parsed)
    finally:
        main.build_indicio_comparison_use_case = real
    assert exit_code == 0, f"imagenes-comparacion-indicios salió con {exit_code}"


def indicio_publication_options(
    publicable: Path,
    capas: Path,
    codes: Sequence[str] | None = None,
    http: HttpGet | None = None,
    work: Path | None = None,
) -> list[str]:
    """Opciones nuevas de `publicar-discrepancias` para un informe publicable en
    `publicable` (normalmente `<salida de discrepancias>/publicable`).

    Si existe el interno hermano (`<padre>/interno`), genera antes el manifiesto de sus
    municipios (`codes`, o todos los que tengan informe interno) con el productor real. Si no
    existe, las opciones apuntan igual a `<padre>/interno` (el comando dirá que falta). El
    manifiesto, su caché y los directorios históricos vacíos van a `work` (por defecto, el
    padre de `publicable`; con datos reales, un `tmp_path`, para no escribir en `data/`)."""
    internal = publicable.parent / "interno"
    base = work or publicable.parent
    comparison = base / "comparacion_indicios"
    if internal.is_dir():
        found = sorted(path.name.removesuffix(".geojson") for path in internal.glob("*.geojson"))
        wanted = [code for code in (codes if codes is not None else found) if code in found]
        write_indicio_manifests(internal, comparison, base / "cache_indicios", wanted, http)
    return [
        "--interno",
        str(internal),
        "--comparacion",
        str(comparison),
        "--capas",
        str(capas),
        "--curated-historico",
        str(base / "sin_curated_historico"),
        "--cache-historico",
        str(base / "sin_cache_historico"),
    ]
