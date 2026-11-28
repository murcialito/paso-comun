# ADR 0057 — Tabla de convergencia en los ficheros publicados: identidad por id interno sin publicarlo, informe interno casado, manifiesto propio de los indicios y todo o nada salvo el tramo demasiado largo

- Estado: aceptada
- Fecha: 2026-11-28
- Historia: #246 (feature #198, épica #4); se implementa en #313, #314, #315 y #316. Aplica y amplía el ADR 0054; toca los ADR 0042, 0043, 0044, 0045, 0048, 0049 y 0050.
- Número: el diseño decía «el siguiente libre al crearlo» (al diseñar, el último era el 0054). Al crearlo en #313, el 0055 y el 0056 ya estaban ocupados (repositorio privado de datos; financiación), así que es el 0057. Las referencias a «ADR 0055 §n» en las issues #246, #313–#316 se refieren a este documento.

## Contexto

El ADR 0054 (#240) dejó la tabla de convergencia calculada en memoria
(`BuildConvergenceTablesUseCase`), sin publicarla y sin implementación de
`HistoricCoverageSource`. Dejó a #246 siete puntos abiertos: identidad del tramo,
informe interno, manifiesto de imágenes de los indicios, fallo por tramo, rótulo del IGN,
dónde va `appears_min_fraction` y de dónde sale la cobertura histórica.

Datos que pesan en la decisión (ficheros publicados el 2026-11-28):

- 83 indicios en 5 municipios, de 2 a 32 por municipio.
- Con las ventanas del ADR 0042 (lado útil de unos 411 m y como mucho 4 ventanas por tramo),
  alrededor de un tercio de los indicios mide más de 1,6 km y saldría `tramo_demasiado_largo`.
  Con todo o nada estricto, ningún municipio podría volver a publicar sus indicios.
- El tope de 400 ventanas por municipio no se alcanza: 32 indicios × 4 ventanas son 128.
- `curated/historico/` aún no existe en el repositorio: no hay ninguna unidad histórica real.

## Decisión

1. **Identidad.** La tabla se calcula con el id interno: `record_id` del informe interno en
   los indicios y `candidate_id` en los tramos a verificar. Ese id solo vive en memoria y en
   ficheros internos (`data/derived/`).
   - La entidad publicada `PublishedConvergence` (`domain/published_convergence.py`) es la
     tabla sin `stretch_id`, sin `municipality_code` y sin `appears_min_fraction`. Nada que
     se publique puede llevar el id interno.
   - El caso de uso casa cada tabla con su elemento publicado por el id interno y se la
     entrega al dominio en un `Mapping[id interno, ConvergenceTable]`.
   - El `public_id` se sigue calculando como hasta ahora (ADR 0044 §5, ADR 0048 §4), en un
     solo sitio por capa.
   - Los mensajes de error de los indicios citan el `public_id`, nunca el `record_id`. Los
     de los tramos a verificar citan el `candidate_id`, que ya es público en
     `revision.json`.
2. **Informe interno casado.** `publicar-discrepancias` lee también el informe interno
   (`data/derived/discrepancias/interno/<ine>.geojson`). Ambos informes deben ser de la
   misma ejecución de `discrepancias`:
   - el interno no puede estar redactado;
   - deben tener el mismo municipio, el mismo `criterion_id` y la misma secuencia de
     `record_id`;
   - de cada indicio que se va a publicar, la zona y las fuentes deben ser iguales en los
     dos informes.

   Si algo no casa, falla el municipio con un motivo fijo («el informe interno no
   corresponde al publicable; vuelve a ejecutar discrepancias»). El `record_id` es una
   huella sha256 del contenido, así que igualar la secuencia equivale a igualar la huella.
3. **Manifiesto propio de los indicios.** El comando nuevo `imagenes-comparacion-indicios`
   escribe `data/derived/comparacion_indicios/<ine>.json`, con el mismo formato, escritor y
   lector que el de los candidatos (`JsonComparisonManifestWriter`/`Reader` sobre otro
   directorio). Así no lo pisa nunca.
   - Pide imágenes solo de los indicios que se van a publicar (`is_publishable_indicio`,
     la misma regla que `build_discrepancy_layer`).
   - El id del tramo es el `record_id`. El trazo es `image_axis(zona)`, la única función
     que lo define (ADR 0054 §5).
   - El tope de 400 ventanas por municipio no cambia. Si los indicios de un municipio lo
     pasan, el comando sale con 1 sin pedir nada (como `imagenes-comparacion`) y ese
     municipio no se puede publicar hasta que se decida otra cosa. Hoy no ocurre (128
     ventanas como mucho).
4. **Todo o nada, con una sola excepción.** Si un tramo no tiene tabla, el municipio falla y
   no se escribe nada. Eso incluye: sin manifiesto, tramo ausente o incoherente, franja con
   `fallo`, manifiesto de otra política, capa ilegible, cobertura histórica con errores y
   fila inválida. La excepción es `tramo_demasiado_largo`, porque es una propiedad del tramo
   y no un fallo:
   - En los **indicios**, la tabla se publica sin filas de ortofoto, con
     `images_unavailable = True`, y la ficha dice «Imagen no disponible». Es el texto que
     ya publica el ADR 0049, así que no se redacta ningún texto nuevo.
   - En los **tramos a verificar** no cambia nada: un aprobado demasiado largo ya hace
     fallar el municipio por #172 (ADR 0049 §6), antes de calcular la tabla.

   Para eso, `build_convergence_table` y `BuildConvergenceTablesUseCase` ganan la opción
   `without_images_if_too_long` (por defecto `False`) y `ConvergenceTable` gana el campo
   `images_unavailable`, que solo puede ser verdadero si la tabla no tiene filas de imagen.
   La regla es nueva, así que `CONVERGENCE_METHOD` pasa a `("convergencia_fuentes", 2)`.
5. **«Camino actual (IGN)»** sigue siendo la unión de la red sin asfaltar y la asfaltada,
   igual que la contraparte de los indicios (ADR 0050). Si la tabla usara otra red, la ficha
   y la tabla de un mismo indicio se contradirían. El rótulo no cambia: el fundador lo
   aprobó tal como está en el ADR 0054. El `.procedencia.json` declara la red usada
   (`red_ign: "sin asfaltar y asfaltada (ADR 0050)"`). Si hace falta explicarlo en pantalla,
   lo hará #248 con un texto que valide el fundador.
6. **Umbral de «Aparece».** El umbral no va en la ficha.
   - El `.procedencia.json` de cada fichero lleva el bloque `convergencia` con `metodo`,
     `criterio`, `umbral_aparece`, `red_ign` y las fuentes de las filas (clave, atribución y
     fecha de consulta más reciente).
   - Todas las tablas de una publicación deben haberse calculado con el mismo umbral; si
     no, es un error de programación.
   - La CLI recibe `--umbral-aparece` (por defecto `APPEARS_MIN_FRACTION`, 0,5) en
     `publicar-discrepancias` y `publicar-candidatos`. Un valor fuera de (0, 1] sale con 1
     antes de leer nada.
7. **Cobertura histórica desde git.** `CuratedHistoricCoverageSource`
   (`application/historic_coverage.py`) implementa `HistoricCoverageSource` componiendo
   ports, sin PostGIS:
   - `CuratedHistoricCatalogPort.unit_ids()` es nuevo y lo implementa `CuratedHistoricFiles`;
   - reutiliza `CuratedHistoricInputPort` y `WorkingImagePort` (`IgnHistoricImageAdapter`
     sin red, sobre la caché);
   - repite los pasos de `historico cargar` sin escribir: `HistoricUnitUseCase.assess`,
     `check_trails_in_image`, `check_trail_municipalities`, `check_approvals_current`,
     exclusión de los extrapolados (`is_extrapolated`, extraída de `load_historic_trails.py`
     sin cambiarla), `to_historic_trail` y `unit_footprint`.

   Cómo trata cada caso:
   - Una unidad con veredicto negativo **no cuenta** y se informa. No es un fallo: es
     trabajo en curso.
   - Una aprobación caducada, una imagen que falta o no casa, o un fichero inválido lanzan
     `HistoricCoverageError(unit_id, motivo)` y hacen fallar la publicación.
   - Sin `curated/historico/`, la cobertura sale vacía y las filas salen «Sin cobertura».
   - La procedencia de la cobertura es la de la imagen de la unidad aceptada más reciente.
     Sin unidades, es una procedencia «sin unidades aceptadas» fechada con el reloj
     inyectado. Ninguna de las dos se publica.
8. **Formato publicado.** Cada feature de `discrepancias/<ine>.geojson` y
   `candidatos/<ine>.geojson` gana la propiedad `convergencia`, con lista blanca cerrada:
   - en la tabla: `titulo`, `filas`, `imagenes` (`null` o «Imagen no disponible»), `aviso`,
     `metodo` y `criterio`;
   - por fila: `fuente`, `rotulo`, `tipo_evidencia`, `valor`, `texto`, `anio`,
     `fecha_consulta`, `enlaces` y `atribucion`.

   Todo texto sale ya redactado del dominio; el visor no compone nada. Comprobaciones:
   - El sink comprueba la tabla como última barrera: título, valores, aviso y rótulo de
     Catastro con la huella `CONVERGENCE_TEXT_VALIDATION`, rótulos de fila aprobados,
     atribuciones de la política de fuentes o de los vuelos, ninguna referencia ni
     localizador de parcela y ninguna palabra vetada.
   - El sink de indicios pasa a **fallar** por encima de 3.000.000 bytes, como el de
     candidatos (ADR 0049 §7).
   - El kit de campo (#213, #222) admite `convergencia` en las dos capas sin exportarla.
9. **Partición.** Cuatro historias hijas de #246:
   - #313: cobertura histórica;
   - #314: manifiesto de los indicios;
   - #315: tabla en los tramos a verificar, con las piezas comunes de dominio y
     serialización;
   - #316: tabla en los indicios.

   #313 y #314 van en paralelo; #315 depende de #313; #316 depende de #314 y #315.

## Consecuencias

- Orden para publicar los indicios: `discrepancias` → `imagenes-comparacion-indicios` (con
  red la primera vez) → `publicar-discrepancias`. Para los tramos a verificar, además de lo
  que ya se pedía, se leen las capas derivadas y `curated/historico/`.
- Un indicio largo publica las filas de trazado y las históricas, y dice «Imagen no
  disponible». Un indicio con un fallo de descarga bloquea su municipio hasta repetir
  `imagenes-comparacion-indicios`.
- Mientras no exista `catastro_caminos/<ine>.geojson` de un municipio, su fila de Catastro
  sale «Sin cobertura» (ADR 0054 §4). Conviene generar la capa de Catastro del ámbito antes
  de publicar.
- Los ficheros de indicios crecen unos 5 a 10 kB por indicio, muy por debajo del umbral.
- Sin dependencias ni proveedores nuevos, sin coste recurrente, sin LLM y sin red al
  publicar. Las imágenes de los indicios usan la misma caché y los mismos servicios que las
  de los candidatos (ADR 0042, 0052).

**A revisar con el fundador (no bloquea):**
- Que un indicio demasiado largo publique la tabla sin ortofotos y con «Imagen no
  disponible», en vez de bloquear su municipio.
- Si #248 debe explicar en pantalla que «Camino actual (IGN)» incluye carreteras.

### Alternativas descartadas

- **Publicar el `record_id` o derivar el `public_id` de él**: su hash lleva ids de trazado,
  posibles referencias de Catastro (ADR 0044 §5).
- **Un único manifiesto por municipio con candidatos e indicios**: obliga a regenerar las
  imágenes de los candidatos aprobados cada vez que cambian los indicios, y mezcla dos
  conjuntos de ids.
- **Bloquear también con `tramo_demasiado_largo`**: con los datos de hoy, ningún municipio
  podría volver a publicar sus indicios.
- **Omitir la tabla del indicio largo**: la ficha quedaría sin la parte que más aporta (las
  filas de trazado).
- **Leer la cobertura histórica de PostGIS**: añade una dependencia de la base al publicar,
  cuando la fuente de verdad es git (ADR 0045).
- **Usar solo la red sin asfaltar en la fila del IGN**: la tabla contradiría la ficha del
  mismo indicio (ADR 0050).

## Nota de implementación (#315)

- El error de fichero de capa que captura `publicar-candidatos` se declara en `ports/errors.py`
  (`TrailLayerFileError`), y `TrailLayerFormatError` hereda de él: `application/` no puede importar
  un adapter.
- Orden de las comprobaciones en `publicar-candidatos`: revisiones vigentes, manifiesto utilizable y,
  por aprobado, las de #170 y #172 van antes que la tabla. Así, sus mensajes no cambian, y un aprobado
  demasiado largo o con una franja `fallo` sigue fallando con el motivo de #172. Si un aprobado no
  tiene tabla por otro motivo, el error es `el candidato <id>: tabla de convergencia: <motivo>`.
- La barrera del sink exceptúa el aviso fijo al buscar «es público» (ADR 0054 §9): el aviso está
  protegido por su huella.
- Los rótulos de fila (`LAYER_ROW_LABEL`, `ORTHOPHOTO_ROW_LABEL`) los aprobó el fundador el
  2026-11-27 en #240 (https://github.com/murcialito/paso-comun/issues/240#issuecomment-7224620545).

## Nota de implementación (#316)

- `publicar-discrepancias` lee el informe interno solo si el publicable tiene algún indicio
  publicable y ha pasado las reglas de #94: así sus mensajes no cambian y un municipio
  «vacío» no necesita informe interno, manifiesto ni capas.
- `public_id` y la selección de lo que se publica salen de un único recorrido
  (`_publishable_records`), que usan `published_indicio_ids` y `build_discrepancy_layer`.
- La comprobación de casado compara municipio, `criterion_id`, la secuencia de `record_id`
  y, por indicio publicado, la zona y las fuentes A y B. Cualquier diferencia da el mismo
  motivo fijo, sin ids.
- Los motivos heredados de #172 que mandan a `imagenes-comparacion` se reescriben a
  `imagenes-comparacion-indicios` en los indicios.
- La `atribucion` de la capa y del `.procedencia.json` de los indicios suma las atribuciones
  de las filas de la tabla (todas de la lista cerrada `ROW_ATTRIBUTIONS`).
- El sink de indicios aplica las mismas barreras que el de candidatos (referencias y
  localizadores de parcela, palabras vetadas) también a `indice.json` y su procedencia.
- Añadido en la implementación (no estaba en el diseño): `CADASTRAL_REFERENCE_RE` y
  `PARCEL_LOCATOR_RE` pasan a `domain/parcel_identifiers.py`, porque
  `published_convergence` las importaba de `published_discrepancy`, que ahora importa
  `published_convergence` (ciclo). `published_discrepancy` las reexporta con su nombre.
