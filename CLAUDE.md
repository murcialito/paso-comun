# Paso Común

Nombre público del proyecto desde el 2026-11-26 (antes «Parcel Intel»). Repo `murcialito/paso-comun`; sitio en `https://pasocomun.org/` (dominio propio desde el 2026-11-27, ADR 0053; la URL antigua de Pages redirige). El paquete Python sigue llamándose `parcel_intel` y no se renombra.

Herramienta abierta y sin ánimo de lucro para la defensa de caminos públicos: reconstruye qué trazados deberían ser públicos (vías pecuarias oficiales, Catastro, cartografía histórica) y señala los tramos donde hoy parecen desaparecer o quedar dentro de una finca cerrada. Principios innegociables: el sistema señala **indicios a verificar**, con confianza y evidencia — nunca ilegalidades ni dictámenes; **nunca se nombra a propietarios**; las reglas propuestas por IA requieren aprobación humana antes de usarse.

Primer hito en Colmenar de Oreja y municipios vecinos (Comunidad de Madrid). Cambio de rumbo del 25 nov 2026: el informe de due diligence por referencia catastral (historias 0001–0020) queda como base reutilizable y posible fase posterior; pisos, fuera. Ver [`docs/vision.md`](docs/vision.md) y el ADR 0021.

## Arquitectura: hexagonal, innegociable

- `src/parcel_intel/domain/` — entidades y lógica pura (parcel, building, finding, rule...). **Cero imports de terceros, cero imports de otras capas.** Solo stdlib.
- `src/parcel_intel/ports/` — interfaces (`typing.Protocol`). Solo pueden importar `domain`.
- `src/parcel_intel/application/` — casos de uso (generar informe, evaluar reglas, resolver geolocalización de boletín). Reciben ports por inyección. Solo importan `ports` y `domain`. **Jamás un adapter.**
- `src/parcel_intel/adapters/` — implementaciones concretas (Catastro, Sentinel-2/STAC, PNOA, LiDAR, BOE, BOCM, IDEM/planeamiento, DeepSeek, PostGIS). Aquí viven los SDKs, HTTP y geoprocesamiento con librerías externas.
- `src/parcel_intel/cli/` y `src/parcel_intel/api/` — composition root: el ÚNICO lugar donde se instancian adapters concretos y se inyectan.

La regla se hace cumplir con `import-linter` (contrato en `pyproject.toml`). Si `lint-imports` falla, el cambio no se mergea. No se discute, se corrige.

Otras reglas:
- Dependencias nuevas de terceros: solo en adapters, y requieren ADR si introducen un proveedor nuevo (fuente de datos, LLM, etc.).
- Validación con pydantic solo en los bordes (adapters, API). El dominio usa dataclasses congeladas.
- mypy estricto en verde siempre.
- Ningún LLM en la evaluación de reglas; solo fuera de línea: autoría de reglas y extracción (con revisión humana) y redacción de textos que se publican ya generados. **Nada llama a un LLM en una consulta de usuario.**
- Coste de funcionamiento casi cero: solo datos públicos, análisis por lotes y publicación como ficheros estáticos siempre que se pueda. Cualquier componente con coste recurrente (backend, almacenamiento de fotos, API de pago) requiere ADR con estimación de coste.
- Ningún dato de titularidad entra en el sistema ni sale en ninguna vista. Una capa solo se publica en el mapa si su licencia permite redistribución.
- Todo dato lleva procedencia: fuente, URL, fecha de descarga y licencia.
- Operaciones métricas en EPSG:25830 (ETRS89 / UTM 30N); almacenamiento en ese CRS.
- Datos crudos cacheados e inmutables en `data/` (fuera de git); el procesamiento es reproducible desde la caché.
- Los tests no llaman a red: fixtures grabadas en `tests/fixtures/`; tests de integración marcados aparte.
- Cada hallazgo debe poder rastrearse hasta su evidencia y su regla.
- Datos internos no publicables (ADR 0055): viven en el repo privado `murcialito/paso-comun-datos` (adjuntos de release y catálogo por fuente). **Ningún agente del repo público tiene acceso al repo privado** (los logs y las PR de aquí son públicos), ni en Actions ni en local: ningún agente ejecuta la sincronización ni `gh` contra él. El procesado interno es local o en workflows del propio repo privado. El acceso es solo del fundador mientras no se aclare la licencia. Ningún dato de titularidad entra tampoco en el repo privado. Lo subido es inmutable: una versión nueva es un fichero nuevo con su procedencia.

## Flujo de trabajo por feature (obligatorio)

Desde el 2026-11-25 el backlog vive en GitHub Issues y los agentes corren en GitHub Actions ([`docs/proceso.md`](docs/proceso.md), ADR 0022). El fundador solo refina, y solo cuando un agente se lo pide.

1. **refinador** (`.claude/agents/refinador.md`) parte épicas y features, escribe la historia en la issue con criterios Dado/Cuando/Entonces y le pone talla (`talla:s`/`m`/`l`) → `estado:lista`, `estado:diseno` o `estado:necesita-fundador`.
2. **arquitecto** (`.claude/agents/arquitecto.md`) diseña toda historia `talla:l` o con ADR antes de desarrollo (ADR 0023).
3. **desarrollador** (`.claude/agents/desarrollador.md`) implementa con tests unitarios y un test de aceptación por criterio (`tests/acceptance/test_historia_<n>.py`), anota lecciones y ADR en la misma PR y la deja abierta: nunca fusiona.
4. **revisor** (`.claude/agents/revisor.md`) revisa la PR; su veredicto es el check obligatorio `revision`.
5. CI verde + `APROBADO` + sin `veto` sobre el último commit → `fusionar.yml` fusiona y despliega (ADR 0024).

El modelo de cada fase depende de la talla: barato solo para desarrollar `talla:s`, capaz para diseñar, desarrollar y revisar `talla:l`, intermedio en lo demás; la revisión nunca usa el barato (`.github/scripts/modelo.sh`, `docs/proceso.md`).

Las historias 0001–0021 de `docs/stories/` son archivo; las nuevas viven en su issue ("Historia #n").

**Todos los agentes y toda implementación deben leer `docs/lessons/LESSONS.md` antes de empezar.**

## Comandos

```bash
uv sync --extra dev     # instalar deps (incluye pytest, mypy, ruff, import-linter)
uv run pytest           # tests
uv run mypy             # tipado estricto
uv run ruff check .     # lint
uv run lint-imports     # verificar capas hexagonales
uv run pytest -m acceptance   # tests de aceptación (uno por criterio de cada historia)

docker compose -f infra/docker-compose.yml up -d    # levantar PostGIS local
uv run python -m parcel_intel.adapters.postgis.migrate up      # aplicar migraciones
uv run python -m parcel_intel.adapters.postgis.migrate down    # revertir la última
uv run python -m parcel_intel.adapters.postgis.migrate status  # ver estado
uv run pytest -m integration    # tests que requieren PostGIS local levantado

# Autoría de reglas asistida por LLM (historia 0019) -- requiere DEEPSEEK_API_KEY en el entorno
uv run python -m parcel_intel.cli.main author --layer dph_cauce --text ruta/articulo.txt
uv run python -m parcel_intel.cli.main review list
uv run python -m parcel_intel.cli.main review approve <identifier>
uv run python -m parcel_intel.cli.main review reject <identifier>

# Capa de vías pecuarias por municipio del ámbito (historias #36, #83): recorte con los límites del IGN, en data/derived/vias_pecuarias/<ine>.geojson
uv run python -m parcel_intel.cli.main vias-pecuarias-municipios             # descarga y cachea en data/raw
uv run python -m parcel_intel.cli.main vias-pecuarias-municipios --sin-red   # solo desde la caché

# Capa de montes de utilidad pública por municipio (historia #220): IDEM `Zonas:IDEM_MA_MONTES_UP`, recorte con los límites del IGN, en
# data/derived/montes_utilidad_publica/<ine>.geojson con procedencia. Nunca lee ni escribe los campos de titularidad (lista blanca). Un municipio sin montes
# (Colmenar de Oreja) sale «sin montes» y no es fallo. Sin --municipio ni --ambito, el ámbito. Todo o nada: sale con 1 si falta algo en la caché o un límite.
uv run python -m parcel_intel.cli.main montes-utilidad-publica-municipios [--municipio 28013 ...] [--ambito] [--sin-red] [--cache data/raw] [--salida data/derived/montes_utilidad_publica]

# Zona de servidumbre aproximada de los cauces por municipio (historia #250): colección `watercourse` de IDEE (descarga paginada y cacheada de #257), franja
# «eje + semianchura + 5 m» (semianchura de `ancho_max`, o `ancho_min`, o 0; se anota en `anchura_usada`), recortada con el límite del IGN, en
# data/derived/servidumbre_cauces/<ine>.geojson con procedencia (licencia CC BY 4.0, marca «Obra derivada de», método «no es deslinde»). Capa de contexto, no indicio;
# NO se publica hasta #251. Propiedades: solo `nombre`, `anchura_usada`, `ancho_m`. Un municipio sin cauces sale «sin cauces» (sin fichero, no es fallo). Todo o nada: sale con 1
# y no escribe si falta la caché, un límite o la licencia de los cauces. Sin --municipio ni --ambito, el ámbito.
uv run python -m parcel_intel.cli.main servidumbre-cauces-municipios [--municipio 28013 ...] [--ambito] [--sin-red] [--cache data/raw] [--salida data/derived/servidumbre_cauces]

# Capa de camino actual (red del IGN sin asfaltar) por municipio, en data/derived/camino_actual/<ine>.geojson (historia #27), y red
# asfaltada en data/derived/camino_asfaltado/<ine>.geojson (historia #183, ADR 0050; solo contraparte de `discrepancias`, NUNCA se publica).
# Dos consultas por municipio (unpaved y paved); todo o nada por municipio: si falla una, no escribe ninguno de sus dos ficheros, informa
# `FALLO <ine> <nombre>: <superficie>: <motivo>` y sale con 1. La primera vez tras #183 necesita red (la caché no tiene las consultas paved).
uv run python -m parcel_intel.cli.main camino-actual-municipios              # descarga y cachea en data/raw
uv run python -m parcel_intel.cli.main camino-actual-municipios --sin-red    # solo desde la caché
uv run python -m parcel_intel.cli.main camino-actual-municipios --salida data/derived/camino_actual --salida-asfaltado data/derived/camino_asfaltado

# Capa interna de caminos de Catastro de un municipio (historia #37): parcelas >= 9000 de clase VT, por teselas de 900 m.
# Capa de uso interno: NUNCA se publica (licencia INSPIRE). Sin fichero derivado: imprime el resumen. Caché inmutable en data/raw/catastro.
# --codigo es el código de Catastro (5 dígitos, como el INE en Colmenar); --bbox, el del término en EPSG:25830 con ejes E,N.
uv run python -m parcel_intel.cli.main catastro-caminos --provincia MADRID --municipio "COLMENAR DE OREJA" --codigo 28043 --bbox 449341,4431623,470702,4442906
uv run python -m parcel_intel.cli.main catastro-caminos ... --sin-red        # solo desde la caché

# Discrepancias entre capas de trazados por municipio (historia #32): lee data/derived/{vias_pecuarias,camino_actual,camino_asfaltado,catastro_caminos}/<ine>.geojson
# y escribe data/derived/discrepancias/{interno,publicable}/<ine>.geojson (el interno NUNCA se publica). Sin red. Sale con 1 si algún municipio falla.
# La red del IGN es camino_actual ∪ camino_asfaltado (#183, ADR 0050): los asfaltados cuentan como contraparte, nunca como sujeto. Si falta uno
# de los dos ficheros (capas anteriores a #183), ese municipio falla y pide regenerar con camino-actual-municipios; sin ninguno, la fuente consta ausente.
uv run python -m parcel_intel.cli.main discrepancias --municipio 28043 [--municipio ...]
uv run python -m parcel_intel.cli.main discrepancias --ambito                # los 7 municipios del primer hito

# Publicar vías pecuarias y camino actual en site/datos/<capa>/<ine>.geojson (historia #84, ADR 0036): EPSG:4326, cada fichero
# con su .procedencia.json, e índice site/datos/indice.json. Sin red. Todo o nada: sale con 1 y no escribe si una capa falta o no es publicable.
uv run python -m parcel_intel.cli.main publicar-capas --municipio 28043 [--municipio ...] [--capa vias_pecuarias|camino_actual]
uv run python -m parcel_intel.cli.main publicar-capas --ambito

# Publicar la cobertura del visor (historia #185, ADR 0051): contornos de los municipios con datos en site/datos/cobertura.geojson
# (EPSG:4326, solo ine y nombre, simplificados a 15 m) con su .procedencia.json, desde la caché cruda de límites del IGN. Volver a
# ejecutarlo cuando el índice gane un municipio. Todo o nada: sale con 1 y no escribe si falta un límite o la caché.
uv run python -m parcel_intel.cli.main publicar-cobertura --ambito --sin-red
uv run python -m parcel_intel.cli.main publicar-cobertura --municipio 28043 [--municipio ...] [--cache data/raw] [--salida site/datos]

# Publicar los indicios de discrepancia en site/datos/discrepancias/<ine>.geojson con la ficha de #16 (historia #94, ADR 0044): lee
# data/derived/discrepancias/publicable/<ine>.geojson (regenerarlo con `discrepancias` si es anterior a #94), EPSG:4326, cada fichero con su
# .procedencia.json, y el índice. Sin red. Todo o nada: sale con 1 y no escribe si un municipio falta o no es publicable; uno sin indicios publicables se informa como vacío.
# Tabla de convergencia (historia #316, ADR 0057): cada indicio lleva `convergencia` y el .procedencia.json su bloque `convergencia`. Orden:
# `discrepancias` → `imagenes-comparacion-indicios` → `publicar-discrepancias`. Lee el informe INTERNO casado (--interno; nunca se publica), el
# manifiesto de indicios (--comparacion), las capas derivadas (--capas) y --curated-historico con --cache-historico, sin red; solo si el publicable
# pasa las reglas de #94 y tiene algún indicio publicable (un municipio vacío no lee nada más). Un interno ausente, redactado o que no casa, sin
# manifiesto, un indicio ausente o incoherente, una franja `fallo`, una capa ilegible, una unidad histórica no utilizable o una fila inválida salen
# con 1 (`ERROR <ine>: [indicio <public_id>: ]tabla de convergencia: <motivo>` o el motivo del municipio; nunca un record_id). Un indicio demasiado
# largo sale con la tabla sin filas de ortofoto («Imagen no disponible»). Por encima de 3.000.000 bytes falla sin escribir. --umbral-aparece como en
# `publicar-candidatos`; las unidades con veredicto negativo se omiten y se informan (`unidad histórica omitida <id>: …`).
uv run python -m parcel_intel.cli.main publicar-discrepancias --municipio 28043 [--municipio ...] [--entrada data/derived/discrepancias/publicable] [--interno data/derived/discrepancias/interno] [--comparacion data/derived/comparacion_indicios] [--capas data/derived] [--curated-historico curated/historico] [--cache-historico data/raw/ign_planimetrias] [--umbral-aparece 0.5] [--salida site/datos]
uv run python -m parcel_intel.cli.main publicar-discrepancias --ambito

# Kit de campo (historia #213): exporta lo ya publicado en site/datos/<capa>/<ine>.geojson (vias_pecuarias, camino_actual, discrepancias y, si existe,
# candidatos; con su .procedencia.json) a site/datos/kit/<ine>/tramos.gpx y tramos.kml (GPX 1.1 y KML 2.2, EPSG:4326) con su .procedencia.json y el índice
# site/datos/kit/indice.json. Solo lee lo publicado (nunca Catastro ni nada interno). El aviso de cada tramo y la fuente, licencia y atribución de cada capa
# van en la descripción y en los metadatos. Lista blanca de propiedades por capa. Sin red. Todo o nada: sale con 1 y no escribe si un elemento no lleva aviso,
# trae una propiedad fuera de la lista blanca o de titularidad/referencia catastral, no hay ninguna capa publicada o un fichero pasa de 3.000.000 bytes.
# El resumen dice qué capas incluyó y cuáles faltaban. Reproducible byte a byte (la fecha de generación es la del día).
uv run python -m parcel_intel.cli.main publicar-kit-campo --municipio 28043 [--municipio ...] [--entrada site/datos] [--salida site/datos]
uv run python -m parcel_intel.cli.main publicar-kit-campo --ambito

# Estrechamiento de vías pecuarias (historia #243): compara la anchura oficial de cada pieza (`area_m2 / length_m` del tramo de IDEM) con la anchura CATASTRAL
# de las parcelas de camino de Catastro (#37, clase VT) recortadas a la franja, y escribe el fichero INTERNO data/derived/estrechamiento/interno/<ine>.geojson
# (EPSG:25830; deriva de Catastro: NUNCA se publica; nada va a site/). Una feature por pieza: indicio, sin_indicio, sin_parcela_camino o sin_datos (con motivo). Sin
# referencias catastrales ni titularidad. Umbrales (razón 0,5, anchura mínima 3 m, pieza mínima 50 m) = valores iniciales NO validados. Anchura media por pieza: un
# estrechamiento local queda diluido. Un municipio sin vías sale «sin vías» y no es fallo; uno que falla no frena el lote y sale con 1. Sin --municipio ni --ambito sale con 1.
# --sin-red: solo la caché; una tesela que falta deja las piezas afectadas en sin_datos.
uv run python -m parcel_intel.cli.main estrechamiento-via-pecuaria --municipio 28043 [--municipio ...] [--sin-red] [--cache data/raw] [--salida data/derived/estrechamiento/interno]
uv run python -m parcel_intel.cli.main estrechamiento-via-pecuaria --ambito

# Proyecto de campo para QField (historia #222): lee lo ya publicado (como publicar-kit-campo, mismas comprobaciones: aviso, lista blanca, sin
# titularidad ni referencia catastral) y escribe site/datos/kit/<ine>/qfield/paso_comun_<ine>.{gpkg,qgs,zip}, LEEME.txt y <zip>.procedencia.json:
# GeoPackage (sqlite3) con una tabla por capa publicada y la capa editable vacía `incidencias` (tipo, estado, fecha, nota corta, foto opcional),
# .qgs con los ValueMap y la advertencia aprobada (constante QFIELD_WARNING). Sin red, sin QGIS, sin QFieldCloud; reproducible byte a byte.
# NO toca kit/indice.json ni se enlaza en el sitio hasta anotar en REGISTRY.md que alguien lo abrió en QField (--incluir-en-indice lo añade entonces).
# Todo o nada: sale con 1 y no escribe si falla un municipio, no hay ninguna capa o un fichero pasa de 3.000.000 bytes.
uv run python -m parcel_intel.cli.main publicar-kit-campo-qfield --municipio 28043 [--municipio ...] [--entrada site/datos] [--salida site/datos] [--incluir-en-indice]
uv run python -m parcel_intel.cli.main publicar-kit-campo-qfield --ambito

# Candidatos a corte por municipio (historia #128, ADR 0039): lee data/derived/discrepancias/interno/<ine>.geojson (#94) y las parcelas de Catastro
# (#37, caché en data/raw/catastro) y escribe el fichero INTERNO data/derived/candidatos/interno/<ine>.geojson (EPSG:25830; deriva de Catastro: NUNCA se publica).
# Un municipio sin informe de discrepancias se registra como «sin capas» (no escribe, no es fallo); uno que falla no frena el lote y hace salir con 1.
# --sin-red: solo la caché; una tesela que falta deja la señal parcela_no_camino en sin_datos con su motivo. Sin --municipio ni --ambito sale con 1.
uv run python -m parcel_intel.cli.main candidatos-corte --municipio 28043 [--municipio ...] [--sin-red]
uv run python -m parcel_intel.cli.main candidatos-corte --ambito

# Imágenes de comparación de los tramos de un municipio (historia #122, ADR 0042): descarga y comprueba las ortofotos en la caché cruda
# data/raw y escribe el manifiesto INTERNO data/derived/comparacion/<ine>.json (URL canónica, sha256, atribución, píxeles del trazo). Entrada:
# GeoJSON de tramos en EPSG:25830 con properties.id de 16 hexadecimales. Nada va a site/. Todo o nada: sale con 1 y no escribe si una franja
# falla, si hay más de 400 ventanas (antes de pedir nada) o si el manifiesto pasa de 3.000.000 bytes. --sin-red: solo desde la caché.
# Serie v2 (historia #208, ADR 0052): siete franjas; las de 1975 y 1991 se piden al WMS de la IDEM (vuelos de la Comunidad de Madrid), con la
# misma caché data/raw y la misma pausa que el IGN. Un manifiesto de la política v1 ya no se publica: volver a ejecutar este comando.
uv run python -m parcel_intel.cli.main imagenes-comparacion --municipio 28043 --tramos ruta/tramos.geojson [--sin-red] [--cache data/raw]
# El fichero interno de candidatos (#128) entra tal cual como --tramos (el id de la feature vale si falta properties.id; historia #168):
# así los tramos[].id del manifiesto son los candidate_id.
uv run python -m parcel_intel.cli.main imagenes-comparacion --municipio 28043 --tramos data/derived/candidatos/interno/28043.geojson

# Imágenes de comparación de los INDICIOS de discrepancia (historia #314, ADR 0055): lo mismo que `imagenes-comparacion`, pero con los indicios publicables del
# informe INTERNO (`data/derived/discrepancias/interno/<ine>.geojson`, con las zonas de Catastro; NUNCA se publica). Un tramo por indicio publicable
# (`is_publishable_indicio`), de id `record_id` y con el trazo `image_axis(zona)` (la multilínea tal cual o el pseudo-eje de una franja). Escribe el manifiesto
# INTERNO data/derived/comparacion_indicios/<ine>.json sin tocar el de los candidatos; nada va a site/. Misma caché (--cache), mismo enrutado IGN/IDEM y misma pausa.
# Un indicio demasiado largo queda como `tramo_demasiado_largo` (no es fallo); un municipio sin indicios publicables escribe un manifiesto con cero tramos. Por
# municipio, todo o nada: más de 400 ventanas, un informe publicable (redactado) en --entrada, una franja con fallo o --sin-red con un hueco en la caché no escriben
# nada de ese municipio; el fallo de uno no frena los demás y el comando sale con 1. Sin --municipio ni --ambito sale con 2. Una línea por municipio: `<ine>: N indicios -> <ruta>`.
uv run python -m parcel_intel.cli.main imagenes-comparacion-indicios --municipio 28043 [--municipio ...] [--entrada data/derived/discrepancias/interno] [--salida data/derived/comparacion_indicios] [--cache data/raw] [--sin-red]
uv run python -m parcel_intel.cli.main imagenes-comparacion-indicios --ambito

# Revisión humana de los candidatos a corte (historia #168, ADR 0047): lista, por candidate_id, el estado (propuesto, aprobado, rechazado,
# caducado), la huella actual (la que se copia en candidate_sha256 al aprobar) y la disponibilidad de imágenes; luego las huérfanas y un resumen.
# Lee el fichero interno de candidatos, curated/candidatos/<ine>/revision.json (entrada humana en git, bajo la guardia de curated/**: se revisa
# editándolo en una PR; ningún agente marca aprobado ni rechazado) y el manifiesto de imagenes-comparacion. Sin red, sin base, no escribe nada.
# Sale con 1 si hay aprobaciones caducadas, revisiones huérfanas o un municipio falla (fichero de revisión o manifiesto no válidos, código no
# válido); un municipio sin fichero de candidatos sale «sin candidatos» y no es fallo. Sin --municipio ni --ambito sale con 1.
uv run python -m parcel_intel.cli.main revision-candidatos --municipio 28043 [--municipio ...] [--curated curated/candidatos] [--entrada data/derived/candidatos/interno] [--comparacion data/derived/comparacion]
uv run python -m parcel_intel.cli.main revision-candidatos --ambito
# Código de inventario (historia #288, nota del ADR 0047): `revision-candidatos` también lee curated/candidatos/<ine>/inventario.json (código `COL-001` o
# `COL-001-<nombre>` por candidate_id, asignado a mano; prefijo por municipio en `MUNICIPALITY_PREFIXES`) e imprime por tramo aprobado su código o «sin código»
# y un resumen (asignados, sin_código, sobre_no_aprobados, huérfanos). Sale con 1 con códigos sobre tramos no aprobados, huérfanos o un fichero no válido; «sin código» no falla.

# Medir los candidatos a corte (historia #111): ejecuta el arnés de #110 con MATCH_CRITERION_V1 sobre el conjunto de validación (casos.json,
# con sus CONGELADO*.md en el mismo directorio) y los ficheros internos de candidatos (#128). Escribe el informe agregado en JSON (cobertura y
# precisión —cota inferior— con sus recuentos, no evaluables con motivo, etiqueta, huella, fecha y versiones) y un resumen por stdout. Sin red.
# Lee los ficheros de candidatos de los municipios de los casos y de los 7 del ámbito (#170): los de un municipio sin casos cuentan como
# candidatos no evaluables y su método entra en metodo_candidatos. Un municipio con casos y sin fichero de candidatos deja sus casos en sin_capas. Sale con 1, sin escribir, si casos.json no valida o un fichero de candidatos
# es ilegible; con la huella distinta de la congelada no falla: avisa en stderr y marca el informe como «conjunto no congelado». Es un fichero de datos, no se publica.
uv run python -m parcel_intel.cli.main medir-candidatos [--casos docs/validacion/casos.json] [--entrada data/derived/candidatos/interno] [--salida data/derived/validacion/medicion.json]

# Publicar los tramos a verificar aprobados (historia #170, ADR 0048) en site/datos/candidatos/<ine>.geojson: EPSG:4326, ficha de #169, id
# público propio, resumen de la medición («no calculable» si no hay evaluables) y «revisado a mano»; con su .procedencia.json y el índice.
# Lee el informe de medir-candidatos (debe ser de un conjunto congelado y medir exactamente el método de los candidatos), el fichero interno
# de candidatos (#128) y curated/candidatos/<ine>/revision.json (#168). Solo publica los aprobados vigentes. Sin red. Todo o nada: sale con 1 y
# no escribe si falta o no vale el informe, si no hay aprobados, si hay aprobaciones caducadas o huérfanas, o si un aprobado no es publicable.
# Imágenes de comparación (historia #172, ADR 0049): cada tramo lleva enlaces al WMS del IGN con año, atribución y trazo en píxeles, sacados del
# manifiesto de imagenes-comparacion (--comparacion; generado con el mismo fichero interno de candidatos como --tramos). Falla si falta el
# manifiesto o no casa con los aprobados (ausente, demasiado largo, sin imágenes, otro trazado, URL o atribución fuera de la lista blanca), o si
# el fichero pasa de 3.000.000 bytes. Ninguna imagen se aloja en site/.
# Tabla de convergencia (historia #315, ADR 0057): cada tramo lleva `convergencia` (qué fuentes muestran el trazado, sin el candidate_id) y el
# .procedencia.json su bloque `convergencia` (método, criterio, umbral_aparece, red del IGN y fuentes). Lee las capas derivadas de --capas
# (vias_pecuarias, camino_actual ∪ camino_asfaltado, catastro_caminos; una ausente sale «Sin cobertura») y las unidades de --curated-historico
# con sus imágenes de --cache-historico, sin red (una unidad con veredicto negativo se omite y se informa: `unidad histórica omitida <id>: …`).
# Los fallos de #170/#172 se informan antes y sin leer capas; una capa no válida, una unidad no utilizable o un aprobado sin tabla salen con 1
# (`ERROR <ine>: [el candidato <id>: ]tabla de convergencia: <motivo>`). --umbral-aparece (0,5 por defecto) fuera de (0, 1] sale con 1.
# Solo municipios con código de Catastro verificado (hoy, 28043); sin --municipio sale con 1; no hay --ambito. Se ejecuta en local.
uv run python -m parcel_intel.cli.main publicar-candidatos --municipio 28043 [--medicion data/derived/validacion/medicion.json] [--entrada data/derived/candidatos/interno] [--curated curated/candidatos] [--comparacion data/derived/comparacion] [--salida site/datos] [--capas data/derived] [--curated-historico curated/historico] [--cache-historico data/raw/ign_planimetrias] [--umbral-aparece 0.5]

# Georreferenciación histórica por unidad (historia #139, ADR 0043/0044): entrada humana en curated/historico/<unit_id>/ (unidad.json,
# puntos_control.csv, trazados.geojson) e imagen de trabajo en data/raw/ign_planimetrias/<unit_id>.png (+ .provenance.json). Opciones, con
# sus valores por defecto: --curated curated/historico, --cache data/raw/ign_planimetrias, --sin-red (solo desde la caché).
# `ajuste` no usa la base: imprime candidatos, retenidos, cuadrantes, RMSE, residuo máximo, RMSE LOO, umbrales, veredicto y una línea por punto
# (puntos_sha256 es lo que se copia en control_points_sha256 al aprobar); sale con 1 si el veredicto es negativo.
# `cargar` (requiere PostGIS) hace lo mismo y, si todo es válido, sincroniza en una transacción solo los trazados aprobados (insertados,
# actualizados, sin_cambios, retirados, propuestos, rechazados, extrapolado <trail_id>, atribucion). Sale con 1 sin escribir nada si algo falla.
# Ejemplos sobre la fixture sintética (la imagen debe estar en la caché; los tests la copian de tests/fixtures/caminos/planimetrias/):
uv run python -m parcel_intel.cli.main historico ajuste 280075-w1 --curated tests/fixtures/caminos/planimetrias/historico --cache data/raw/ign_planimetrias --sin-red
uv run python -m parcel_intel.cli.main historico cargar 280075-w1 --curated tests/fixtures/caminos/planimetrias/historico --cache data/raw/ign_planimetrias --sin-red

# Evidencia de los trazados históricos propuestos de una unidad (historia #140, ADR 0043): un PNG de dos paneles por trazado `propuesto`
# (la planimetría con el trazado; PNOA en PNG con el trazado transformado y su banda de ± 2 × LOO) y su .procedencia.json, en
# <curated>/<unit_id>/evidencia/. Los aprobados y rechazados no se tocan. Sin base de datos. Sale con 1, sin escribir, si la unidad no es
# aceptable o tiene aprobaciones caducadas. La imagen de trabajo sale de --cache y PNOA de --cache-pnoa; --sin-red: solo desde las cachés.
uv run python -m parcel_intel.cli.main historico evidencia <unit_id> [--curated curated/historico] [--cache data/raw/ign_planimetrias] [--cache-pnoa data/raw/pnoa_ma] [--sin-red]
```

## Contexto de negocio (resumen)

- Modelo: sin ánimo de lucro y código abierto. Sin clientes, precios ni cuentas de pago; solo se plantearían aportaciones si hubiera costes reales de funcionamiento.
- Usuarios: asociaciones de defensa de caminos, senderistas, ciclistas, ganaderos y ayuntamientos pequeños.
- Fase 1 (actual): caminos públicos. Primer hito en Colmenar de Oreja y vecinos: (a) mapa de trazados que deberían ser públicos, (b) candidatos a corte, (c) visor web estático. Después: avisos ciudadanos con foto y dossier para el ayuntamiento.
- Fase posterior posible: informe de rústico para compradores (reutiliza 0001–0020). Pisos: fuera del alcance.
- Validación: casos de caminos cerrados ya documentados públicamente; el sistema debe señalarlos por sí solo.
- **Antes de programar el hito**: verificar resolución, frecuencia y licencia de cada fuente de imágenes y cartografía, y si cada municipio tiene inventario de caminos publicado (resultado en `src/parcel_intel/sources/REGISTRY.md`).
- LLM: API de DeepSeek (`DEEPSEEK_API_KEY`), solo fuera de línea, siempre detrás de una interfaz única en `ports/`. Todo consumo se registra (coste y tokens).
- Riesgos abiertos (ver `docs/vision.md`): que un candidato publicado se lea como acusación o identifique indirectamente a un propietario; licencias de redistribución de las capas; método de detección de cortes.
