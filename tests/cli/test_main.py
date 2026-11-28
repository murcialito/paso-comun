from datetime import date, datetime
from pathlib import Path
from types import TracebackType

import pytest
import requests

from parcel_intel.cli import main
from parcel_intel.domain.norm import Norm, Provision
from parcel_intel.domain.published_discrepancy import DiscrepancyPublicationResult
from parcel_intel.domain.published_layers import PublicationResult
from parcel_intel.domain.rule import (
    AttributeCondition,
    LlmUsage,
    ProposedRule,
    Rule,
    RuleLayer,
    RuleProposalDraft,
    RuleScope,
    WithinDistanceCondition,
)
from parcel_intel.ports.errors import InvalidLlmProposalError, RuleProposalNotFoundError

_CITATION = Provision(
    norm=Norm(title="Norma de prueba", url="https://example.org/norma"),
    article="art. 1",
    in_force_since=date(2020, 1, 1),
)
_USAGE = LlmUsage(model="deepseek-chat", prompt_tokens=100, completion_tokens=50, cost_usd=0.001)


def _proposal(identifier: str = "madrid-proposed-dph_cauce-aaaaaaaa") -> ProposedRule:
    rule = Rule(
        identifier=identifier,
        scope=RuleScope(region="madrid", soil_classes=("no_urbanizable",)),
        spatial=WithinDistanceCondition(
            operation="within_distance", layer="dph_cauce", meters=100.0
        ),
        attributes=AttributeCondition(building_origin=("catastro", "detected")),
        outcome="requires_authorization",
        authority="Autoridad de prueba",
        citation=_CITATION,
        status="proposed",
    )
    return ProposedRule(
        rule=rule,
        explanation="Explicación de prueba.",
        usage=_USAGE,
        created_at=datetime(2026, 10, 16, 12, 0),
    )


class _FakeConnection:
    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


class _FakeRuleRepository:
    def __init__(self, connection: object) -> None:
        pass

    proposed: list[ProposedRule] = []
    approved: list[str] = []
    rejected: list[str] = []
    not_found_on_review: bool = False

    def save_proposal(self, proposal: ProposedRule) -> None:
        pass

    def list_proposed(self) -> list[ProposedRule]:
        return list(_FakeRuleRepository.proposed)

    def approve(self, identifier: str) -> None:
        if _FakeRuleRepository.not_found_on_review:
            raise RuleProposalNotFoundError(identifier)
        _FakeRuleRepository.approved.append(identifier)

    def reject(self, identifier: str) -> None:
        if _FakeRuleRepository.not_found_on_review:
            raise RuleProposalNotFoundError(identifier)
        _FakeRuleRepository.rejected.append(identifier)


class _FakeDeepSeekAdapter:
    behavior: str = "ok"

    def __init__(self) -> None:
        pass

    def propose_rule(self, article_text: str, layer: RuleLayer) -> RuleProposalDraft:
        if _FakeDeepSeekAdapter.behavior == "invalid":
            raise InvalidLlmProposalError("respuesta simulada inválida")
        return RuleProposalDraft(
            soil_classes=("no_urbanizable",),
            meters=100.0,
            outcome="requires_authorization",
            authority="Autoridad de prueba",
            norm_title="Norma de prueba",
            norm_url="https://example.org/norma",
            article="art. 1",
            in_force_since=date(2020, 1, 1),
            provision_url=None,
            explanation="Explicación de prueba.",
            usage=_USAGE,
        )


@pytest.fixture(autouse=True)
def _reset_fakes() -> None:
    _FakeRuleRepository.proposed = []
    _FakeRuleRepository.approved = []
    _FakeRuleRepository.rejected = []
    _FakeRuleRepository.not_found_on_review = False
    _FakeDeepSeekAdapter.behavior = "ok"


@pytest.fixture(autouse=True)
def _patch_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(main, "PostgisRuleRepository", _FakeRuleRepository)
    monkeypatch.setattr(main, "DeepSeekAdapter", _FakeDeepSeekAdapter)


def test_author_imprime_identificador_y_sale_con_cero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    article_file = tmp_path / "articulo.txt"
    article_file.write_text("texto del artículo", encoding="utf-8")
    parser = main._build_parser()
    args = parser.parse_args(["author", "--layer", "dph_cauce", "--text", str(article_file)])

    exit_code = args.func(args)

    assert exit_code == 0
    salida = capsys.readouterr().out
    assert "madrid-proposed-dph_cauce-" in salida
    assert "requires_authorization" in salida


def test_author_respuesta_invalida_del_llm_sale_con_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _FakeDeepSeekAdapter.behavior = "invalid"
    article_file = tmp_path / "articulo.txt"
    article_file.write_text("texto del artículo", encoding="utf-8")
    parser = main._build_parser()
    args = parser.parse_args(["author", "--layer", "dph_cauce", "--text", str(article_file)])

    exit_code = args.func(args)

    assert exit_code == 1
    assert "no válida" in capsys.readouterr().err


def test_author_fichero_inexistente_sale_con_error_claro(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fichero_inexistente = tmp_path / "no-existe.txt"
    parser = main._build_parser()
    args = parser.parse_args(["author", "--layer", "dph_cauce", "--text", str(fichero_inexistente)])

    exit_code = args.func(args)

    assert exit_code == 1
    assert "No se pudo leer el fichero" in capsys.readouterr().err


def test_author_error_de_conexion_sale_con_error_claro(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_connection_error(
        self: object, article_text: str, layer: RuleLayer
    ) -> RuleProposalDraft:
        raise requests.exceptions.ConnectionError("fallo de red simulado")

    monkeypatch.setattr(_FakeDeepSeekAdapter, "propose_rule", _raise_connection_error)
    article_file = tmp_path / "articulo.txt"
    article_file.write_text("texto del artículo", encoding="utf-8")
    parser = main._build_parser()
    args = parser.parse_args(["author", "--layer", "dph_cauce", "--text", str(article_file)])

    exit_code = args.func(args)

    assert exit_code == 1
    assert "No se pudo conectar con DeepSeek" in capsys.readouterr().err


def test_review_list_sin_pendientes(capsys: pytest.CaptureFixture[str]) -> None:
    parser = main._build_parser()
    args = parser.parse_args(["review", "list"])

    exit_code = args.func(args)

    assert exit_code == 0
    assert "No hay propuestas pendientes." in capsys.readouterr().out


def test_review_list_con_pendientes_muestra_todos_los_campos_estructurados(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _FakeRuleRepository.proposed = [_proposal()]
    parser = main._build_parser()
    args = parser.parse_args(["review", "list"])

    exit_code = args.func(args)

    assert exit_code == 0
    salida = capsys.readouterr().out
    assert "madrid-proposed-dph_cauce-aaaaaaaa" in salida
    assert "Explicación de prueba." in salida
    assert "region: madrid" in salida
    assert "no_urbanizable" in salida
    assert "within_distance" in salida
    assert "dph_cauce" in salida
    assert "meters=100.0" in salida
    assert "catastro" in salida and "detected" in salida
    assert "art. 1" in salida
    assert "2020-01-01" in salida
    assert "$0.001000" in salida
    assert "100 + 50 tokens" in salida


def test_review_approve_exitoso(capsys: pytest.CaptureFixture[str]) -> None:
    parser = main._build_parser()
    args = parser.parse_args(["review", "approve", "madrid-proposed-dph_cauce-aaaaaaaa"])

    exit_code = args.func(args)

    assert exit_code == 0
    assert _FakeRuleRepository.approved == ["madrid-proposed-dph_cauce-aaaaaaaa"]


def test_review_approve_identificador_inexistente(capsys: pytest.CaptureFixture[str]) -> None:
    _FakeRuleRepository.not_found_on_review = True
    parser = main._build_parser()
    args = parser.parse_args(["review", "approve", "no-existe"])

    exit_code = args.func(args)

    assert exit_code == 1
    assert "no-existe" in capsys.readouterr().err


def test_review_reject_exitoso(capsys: pytest.CaptureFixture[str]) -> None:
    parser = main._build_parser()
    args = parser.parse_args(["review", "reject", "madrid-proposed-dph_cauce-aaaaaaaa"])

    exit_code = args.func(args)

    assert exit_code == 0
    assert _FakeRuleRepository.rejected == ["madrid-proposed-dph_cauce-aaaaaaaa"]


def test_imagenes_comparacion_exige_municipio_y_tramos() -> None:
    parser = main._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["imagenes-comparacion", "--tramos", "t.geojson"])
    with pytest.raises(SystemExit):
        parser.parse_args(["imagenes-comparacion", "--municipio", "28043"])
    with pytest.raises(SystemExit):
        parser.parse_args(["imagenes-comparacion", "--municipio", "28", "--tramos", "t"])


def test_imagenes_comparacion_indicios_sin_municipio_ni_ambito_sale_con_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = main._build_parser().parse_args(["imagenes-comparacion-indicios"])
    assert args.func(args) == 2
    assert "--municipio" in capsys.readouterr().err


def test_imagenes_comparacion_indicios_sin_informe_sale_con_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = main._build_parser().parse_args(
        [
            "imagenes-comparacion-indicios",
            "--municipio",
            "28043",
            "--entrada",
            str(tmp_path),
            "--salida",
            str(tmp_path / "salida"),
        ]
    )
    assert args.func(args) == 1
    assert "28043" in capsys.readouterr().err
    assert not (tmp_path / "salida").exists()


def test_imagenes_comparacion_fichero_de_tramos_ilegible_sale_con_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = main._build_parser()
    args = parser.parse_args(
        ["imagenes-comparacion", "--municipio", "28043", "--tramos", str(tmp_path / "no.geojson")]
    )
    assert args.func(args) == 1
    assert "Fichero de tramos no válido" in capsys.readouterr().err


class _FakePublishUseCase:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], list[str]]] = []

    def execute(self, codes: list[str], layer_ids: list[str]) -> PublicationResult:
        self.calls.append((list(codes), list(layer_ids)))
        return PublicationResult(published=(), empty=(), failures=())


def _patch_publish(monkeypatch: pytest.MonkeyPatch) -> _FakePublishUseCase:
    fake = _FakePublishUseCase()
    monkeypatch.setattr(main, "build_publish_use_case", lambda *args, **kwargs: fake)
    return fake


def test_publicar_capas_sin_municipio_sale_con_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _patch_publish(monkeypatch)
    args = main._build_parser().parse_args(["publicar-capas"])

    assert args.func(args) == 2
    assert fake.calls == []
    assert "--ambito" in capsys.readouterr().err


def test_publicar_capas_ambito_pide_los_7_municipios_y_ambas_capas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_publish(monkeypatch)
    args = main._build_parser().parse_args(["publicar-capas", "--ambito", "--municipio", "28043"])

    assert args.func(args) == 0
    assert fake.calls == [(list(main.AMBITO_HITO_INE), ["vias_pecuarias", "camino_actual"])]


def test_publicar_capas_una_capa_sin_repetidos(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_publish(monkeypatch)
    args = main._build_parser().parse_args(
        [
            "publicar-capas",
            "--municipio",
            "28043",
            "--municipio",
            "28043",
            "--capa",
            "camino_actual",
            "--capa",
            "camino_actual",
        ]
    )

    assert args.func(args) == 0
    assert fake.calls == [(["28043"], ["camino_actual"])]


def test_publicar_capas_rechaza_una_capa_desconocida() -> None:
    with pytest.raises(SystemExit):
        main._build_parser().parse_args(["publicar-capas", "--ambito", "--capa", "catastro"])


class _FakePublishDiscrepanciesUseCase:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def execute(self, codes: list[str]) -> DiscrepancyPublicationResult:
        self.calls.append(list(codes))
        return DiscrepancyPublicationResult(published=(), empty=(), omitted=(), failures=())


def _patch_publish_discrepancies(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakePublishDiscrepanciesUseCase:
    fake = _FakePublishDiscrepanciesUseCase()
    monkeypatch.setattr(main, "build_publish_discrepancies_use_case", lambda *a, **k: fake)
    return fake


def test_publicar_discrepancias_sin_municipio_sale_con_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _patch_publish_discrepancies(monkeypatch)
    args = main._build_parser().parse_args(["publicar-discrepancias"])

    assert args.func(args) == 2
    assert fake.calls == []
    assert "--ambito" in capsys.readouterr().err


def test_publicar_discrepancias_ambito_sin_repetidos_y_rutas_por_defecto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_publish_discrepancies(monkeypatch)
    args = main._build_parser().parse_args(
        ["publicar-discrepancias", "--ambito", "--municipio", "28043"]
    )

    assert args.func(args) == 0
    assert fake.calls == [list(main.AMBITO_HITO_INE)]
    assert args.entrada == Path("data/derived/discrepancias/publicable")
    assert args.salida == Path("site/datos")
    # Opciones de #316, con los mismos valores por defecto que `publicar-candidatos`.
    assert args.interno == Path("data/derived/discrepancias/interno")
    assert args.comparacion == Path("data/derived/comparacion_indicios")
    assert args.capas == Path("data/derived")
    assert args.curated_historico == Path("curated/historico")
    assert args.cache_historico == Path("data/raw/ign_planimetrias")
    assert args.umbral_aparece == 0.5


@pytest.mark.parametrize("value", ["0", "1.5", "nan", "-1"])
def test_publicar_discrepancias_umbral_no_valido_sale_con_1_sin_leer_nada(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    def no_debe_llamarse(*args: object, **kwargs: object) -> object:
        raise AssertionError("no debe construir nada")

    monkeypatch.setattr(main, "build_publish_discrepancies_use_case", no_debe_llamarse)
    monkeypatch.setattr(main, "build_historic_coverage_source", no_debe_llamarse)
    args = main._build_parser().parse_args(
        ["publicar-discrepancias", "--municipio", "28043", "--umbral-aparece", value]
    )
    assert args.func(args) == 1
    out = capsys.readouterr()
    assert out.err.splitlines() == ["ERROR --umbral-aparece: debe estar en (0, 1]"]
    assert out.out == ""


# --- publicar-candidatos (#315) -----------------------------------------------------------


def test_publicar_candidatos_opciones_nuevas_y_sus_valores_por_defecto() -> None:
    args = main._build_parser().parse_args(["publicar-candidatos", "--municipio", "28043"])
    assert args.capas == Path("data/derived")
    assert args.curated_historico == Path("curated/historico")
    assert args.cache_historico == Path("data/raw/ign_planimetrias")
    assert args.umbral_aparece == 0.5


@pytest.mark.parametrize("value", ["0", "-0.1", "1.5", "nan", "inf"])
def test_publicar_candidatos_umbral_fuera_de_rango_sale_con_1_sin_leer(
    value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_debe_llamarse(*args: object, **kwargs: object) -> object:
        raise AssertionError("no debe leer nada")

    monkeypatch.setattr(main, "build_historic_coverage_source", no_debe_llamarse)
    monkeypatch.setattr(main, "build_publish_candidates_use_case", no_debe_llamarse)
    args = main._build_parser().parse_args(
        ["publicar-candidatos", "--municipio", "28043", "--umbral-aparece", value]
    )
    assert args.func(args) == 1
    assert capsys.readouterr().err == "ERROR --umbral-aparece: debe estar en (0, 1]\n"


def test_la_cobertura_historica_de_publicar_no_usa_red(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no usa red"):
        main._offline_http_get("https://example.invalid", {})
    source = main.build_historic_coverage_source(tmp_path / "curated", tmp_path / "cache")
    coverage = source.get_coverage("planimetria")
    assert coverage.units == () and coverage.trails == ()
    assert source.skipped_units() == ()


def test_discrepancias_y_convergencia_leen_las_mismas_capas(tmp_path: Path) -> None:
    reader = main._trail_layer_reader(tmp_path)
    assert reader._paths == {
        "via_pecuaria_oficial": tmp_path / "vias_pecuarias",
        "ign_red_transporte": tmp_path / "camino_actual",
        "catastro_vt": tmp_path / "catastro_caminos",
    }
    assert reader._complements == {"ign_red_transporte": tmp_path / "camino_asfaltado"}
