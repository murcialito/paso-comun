import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import requests

from parcel_intel.adapters.catastro import client as catastro_client
from parcel_intel.adapters.catastro.area_adapter import (
    CATASTRO_INSPIRE_LICENSE,
    CATASTRO_WFS_TILE_SIDE_M,
    CatastroAreaAdapter,
)
from parcel_intel.adapters.catastro.zoning_adapter import CatastroZoningAdapter
from parcel_intel.adapters.comparison_files import (
    JsonComparisonAvailabilityReader,
    JsonComparisonManifestReader,
    JsonComparisonManifestWriter,
    ManifestTooLargeError,
    StretchFileFormatError,
    read_stretches,
)
from parcel_intel.adapters.comparison_windows.router import FlightRoutedWindowAdapter
from parcel_intel.adapters.curated_candidates import (
    CuratedCandidateInventoryFiles,
    CuratedCandidateReviewFiles,
)
from parcel_intel.adapters.curated_historic import CuratedHistoricFiles
from parcel_intel.adapters.cut_candidate_files import (
    GeoJsonCutCandidateReader,
    GeoJsonCutCandidateWriter,
)
from parcel_intel.adapters.deepseek.adapter import DeepSeekAdapter, MissingDeepSeekApiKeyError
from parcel_intel.adapters.deepseek.client import DeepSeekApiError
from parcel_intel.adapters.discrepancy_files import (
    DiscrepancyFileFormatError,
    GeoJsonDiscrepancyReportReader,
    GeoJsonDiscrepancyWriter,
)
from parcel_intel.adapters.evidence import (
    PngTrailEvidence,
    PnoaPngSource,
    default_png_http_get,
)
from parcel_intel.adapters.evidence.pnoa_png import NotPngResponseError
from parcel_intel.adapters.hydrography import client as hydrography_client
from parcel_intel.adapters.hydrography.adapter import HydrographyAdapter
from parcel_intel.adapters.idem_vuelos.window import IdemVuelosWindowAdapter
from parcel_intel.adapters.ign_admin_units import IgnAdministrativeUnitsAdapter
from parcel_intel.adapters.ign_admin_units import client as ign_client
from parcel_intel.adapters.ign_historic import HttpGet as HistoricHttpGet
from parcel_intel.adapters.ign_historic import IgnHistoricImageAdapter
from parcel_intel.adapters.ign_historic import default_http_get as historic_default_http_get
from parcel_intel.adapters.kit_campo import (
    FileFieldKitSink,
    FileFieldProjectSink,
    PublishedLayerFileReader,
)
from parcel_intel.adapters.livestock_trails import client as livestock_client
from parcel_intel.adapters.livestock_trails.adapter import LivestockTrailAdapter
from parcel_intel.adapters.pnoa import client as pnoa_client
from parcel_intel.adapters.pnoa.window import PnoaWindowAdapter
from parcel_intel.adapters.postgis.connection import get_connection
from parcel_intel.adapters.postgis.georeference_repository import PostgisGeoreferenceRepository
from parcel_intel.adapters.postgis.rule_repository import PostgisRuleRepository
from parcel_intel.adapters.public_utility_forests import client as forest_client
from parcel_intel.adapters.public_utility_forests.adapter import PublicUtilityForestAdapter
from parcel_intel.adapters.published_layers import (
    GeoJsonCoverageSink,
    GeoJsonPublishedCandidateSink,
    GeoJsonPublishedDiscrepancySink,
    GeoJsonPublishedLayerSink,
    JsonMeasurementReportReader,
    PublishedLayerError,
)
from parcel_intel.adapters.raw_cache import (
    CachedRawFetcher,
    HttpGet,
    RawCacheCorruptError,
    RawCacheMissError,
)
from parcel_intel.adapters.road_layer_files import (
    GeoJsonLivestockTrailLayerWriter,
    GeoJsonPublicUtilityForestLayerWriter,
    GeoJsonRoadLayerWriter,
    LivestockTrailLayerFileError,
    PublicUtilityForestLayerFileError,
)
from parcel_intel.adapters.road_layer_files.public_utility_forest_writer import (
    layer_to_geojson_text as forest_layer_to_geojson_text,
)
from parcel_intel.adapters.road_layer_files.watercourse_easement_writer import (
    GeoJsonWatercourseEasementLayerWriter,
    WatercourseEasementFileError,
)
from parcel_intel.adapters.road_layer_files.watercourse_easement_writer import (
    layer_to_geojson_text as easement_layer_to_geojson_text,
)
from parcel_intel.adapters.road_network import client as roadlink_client
from parcel_intel.adapters.road_network.adapter import RoadNetworkAdapter
from parcel_intel.adapters.shapely_geometry import (
    ShapelyLineBuffer,
    ShapelyLineClipper,
    ShapelyPolygonClipper,
)
from parcel_intel.adapters.trail_layers import (
    GeoJsonCadastralRoadWriter,
    GeoJsonTrailLayerReader,
    TrailLayerFormatError,
)
from parcel_intel.adapters.validation_cases.casos_json import parse_validation_cases
from parcel_intel.adapters.validation_cases.congelacion import (
    ErrorCongelacion,
    calcular_huella,
    cargar_casos,
    versiones,
)
from parcel_intel.adapters.width_signal_files import GeoJsonWidthSignalWriter
from parcel_intel.application.author_rule_proposal import AuthorRuleProposalUseCase
from parcel_intel.application.build_cadastral_road_layer import BuildCadastralRoadLayerUseCase
from parcel_intel.application.build_comparison_images import BuildComparisonImagesUseCase
from parcel_intel.application.build_convergence_tables import BuildConvergenceTablesUseCase
from parcel_intel.application.build_indicio_comparisons import BuildIndicioComparisonsUseCase
from parcel_intel.application.build_municipal_comparisons import (
    BuildMunicipalComparisonsUseCase,
    ComparisonFailuresError,
    MunicipalWindowLimitError,
)
from parcel_intel.application.build_municipal_livestock_trail_layers import (
    BuildMunicipalLivestockTrailLayersUseCase,
)
from parcel_intel.application.build_municipal_public_utility_forest_layers import (
    BuildMunicipalPublicUtilityForestLayersUseCase,
)
from parcel_intel.application.build_municipal_road_layers import BuildMunicipalRoadLayersUseCase
from parcel_intel.application.build_municipal_watercourse_easement_layers import (
    BuildMunicipalWatercourseEasementLayersUseCase,
)
from parcel_intel.application.build_trail_evidence import BuildTrailEvidenceUseCase
from parcel_intel.application.cut_signals import CadastralParcelSignalProvider
from parcel_intel.application.detect_cut_candidates import DetectCutCandidatesUseCase
from parcel_intel.application.detect_discrepancies import DetectDiscrepanciesUseCase
from parcel_intel.application.detect_width_narrowing import DetectWidthNarrowingUseCase
from parcel_intel.application.evaluate_candidates import EvaluateCandidatesUseCase
from parcel_intel.application.historic_coverage import CuratedHistoricCoverageSource
from parcel_intel.application.kit_campo import PublishFieldKitUseCase, PublishFieldProjectUseCase
from parcel_intel.application.list_candidate_reviews import (
    ListCandidateReviewsUseCase,
    MunicipalReviewListing,
)
from parcel_intel.application.load_historic_trails import HistoricUnitUseCase
from parcel_intel.application.publish_candidates import PublishCandidatesUseCase
from parcel_intel.application.publish_coverage import PublishCoverageUseCase
from parcel_intel.application.publish_discrepancies import PublishDiscrepanciesUseCase
from parcel_intel.application.publish_layers import (
    DEFAULT_LAYERS,
    PublishLayersUseCase,
)
from parcel_intel.application.read_cadastral_zoning import ReadCadastralZoningUseCase
from parcel_intel.domain.cadastral_road import CadastralRoadLayer
from parcel_intel.domain.cadastral_zoning import CadastralZoningLayer
from parcel_intel.domain.candidate_evaluation import (
    MATCH_CRITERION_V1,
    CandidateEvaluationInputError,
    EvaluableCandidate,
    EvaluationResult,
)
from parcel_intel.domain.candidate_inventory import CandidateInventoryState
from parcel_intel.domain.comparison_image import DegenerateStretchError, StretchComparison
from parcel_intel.domain.convergence import (
    APPEARS_MIN_FRACTION,
    InvalidConvergenceStretchError,
    check_appears_min_fraction,
)
from parcel_intel.domain.digitization import StaleApprovalError
from parcel_intel.domain.discrepancy import DiscrepancyKind
from parcel_intel.domain.discrepancy_report import LayerStatus
from parcel_intel.domain.geometry import BBox, outward_integer_bbox, shape_bbox
from parcel_intel.domain.georeferencing import (
    ControlPointSet,
    GeoreferenceFit,
    GeoreferenceRejectedError,
    UnitSpec,
    Verdict,
    WorkingImage,
    occupied_quadrants,
    thresholds_m,
)
from parcel_intel.domain.municipal_livestock_trails import MunicipalLivestockTrailLayer
from parcel_intel.domain.municipal_public_utility_forests import MunicipalPublicUtilityForestLayer
from parcel_intel.domain.municipal_road_layer import MunicipalRoadLayerBatch
from parcel_intel.domain.municipality import HITO_MUNICIPALITY_CODES
from parcel_intel.domain.parcel import Parcel
from parcel_intel.domain.provenance import Provenance, Redistribution
from parcel_intel.domain.publication import NotRedistributableError
from parcel_intel.domain.published_discrepancy import OmittedByLicense
from parcel_intel.domain.published_layers import PUBLISHED_LAYERS, PublishedLayerId
from parcel_intel.domain.road_segment import ROAD_SURFACES, RoadSurface
from parcel_intel.domain.rule import Rule, WithinDistanceCondition
from parcel_intel.domain.watercourse_easement import MunicipalWatercourseEasementLayer
from parcel_intel.domain.width_signal import MunicipalWidthReport
from parcel_intel.ports.errors import (
    AreaQueryLimitExceededError,
    CadastralServiceError,
    ClippedGeometryHasHolesError,
    CuratedUnitNotFoundError,
    CutCandidateFileError,
    DiscrepancyReportUnavailableError,
    EvidenceImageFormatError,
    IncompleteWfsResponseError,
    InternalReportRequiredError,
    InvalidLlmProposalError,
    InvalidRuleLayerError,
    MunicipalBoundaryNotFoundError,
    MunicipalBoundaryUnusableError,
    RawImageMismatchError,
    RuleProposalNotFoundError,
    TruncatedAreaQueryError,
    WatercourseDownloadError,
    WatercourseLicenseMissingError,
    WorkingImageUnavailableError,
)
from parcel_intel.ports.georeference_repository import GeoreferenceRepositoryPort
from parcel_intel.ports.historic_coverage import HistoricCoverageSource
from parcel_intel.ports.kit_campo import KitSinkError
from parcel_intel.ports.municipal_boundary import MunicipalBoundaryPort

_LAYER_CHOICES = ["dph_cauce", "via_pecuaria", "red_natura_plan"]

AMBITO_HITO_INE = HITO_MUNICIPALITY_CODES
"""Los 7 municipios del primer hito (REGISTRY.md, "Ámbito del hito"): Colmenar de
Oreja, Aranjuez, Chinchón, Belmonte de Tajo, Villaconejos, Villarejo de Salvanés y
Valdelaguna. Configuración del hito, no dominio."""

AMBITO_HITO_BBOX_ETRS89 = (39.98, -3.62, 40.22, -3.15)
"""(lat_min, lon_min, lat_max, lon_max) en EPSG:4258 con el que se pide el WFS del IGN."""

REPOSITORY_URL = "https://github.com/murcialito/paso-comun"
"""Procedencia del índice de capas publicadas (historia #84)."""

PUBLISH_SIZE_THRESHOLD_BYTES = 3_000_000
"""Por encima de este peso, `publicar-capas` simplifica la geometría (ADR 0036, umbral de
producto alineado con #86)."""


def _author(args: argparse.Namespace) -> int:
    try:
        article_text = args.text.read_text(encoding="utf-8")
    except OSError as error:
        print(f"No se pudo leer el fichero {args.text}: {error}", file=sys.stderr)
        return 1

    with get_connection() as conn:
        repository = PostgisRuleRepository(conn)
        try:
            llm = DeepSeekAdapter()
        except MissingDeepSeekApiKeyError:
            print("Falta la variable de entorno DEEPSEEK_API_KEY.", file=sys.stderr)
            return 1
        use_case = AuthorRuleProposalUseCase(llm=llm, repository=repository)
        try:
            proposal = use_case.execute(article_text, args.layer)
        except InvalidRuleLayerError as error:
            print(f"Capa no reconocida: {error}", file=sys.stderr)
            return 1
        except InvalidLlmProposalError as error:
            print(f"Respuesta del LLM no válida, no se ha guardado nada: {error}", file=sys.stderr)
            return 1
        except DeepSeekApiError as error:
            print(f"Error llamando a DeepSeek: {error}", file=sys.stderr)
            return 1
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as error:
            print(f"No se pudo conectar con DeepSeek: {error}", file=sys.stderr)
            return 1

    rule = proposal.rule
    print(f"Propuesta creada: {rule.identifier}")
    print(f"  outcome: {rule.outcome}")
    print(f"  authority: {rule.authority}")
    print(f"  explicación: {proposal.explanation}")
    print(
        f"  coste: ${proposal.usage.cost_usd:.6f} "
        f"({proposal.usage.prompt_tokens} + {proposal.usage.completion_tokens} tokens, "
        f"modelo {proposal.usage.model})"
    )
    return 0


def _format_spatial(rule: Rule) -> str:
    spatial = rule.spatial
    if isinstance(spatial, WithinDistanceCondition):
        return f"{spatial.operation} / {spatial.layer} / meters={spatial.meters}"
    return f"{spatial.operation} / {spatial.layer}"


def _review_list(_args: argparse.Namespace) -> int:
    with get_connection() as conn:
        repository = PostgisRuleRepository(conn)
        proposals = repository.list_proposed()
    if not proposals:
        print("No hay propuestas pendientes.")
        return 0
    for proposal in proposals:
        rule = proposal.rule
        print(f"{rule.identifier}")
        print(f"  region: {rule.scope.region}")
        print(f"  soil_classes: {rule.scope.soil_classes}")
        print(f"  spatial: {_format_spatial(rule)}")
        print(f"  building_origin: {rule.attributes.building_origin}")
        print(f"  outcome: {rule.outcome}")
        print(f"  authority: {rule.authority}")
        print(
            f"  cita: {rule.citation.norm.title} — {rule.citation.article} "
            f"(vigente desde {rule.citation.in_force_since}, "
            f"{rule.citation.url or rule.citation.norm.url})"
        )
        print(f"  explicación: {proposal.explanation}")
        print(
            f"  coste: ${proposal.usage.cost_usd:.6f} "
            f"({proposal.usage.prompt_tokens} + {proposal.usage.completion_tokens} tokens, "
            f"modelo {proposal.usage.model})"
        )
        print()
    return 0


def _review_approve(args: argparse.Namespace) -> int:
    with get_connection() as conn:
        repository = PostgisRuleRepository(conn)
        try:
            repository.approve(args.identifier)
        except RuleProposalNotFoundError as error:
            print(str(error), file=sys.stderr)
            return 1
    print(f"Aprobada: {args.identifier}")
    return 0


def _review_reject(args: argparse.Namespace) -> int:
    with get_connection() as conn:
        repository = PostgisRuleRepository(conn)
        try:
            repository.reject(args.identifier)
        except RuleProposalNotFoundError as error:
            print(str(error), file=sys.stderr)
            return 1
    print(f"Rechazada: {args.identifier}")
    return 0


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _format_provenance(provenance: Provenance) -> str:
    return (
        f"fuente={provenance.source} url={provenance.url} "
        f"fecha={provenance.retrieved_at.isoformat()} licencia={provenance.license}"
    )


def _format_layer(layer: MunicipalLivestockTrailLayer) -> str:
    municipality = layer.municipality
    lines = [
        f"{municipality.ine_code} {municipality.name}",
        f"  aviso: {layer.notice}",
        f"  límite: {_format_provenance(municipality.provenance)}",
        f"  piezas: {len(layer.trails)}",
    ]
    for piece in layer.trails:
        source = piece.source
        lines.append(
            f"    trail_code={source.trail_code} trail_type={source.trail_type} "
            f"name={source.name} area_m2={piece.area_m2:.2f} "
            f"share_of_source={piece.share_of_source * 100:.2f}%"
        )
        lines.append(f"      procedencia: {_format_provenance(piece.provenance)}")
    return "\n".join(lines)


def build_municipal_layers(
    cache_dir: Path,
    offline: bool,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    idem_http_get: HttpGet = livestock_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[MunicipalLivestockTrailLayer, ...]:
    """Composition root de la capa de vías pecuarias por municipio (ADR 0032).

    Un `CachedRawFetcher` por fuente sobre el mismo directorio: la clave es la
    URL completa, así que no colisionan. Con `offline=True` nada llama a la red."""
    ign_fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
    idem_fetcher = CachedRawFetcher(idem_http_get, cache_dir, today, sleep=sleep, offline=offline)
    use_case = BuildMunicipalLivestockTrailLayersUseCase(
        boundaries=IgnAdministrativeUnitsAdapter(
            AMBITO_HITO_BBOX_ETRS89, fetcher=ign_fetcher, today=today
        ),
        trails=LivestockTrailAdapter(fetcher=idem_fetcher, today=today),
        clipper=ShapelyPolygonClipper(),
    )
    return use_case.execute(AMBITO_HITO_INE)


def _vias_pecuarias_municipios(args: argparse.Namespace) -> int:
    try:
        layers = build_municipal_layers(args.cache, args.sin_red)
    except (
        MunicipalBoundaryNotFoundError,
        MunicipalBoundaryUnusableError,
        IncompleteWfsResponseError,
        ClippedGeometryHasHolesError,
        RawCacheMissError,
        RawCacheCorruptError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}", file=sys.stderr)
        return 1
    writer = GeoJsonLivestockTrailLayerWriter(args.salida)
    failed = False
    for layer in layers:
        try:
            path = writer.write(layer)
        except (OSError, LivestockTrailLayerFileError) as error:
            print(
                f"FALLO {layer.municipality.ine_code} {layer.municipality.name}: {error}",
                file=sys.stderr,
            )
            failed = True
            continue
        print(_format_layer(layer))
        print(f"  fichero: {path}")
    return 1 if failed else 0


def _format_forest_layer(layer: MunicipalPublicUtilityForestLayer) -> str:
    municipality = layer.municipality
    lines = [
        f"{municipality.ine_code} {municipality.name}",
        f"  aviso: {layer.notice}",
        f"  límite: {_format_provenance(municipality.provenance)}",
    ]
    if not layer.forests:
        lines.append("  sin montes")
        return "\n".join(lines)
    lines.append(f"  piezas: {len(layer.forests)}")
    for piece in layer.forests:
        source = piece.source
        lines.append(
            f"    forest_code={source.forest_code} name={source.name} area_m2={piece.area_m2:.2f}"
        )
        lines.append(f"      procedencia: {_format_provenance(piece.provenance)}")
    return "\n".join(lines)


def build_municipal_forest_layers(
    cache_dir: Path,
    offline: bool,
    ine_codes: Sequence[str] = AMBITO_HITO_INE,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    idem_http_get: HttpGet = forest_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[MunicipalPublicUtilityForestLayer, ...]:
    """Composition root de la capa de montes de utilidad pública por municipio (historia #220).

    Misma caché cruda inmutable que las demás capas (la clave es la URL completa).
    Con `offline=True` nada llama a la red."""
    ign_fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
    idem_fetcher = CachedRawFetcher(idem_http_get, cache_dir, today, sleep=sleep, offline=offline)
    use_case = BuildMunicipalPublicUtilityForestLayersUseCase(
        boundaries=IgnAdministrativeUnitsAdapter(
            AMBITO_HITO_BBOX_ETRS89, fetcher=ign_fetcher, today=today
        ),
        forests=PublicUtilityForestAdapter(fetcher=idem_fetcher, today=today),
        clipper=ShapelyPolygonClipper(),
    )
    return use_case.execute(ine_codes)


def _montes_utilidad_publica_municipios(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        codes = list(AMBITO_HITO_INE)
    try:
        layers = build_municipal_forest_layers(args.cache, args.sin_red, codes)
        # Todo o nada: se serializan todas las capas antes de escribir ninguna.
        for layer in layers:
            forest_layer_to_geojson_text(layer)
    except (
        MunicipalBoundaryNotFoundError,
        MunicipalBoundaryUnusableError,
        IncompleteWfsResponseError,
        ClippedGeometryHasHolesError,
        RawCacheMissError,
        RawCacheCorruptError,
        PublicUtilityForestLayerFileError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}", file=sys.stderr)
        return 1
    writer = GeoJsonPublicUtilityForestLayerWriter(args.salida)
    failed = False
    for layer in layers:
        try:
            path = writer.write(layer)
        except OSError as error:
            print(
                f"FALLO {layer.municipality.ine_code} {layer.municipality.name}: {error}",
                file=sys.stderr,
            )
            failed = True
            continue
        print(_format_forest_layer(layer))
        print(f"  fichero: {path}")
    return 1 if failed else 0


def _format_easement_layer(layer: MunicipalWatercourseEasementLayer) -> str:
    municipality = layer.municipality
    lines = [
        f"{municipality.ine_code} {municipality.name}",
        f"  aviso: {layer.notice}",
        f"  método: {layer.method}",
        f"  límite: {_format_provenance(municipality.provenance)}",
    ]
    if not layer.strips:
        lines.append("  sin cauces")
        return "\n".join(lines)
    lines.append(f"  franjas: {len(layer.strips)}")
    lines.append(f"  procedencia: {_format_provenance(layer.strips[0].provenance)}")
    return "\n".join(lines)


def build_municipal_easement_layers(
    cache_dir: Path,
    offline: bool,
    ine_codes: Sequence[str] = AMBITO_HITO_INE,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    hydrography_http_get: HttpGet = hydrography_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[MunicipalWatercourseEasementLayer, ...]:
    """Composition root de la capa de servidumbre aproximada de cauces (historia #250).

    Misma caché cruda inmutable que las demás capas. Con `offline=True` nada llama a la red."""
    ign_fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
    water_fetcher = CachedRawFetcher(
        hydrography_http_get, cache_dir, today, sleep=sleep, offline=offline
    )
    use_case = BuildMunicipalWatercourseEasementLayersUseCase(
        boundaries=IgnAdministrativeUnitsAdapter(
            AMBITO_HITO_BBOX_ETRS89, fetcher=ign_fetcher, today=today
        ),
        watercourses=HydrographyAdapter(fetcher=water_fetcher, today=today),
        buffering=ShapelyLineBuffer(),
        clipper=ShapelyPolygonClipper(),
    )
    return use_case.execute(ine_codes)


def _servidumbre_cauces_municipios(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        codes = list(AMBITO_HITO_INE)
    try:
        layers = build_municipal_easement_layers(args.cache, args.sin_red, codes)
        # Todo o nada: se serializan todas las capas con cauces antes de escribir ninguna.
        for layer in layers:
            if layer.strips:
                easement_layer_to_geojson_text(layer)
    except (
        MunicipalBoundaryNotFoundError,
        MunicipalBoundaryUnusableError,
        IncompleteWfsResponseError,
        ClippedGeometryHasHolesError,
        RawCacheMissError,
        RawCacheCorruptError,
        WatercourseDownloadError,
        WatercourseLicenseMissingError,
        WatercourseEasementFileError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}", file=sys.stderr)
        return 1
    writer = GeoJsonWatercourseEasementLayerWriter(args.salida)
    failed = False
    for layer in layers:
        print(_format_easement_layer(layer))
        if not layer.strips:
            continue
        try:
            path = writer.write(layer)
        except OSError as error:
            print(
                f"FALLO {layer.municipality.ine_code} {layer.municipality.name}: {error}",
                file=sys.stderr,
            )
            failed = True
            continue
        print(f"  fichero: {path}")
    return 1 if failed else 0


def build_municipal_road_layers(
    cache_dir: Path,
    offline: bool,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    roadlink_http_get: HttpGet = roadlink_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
    surface: RoadSurface = "unpaved",
) -> MunicipalRoadLayerBatch:
    """Composition root de la capa de camino actual por municipio (historia #27) y,
    con `surface="paved"`, de la red asfaltada que solo sirve de contraparte (#183).

    Misma caché cruda inmutable que las vías pecuarias (la clave es la URL
    completa). Con `offline=True` nada llama a la red."""
    ign_fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
    road_fetcher = CachedRawFetcher(
        roadlink_http_get, cache_dir, today, sleep=sleep, offline=offline
    )
    use_case = BuildMunicipalRoadLayersUseCase(
        boundaries=IgnAdministrativeUnitsAdapter(
            AMBITO_HITO_BBOX_ETRS89, fetcher=ign_fetcher, today=today
        ),
        roads=RoadNetworkAdapter(fetcher=road_fetcher, today=today),
        clipper=ShapelyLineClipper(),
        surface=surface,
    )
    return use_case.execute(AMBITO_HITO_INE)


def _camino_actual_municipios(args: argparse.Namespace) -> int:
    """Dos pasadas (sin asfaltar y asfaltada) con la misma caché. Todo o nada por
    municipio (CA12 de #183): solo se escriben sus dos ficheros si sale en las dos."""
    try:
        batches = {
            surface: build_municipal_road_layers(args.cache, args.sin_red, surface=surface)
            for surface in ROAD_SURFACES
        }
    except (
        MunicipalBoundaryNotFoundError,
        MunicipalBoundaryUnusableError,
        IncompleteWfsResponseError,
        RawCacheMissError,
        RawCacheCorruptError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}", file=sys.stderr)
        return 1
    unpaved, paved = batches["unpaved"], batches["paved"]
    paved_by_ine = {layer.municipality.ine_code: layer for layer in paved.layers}
    failed_ines = {f.ine_code for batch in batches.values() for f in batch.failures}
    writer = GeoJsonRoadLayerWriter(args.salida)
    paved_writer = GeoJsonRoadLayerWriter(args.salida_asfaltado)
    for layer in unpaved.layers:
        ine = layer.municipality.ine_code
        paved_layer = paved_by_ine.get(ine)
        if ine in failed_ines or paved_layer is None:
            continue
        path = writer.write(layer)
        paved_path = paved_writer.write(paved_layer)
        print(f"{ine} {layer.municipality.name}: {len(layer.segments)} tramos -> {path}")
        print(
            f"{ine} {layer.municipality.name}: "
            f"{len(paved_layer.segments)} tramos asfaltados -> {paved_path}"
        )
    for surface, batch in batches.items():
        for failure in batch.failures:
            print(
                f"FALLO {failure.ine_code} {failure.name}: {surface}: {failure.reason}",
                file=sys.stderr,
            )
    return 1 if failed_ines else 0


def build_cadastral_road_layer(
    provincia: str,
    municipio: str,
    municipality_code: str,
    bbox: BBox,
    cache_dir: Path,
    offline: bool,
    http_get: HttpGet = catastro_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> CadastralRoadLayer:
    """Composition root de la capa interna de caminos de Catastro (historia #37, ADR 0028).

    Con `offline=True` nada llama a la red: solo se lee la caché cruda."""
    fetcher = CachedRawFetcher(http_get, cache_dir, today, sleep=sleep, offline=offline)
    adapter = CatastroAreaAdapter(provincia, municipio, fetcher)
    use_case = BuildCadastralRoadLayerUseCase(
        parcels=adapter, land_use=adapter, tile_side_m=CATASTRO_WFS_TILE_SIDE_M
    )
    return use_case.execute(municipality_code, bbox)


def _format_cadastral_road_layer(layer: CadastralRoadLayer, municipio: str) -> str:
    lines = [
        f"{layer.municipality_code} {municipio}",
        f"  teselas: {layer.tiles}",
        f"  parcelas vistas: {layer.parcels_seen}",
        f"  candidatas (número >= 9000): {layer.road_number_candidates}",
        f"  caminos: {len(layer.roads)}",
        f"  excluidas por clase: {len(layer.excluded)}",
        f"  sin clasificar: {len(layer.unclassified)}",
    ]
    for road in layer.roads:
        lines.append(
            f"    {road.cadastral_reference} area_m2={road.area_m2:.1f} "
            f"clases={','.join(road.land_use_classes)}"
        )
    lines.append("Capa de uso interno: no publicable (licencia INSPIRE de Catastro).")
    return "\n".join(lines)


def _bbox_argument(value: str) -> BBox:
    parts = value.split(",")
    try:
        numbers = tuple(float(part) for part in parts)
    except ValueError:
        numbers = ()
    if len(numbers) != 4 or numbers[0] >= numbers[2] or numbers[1] >= numbers[3]:
        raise argparse.ArgumentTypeError(
            f"bbox MINX,MINY,MAXX,MAXY en EPSG:25830 (E,N) esperado: {value!r}"
        )
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def municipality_grid_bbox(
    municipality_code: str,
    cache_dir: Path,
    offline: bool,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> BBox:
    """Bbox de la rejilla de teselas de un municipio (historia #160): el del límite del IGN
    redondeado hacia fuera a metro entero, el mismo que usa `candidatos-corte`."""
    fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
    boundaries = IgnAdministrativeUnitsAdapter(
        AMBITO_HITO_BBOX_ETRS89, fetcher=fetcher, today=today
    )
    [boundary] = boundaries.get_municipal_boundaries([municipality_code])
    return outward_integer_bbox(shape_bbox(boundary.geometry))


def cadastral_road_layer_provenance(layer: CadastralRoadLayer) -> Provenance:
    """Procedencia de la capa: la petición WFS más antigua de sus caminos (fecha real de
    descarga); sin caminos, la fuente genérica con la fecha de hoy."""
    if layer.roads:
        first = min(layer.roads, key=lambda road: road.provenance.retrieved_at)
        return first.provenance
    return Provenance(
        source="catastro_wfs_cp",
        url=catastro_client.WFS_PARCEL_URL,
        retrieved_at=_today(),
        license=CATASTRO_INSPIRE_LICENSE,
        redistribution=Redistribution.INTERNAL_ONLY,
    )


def _format_bbox(bbox: BBox) -> str:
    return ",".join(f"{value:.2f}".rstrip("0").rstrip(".") for value in bbox)


def _catastro_caminos(args: argparse.Namespace) -> int:
    try:
        if args.bbox is None:
            bbox = municipality_grid_bbox(args.codigo, args.cache, args.sin_red)
        else:
            bbox = args.bbox
        layer = build_cadastral_road_layer(
            args.provincia,
            args.municipio,
            args.codigo,
            bbox,
            args.cache_dir,
            args.sin_red,
        )
        # Todo o nada: solo se escribe con la capa completa y validada.
        path = GeoJsonCadastralRoadWriter(args.salida).write(
            layer, cadastral_road_layer_provenance(layer)
        )
    except (
        MunicipalBoundaryNotFoundError,
        MunicipalBoundaryUnusableError,
        AreaQueryLimitExceededError,
        TruncatedAreaQueryError,
        CadastralServiceError,
        RawCacheMissError,
        RawCacheCorruptError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Error al escribir la capa: {error}", file=sys.stderr)
        return 1
    print(_format_cadastral_road_layer(layer, args.municipio))
    print(f"  rejilla: bbox {_format_bbox(bbox)} (teselas de {CATASTRO_WFS_TILE_SIDE_M:g} m)")
    print(f"Capa escrita en {path}")
    return 0


def build_cadastral_zoning_layer(
    bbox: BBox,
    cache_dir: Path,
    offline: bool,
    http_get: HttpGet = catastro_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> CadastralZoningLayer:
    """Composition root de los polígonos catastrales (historia #217): descarga por teselas
    y deja las respuestas en la caché cruda. Con `offline=True` solo se lee la caché."""
    fetcher = CachedRawFetcher(http_get, cache_dir, today, sleep=sleep, offline=offline)
    use_case = ReadCadastralZoningUseCase(
        CatastroZoningAdapter(fetcher), tile_side_m=CATASTRO_WFS_TILE_SIDE_M
    )
    return use_case.execute(bbox)


def _catastro_poligonos(args: argparse.Namespace) -> int:
    try:
        if args.bbox is None:
            bbox = municipality_grid_bbox(args.codigo, args.cache, args.sin_red)
        else:
            bbox = args.bbox
        layer = build_cadastral_zoning_layer(bbox, args.cache_dir, args.sin_red)
    except (
        MunicipalBoundaryNotFoundError,
        MunicipalBoundaryUnusableError,
        AreaQueryLimitExceededError,
        TruncatedAreaQueryError,
        CadastralServiceError,
        RawCacheMissError,
        RawCacheCorruptError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}", file=sys.stderr)
        return 1
    print(f"{args.codigo} polígonos catastrales")
    print(f"  rejilla: bbox {_format_bbox(bbox)} (teselas de {CATASTRO_WFS_TILE_SIDE_M:g} m)")
    print(f"  teselas: {layer.tiles}")
    print(f"  polígonos (nivel 2ndOrder): {len(layer.zones)}")
    for zone in layer.zones:
        print(f"    {zone.label} anillos={len(zone.geometry.rings)}")
    print("Capa de uso interno: caché cruda, no publicable (licencia INSPIRE de Catastro).")
    return 0


def _trail_layer_reader(derived_dir: Path) -> GeoJsonTrailLayerReader:
    """Las capas derivadas de trazados de `discrepancias` y de la tabla de convergencia
    (#315): vía pecuaria, camino actual (∪ asfaltado) y Catastro, en `derived_dir`."""
    return GeoJsonTrailLayerReader(
        {
            "via_pecuaria_oficial": derived_dir / "vias_pecuarias",
            "ign_red_transporte": derived_dir / "camino_actual",
            "catastro_vt": derived_dir / "catastro_caminos",
        },
        # La red asfaltada cuenta como contraparte del IGN, nunca como sujeto (#183).
        complements={"ign_red_transporte": derived_dir / "camino_asfaltado"},
    )


def build_discrepancy_use_case(derived_dir: Path, output_dir: Path) -> DetectDiscrepanciesUseCase:
    """Composition root de la detección de discrepancias (historia #32).

    Lee las capas derivadas de `derived_dir` y escribe el interno y el publicable
    bajo `output_dir`. Sin red ni LLM. El histórico no tiene ruta todavía: cuenta
    como fuente ausente."""
    return DetectDiscrepanciesUseCase(
        layers=_trail_layer_reader(derived_dir),
        internal_sink=GeoJsonDiscrepancyWriter(output_dir / "interno", publishable=False),
        public_sink=GeoJsonDiscrepancyWriter(output_dir / "publicable", publishable=True),
    )


def _municipality_code(value: str) -> str:
    if not (len(value) == 5 and value.isdigit()):
        raise argparse.ArgumentTypeError(f"código INE de 5 dígitos esperado: {value!r}")
    return value


def _discrepancias(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 2
    use_case = build_discrepancy_use_case(args.entrada, args.salida)
    failed = False
    for code in codes:
        started = time.monotonic()
        try:
            report = use_case.execute(code)
        except TrailLayerFormatError as error:
            print(
                f"FALLO {code}: fuente={error.source} fichero={error.path}: {error.detail}",
                file=sys.stderr,
            )
            failed = True
            continue
        except Exception as error:  # un municipio roto no frena el lote
            print(f"FALLO {code}: {type(error).__name__}: {error}", file=sys.stderr)
            failed = True
            continue
        by_kind = Counter(record.discrepancy.kind for record in report.records)
        summary = ", ".join(f"{kind}={by_kind[kind]}" for kind in sorted(by_kind)) or "ninguna"
        absent = [layer.source for layer in report.layers if layer.status is LayerStatus.AUSENTE]
        print(
            f"{code}: {len(report.records)} discrepancias ({summary}); "
            f"capas ausentes: {', '.join(absent) or 'ninguna'}; "
            f"{time.monotonic() - started:.1f} s -> {args.salida}"
        )
    return 1 if failed else 0


class _CachedParcels:
    """`ParcelsInAreaPort` sobre la caché cruda de Catastro. Una tesela que falta
    en la caché (modo `--sin-red`) es «tesela sin datos», no un fallo del lote:
    la señal queda `sin_datos` con su motivo."""

    def __init__(self, inner: CatastroAreaAdapter) -> None:
        self._inner = inner

    def get_parcels_in_bbox(self, bbox: BBox) -> list[Parcel]:
        try:
            return self._inner.get_parcels_in_bbox(bbox)
        except RawCacheMissError as error:
            raise CadastralServiceError(f"tesela no cacheada y sin red: {error}") from error


CUT_CANDIDATES_PROVINCE = "MADRID"


def build_cut_candidate_use_case(
    derived_dir: Path,
    output_dir: Path,
    cache_dir: Path,
    offline: bool,
    catastro_http_get: HttpGet = catastro_client.default_http_getter,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
    boundaries: MunicipalBoundaryPort | None = None,
) -> DetectCutCandidatesUseCase:
    """Composition root de `candidatos-corte` (historia #128, ADR 0039 §8).

    Lee el informe interno de discrepancias de `derived_dir/discrepancias/interno`
    y las parcelas de Catastro (#37, interno) y escribe en `output_dir`. Sin LLM.
    Con `offline=True` nada llama a la red: solo la caché cruda."""
    catastro_fetcher = CachedRawFetcher(
        catastro_http_get, cache_dir / "catastro", today, sleep=sleep, offline=offline
    )
    if boundaries is None:
        ign_fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
        boundaries = IgnAdministrativeUnitsAdapter(
            AMBITO_HITO_BBOX_ETRS89, fetcher=ign_fetcher, today=today
        )
    provider = CadastralParcelSignalProvider(
        parcels_for=lambda code: _CachedParcels(
            CatastroAreaAdapter(CUT_CANDIDATES_PROVINCE, code, catastro_fetcher)
        ),
        boundaries=boundaries,
        tile_side_m=CATASTRO_WFS_TILE_SIDE_M,
    )
    return DetectCutCandidatesUseCase(
        reports=GeoJsonDiscrepancyReportReader(derived_dir / "discrepancias" / "interno"),
        providers=[provider],
        sink=GeoJsonCutCandidateWriter(output_dir),
    )


def _candidatos_corte(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 1
    use_case = build_cut_candidate_use_case(args.entrada, args.salida, args.cache, args.sin_red)
    failed = False
    for code in codes:
        try:
            report = use_case.execute(code)
        except Exception as error:  # un municipio roto no frena el lote
            print(f"FALLO {code}: {type(error).__name__}: {error}", file=sys.stderr)
            failed = True
            continue
        if not report.discrepancy_criterion_id:
            print(f"{code}: sin capas (sin informe de discrepancias); no se escribe fichero")
            continue
        no_data = [s.signal.identifier for s in report.signals if s.status == "sin_datos"]
        print(
            f"{code}: {len(report.candidates)} candidatos; "
            f"señales sin_datos: {', '.join(no_data) or 'ninguna'} -> {args.salida}"
        )
    return 1 if failed else 0


class _LivestockLayers:
    """`MunicipalLivestockTrailLayerPort` sobre el caso de uso de #83 (IDEM + límites del IGN)."""

    def __init__(self, use_case: BuildMunicipalLivestockTrailLayersUseCase) -> None:
        self._use_case = use_case

    def get_layer(self, municipality_code: str) -> MunicipalLivestockTrailLayer:
        [layer] = self._use_case.execute([municipality_code])
        return layer


def build_width_narrowing_use_case(
    output_dir: Path,
    cache_dir: Path,
    offline: bool,
    catastro_http_get: HttpGet = catastro_client.default_http_getter,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    idem_http_get: HttpGet = livestock_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> DetectWidthNarrowingUseCase:
    """Composition root de `estrechamiento-via-pecuaria` (historia #243). Mismas cachés
    crudas que #83 y #128; con `offline=True` nada llama a la red. Sin LLM."""
    ign_fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
    idem_fetcher = CachedRawFetcher(idem_http_get, cache_dir, today, sleep=sleep, offline=offline)
    catastro_fetcher = CachedRawFetcher(
        catastro_http_get, cache_dir / "catastro", today, sleep=sleep, offline=offline
    )
    layers = BuildMunicipalLivestockTrailLayersUseCase(
        boundaries=IgnAdministrativeUnitsAdapter(
            AMBITO_HITO_BBOX_ETRS89, fetcher=ign_fetcher, today=today
        ),
        trails=LivestockTrailAdapter(fetcher=idem_fetcher, today=today),
        clipper=ShapelyPolygonClipper(),
    )
    return DetectWidthNarrowingUseCase(
        layers=_LivestockLayers(layers),
        parcels_for=lambda code: _CachedParcels(
            CatastroAreaAdapter(CUT_CANDIDATES_PROVINCE, code, catastro_fetcher)
        ),
        clipper=ShapelyPolygonClipper(),
        sink=GeoJsonWidthSignalWriter(output_dir),
        tile_side_m=CATASTRO_WFS_TILE_SIDE_M,
    )


def _format_width_report(code: str, report: MunicipalWidthReport, output_dir: Path) -> str:
    if not report.pieces:
        return f"{code}: sin vías pecuarias; no se escribe fichero"
    counts = Counter(piece.assessment.outcome for piece in report.pieces)
    summary = ", ".join(f"{outcome}={counts[outcome]}" for outcome in sorted(counts))
    return f"{code}: {len(report.pieces)} piezas ({summary}) -> {output_dir}"


def _estrechamiento_via_pecuaria(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 1
    use_case = build_width_narrowing_use_case(args.salida, args.cache, args.sin_red)
    failed = False
    for code in codes:
        try:
            report = use_case.execute(code)
        except Exception as error:  # un municipio roto no frena el lote
            print(f"FALLO {code}: {type(error).__name__}: {error}", file=sys.stderr)
            failed = True
            continue
        print(_format_width_report(code, report, args.salida))
    return 1 if failed else 0


def build_candidate_review_use_case(
    curated_dir: Path, candidates_dir: Path, comparison_dir: Path
) -> ListCandidateReviewsUseCase:
    """Composition root de `revision-candidatos` (historia #168, ADR 0047). Solo
    lectores de ficheros: nada con red, base de datos ni LLM, y nada que escriba."""
    return ListCandidateReviewsUseCase(
        candidates=GeoJsonCutCandidateReader(candidates_dir),
        reviews=CuratedCandidateReviewFiles(curated_dir),
        images=JsonComparisonAvailabilityReader(comparison_dir),
        inventory=CuratedCandidateInventoryFiles(curated_dir),
    )


_REVIEW_LABEL = {
    "proposed": "propuesto",
    "approved": "aprobado",
    "rejected": "rechazado",
    "stale": "caducado",
}


def _print_review_listing(listing: MunicipalReviewListing) -> None:
    """Salida literal del Diseño de #168. No imprime notas, `trail_id` del sujeto,
    procedencias ni URLs: el sujeto puede ser un camino de Catastro (CA7)."""
    code = listing.municipality_code
    if listing.outcome == "fallo":
        shown = code if code.isascii() and code.isdigit() and len(code) == 5 else "(no válido)"
        print(f"municipio {shown}: fallo: {listing.error}", file=sys.stderr)
        return
    if listing.outcome == "sin_candidatos":
        unchecked = listing.unchecked_reviews
        codes = (
            f", {listing.unchecked_codes} códigos sin comprobar" if listing.unchecked_codes else ""
        )
        print(f"municipio {code}: sin candidatos ({unchecked} revisiones sin comprobar{codes})")
        return
    state = listing.state
    assert state is not None  # garantizado con "listado"
    reviewed = sum(1 for item in state.candidates if item.review is not None)
    print(
        f"municipio {code}: {len(state.candidates)} candidatos, "
        f"{reviewed + len(state.orphans)} revisiones"
    )
    counts = Counter(item.status for item in state.candidates)
    without_images = 0
    for item in state.candidates:
        candidate_id = item.candidate.candidate_id
        images = listing.images[candidate_id]
        if images != "con_imagenes":
            without_images += 1
        approved = ""
        if item.status == "stale" and item.review is not None:
            approved = f" aprobada={item.review.candidate_sha256}"
        print(
            f"{candidate_id} {_REVIEW_LABEL[item.status]} huella={item.fingerprint}"
            f"{approved} imagenes={images}"
        )
    for orphan in state.orphans:
        print(f"huérfana {orphan.candidate_id} {_REVIEW_LABEL[orphan.status]}")
    print(
        f"resumen {code}: propuestos={counts['proposed']} aprobados={counts['approved']} "
        f"rechazados={counts['rejected']} caducados={counts['stale']} "
        f"huérfanas={len(state.orphans)} sin_imagenes={without_images}"
    )
    if without_images:
        print(f"aviso {code}: {without_images} candidatos sin imágenes de comparación")
    _print_inventory(code, listing.inventory)


def _print_inventory(code: str, inventory: CandidateInventoryState | None) -> None:
    """Códigos de inventario por tramo aprobado y resumen (historia #288). El código es
    público por diseño; el nombre ya pasó los filtros de `name_problem`."""
    if inventory is None:
        return
    for item in inventory.assigned:
        print(f"codigo {item.candidate_id} {item.code} numero={item.number}")
    for candidate_id in inventory.without_code:
        print(f"codigo {candidate_id} sin código")
    for item in inventory.on_unapproved:
        print(f"código sobre tramo no aprobado {item.candidate_id} {item.code}")
    for item in inventory.orphans:
        print(f"código huérfano {item.candidate_id} {item.code}")
    print(
        f"resumen códigos {code}: asignados={len(inventory.assigned)} "
        f"sin_código={len(inventory.without_code)} "
        f"sobre_no_aprobados={len(inventory.on_unapproved)} "
        f"huérfanos={len(inventory.orphans)}"
    )


def _revision_candidatos(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 1
    use_case = build_candidate_review_use_case(args.curated, args.entrada, args.comparacion)
    listings = use_case.execute(codes)
    for listing in listings:
        _print_review_listing(listing)
    return 1 if any(listing.failed for listing in listings) else 0


def _ratio_entry(value: float | None, **counts: int | str | list[str]) -> dict[str, object]:
    return {"valor": None if value is None else round(value, 4), **counts}


def build_measurement_report(
    result: EvaluationResult,
    *,
    label: str | None,
    sha256: str,
    frozen: bool,
    run_date: date,
    candidate_methods: list[str],
    total_cases: int,
) -> dict[str, object]:
    """Informe agregado de `medir-candidatos` (historia #111). Solo ids públicos de caso
    y agregados: ni ids de candidato, ni descripciones, ni referencias catastrales."""
    reasons = Counter(item.reason for item in result.not_evaluable_cases)
    criterion = result.criterion
    return {
        "etiqueta": label,
        "sha256": sha256,
        "congelado": frozen,
        "fecha": run_date.isoformat(),
        "metodo_candidatos": candidate_methods,
        "criterio_casamiento": {
            "identificador": criterion.identifier,
            "version": criterion.version,
            "distancia_max_m": criterion.max_distance_m,
            "solape_min": criterion.min_overlap,
            "paso_muestreo_m": result.sample_step_m,
        },
        "casos_total": total_cases,
        "evaluables": {
            "total": len(result.evaluable_case_ids),
            "casos": list(result.evaluable_case_ids),
        },
        "no_evaluables": {
            "total": len(result.not_evaluable_cases),
            "por_motivo": {reason: reasons[reason] for reason in sorted(reasons)},
            "casos": [
                {"caso": item.case_id, "motivo": item.reason} for item in result.not_evaluable_cases
            ],
        },
        "cobertura": _ratio_entry(
            result.coverage,
            casos_senalados=len(result.flagged_case_ids),
            casos_evaluables=len(result.evaluable_case_ids),
            casos=list(result.flagged_case_ids),
        ),
        "precision": _ratio_entry(
            result.precision,
            cota="inferior",
            candidatos_acertados=result.hit_candidates,
            candidatos_evaluables=result.evaluable_candidates,
        ),
        "candidatos": {
            "no_evaluables": result.not_evaluable_candidates,
            "descartados": result.discarded_candidates,
        },
    }


def _percent(entry: object) -> str:
    value = entry.get("valor") if isinstance(entry, dict) else None
    return "no calculable" if value is None else f"{value * 100:.1f} %"


def _medir_candidatos(args: argparse.Namespace) -> int:
    """Composition root de `medir-candidatos` (historia #111). Sin red ni LLM."""
    casos_path: Path = args.casos
    try:
        raw_cases = cargar_casos(casos_path)
        frozen_versions = versiones(casos_path.parent)
        cases = parse_validation_cases({"casos": raw_cases})
    except (ErrorCongelacion, ValueError, OSError) as error:
        print(f"ERROR en el conjunto de validación: {error}", file=sys.stderr)
        return 1
    sha256 = calcular_huella(casos_path)
    current = frozen_versions[-1] if frozen_versions else None
    frozen = current is not None and current.sha256 == sha256
    if not frozen:
        reason = (
            "no hay ningún CONGELADO*.md"
            if current is None
            else f"la huella no coincide con la de {current.etiqueta}"
        )
        print(f"AVISO: conjunto no congelado: {reason}", file=sys.stderr)

    reader = GeoJsonCutCandidateReader(args.entrada)
    candidates: list[EvaluableCandidate] = []
    with_candidates: set[str] = set()
    methods: set[str] = set()
    try:
        # Los municipios de los casos y los 7 del ámbito (historia #170, ADR 0048 §9): los
        # candidatos de un municipio del ámbito sin casos cuentan como no evaluables, y así
        # `metodo_candidatos` recoge lo que de verdad se midió.
        case_codes = {case.municipality_ine for case in cases if case.municipality_ine}
        for code in sorted(case_codes | set(HITO_MUNICIPALITY_CODES)):
            report = reader.get_report(code)
            if report is None:
                continue
            with_candidates.add(code)
            methods.add(f"{report.method.identifier}@{report.method.version}")
            candidates.extend(
                EvaluableCandidate(c.candidate_id, c.municipality_code, c.stretch)
                for c in report.candidates
            )
        result = EvaluateCandidatesUseCase().execute(
            cases, candidates, with_candidates, MATCH_CRITERION_V1
        )
    except CutCandidateFileError as error:
        print(f"ERROR fichero de candidatos {error.path}: {error.detail}", file=sys.stderr)
        print("No se ha escrito el informe.", file=sys.stderr)
        return 1
    except CandidateEvaluationInputError as error:
        print(f"ERROR en la evaluación: {error}; no se ha escrito el informe.", file=sys.stderr)
        return 1

    report_data = build_measurement_report(
        result,
        label=current.etiqueta if frozen and current is not None else None,
        sha256=sha256,
        frozen=frozen,
        run_date=_today(),
        candidate_methods=sorted(methods),
        total_cases=len(cases),
    )
    if not frozen:
        report_data["aviso"] = "conjunto no congelado: la huella no coincide con la vigente"
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    no_eval = report_data["no_evaluables"]
    assert isinstance(no_eval, dict)
    print(
        f"Casos: {len(cases)}; evaluables: {len(result.evaluable_case_ids)}; "
        f"no evaluables: {no_eval['total']} {no_eval['por_motivo']}\n"
        f"Cobertura: {_percent(report_data['cobertura'])} "
        f"({len(result.flagged_case_ids)}/{len(result.evaluable_case_ids)} casos)\n"
        f"Precisión (cota inferior): {_percent(report_data['precision'])} "
        f"({result.hit_candidates}/{result.evaluable_candidates} candidatos)\n"
        f"-> {args.salida}"
    )
    return 0


def build_publish_use_case(
    derived_dir: Path,
    output_dir: Path,
    size_threshold_bytes: int = PUBLISH_SIZE_THRESHOLD_BYTES,
) -> PublishLayersUseCase:
    """Composition root de `publicar-capas` (historia #84, ADR 0036).

    Lee las mismas capas derivadas que `discrepancias` y escribe en `output_dir`
    (normalmente `site/datos`). Sin red."""
    return PublishLayersUseCase(
        layers=GeoJsonTrailLayerReader(
            {
                "via_pecuaria_oficial": derived_dir / "vias_pecuarias",
                "ign_red_transporte": derived_dir / "camino_actual",
            }
        ),
        sink=GeoJsonPublishedLayerSink(
            output_dir, REPOSITORY_URL, size_threshold_bytes=size_threshold_bytes
        ),
    )


_REGENERATE_HINT = (
    "regenera la capa con vias-pecuarias-municipios --sin-red o camino-actual-municipios --sin-red"
)


def build_publish_coverage_use_case(
    cache_dir: Path,
    output_dir: Path,
    offline: bool,
    ign_http_get: HttpGet = ign_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> PublishCoverageUseCase:
    """Composition root de `publicar-cobertura` (historia #185, ADR 0051): límites del IGN
    desde la caché cruda (la misma que `vias-pecuarias-municipios`) a `output_dir`."""
    fetcher = CachedRawFetcher(ign_http_get, cache_dir, today, sleep=sleep, offline=offline)
    return PublishCoverageUseCase(
        boundaries=IgnAdministrativeUnitsAdapter(
            AMBITO_HITO_BBOX_ETRS89, fetcher=fetcher, today=today
        ),
        sink=GeoJsonCoverageSink(output_dir),
    )


def _publicar_cobertura(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 2
    use_case = build_publish_coverage_use_case(args.cache, args.salida, args.sin_red)
    try:
        published = use_case.execute(codes)
    except (
        MunicipalBoundaryNotFoundError,
        MunicipalBoundaryUnusableError,
        NotRedistributableError,
        RawCacheMissError,
        RawCacheCorruptError,
        PublishedLayerError,
        OSError,
    ) as error:
        print(f"ERROR al publicar la cobertura: {error}; no se ha publicado nada", file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}; no se ha publicado nada", file=sys.stderr)
        return 1
    print(
        f"publicada {args.salida / published.path} {published.size_bytes} bytes "
        f"({len(published.municipality_codes)} municipios: "
        f"{', '.join(published.municipality_codes)})"
    )
    return 0


def _publicar_capas(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 2
    layer_ids: list[PublishedLayerId] = []
    for layer_id in args.capa or DEFAULT_LAYERS:
        if layer_id not in layer_ids:
            layer_ids.append(layer_id)
    use_case = build_publish_use_case(
        args.entrada, args.salida, size_threshold_bytes=PUBLISH_SIZE_THRESHOLD_BYTES
    )
    try:
        result = use_case.execute(codes, layer_ids)
    except TrailLayerFormatError as error:
        print(
            f"ERROR fichero corrupto {error.path} (fuente {error.source}): {error.detail}; "
            "no se ha publicado nada",
            file=sys.stderr,
        )
        return 1
    except (PublishedLayerError, OSError) as error:
        print(f"ERROR al publicar: {error}; no se ha publicado nada", file=sys.stderr)
        return 1
    for failure in result.failures:
        hint = ""
        if failure.reason.startswith("no redistribuible"):
            hint = f"; {_REGENERATE_HINT}"
        print(
            f"ERROR {failure.layer_id} {failure.municipality_code}: {failure.reason}{hint}",
            file=sys.stderr,
        )
    if result.failures:
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    for layer_id, code in result.empty:
        print(f"capa vacía, no se publica: {layer_id} {code}")
    for published in result.published:
        print(f"publicada {args.salida / published.path} {published.size_bytes} bytes")
        if published.simplification is not None:
            print(
                f"  simplificada con tolerancia {published.simplification.tolerance_m} m "
                f"(antes {published.simplification.bytes_before} bytes)"
            )
        if published.over_threshold:
            print("  AVISO: sigue por encima del umbral")
    return 0


def build_publish_discrepancies_use_case(
    input_dir: Path,
    internal_dir: Path,
    comparison_dir: Path,
    output_dir: Path,
    derived_dir: Path,
    historic: HistoricCoverageSource,
    appears_min_fraction: float = APPEARS_MIN_FRACTION,
    size_threshold_bytes: int = PUBLISH_SIZE_THRESHOLD_BYTES,
) -> PublishDiscrepanciesUseCase:
    """Composition root de `publicar-discrepancias` (historias #94 y #316, ADR 0044 y 0057).

    Lee el fichero publicable de `discrepancias` en `input_dir`, el interno casado en
    `internal_dir`, el manifiesto de `imagenes-comparacion-indicios` en `comparison_dir`,
    las capas derivadas de `derived_dir` y la cobertura histórica de `historic`, y escribe
    en `output_dir` (normalmente `site/datos`). Sin red ni LLM."""
    return PublishDiscrepanciesUseCase(
        reports=GeoJsonDiscrepancyReportReader(input_dir),
        sink=GeoJsonPublishedDiscrepancySink(
            output_dir, REPOSITORY_URL, size_threshold_bytes=size_threshold_bytes
        ),
        internal_reports=GeoJsonDiscrepancyReportReader(internal_dir),
        convergence=BuildConvergenceTablesUseCase(
            layers=_trail_layer_reader(derived_dir),
            historic=historic,
            comparisons=JsonComparisonManifestReader(comparison_dir),
            appears_min_fraction=appears_min_fraction,
            # Un indicio demasiado largo sale sin filas de ortofoto (ADR 0057 §4).
            without_images_if_too_long=True,
        ),
    )


_INDICIO_KINDS: tuple[DiscrepancyKind, ...] = ("AUSENTE_EN_FUENTE", "DESPLAZAMIENTO", "SOLAPE")


def _format_omitted(omitted: OmittedByLicense) -> str:
    return (
        f"omitidos por licencia {omitted.total} "
        f"(lado A: {omitted.subject}, contraparte: {omitted.counterpart})"
    )


def _publicar_discrepancias(args: argparse.Namespace) -> int:
    try:
        threshold = check_appears_min_fraction(args.umbral_aparece)
    except ValueError:
        print("ERROR --umbral-aparece: debe estar en (0, 1]", file=sys.stderr)
        return 1
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 2
    historic = build_historic_coverage_source(args.curated_historico, args.cache_historico)
    use_case = build_publish_discrepancies_use_case(
        args.entrada,
        args.interno,
        args.comparacion,
        args.salida,
        args.capas,
        historic,
        appears_min_fraction=threshold,
    )
    try:
        result = use_case.execute(codes)
    except DiscrepancyFileFormatError as error:
        print(f"ERROR fichero corrupto {error.path}: {error.detail}", file=sys.stderr)
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    except (PublishedLayerError, OSError) as error:
        print(f"ERROR al publicar: {error}", file=sys.stderr)
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    finally:
        # Unidades históricas con veredicto negativo: trabajo en curso, no un fallo (#315).
        for unit_id, reasons in historic.skipped_units():
            print(f"unidad histórica omitida {unit_id}: {'; '.join(reasons)}")
    for failure in result.failures:
        print(f"ERROR {failure.municipality_code}: {failure.reason}", file=sys.stderr)
    if result.failures:
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    omitted = dict(result.omitted)
    for code in result.empty:
        print(f"sin indicios publicables, no se publica: {code}; {_format_omitted(omitted[code])}")
        previous = args.salida / "discrepancias" / f"{code}.geojson"
        if previous.is_file():
            print(
                f"  AVISO: sigue publicado el fichero anterior {previous}; "
                "bórralo a mano si procede"
            )
    kinds = dict(result.kinds)
    for published in result.published:
        by_kind = Counter(kinds[published.municipality_code])
        total = sum(by_kind.values())
        summary = ", ".join(f"{kind}={by_kind[kind]}" for kind in _INDICIO_KINDS)
        print(
            f"publicado {args.salida / published.path} {published.size_bytes} bytes: "
            f"{total} indicios ({summary}); "
            f"{_format_omitted(omitted[published.municipality_code])}"
        )
    return 0


def _offline_http_get(url: str, headers: Mapping[str, str]) -> bytes:
    raise RuntimeError("publicar no usa red")


def build_historic_coverage_source(
    curated: Path, cache: Path, today: Callable[[], date] = _today
) -> CuratedHistoricCoverageSource:
    """Cobertura histórica de la tabla de convergencia desde `curated/historico/` y la caché
    de imágenes de trabajo, sin red ni PostGIS (historia #313, ADR 0057 §7). La comparten
    `publicar-candidatos` (#315) y `publicar-discrepancias` (#316)."""
    files = CuratedHistoricFiles(curated)
    return CuratedHistoricCoverageSource(
        files,
        files,
        IgnHistoricImageAdapter(cache, http_get=_offline_http_get, clock=today, offline=True),
        today,
    )


def build_publish_candidates_use_case(
    measurement_path: Path,
    candidates_dir: Path,
    curated_dir: Path,
    comparison_dir: Path,
    output_dir: Path,
    derived_dir: Path,
    historic: HistoricCoverageSource,
    appears_min_fraction: float = APPEARS_MIN_FRACTION,
    size_threshold_bytes: int = PUBLISH_SIZE_THRESHOLD_BYTES,
) -> PublishCandidatesUseCase:
    """Composition root de `publicar-candidatos` (historias #170, #172 y #315, ADR 0048,
    0049 y 0057). Solo ficheros: nada con red, base de datos ni LLM. La tabla de
    convergencia lee las capas derivadas de `derived_dir` y la cobertura de `historic`."""
    comparisons = JsonComparisonManifestReader(comparison_dir)
    return PublishCandidatesUseCase(
        measurement=JsonMeasurementReportReader(measurement_path),
        candidates=GeoJsonCutCandidateReader(candidates_dir),
        reviews=CuratedCandidateReviewFiles(curated_dir),
        comparisons=comparisons,
        sink=GeoJsonPublishedCandidateSink(
            output_dir, REPOSITORY_URL, size_threshold_bytes=size_threshold_bytes
        ),
        convergence=BuildConvergenceTablesUseCase(
            layers=_trail_layer_reader(derived_dir),
            historic=historic,
            comparisons=comparisons,
            appears_min_fraction=appears_min_fraction,
        ),
    )


def _publicar_candidatos(args: argparse.Namespace) -> int:
    try:
        threshold = check_appears_min_fraction(args.umbral_aparece)
    except ValueError:
        print("ERROR --umbral-aparece: debe estar en (0, 1]", file=sys.stderr)
        return 1
    codes: list[str] = []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible).", file=sys.stderr)
        return 1
    nothing_written = f"No se ha escrito nada en {args.salida}."
    historic = build_historic_coverage_source(args.curated_historico, args.cache_historico)
    use_case = build_publish_candidates_use_case(
        args.medicion,
        args.entrada,
        args.curated,
        args.comparacion,
        args.salida,
        args.capas,
        historic,
        appears_min_fraction=threshold,
    )
    try:
        result = use_case.execute(codes)
    except (PublishedLayerError, OSError) as error:
        print(f"ERROR al publicar: {error}", file=sys.stderr)
        print(nothing_written, file=sys.stderr)
        return 1
    finally:
        # Unidades históricas con veredicto negativo: trabajo en curso, no un fallo (CA14).
        for unit_id, reasons in historic.skipped_units():
            print(f"unidad histórica omitida {unit_id}: {'; '.join(reasons)}")
    if result.measurement_error is not None:
        print(f"ERROR informe de medición: {result.measurement_error}", file=sys.stderr)
        print(nothing_written, file=sys.stderr)
        return 1
    for failure in result.failures:
        code = failure.municipality_code
        shown = code if code.isascii() and code.isdigit() and len(code) == 5 else "(no válido)"
        print(f"ERROR {shown}: {failure.reason}", file=sys.stderr)
    if result.failures:
        print(nothing_written, file=sys.stderr)
        return 1
    counts = {code: (a, p, r) for code, a, p, r in result.counts}
    for published in result.published:
        approved, proposed, rejected = counts[published.municipality_code]
        print(
            f"publicado {args.salida / published.path} {published.size_bytes} bytes: "
            f"{approved} tramos aprobados (sin publicar: {proposed} propuestos, "
            f"{rejected} rechazados)"
        )
    if result.measurement_texts:
        print("medición: " + " ".join(result.measurement_texts[:3]))
    return 0


def build_field_kit_use_case(input_dir: Path, output_dir: Path) -> PublishFieldKitUseCase:
    """Composition root de `publicar-kit-campo` (historia #213): lee lo ya publicado en
    `input_dir` (normalmente `site/datos`) y escribe en `output_dir`. Sin red ni LLM."""
    return PublishFieldKitUseCase(
        source=PublishedLayerFileReader(input_dir),
        sink=FileFieldKitSink(output_dir, REPOSITORY_URL),
    )


def _publicar_kit_campo(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 2
    use_case = build_field_kit_use_case(args.entrada, args.salida)
    try:
        result = use_case.execute(codes, _today())
    except (KitSinkError, OSError) as error:
        print(f"ERROR al escribir el kit: {error}", file=sys.stderr)
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    for failure in result.failures:
        print(f"ERROR {failure.municipality_code}: {failure.reason}", file=sys.stderr)
    if result.failures:
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    for code, included, missing in result.included:
        print(
            f"kit {code}: capas incluidas: {', '.join(included)}; "
            f"capas que faltaban: {', '.join(missing) or 'ninguna'}"
        )
    for published in result.published:
        print(f"publicado {args.salida / published.path} {published.size_bytes} bytes")
    return 0


def build_field_project_use_case(
    input_dir: Path, output_dir: Path, include_in_index: bool = False
) -> PublishFieldProjectUseCase:
    """Composition root de `publicar-kit-campo-qfield` (historia #222): mismas lecturas que
    `publicar-kit-campo`; escribe el proyecto de QField. Sin red ni LLM."""
    return PublishFieldProjectUseCase(
        source=PublishedLayerFileReader(input_dir),
        sink=FileFieldProjectSink(output_dir, REPOSITORY_URL),
        include_in_index=include_in_index,
    )


def _publicar_kit_campo_qfield(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 2
    use_case = build_field_project_use_case(args.entrada, args.salida, args.incluir_en_indice)
    try:
        result = use_case.execute(codes, _today())
    except (KitSinkError, OSError) as error:
        print(f"ERROR al escribir el proyecto de QField: {error}", file=sys.stderr)
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    for failure in result.failures:
        print(f"ERROR {failure.municipality_code}: {failure.reason}", file=sys.stderr)
    if result.failures:
        print(f"No se ha escrito nada en {args.salida}.", file=sys.stderr)
        return 1
    for code, included, missing in result.included:
        print(
            f"qfield {code}: capas incluidas: {', '.join(included)}; "
            f"capas que faltaban: {', '.join(missing) or 'ninguna'}"
        )
    for published in result.published:
        print(f"publicado {args.salida / published.path} {published.size_bytes} bytes")
    if not args.incluir_en_indice:
        print("kit/indice.json sin tocar (usa --incluir-en-indice tras probarlo en QField).")
    return 0


def build_comparison_use_case(
    cache_dir: Path,
    output_dir: Path,
    offline: bool,
    http_get: HttpGet = pnoa_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> BuildMunicipalComparisonsUseCase:
    """Composition root de `imagenes-comparacion` (historia #122, ADR 0042 §5).

    Las imágenes entran en la caché cruda; el manifiesto interno va a `output_dir`.
    Los vuelos `CM_*` van a la IDEM y el resto al IGN (historia #208), con un único
    fetcher (misma caché y pausa). Con `offline=True` nada llama a la red. Nada se escribe
    en `site/`."""
    fetcher = CachedRawFetcher(http_get, cache_dir, today, sleep=sleep, offline=offline)
    windows = FlightRoutedWindowAdapter(
        ign=PnoaWindowAdapter(fetcher), idem=IdemVuelosWindowAdapter(fetcher)
    )
    return BuildMunicipalComparisonsUseCase(
        builder=BuildComparisonImagesUseCase(windows),
        sink=JsonComparisonManifestWriter(output_dir),
    )


def _imagenes_comparacion(args: argparse.Namespace) -> int:
    code: str = args.municipio
    try:
        requests_ = read_stretches(args.tramos, code)
    except StretchFileFormatError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1
    use_case = build_comparison_use_case(args.cache, args.salida, args.sin_red)
    try:
        results = use_case.execute(code, requests_)
    except MunicipalWindowLimitError as error:
        print(f"ERROR {code}: {error}; no se ha pedido ni escrito nada", file=sys.stderr)
        return 1
    except DegenerateStretchError as error:
        print(f"ERROR {code}: tramo sin segmento medible: {error}", file=sys.stderr)
        return 1
    except ComparisonFailuresError as error:
        for stretch_id, slot, error_type in error.failures:
            print(f"FALLO {stretch_id} franja {slot}: {error_type}", file=sys.stderr)
        print(f"No se ha escrito el manifiesto de {code}.", file=sys.stderr)
        return 1
    except (ManifestTooLargeError, OSError) as error:
        print(f"ERROR {code}: {error}; no se ha escrito el manifiesto", file=sys.stderr)
        return 1
    _print_comparisons(results)
    print(f"{code}: {len(results)} tramos -> {args.salida / (code + '.json')}")
    return 0


def _print_comparisons(results: Sequence[StretchComparison]) -> None:
    for comparison in results:
        images = sum(len(window.images) for window in comparison.windows)
        omitted = [o for window in comparison.windows for o in window.omitted]
        reasons = Counter(o.reason for o in omitted)
        print(
            f"{comparison.stretch_id}: {comparison.status}; {len(comparison.windows)} ventanas, "
            f"{images} imágenes, {len(omitted)} omisiones "
            f"(sin_cobertura={reasons['sin_cobertura']})"
        )


def build_indicio_comparison_use_case(
    input_dir: Path,
    cache_dir: Path,
    output_dir: Path,
    offline: bool,
    http_get: HttpGet = pnoa_client.default_http_getter,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> BuildIndicioComparisonsUseCase:
    """Composition root de `imagenes-comparacion-indicios` (historia #314, ADR 0055).

    Lee el informe INTERNO de `input_dir`; reutiliza el enrutado, la caché y la pausa de
    `imagenes-comparacion`. El manifiesto interno va a `output_dir`; nada va a `site/`."""
    return BuildIndicioComparisonsUseCase(
        reports=GeoJsonDiscrepancyReportReader(input_dir),
        comparisons=build_comparison_use_case(
            cache_dir, output_dir, offline, http_get, today, sleep
        ),
    )


def _imagenes_comparacion_indicios(args: argparse.Namespace) -> int:
    codes: list[str] = list(AMBITO_HITO_INE) if args.ambito else []
    codes.extend(code for code in args.municipio if code not in codes)
    if not codes:
        print("Indica --municipio <INE> (repetible) o --ambito.", file=sys.stderr)
        return 2
    use_case = build_indicio_comparison_use_case(
        args.entrada, args.cache, args.salida, args.sin_red
    )
    failed = False
    for code in codes:
        try:
            results = use_case.execute(code)
        except MunicipalWindowLimitError as error:
            print(f"ERROR {code}: {error}; no se ha pedido ni escrito nada", file=sys.stderr)
            failed = True
        except (InternalReportRequiredError, DiscrepancyReportUnavailableError) as error:
            print(f"ERROR {error}; no se ha escrito nada", file=sys.stderr)
            failed = True
        except DiscrepancyFileFormatError as error:
            print(f"ERROR {code}: fichero corrupto {error.path}: {error.detail}", file=sys.stderr)
            failed = True
        except (DegenerateStretchError, InvalidConvergenceStretchError) as error:
            print(f"ERROR {code}: indicio sin trazo medible: {error}", file=sys.stderr)
            failed = True
        except ComparisonFailuresError as error:
            for stretch_id, slot, error_type in error.failures:
                print(f"FALLO {stretch_id} franja {slot}: {error_type}", file=sys.stderr)
            print(f"No se ha escrito el manifiesto de {code}.", file=sys.stderr)
            failed = True
        except (ManifestTooLargeError, OSError) as error:
            print(f"ERROR {code}: {error}; no se ha escrito el manifiesto", file=sys.stderr)
            failed = True
        else:
            _print_comparisons(results)
            print(f"{code}: {len(results)} indicios -> {args.salida / (code + '.json')}")
    return 1 if failed else 0


def build_historic_unit_use_case(
    curated: Path,
    cache: Path,
    offline: bool,
    repository: GeoreferenceRepositoryPort | None = None,
    http_get: HistoricHttpGet = historic_default_http_get,
    today: Callable[[], date] = _today,
) -> HistoricUnitUseCase:
    """Composition root de `historico ajuste|cargar` (historia #139, ADR 0044).

    `repository` solo hace falta para `cargar`; `ajuste` no usa la base."""
    return HistoricUnitUseCase(
        inputs=CuratedHistoricFiles(curated),
        images=IgnHistoricImageAdapter(
            raw_dir=cache, http_get=http_get, clock=today, offline=offline
        ),
        repository=repository,
    )


@contextmanager
def _georeference_repository() -> Iterator[GeoreferenceRepositoryPort]:
    connection = get_connection()
    try:
        yield PostgisGeoreferenceRepository(connection)
    finally:
        connection.close()


def _format_fit(
    spec: UnitSpec,
    image: WorkingImage,
    control_points: ControlPointSet,
    fit: GeoreferenceFit,
    verdict: Verdict,
) -> list[str]:
    points = control_points.points
    loo_limit, max_limit = thresholds_m(spec.scale_denominator)
    quadrants = occupied_quadrants(points, spec.image_width_px, spec.image_height_px)
    lines = [
        f"unidad: {spec.unit_id}",
        f"documento: {spec.document_id}",
        f"imagen_sha256: {image.sha256}",
        f"puntos_sha256: {control_points.sha256}",
        f"candidatos: {len(points)}",
        f"retenidos: {fit.n_retained}",
        f"descartados: {len(points) - fit.n_retained}",
        f"cuadrantes: {quadrants}",
        f"rmse_m: {fit.rmse_m:.2f}",
        f"residuo_max_m: {fit.max_residual_m:.2f}",
        f"rmse_loo_m: {fit.loo_rmse_m:.2f}",
        f"umbral_rmse_loo_m: {loo_limit:.2f}",
        f"umbral_residuo_max_m: {max_limit:.2f}",
        "veredicto: aceptable"
        if verdict.ok
        else "veredicto: rechazada: " + "; ".join(verdict.reasons),
    ]
    retained = {p.point_id: p.retained for p in points}
    for residual in fit.residuals:
        state = "retenido" if retained[residual.point_id] else "descartado"
        loo = "-" if residual.loo_residual_m is None else f"{residual.loo_residual_m:.2f}"
        lines.append(f"{residual.point_id} {state} residuo_m={residual.residual_m:.2f} loo_m={loo}")
    return lines


def _historic_failure(error: Exception) -> str | None:
    """Motivo de un rechazo de la unidad, o `None` si el error no es de los esperados."""
    if isinstance(error, StaleApprovalError):
        return (
            "aprobación caducada: los puntos de control cambiaron; devolver a propuesto: "
            + ", ".join(error.trail_ids)
        )
    if isinstance(
        error,
        GeoreferenceRejectedError
        | RawImageMismatchError
        | WorkingImageUnavailableError
        | CuratedUnitNotFoundError
        | ValueError,
    ):
        return str(error)
    return None


def _historico_ajuste(args: argparse.Namespace) -> int:
    use_case = build_historic_unit_use_case(args.curated, args.cache, args.sin_red)
    try:
        assessment = use_case.assess(args.unit_id)
    except Exception as error:
        reason = _historic_failure(error)
        if reason is None:
            raise
        print(f"ERROR {args.unit_id}: {reason}", file=sys.stderr)
        return 1
    for line in _format_fit(
        assessment.spec,
        assessment.image,
        assessment.control_points,
        assessment.fit,
        assessment.verdict,
    ):
        print(line)
    if not assessment.verdict.ok:
        print(f"ERROR {args.unit_id}: " + "; ".join(assessment.verdict.reasons), file=sys.stderr)
        return 1
    return 0


def _historico_cargar(args: argparse.Namespace) -> int:
    try:
        with _georeference_repository() as repository:
            use_case = build_historic_unit_use_case(
                args.curated, args.cache, args.sin_red, repository
            )
            outcome = use_case.load(args.unit_id)
    except Exception as error:
        reason = _historic_failure(error)
        if reason is None:
            raise
        print(f"ERROR {args.unit_id}: {reason}; no se ha escrito nada", file=sys.stderr)
        return 1
    unit = outcome.unit
    for line in _format_fit(
        unit.spec, unit.image, unit.control_points, unit.fit, Verdict(ok=True, reasons=())
    ):
        print(line)
    sync = outcome.sync
    print(f"insertados: {len(sync.inserted)}")
    print(f"actualizados: {len(sync.updated)}")
    print(f"sin_cambios: {len(sync.unchanged)}")
    print(f"retirados: {len(sync.withdrawn)}")
    print(f"propuestos: {len(outcome.proposed)}")
    print(f"rechazados: {len(outcome.rejected)}")
    for trail_id, reason in outcome.extrapolated:
        print(f"extrapolado {trail_id}: {reason}")
    for attribution in outcome.attributions:
        print(f"atribucion: {attribution}")
    return 0


def build_evidence_use_case(
    curated: Path,
    cache: Path,
    cache_pnoa: Path,
    offline: bool,
    image_http_get: HistoricHttpGet = historic_default_http_get,
    pnoa_http_get: HttpGet = default_png_http_get,
    today: Callable[[], date] = _today,
    sleep: Callable[[float], None] = time.sleep,
) -> BuildTrailEvidenceUseCase:
    """Composition root de `historico evidencia` (historia #140, ADR 0043).

    Lee la entrada humana de `curated`, la imagen de trabajo de `cache` y pide PNOA en PNG a
    través de la caché cruda `cache_pnoa`. La evidencia se escribe junto a la entrada, en
    `curated/<unit_id>/evidencia/`. Con `offline=True` nada llama a la red."""
    fetcher = CachedRawFetcher(pnoa_http_get, cache_pnoa, today, sleep=sleep, offline=offline)
    return BuildTrailEvidenceUseCase(
        inputs=CuratedHistoricFiles(curated),
        images=IgnHistoricImageAdapter(cache, image_http_get, today, sleep=sleep, offline=offline),
        evidence=PngTrailEvidence(cache, curated, PnoaPngSource(fetcher)),
    )


def _historico_evidencia(args: argparse.Namespace) -> int:
    unit_id: str = args.unit_id
    use_case = build_evidence_use_case(args.curated, args.cache, args.cache_pnoa, args.sin_red)
    try:
        records = use_case.execute(unit_id)
    except GeoreferenceRejectedError as error:
        print(f"ERROR {unit_id}: {error}; no se ha escrito nada", file=sys.stderr)
        return 1
    except StaleApprovalError as error:
        print(
            f"ERROR {unit_id}: {error}; devuelve esos trazados a `propuesto` y regenera la "
            "evidencia; no se ha escrito nada",
            file=sys.stderr,
        )
        return 1
    except (
        CuratedUnitNotFoundError,
        RawImageMismatchError,
        WorkingImageUnavailableError,
        EvidenceImageFormatError,
        NotPngResponseError,
        RawCacheMissError,
        RawCacheCorruptError,
        requests.RequestException,
        ValueError,
        OSError,
    ) as error:
        print(f"ERROR {unit_id}: {error}", file=sys.stderr)
        return 1
    folder = args.curated / unit_id / "evidencia"
    for record in records:
        path = folder / f"{record.trail_id}.png"
        print(f"{record.trail_id}: {path} sha256={record.png_sha256}")
    print(f"{unit_id}: {len(records)} evidencias")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parcel_intel.cli.main")
    subparsers = parser.add_subparsers(dest="command", required=True)

    author_parser = subparsers.add_parser(
        "author", help="Proponer una regla a partir de texto normativo real"
    )
    author_parser.add_argument(
        "--layer", choices=_LAYER_CHOICES, required=True, help="Capa de evidencia a la que aplica"
    )
    author_parser.add_argument("--text", type=Path, required=True, help="Fichero con el artículo")
    author_parser.set_defaults(func=_author)

    review_parser = subparsers.add_parser("review", help="Revisar propuestas pendientes")
    review_subparsers = review_parser.add_subparsers(dest="review_command", required=True)

    list_parser = review_subparsers.add_parser("list", help="Listar propuestas pendientes")
    list_parser.set_defaults(func=_review_list)

    approve_parser = review_subparsers.add_parser("approve", help="Aprobar una propuesta")
    approve_parser.add_argument("identifier")
    approve_parser.set_defaults(func=_review_approve)

    reject_parser = review_subparsers.add_parser("reject", help="Rechazar una propuesta")
    reject_parser.add_argument("identifier")
    reject_parser.set_defaults(func=_review_reject)

    vias_parser = subparsers.add_parser(
        "vias-pecuarias-municipios",
        help="Capa de vías pecuarias recortada al término de cada municipio del ámbito",
    )
    vias_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; falla si falta algo"
    )
    vias_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    vias_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/vias_pecuarias"),
        help="Directorio de los ficheros <codigo_ine>.geojson",
    )
    vias_parser.set_defaults(func=_vias_pecuarias_municipios)

    montes_parser = subparsers.add_parser(
        "montes-utilidad-publica-municipios",
        help="Capa de montes de utilidad pública recortada al término de cada municipio",
    )
    montes_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    montes_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito (por defecto)"
    )
    montes_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; falla si falta algo"
    )
    montes_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    montes_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/montes_utilidad_publica"),
        help="Directorio de los ficheros <codigo_ine>.geojson",
    )
    montes_parser.set_defaults(func=_montes_utilidad_publica_municipios)

    servidumbre_parser = subparsers.add_parser(
        "servidumbre-cauces-municipios",
        help="Capa derivada de la zona de servidumbre aproximada de los cauces, por municipio",
    )
    servidumbre_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    servidumbre_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito (por defecto)"
    )
    servidumbre_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; falla si falta algo"
    )
    servidumbre_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    servidumbre_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/servidumbre_cauces"),
        help="Directorio de los ficheros <codigo_ine>.geojson",
    )
    servidumbre_parser.set_defaults(func=_servidumbre_cauces_municipios)

    camino_parser = subparsers.add_parser(
        "camino-actual-municipios",
        help=(
            "Capa de camino actual (tramos sin asfaltar del IGN) y red asfaltada "
            "(solo contraparte) por municipio, en GeoJSON"
        ),
    )
    camino_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; falla si falta algo"
    )
    camino_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    camino_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/camino_actual"),
        help="Directorio de los ficheros <codigo_ine>.geojson",
    )
    camino_parser.add_argument(
        "--salida-asfaltado",
        type=Path,
        default=Path("data/derived/camino_asfaltado"),
        help="Directorio de los ficheros <codigo_ine>.geojson de la red asfaltada (no se publica)",
    )
    camino_parser.set_defaults(func=_camino_actual_municipios)

    caminos_parser = subparsers.add_parser(
        "catastro-caminos",
        help="Capa interna de caminos de Catastro (parcelas >= 9000 de clase VT) de un municipio",
    )
    caminos_parser.add_argument("--provincia", required=True, help="Nombre, p. ej. MADRID")
    caminos_parser.add_argument(
        "--municipio", required=True, help="Nombre, p. ej. 'COLMENAR DE OREJA'"
    )
    caminos_parser.add_argument(
        "--codigo", type=_municipality_code, required=True, help="Código de Catastro, 5 dígitos"
    )
    caminos_parser.add_argument(
        "--bbox",
        type=_bbox_argument,
        default=None,
        help="MINX,MINY,MAXX,MAXY en EPSG:25830 (E,N); por defecto, el límite del IGN "
        "redondeado hacia fuera (la rejilla que usa candidatos-corte)",
    )
    caminos_parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/raw"),
        help="Caché cruda del límite del IGN (solo sin --bbox)",
    )
    caminos_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/catastro_caminos"),
        help="Directorio de los ficheros <codigo>.geojson (uso interno, nunca se publica)",
    )
    caminos_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/raw/catastro"),
        help="Directorio de la caché cruda de Catastro",
    )
    caminos_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; falla si falta algo"
    )
    caminos_parser.set_defaults(func=_catastro_caminos)

    poligonos_parser = subparsers.add_parser(
        "catastro-poligonos",
        help="Descarga y cachea los polígonos catastrales (CadastralZoning) de un municipio",
    )
    poligonos_parser.add_argument(
        "--codigo", type=_municipality_code, required=True, help="Código de Catastro, 5 dígitos"
    )
    poligonos_parser.add_argument(
        "--bbox",
        type=_bbox_argument,
        default=None,
        help="MINX,MINY,MAXX,MAXY en EPSG:25830 (E,N); por defecto, el límite del IGN "
        "redondeado hacia fuera (la misma rejilla que catastro-caminos)",
    )
    poligonos_parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/raw"),
        help="Caché cruda del límite del IGN (solo sin --bbox)",
    )
    poligonos_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/raw/catastro"),
        help="Directorio de la caché cruda de Catastro (uso interno, nunca se publica)",
    )
    poligonos_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; falla si falta una tesela"
    )
    poligonos_parser.set_defaults(func=_catastro_poligonos)

    discrepancias_parser = subparsers.add_parser(
        "discrepancias",
        help="Discrepancias entre las capas de trazados de uno o varios municipios, en GeoJSON",
    )
    discrepancias_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    discrepancias_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    discrepancias_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived"),
        help="Directorio con las capas derivadas (vias_pecuarias/, camino_actual/, ...)",
    )
    discrepancias_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/discrepancias"),
        help="Directorio de salida (interno/ y publicable/)",
    )
    discrepancias_parser.set_defaults(func=_discrepancias)

    candidatos_parser = subparsers.add_parser(
        "candidatos-corte",
        help="Candidatos a corte de uno o varios municipios, en un fichero interno (no se publica)",
    )
    candidatos_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    candidatos_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    candidatos_parser.add_argument(
        "--sin-red",
        action="store_true",
        help="Solo la caché cruda; una tesela que falta deja la señal sin_datos",
    )
    candidatos_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived"),
        help="Directorio con las capas derivadas (discrepancias/interno/)",
    )
    candidatos_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    candidatos_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/candidatos/interno"),
        help="Directorio de salida de los ficheros internos <codigo_ine>.geojson",
    )
    candidatos_parser.set_defaults(func=_candidatos_corte)

    estrechamiento_parser = subparsers.add_parser(
        "estrechamiento-via-pecuaria",
        help="Vías pecuarias donde la parcela de camino de Catastro es mucho más estrecha que la "
        "franja oficial, en un fichero interno (no se publica)",
    )
    estrechamiento_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    estrechamiento_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    estrechamiento_parser.add_argument(
        "--sin-red",
        action="store_true",
        help="Solo la caché cruda; una tesela que falta deja las piezas afectadas sin_datos",
    )
    estrechamiento_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    estrechamiento_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/estrechamiento/interno"),
        help="Directorio de salida de los ficheros internos <codigo_ine>.geojson",
    )
    estrechamiento_parser.set_defaults(func=_estrechamiento_via_pecuaria)

    revision_parser = subparsers.add_parser(
        "revision-candidatos",
        help="Estado de la revisión humana de los candidatos a corte (sin red; no escribe nada)",
    )
    revision_parser.add_argument(
        "--municipio",
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible); uno no válido falla solo ese municipio",
    )
    revision_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    revision_parser.add_argument(
        "--curated",
        type=Path,
        default=Path("curated/candidatos"),
        help="Entrada humana versionada: <ine>/revision.json e <ine>/inventario.json",
    )
    revision_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived/candidatos/interno"),
        help="Directorio con los ficheros internos <codigo_ine>.geojson de candidatos",
    )
    revision_parser.add_argument(
        "--comparacion",
        type=Path,
        default=Path("data/derived/comparacion"),
        help="Directorio de los manifiestos <codigo_ine>.json de imágenes de comparación",
    )
    revision_parser.set_defaults(func=_revision_candidatos)

    medir_parser = subparsers.add_parser(
        "medir-candidatos",
        help="Cobertura y precisión de los candidatos a corte frente al conjunto de validación",
    )
    medir_parser.add_argument(
        "--casos",
        type=Path,
        default=Path("docs/validacion/casos.json"),
        help="casos.json; los CONGELADO*.md se buscan en su directorio",
    )
    medir_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived/candidatos/interno"),
        help="Directorio con los ficheros internos <codigo_ine>.geojson de candidatos",
    )
    medir_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/validacion/medicion.json"),
        help="Informe agregado en JSON (fichero de datos, no se publica)",
    )
    medir_parser.set_defaults(func=_medir_candidatos)

    publicar_parser = subparsers.add_parser(
        "publicar-capas",
        help="Publica vías pecuarias y camino actual en site/datos (EPSG:4326, con procedencia)",
    )
    publicar_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    publicar_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    publicar_parser.add_argument(
        "--capa",
        choices=sorted(PUBLISHED_LAYERS),
        action="append",
        default=[],
        help="Capa a publicar (repetible; por defecto, todas)",
    )
    publicar_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived"),
        help="Directorio con las capas derivadas (vias_pecuarias/, camino_actual/)",
    )
    publicar_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("site/datos"),
        help="Directorio publicado del sitio",
    )
    publicar_parser.set_defaults(func=_publicar_capas)

    cobertura_parser = subparsers.add_parser(
        "publicar-cobertura",
        help="Publica en site/datos los contornos de los municipios cubiertos (visor)",
    )
    cobertura_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    cobertura_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    cobertura_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; falla si falta algo"
    )
    cobertura_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    cobertura_parser.add_argument(
        "--salida", type=Path, default=Path("site/datos"), help="Directorio publicado del sitio"
    )
    cobertura_parser.set_defaults(func=_publicar_cobertura)

    indicios_parser = subparsers.add_parser(
        "publicar-discrepancias",
        help="Publica los indicios de discrepancia en site/datos (EPSG:4326, con su ficha)",
    )
    indicios_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    indicios_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    indicios_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived/discrepancias/publicable"),
        help="Directorio con los ficheros publicables de `discrepancias`",
    )
    indicios_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("site/datos"),
        help="Directorio publicado del sitio",
    )
    indicios_parser.add_argument(
        "--interno",
        type=Path,
        default=Path("data/derived/discrepancias/interno"),
        help="Directorio con los ficheros internos de `discrepancias` (nunca se publican)",
    )
    indicios_parser.add_argument(
        "--comparacion",
        type=Path,
        default=Path("data/derived/comparacion_indicios"),
        help="Directorio con los manifiestos <ine>.json de `imagenes-comparacion-indicios`",
    )
    indicios_parser.add_argument(
        "--capas",
        type=Path,
        default=Path("data/derived"),
        help="Capas derivadas de la tabla de convergencia (vias_pecuarias, camino_actual, "
        "camino_asfaltado, catastro_caminos)",
    )
    indicios_parser.add_argument(
        "--curated-historico",
        type=Path,
        default=Path("curated/historico"),
        help="Unidades históricas georreferenciadas (entrada humana versionada)",
    )
    indicios_parser.add_argument(
        "--cache-historico",
        type=Path,
        default=Path("data/raw/ign_planimetrias"),
        help="Caché de las imágenes de trabajo de las unidades históricas (sin red)",
    )
    indicios_parser.add_argument(
        "--umbral-aparece",
        # Sin validar aquí: argparse saldría con 2 y el criterio pide 1 (#315, #316).
        type=float,
        default=APPEARS_MIN_FRACTION,
        help="Fracción mínima del tramo para «Aparece», en (0, 1] (por defecto 0,5)",
    )
    indicios_parser.set_defaults(func=_publicar_discrepancias)

    kit_parser = subparsers.add_parser(
        "publicar-kit-campo",
        help="Exporta los tramos publicados de un municipio a GPX y KML en site/datos/kit",
    )
    kit_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    kit_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    kit_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("site/datos"),
        help="Directorio publicado con las capas (site/datos)",
    )
    kit_parser.add_argument(
        "--salida", type=Path, default=Path("site/datos"), help="Directorio publicado del sitio"
    )
    kit_parser.set_defaults(func=_publicar_kit_campo)

    qfield_parser = subparsers.add_parser(
        "publicar-kit-campo-qfield",
        help="Genera el proyecto de QField (GeoPackage, .qgs y ZIP) en site/datos/kit/<ine>/qfield",
    )
    qfield_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    qfield_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    qfield_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("site/datos"),
        help="Directorio publicado con las capas (site/datos)",
    )
    qfield_parser.add_argument(
        "--salida", type=Path, default=Path("site/datos"), help="Directorio publicado del sitio"
    )
    qfield_parser.add_argument(
        "--incluir-en-indice",
        action="store_true",
        help="Añade el proyecto a kit/indice.json (solo tras abrirlo en QField)",
    )
    qfield_parser.set_defaults(func=_publicar_kit_campo_qfield)

    tramos_parser = subparsers.add_parser(
        "publicar-candidatos",
        help="Publica los tramos a verificar aprobados en site/datos (EPSG:4326, con su ficha)",
    )
    tramos_parser.add_argument(
        "--municipio",
        action="append",
        default=[],
        help="Código INE (repetible); solo se publican los de código de Catastro verificado",
    )
    tramos_parser.add_argument(
        "--medicion",
        type=Path,
        default=Path("data/derived/validacion/medicion.json"),
        help="Informe de `medir-candidatos` de la misma versión del método",
    )
    tramos_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived/candidatos/interno"),
        help="Directorio con los ficheros internos <codigo_ine>.geojson de candidatos",
    )
    tramos_parser.add_argument(
        "--curated",
        type=Path,
        default=Path("curated/candidatos"),
        help="Entrada humana versionada: <ine>/revision.json",
    )
    tramos_parser.add_argument(
        "--comparacion",
        type=Path,
        default=Path("data/derived/comparacion"),
        help="Directorio con los manifiestos <ine>.json de `imagenes-comparacion`",
    )
    tramos_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("site/datos"),
        help="Directorio publicado del sitio",
    )
    tramos_parser.add_argument(
        "--capas",
        type=Path,
        default=Path("data/derived"),
        help="Capas derivadas de la tabla de convergencia (vias_pecuarias, camino_actual, "
        "camino_asfaltado, catastro_caminos)",
    )
    tramos_parser.add_argument(
        "--curated-historico",
        type=Path,
        default=Path("curated/historico"),
        help="Unidades históricas georreferenciadas (entrada humana versionada)",
    )
    tramos_parser.add_argument(
        "--cache-historico",
        type=Path,
        default=Path("data/raw/ign_planimetrias"),
        help="Caché de las imágenes de trabajo de las unidades históricas (sin red)",
    )
    tramos_parser.add_argument(
        "--umbral-aparece",
        # Sin validar aquí: argparse saldría con 2 y el criterio pide 1 (#315).
        type=float,
        default=APPEARS_MIN_FRACTION,
        help="Fracción mínima del tramo para «Aparece», en (0, 1] (por defecto 0,5)",
    )
    tramos_parser.set_defaults(func=_publicar_candidatos)

    comparacion_parser = subparsers.add_parser(
        "imagenes-comparacion",
        help="Imágenes de comparación de los tramos de un municipio y su manifiesto interno",
    )
    comparacion_parser.add_argument(
        "--municipio", type=_municipality_code, required=True, help="Código INE de 5 dígitos"
    )
    comparacion_parser.add_argument(
        "--tramos", type=Path, required=True, help="GeoJSON de tramos en EPSG:25830, con id opaco"
    )
    comparacion_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; un hueco es un fallo"
    )
    comparacion_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    comparacion_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/comparacion"),
        help="Directorio de los manifiestos <codigo_ine>.json",
    )
    comparacion_parser.set_defaults(func=_imagenes_comparacion)

    indicios_parser = subparsers.add_parser(
        "imagenes-comparacion-indicios",
        help="Imágenes de comparación de los indicios publicables y su manifiesto interno",
    )
    indicios_parser.add_argument(
        "--municipio",
        type=_municipality_code,
        action="append",
        default=[],
        help="Código INE de 5 dígitos (repetible)",
    )
    indicios_parser.add_argument(
        "--ambito", action="store_true", help="Los 7 municipios del primer hito"
    )
    indicios_parser.add_argument(
        "--entrada",
        type=Path,
        default=Path("data/derived/discrepancias/interno"),
        help="Directorio del informe INTERNO de discrepancias (<ine>.geojson)",
    )
    indicios_parser.add_argument(
        "--sin-red", action="store_true", help="Solo la caché cruda; un hueco es un fallo"
    )
    indicios_parser.add_argument(
        "--cache", type=Path, default=Path("data/raw"), help="Directorio de la caché cruda"
    )
    indicios_parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/derived/comparacion_indicios"),
        help="Directorio de los manifiestos <codigo_ine>.json (interno, no se publica)",
    )
    indicios_parser.set_defaults(func=_imagenes_comparacion_indicios)

    historico_parser = subparsers.add_parser(
        "historico", help="Trazados históricos georreferenciados de una unidad (ADR 0043)"
    )
    historico_subparsers = historico_parser.add_subparsers(dest="historico_command", required=True)
    for name, helptext, handler in (
        (
            "ajuste",
            "Medir el ajuste de los puntos de control de una unidad (sin base)",
            _historico_ajuste,
        ),
        ("cargar", "Cargar en PostGIS los trazados aprobados de una unidad", _historico_cargar),
    ):
        unit_parser = historico_subparsers.add_parser(name, help=helptext)
        unit_parser.add_argument("unit_id", help="Identificador de la unidad, p. ej. 280075-w1")
        unit_parser.add_argument(
            "--curated",
            type=Path,
            default=Path("curated/historico"),
            help="Directorio con una carpeta por unidad",
        )
        unit_parser.add_argument(
            "--cache",
            type=Path,
            default=Path("data/raw/ign_planimetrias"),
            help="Caché cruda de las imágenes de trabajo",
        )
        unit_parser.add_argument(
            "--sin-red", action="store_true", help="Solo la caché; sin imagen es un error"
        )
        unit_parser.set_defaults(func=handler)
    evidencia_parser = historico_subparsers.add_parser(
        "evidencia",
        help="PNG de dos paneles de cada trazado propuesto de una unidad, para revisarlo",
    )
    evidencia_parser.add_argument("unit_id", help="Unidad, p. ej. 280075-w1")
    evidencia_parser.add_argument(
        "--curated",
        type=Path,
        default=Path("curated/historico"),
        help="Entrada humana versionada; la evidencia se escribe en <unit_id>/evidencia/",
    )
    evidencia_parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/raw/ign_planimetrias"),
        help="Caché cruda de las imágenes de trabajo",
    )
    evidencia_parser.add_argument(
        "--cache-pnoa",
        type=Path,
        default=Path("data/raw/pnoa_ma"),
        help="Caché cruda de las respuestas PNG de PNOA",
    )
    evidencia_parser.add_argument(
        "--sin-red", action="store_true", help="Solo las cachés; un hueco es un fallo"
    )
    evidencia_parser.set_defaults(func=_historico_evidencia)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    exit_code: int = args.func(args)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
