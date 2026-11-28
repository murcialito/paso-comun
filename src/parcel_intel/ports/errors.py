from collections.abc import Sequence

from parcel_intel.domain.orthophoto import HistoricalVintage


class RoadNetworkError(Exception):
    """La respuesta del servicio de red de transporte (`roadlink`) no es
    utilizable: error HTTP, JSON malformado, estructura inesperada o
    resultado truncado -- historia #25. Nunca se devuelve una lista vacía
    en su lugar: "sin tramos" y "no se pudo consultar" son cosas distintas."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Respuesta de la red de transporte no utilizable: {detail}")
        self.detail = detail


class WatercourseDownloadError(Exception):
    """La descarga paginada de cauces de un bbox no se pudo completar (página
    ausente de la caché con --sin-red, respuesta ilegible, enlace `next` ajeno
    al servicio o más páginas del máximo) -- historia #257. Nunca se devuelve
    una lista parcial."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Descarga de cauces incompleta: {detail}")
        self.detail = detail


class MunicipalBoundaryNotFoundError(Exception):
    """Alguno de los códigos INE pedidos no está en la respuesta del servicio
    de unidades administrativas -- historia #35. Nunca se devuelve una lista
    parcial de límites."""

    def __init__(self, missing_codes: Sequence[str]) -> None:
        self.missing_codes = tuple(missing_codes)
        super().__init__(f"Sin límite municipal para los códigos INE: {', '.join(missing_codes)}")


class ClippedGeometryHasHolesError(Exception):
    """El recorte de dos superficies produjo un polígono con huecos, que
    `Geometry` no modela: descartarlos en silencio rompería la suma de
    áreas -- historia #36."""

    def __init__(self, interior_rings: int) -> None:
        super().__init__(f"El recorte produjo {interior_rings} hueco(s), no modelados")
        self.interior_rings = interior_rings


class IncompleteWfsResponseError(Exception):
    """`numberMatched` de una respuesta WFS no coincide con las features
    parseadas: respuesta truncada o estructura inesperada -- historia #35."""

    def __init__(self, url: str, number_matched: int, number_parsed: int) -> None:
        super().__init__(
            f"Respuesta WFS incompleta: numberMatched={number_matched} pero se parsearon "
            f"{number_parsed} features ({url})"
        )
        self.url = url
        self.number_matched = number_matched
        self.number_parsed = number_parsed


class MunicipalBoundaryUnusableError(Exception):
    """La respuesta del servicio de unidades administrativas no es utilizable:
    error HTTP, XML malformado, estructura inesperada o un municipio pedido
    con huecos (`gml:interior`, no modelados) -- historia #35."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Límites municipales no utilizables: {detail}")
        self.detail = detail


class InvalidCadastralReferenceError(Exception):
    """La referencia catastral no tiene un formato reconocible (14 o 20 caracteres)."""

    def __init__(self, reference: str) -> None:
        super().__init__(f"Referencia catastral con formato no reconocido: {reference!r}")
        self.reference = reference


class ParcelNotFoundError(Exception):
    """No existe ninguna parcela para la referencia catastral dada."""

    def __init__(self, reference: str) -> None:
        super().__init__(
            f"No se encontró ninguna parcela para la referencia catastral: {reference!r}"
        )
        self.reference = reference


class ParcelNotCachedError(Exception):
    """No hay ningún informe cacheado para la referencia catastral dada.

    Señal de "cache miss" para `ParcelRepositoryPort.get_report` (ver ADR
    0005) — vive aquí, no en `adapters/postgis/`, porque `application/`
    necesita capturarla y solo puede importar `ports`/`domain`.
    """

    def __init__(self, reference: str) -> None:
        super().__init__(
            f"No hay ningún informe cacheado para la referencia catastral: {reference!r}"
        )
        self.reference = reference


class InvalidHistoricalVintageError(Exception):
    """El año o vuelo histórico pedido no está entre los confirmados en REGISTRY.md."""

    def __init__(self, vintage: object) -> None:
        super().__init__(f"Año o vuelo histórico no reconocido: {vintage!r}")
        self.vintage = vintage


class NoScenesAfterCloudFilterError(Exception):
    """Ninguna escena de Sentinel-2 encontrada para el bbox/rango de fechas
    pedidos pasa el umbral de nubosidad."""

    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        start: object,
        end: object,
        max_cloud_cover_percent: float,
    ) -> None:
        super().__init__(
            f"Ninguna escena Sentinel-2 por debajo de {max_cloud_cover_percent}% de nubes "
            f"para bbox={bbox}, rango {start}..{end}"
        )
        self.bbox = bbox
        self.start = start
        self.end = end
        self.max_cloud_cover_percent = max_cloud_cover_percent


class InvalidBulletinDayError(Exception):
    """El día pedido no tiene formato AAAAMMDD válido (longitud, no numérico,
    o fecha de calendario inexistente). Se lanza antes de cualquier petición
    de red."""

    def __init__(self, day: str) -> None:
        super().__init__(f"Día de boletín con formato no reconocido: {day!r}")
        self.day = day


class BulletinNotPublishedError(Exception):
    """No hay sumario publicado para el día pedido (fin de semana, festivo, o
    fecha futura todavía sin publicar) — el proveedor no distingue estos
    casos (confirmado real en REGISTRY.md), así que tampoco este cliente."""

    def __init__(self, day: str) -> None:
        super().__init__(f"No hay sumario de boletín publicado para el día: {day!r}")
        self.day = day


class InvalidRuleLayerError(Exception):
    """La capa de evidencia pedida al flujo de autoría de reglas no es
    ninguno de los valores cerrados que `domain/rule.py` reconoce hoy
    (`dph_cauce`, `via_pecuaria`, `red_natura_plan`) -- historia 0019.
    Vive aquí, no en `adapters/deepseek/`, porque la lanza
    `AuthorRuleProposalUseCase` (`application/`), que solo puede
    importar `ports`/`domain` -- mismo criterio que
    `InvalidCadastralReferenceError`."""

    def __init__(self, layer: str) -> None:
        super().__init__(f"Capa de evidencia no reconocida para autoría de reglas: {layer!r}")
        self.layer = layer


class InvalidLlmProposalError(Exception):
    """La respuesta del LLM no se pudo interpretar como una propuesta de
    `Rule` válida (JSON inválido, campo fuera de los tipos cerrados, o
    `meters` incoherente con la capa pedida) -- historia 0019. Ninguna
    propuesta a medias llega a persistirse cuando se lanza este error."""

    def __init__(self, raw_content: str) -> None:
        super().__init__(
            f"La respuesta del LLM no es una propuesta de regla válida: {raw_content[:200]!r}"
        )
        self.raw_content = raw_content


class RuleProposalNotFoundError(Exception):
    """No hay ninguna propuesta `proposed` con ese identificador -- el
    identificador no existe, o ya fue revisada (aprobada/rechazada). Esta
    ambigüedad se acepta explícitamente para el MVP (ADR 0019), mismo
    nivel de rigor que `NoLandClassificationFoundError` ya acepta."""

    def __init__(self, identifier: str) -> None:
        super().__init__(
            f"No hay ninguna propuesta de regla pendiente con el identificador: {identifier!r}"
        )
        self.identifier = identifier


class HistoricTrailNotFoundError(Exception):
    """No existe ningún trazado histórico con ese identificador -- historia
    #19, ADR 0026. Vive aquí porque la capturará `application/` (#20)."""

    def __init__(self, identifier: str) -> None:
        super().__init__(f"No existe ningún trazado histórico con el identificador: {identifier!r}")
        self.identifier = identifier


class HistoricTrailNotPendingError(Exception):
    """No hay ningún trazado histórico `proposed` con ese identificador en la
    base: no existe o ya fue revisado -- historia #19, ADR 0026. Misma
    ambigüedad aceptada que `RuleProposalNotFoundError`."""

    def __init__(self, identifier: str) -> None:
        super().__init__(
            "No hay ningún trazado histórico pendiente de revisión con el identificador: "
            f"{identifier!r}"
        )
        self.identifier = identifier


class GeoreferenceUnitNotFoundError(Exception):
    """No existe ninguna unidad georreferenciada con ese `unit_id` -- historia
    #138, ADR 0044."""

    def __init__(self, unit_id: str) -> None:
        super().__init__(f"No existe ninguna unidad georreferenciada: {unit_id!r}")
        self.unit_id = unit_id


class TrailLayerUnavailableError(Exception):
    """No hay capa de trazados para esa fuente y municipio -- historia #31,
    ADR 0030. Significa solo "no hay capa" (p. ej. sin fichero derivado en
    caché). Cualquier otra excepción es un fallo real y no se convierte en
    "ausente": si se convirtiera, una fuente rota bajaría el número de
    discrepancias sin que nadie lo notara."""

    def __init__(self, source: str, municipality_code: str, reason: str) -> None:
        super().__init__(
            f"Sin capa de trazados {source!r} para el municipio {municipality_code}: {reason}"
        )
        self.source = source
        self.municipality_code = municipality_code
        self.reason = reason


class TrailLayerFileError(Exception):
    """El fichero de una capa derivada existe pero no es válido (historia #315). `source` es
    el `SourceKind`. Vive aquí porque `publicar-candidatos` lo captura en `application/`,
    que no puede importar el error concreto del adapter (`TrailLayerFormatError`, que
    hereda de este). Su mensaje puede citar rutas y detalles de pydantic: quien lo capture
    para un mensaje público usa solo `source`."""

    source: str


class DiscrepancyReportUnavailableError(Exception):
    """No hay informe de discrepancias escrito para ese municipio -- historia
    #94. Significa solo "no hay fichero"; un fichero corrupto es otro error."""

    def __init__(self, municipality_code: str, reason: str) -> None:
        super().__init__(
            f"Sin informe de discrepancias para el municipio {municipality_code}: {reason}"
        )
        self.municipality_code = municipality_code
        self.reason = reason


class InternalReportRequiredError(Exception):
    """Se pidió el informe INTERNO de discrepancias y se leyó el publicable (redactado,
    sin las zonas de Catastro) -- historia #314. No hay trazo del que sacar imágenes."""

    def __init__(self, municipality_code: str) -> None:
        super().__init__(
            f"{municipality_code}: el informe es la variante publicable (redactada); "
            "hace falta el interno (data/derived/discrepancias/interno)"
        )
        self.municipality_code = municipality_code


class CutCandidateFileError(Exception):
    """El fichero de candidatos a corte existe pero no se puede leer o no cumple
    el formato -- historia #128. No equivale a «sin capas» (`None`)."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Fichero de candidatos a corte no válido {path}: {detail}")
        self.path = path
        self.detail = detail


class CandidateReviewFileError(Exception):
    """El fichero de revisión de candidatos (`curated/candidatos/<ine>/revision.json`)
    no cumple el formato -- historia #168, ADR 0047. Se rechaza entero. El detalle
    nombra campos, posiciones y `candidate_id` ya validados; nunca el valor rechazado."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Fichero de revisión de candidatos no válido {path}: {detail}")
        self.path = path
        self.detail = detail


class CandidateInventoryFileError(Exception):
    """El fichero de códigos de inventario (`curated/candidatos/<ine>/inventario.json`)
    no cumple el formato -- historia #288, ADR 0047. Se rechaza entero. El detalle
    nombra campos y `candidate_id` ya validados; nunca el valor rechazado."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Fichero de inventario de candidatos no válido {path}: {detail}")
        self.path = path
        self.detail = detail


class ComparisonManifestFileError(Exception):
    """El manifiesto de imágenes de comparación de un municipio existe pero no se
    puede leer, no cumple el formato o es de otro municipio -- historia #168. No
    equivale a «sin manifiesto» (`None`)."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Manifiesto de imágenes de comparación no válido {path}: {detail}")
        self.path = path
        self.detail = detail


class MeasurementReportUnavailableError(Exception):
    """El informe de medición de `medir-candidatos` no existe, no se puede leer o le
    falta alguna clave que se usa -- historia #170, ADR 0048. El detalle nombra la clave,
    nunca su valor."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Informe de medición no disponible {path}: {detail}")
        self.path = path
        self.detail = detail


class NoLandClassificationFoundError(Exception):
    """Ningún polígono de clasificación de suelo intersecta el bbox pedido.
    El servicio no distingue "fuera de cobertura de la Comunidad de Madrid"
    de "hueco real dentro de la cobertura" — confirmado real en
    REGISTRY.md (ambos casos devuelven una FeatureCollection vacía
    idéntica, 200 OK, 0 features) — así que tampoco este cliente los
    distingue."""

    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        super().__init__(f"Ninguna clasificación de suelo encontrada para el bbox: {bbox}")
        self.bbox = bbox


class CadastralServiceError(Exception):
    """El servicio de Catastro respondió con una excepción o con una estructura
    inesperada -- historia #37. Nunca se cachea ni se devuelve como lista vacía."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Respuesta de Catastro no utilizable: {detail}")
        self.detail = detail


class AreaQueryLimitExceededError(Exception):
    """El bbox pedido supera el límite de área del proveedor (WFS de parcelas de
    Catastro: "Area of extension out of limits") -- historia #37."""

    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        super().__init__(f"El área pedida supera el límite del proveedor: {bbox}")
        self.bbox = bbox


class TruncatedAreaQueryError(Exception):
    """El proveedor devolvió menos elementos de los que dice haber encontrado
    (`numberReturned < numberMatched`): usarla perdería parcelas en silencio --
    historia #37."""

    def __init__(
        self, bbox: tuple[float, float, float, float], matched: int, returned: int
    ) -> None:
        super().__init__(
            f"Respuesta truncada para {bbox}: encontradas {matched}, devueltas {returned}"
        )
        self.bbox = bbox
        self.matched = matched
        self.returned = returned


class OrthophotoNotCoveredError(Exception):
    """El vuelo pedido no cubre la ventana (el WMS devuelve una imagen blanca
    sin error) -- historia #121, ADR 0042 §3. No es un fallo: la franja prueba
    el siguiente vuelo de su lista."""

    def __init__(self, vintage: HistoricalVintage) -> None:
        super().__init__(f"El vuelo {vintage!r} no cubre la ventana pedida")
        self.vintage = vintage


class RawImageMismatchError(Exception):
    """La imagen de trabajo de una unidad no es la que declara `unidad.json`:
    otro sha256, otras dimensiones o un sidecar que no cuadra con su cuerpo --
    historia #137, ADR 0043. Nunca se usa una imagen distinta de la declarada:
    los puntos de control se tomaron sobre aquella."""

    def __init__(self, unit_id: str, detail: str) -> None:
        super().__init__(f"Imagen de trabajo de {unit_id} no coincide con la declarada: {detail}")
        self.unit_id = unit_id
        self.detail = detail


class WorkingImageUnavailableError(Exception):
    """No hay imagen de trabajo en la caché y no se puede (o no se debe)
    descargar: modo sin red, o una unidad `original_cnig`, cuya descarga pasa
    por un captcha y la hace una persona -- historia #137."""

    def __init__(self, unit_id: str, detail: str) -> None:
        super().__init__(f"Imagen de trabajo de {unit_id} no disponible: {detail}")
        self.unit_id = unit_id
        self.detail = detail


class CuratedUnitNotFoundError(Exception):
    """Falta el directorio de una unidad en `curated/historico/` o alguno de
    sus tres ficheros -- historia #137."""

    def __init__(self, unit_id: str, detail: str) -> None:
        super().__init__(f"Sin entrada curada para la unidad {unit_id}: {detail}")
        self.unit_id = unit_id
        self.detail = detail


class EvidenceImageFormatError(Exception):
    """Una imagen de la evidencia no es un PNG de 8 bits no entrelazado que el
    decodificador propio sepa leer (p. ej. entrelazado, de 16 bits, un JPEG o un
    PNG corrupto) -- historia #140, ADR 0043 («Herramientas»: sin Pillow)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Imagen de evidencia no legible: {detail}")
        self.detail = detail


class WatercourseLicenseMissingError(Exception):
    """Los cauces llegan sin licencia en su procedencia: no se deriva nada (historia #250)."""

    def __init__(self) -> None:
        super().__init__("Los cauces no traen licencia en su procedencia; no se deriva la capa")


class HistoricCoverageError(Exception):
    """Una unidad histórica no se puede usar en la cobertura de la tabla de convergencia
    (aprobación caducada, imagen ausente o distinta, fichero no válido) -- historia #313,
    ADR 0057. El motivo es siempre un texto fijo, sin mensajes ajenos."""

    def __init__(self, unit_id: str, reason: str) -> None:
        super().__init__(f"Cobertura histórica: la unidad {unit_id} no es utilizable: {reason}")
        self.unit_id = unit_id
        self.reason = reason


class PrivateDataUnavailable(Exception):
    """Sin acceso al repo de datos privados: sin `gh`, sin sesión o sin permisos
    (historia #295, ADR 0059). El mensaje es fijo: ni repo, ni rutas, ni `stderr` de `gh`."""

    def __init__(self) -> None:
        super().__init__("sin acceso al repo de datos privados")


class PrivateDataStoreError(Exception):
    """Cualquier otro fallo del almacén de datos privados (red, 5xx, tiempo agotado,
    respuesta ilegible) -- historia #295. El motivo es un texto fijo del adapter."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"fallo del almacén de datos privados: {reason}")
        self.reason = reason


class PrivateDataConflictError(Exception):
    """Lo que hay en `data/` no es lo del catálogo (fichero o registro distintos, enlace
    simbólico, ruta fuera de `data/`): no se sustituye nada -- historia #295."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"conflicto con lo local: {reason}")
        self.reason = reason
