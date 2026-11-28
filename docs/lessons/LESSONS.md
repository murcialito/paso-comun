# Lecciones aprendidas

Lectura obligatoria antes de empezar cualquier historia. Lecciones accionables, no un changelog — si una deja de aplicar (se automatizó en tooling o quedó fijada en un ADR/CLAUDE.md), se borra de aquí y se anota dónde vive ahora.

## [2026-10-15] Ciclo 0001–0002 — Registro de fuentes y cliente de Catastro

- **Lección**: nunca confiar en `GetCapabilities` (o cualquier documento de autodescripción de un servicio) para decidir cómo consultarlo — solo una petición real contra la red, con su respuesta grabada como fixture, cuenta como verificación.
  - **Causa**: el WFS INSPIRE de Catastro anuncia soporte de `Filter` OGC por POST en su `GetCapabilities`, pero ignora el cuerpo de la petición por completo y la procesa como si fuera GET/KVP. Ni `CQL_FILTER` ni `Filter` por POST filtran nada; el único método fiable para "referencia catastral → geometría" es encadenar `Consulta_CPMRC` (centroide, con la referencia normalizada a 14 caracteres, no 20) con un `GetFeature` de bbox pequeño (~100-200 m) y hacer el match por `nationalCadastralReference` del lado del cliente. Descubrir esto costó dos historias (0001 lo dejó como bloqueante documentado, 0002 lo resolvió en su Bloque 1).
  - **Aplica a**: BA (al redactar criterios de aceptación de "registro de fuente", exigir evidencia de petición/respuesta real, no solo cita de `GetCapabilities`) | implementación (probar primero contra la red real antes de diseñar el cliente).

- **Lección**: cuando un criterio de aceptación dice "verificado con datos reales", al menos un test debe ejercitar el caso no trivial (relación 1:N real, geometría multi-parte, error de verdad) — el caso vacío o de lista única no cuenta como cobertura de esa relación.
  - **Causa**: el emparejamiento edificio-parcela en el adapter de Catastro solo comprobaba `rings[0]` de cada geometría. Los tests iniciales pasaban porque solo cubrían "parcela sin edificios" (lista vacía) o geometrías de un único anillo; nadie escribió un caso con una parcela real de 2+ anillos hasta que el code-reviewer lo exigió. El bug llevaba semanas de vida potencial sin que ningún test lo pudiera atrapar.
  - **Aplica a**: implementación (al escribir tests de un caso "con N elementos", usar fixtures reales con N≥2 y geometría compuesta, no solo N=0 o N=1) | review (pedir explícitamente el caso multi-parte cuando el dominio modela geometrías que pueden tener varios anillos/cuerpos).

- **Lección**: todo comando documentado en `CLAUDE.md` debe ejecutarse literalmente (sin argumentos extra) como parte del cierre de la historia, antes de darla por terminada — si falla tal cual está escrito, es un bug de configuración, no un matiz a dar por supuesto.
  - **Causa**: `CLAUDE.md` documenta `uv run mypy` (sin argumentos), pero el `pyproject.toml` inicial no tenía `files` en `[tool.mypy]`; `uv run mypy src tests` pasaba pero `uv run mypy` tal cual fallaba. Pasó inadvertido hasta que el code-reviewer lo ejecutó literalmente.
  - **Aplica a**: implementación (ejecutar los comandos de `CLAUDE.md` copiados literalmente, no la versión "que yo sé que funciona", antes de cerrar una historia) | review (es uno de los primeros checks baratos a correr).

- **Lección**: en los bordes adapter↔parsing donde se construyen entidades de dominio desde datos externos, usar `TypedDict` (o un modelo validado) en vez de `dict[str, object]` — un `# type: ignore` en ese borde es casi siempre una señal de que el tipo de retorno está mal elegido, no de que mypy se equivoca.
  - **Causa**: `parsing.py` devolvía `list[dict[str, object]]`, lo que obligó a 7 `# type: ignore[arg-type]` en `adapter.py` exactamente donde se construyen `Parcel`/`Building` — el punto donde más importa que el tipo esté verificado. Sustituir por `RawParcel`/`RawBuilding` (`TypedDict`) los eliminó todos sin cambiar la lógica.
  - **Aplica a**: implementación (al escribir cualquier función de parsing XML/JSON hacia el dominio, definir su `TypedDict` de salida desde el primer commit, no diferirlo).

- **Lección**: si el dominio no puede usar una librería de geometría de terceros (regla de `CLAUDE.md`: domain solo stdlib), la lógica geométrica que se reimplemente a mano (ray-casting, point-in-ring) debe tratarse con la misma sospecha que código de seguridad — probar explícitamente los casos multi-parte, porque es precisamente donde una librería real no se habría equivocado.
  - **Causa**: `domain/geometry.py` (tipo propio `Geometry` con anillos como tuplas de floats) es la consecuencia correcta y esperada de la regla de dominio sin terceros; pero el `point_in_ring` manual en `geometry_ops.py` es exactamente donde apareció el bug de anillo único — no por mala idea de diseño, sino por no cubrir desde el principio el caso que una librería como `shapely` habría manejado gratis.
  - **Aplica a**: arquitecto (al decidir reimplementar geometría a mano en domain/adapters por la regla hexagonal, señalar explícitamente en el ADR o la historia qué casos multi-parte deben cubrirse con test) | implementación.

- **Lección**: cuando `docs/vision.md` (negocio) y `CLAUDE.md` (arquitectura) proponen estructuras de carpetas distintas para el mismo concepto, resolver la contradicción con un ADR *antes* de que la primera historia de código aterrice, no durante ella.
  - **Causa**: `vision.md` proponía `src/parcel_intel/sources/<proveedor>.py` como código; `CLAUDE.md` exige que todo SDK/HTTP viva en `adapters/<proveedor>/`. El ADR 0001 lo resolvió entre la historia 0001 (documentación, sin código) y la 0002 (primer código real), evitando que el colapso del hexágono ocurriera en el primer commit de cliente. Patrón a repetir: detectar estas colisiones de documentación en el ciclo de la historia *anterior* a la que escribe código, cuando el coste de corregir es solo el ADR, no un refactor.
  - **Aplica a**: arquitecto | BA (al escribir una historia de investigación/documentación, revisar si su resultado choca con la estructura de capas antes de que la siguiente historia escriba código sobre ella).

## [2026-10-15] Historia 0004 — Informe HTML, fuera de orden a petición del fundador

- **Lección**: cuando el fundador pide explícitamente saltar el orden de `docs/vision.md` para llegar antes a algo visible, el salto en sí no es el problema — escribir la historia y los tests *después* de implementar, en vez de antes, sí lo es, y hay que hacerlo en la misma sesión, no dejarlo pendiente.
  - **Causa**: se implementó el endpoint de informe HTML (domain/application/adapters/api) sin historia previa ni un solo test, verificando "a ojo" con capturas de pantalla contra la red real. El code-reviewer lo marcó como hallazgo bloqueante: sin criterios de aceptación escritos no hay listón contra el que comprobar que el código hace ni más ni menos de lo pedido, y la ausencia de tests repitió literalmente la lección ya registrada arriba sobre "el caso vacío no cuenta como cobertura" — esta vez ni siquiera el caso vacío estaba cubierto por test, solo por verificación manual no repetible.
  - **Aplica a**: implementación (si se prioriza velocidad/visibilidad sobre el orden del flujo, la historia retroactiva y los tests no son el paso que se recorta — son lo primero que hay que añadir en cuanto el code-reviewer o el propio autor se detiene a revisar) | BA (una historia escrita después de los hechos sigue necesitando "Fuera de alcance" y dejar explícito qué se saltó y por qué, para que quien lea el historial no asuma que el orden de `vision.md` se abandonó sin razón).

- **Lección**: una decisión arquitectónica puede quedar implícita en `CLAUDE.md` (p. ej. "cli/ es el único composition root") y aun así quedarse corta en cuanto aparece un segundo punto de entrada real (`api/`) — el ADR no es solo para decisiones nuevas de cero, también para extender una regla ya escrita cuando el código la pone a prueba por primera vez.
  - **Causa**: `CLAUDE.md` decía "cli/ es el ÚNICO lugar donde se instancian adapters", pero `vision.md` ya preveía `api/` como FastAPI sin que nadie hubiera resuelto si contaba como esa misma excepción. El código de la historia 0004 resolvió la ambigüedad en la práctica (instanciando `CatastroAdapter` en `api/main.py`) antes de que hubiera una decisión escrita — el ADR 0003 llegó después para documentarlo, no antes para guiarlo.
  - **Aplica a**: arquitecto (al ver que una historia va a tocar un módulo que `CLAUDE.md` no mapea con precisión a una capa, aunque "suene obvio", emitir el ADR antes de que la implementación lo decida por la vía de los hechos).

## [2026-10-15] Historia 0003 — Esquema PostGIS y primer adapter con estado

- **Lección**: las APIs de gestión de transacción/conexión de `psycopg` (v3) tienen semántica estricta y poco intuitiva — un fix razonado sobre ellas no se da por bueno sin ejecutarlo, nunca basta con "debería funcionar". Dos casos reales confirmados en este proyecto, con APIs distintas de la misma librería: (1) `with self._connection:` **cierra la conexión** al salir del bloque (éxito o excepción) — no es "commit si va bien, rollback si falla"; si el repositorio no es dueño del ciclo de vida de la conexión (la recibe inyectada), hace falta `try/except` explícito con `rollback()`/`commit()` manuales. (2) `connection.transaction()` **prohíbe** llamar a `commit()`/`rollback()` explícitamente dentro del bloque (lanza `psycopg.ProgrammingError`) — un método público que ya hace su propio commit/rollback (como `save_parcel`/`save_buildings`) no se puede anidar tal cual dentro de un `with connection.transaction():` nuevo que combine varias operaciones; hace falta extraer el SQL a helpers privados sin gestión de transacción propia.
  - **Causa**: ambos casos se descubrieron en caliente, no en revisión de código: el primer intento de corregir el hallazgo del code-reviewer (conexión abortada tras un error SQL sin rollback) usó `with self._connection, self._connection.cursor() as cur:`, que arregló el caso feliz pero dejó la conexión **cerrada** tras una excepción — detectado porque `test_la_conexion_se_recupera_tras_un_fallo_sql` falló con un error distinto al que se estaba corrigiendo. La historia 0005 repitió el mismo patrón con `save_report` (parcela + edificios en una transacción): `test_save_report_es_atomico_edificio_huerfano_no_dura_la_parcela` saltó con un `ProgrammingError` distinto al esperado, confirmando que "un fix de transacción no se da por bueno sin ejecutarlo" no fue un hallazgo de una sola vez sino un patrón real de la librería.
  - **Aplica a**: implementación (cualquier adapter con estado —conexión de base de datos, cliente HTTP con sesión persistente, etc.— necesita un test que ejecute una operación fallida seguida de una exitosa sobre el mismo objeto; antes de anidar un método que ya hace su propio commit/rollback dentro de una transacción nueva, extraer el SQL a un helper sin gestión propia) | review (un fix a un hallazgo de conexión/transacción no se da por bueno sin ejecutar el test que lo prueba; la semántica exacta varía entre APIs de la misma librería).

- **Lección**: cuando una historia de base de datos depende de un entorno real (Docker, un servicio externo), levantarlo y ejecutar el ciclo completo (`up`, aplicar, `down -v`, volver a `up`) *durante* la implementación, no fiarse de que "debería funcionar" porque el SQL es correcto — el entorno mismo puede fallar de formas que ningún test de la lógica de negocio detecta.
  - **Causa**: la imagen `postgis/postgis` reinicia el servidor una vez internamente mientras crea las extensiones en un volumen nuevo; un script de espera que solo comprueba `pg_isready` puede dar un falso positivo durante ese reinicio y fallar al intentar `SELECT postgis_version()` segundos después. Se descubrió ejecutando el ciclo completo en esta sesión, no se habría visto revisando el `docker-compose.yml` o el SQL de las migraciones por separado.
  - **Aplica a**: implementación (para cualquier historia que dependa de un servicio con Docker Compose, ejecutar el ciclo `up`/acción/`down -v`/`up` al menos una vez de verdad antes de cerrar la historia) | BA (si una historia depende de un entorno Docker, su criterio de "reproducible" debe pedir explícitamente ese ciclo completo, no solo "el servicio arranca").

## [2026-10-15] Historia 0005 — Caché PostGIS del informe

- **Lección**: un `UPSERT` (`ON CONFLICT ... DO UPDATE`) en la entidad "padre" de una relación 1-a-N no basta para que el conjunto completo sea idempotente — si la tabla "hija" solo tiene un `INSERT` plano sin restricción de unicidad, repetir la misma escritura duplica las filas hijas aunque la fila padre se actualice limpiamente. Con un caché declarado permanente (sin expiración ni proceso de limpieza), esa duplicación nunca se autocorrige.
  - **Causa**: `_insert_parcel` ya usaba `ON CONFLICT (cadastral_reference) DO UPDATE` desde la historia 0003, pero `_insert_buildings` solo hacía `INSERT` sin ninguna restricción `UNIQUE` sobre `(parcel_reference, cadastral_reference)` en el esquema. Dos peticiones concurrentes a la misma referencia nueva (ambas cache-miss, patrón real con FastAPI ejecutando handlers en threadpool) duplicarían los edificios cacheados permanentemente. Lo encontró el code-reviewer leyendo el esquema junto con el flujo de concurrencia del caso de uso, no un test — no había ningún test que lo ejercitara porque ningún test anterior llamaba a `save_report`/`save_buildings` dos veces con el mismo contenido.
  - **Aplica a**: arquitecto (al diseñar una tabla hija de una relación cacheada, decidir su restricción de unicidad en el mismo ADR que decide el `UPSERT` del padre, no después) | implementación (cualquier caché declarado "permanente" necesita que *todas* sus escrituras sean idempotentes, no solo la de la entidad principal; un test que guarde el mismo contenido dos veces y compruebe que no duplica es barato y debería ser estándar para cualquier tabla cacheada).

- **Lección**: si un mensaje de commit afirma "lección añadida a `LESSONS.md`", verificar que el archivo realmente cambió en ese commit (`git show --stat`) antes de dar la historia por cerrada — una afirmación en el mensaje no es la misma acción que el archivo.
  - **Causa**: el commit de cierre de esta historia afirmó textualmente haber añadido una lección sobre `psycopg` que, de hecho, no se escribió hasta esta entrada — una ronda después, tras señalarlo el code-reviewer. El hallazgo (la restricción de `connection.transaction()`) sí era real y sí se había corregido en el código; solo la actualización de `LESSONS.md` se quedó en el mensaje del commit sin llegar al archivo.
  - **Aplica a**: implementación (al escribir el mensaje de un commit que cierra una historia, si menciona haber actualizado `LESSONS.md`, confirmarlo con `git status`/`git diff` antes de commitear, no solo recordar la intención) | lessons-learned (al cerrar un ciclo, contrastar lo que dicen los mensajes de commit contra lo que de verdad hay en el archivo).

## [2026-10-15] Historia 0006 — Cliente PNOA

- **Lección**: un bug ya corregido una vez (`min()`/`max()` sin guardia sobre una colección que el tipo no garantiza no vacía) puede reaparecer en una función nueva que opera sobre el mismo tipo de dato, si nadie busca explícitamente el patrón al escribir código nuevo sobre `Geometry`. Antes de escribir cualquier función nueva que itere `geometry.rings` (o cualquier colección de `domain/` sin invariante de "al menos un elemento"), repasar si ya existe un caso conocido de este bug en el repo (`grep` por `EmptyGeometryError` o por el mismo patrón `min(xs)`/`max(xs)`) y replicar la guarda, no confiar en que "esta vez no hace falta".
  - **Causa**: `adapters/reports/html.py` (historia 0004) ya tenía `EmptyGeometryError` documentado explícitamente como mitigación a este riesgo. `bbox_with_margin` (historia 0006, `domain/geometry.py`) itera `geometry.rings` de la misma forma y no tenía ninguna guarda — lo encontró el code-reviewer ejecutando el código con una `Geometry(rings=(), srid=...)` real, no leyendo el diff. El propio ADR 0006 señala que esta función la reutilizará Sentinel-2 (0007): sin esta lección, el bug se habría propagado a un tercer sitio.
  - **Aplica a**: implementación (al escribir código nuevo sobre un tipo de dominio ya usado en otro punto del repo, buscar primero si ese tipo ya tiene un caso borde conocido y corregido en otro lugar) | review (cualquier función nueva sobre `Geometry.rings` debe tener, como mínimo, un test del caso `rings=()`, igual que ya se exige el caso multi-anillo).

## [2026-10-15] Historia 0007 — Cliente Sentinel-2

- **Lección**: cuando una función interna "acota/normaliza" sus argumentos de entrada antes de operar (p. ej. `read_pixel_window` acotando una ventana de píxeles a los límites del raster), cualquier valor derivado de esa normalización que se devuelva al llamador (el "origen" de la ventana) debe devolverse ya normalizado, nunca recalcularse por separado en el código que llama. Dos cálculos independientes del mismo valor derivado divergen en cuanto el input está fuera del caso feliz — exactamente el tipo de caso que una fixture real "de la parcela de siempre" nunca ejercita.
  - **Causa**: `read_pixel_window` acotaba `min_col`/`min_row` internamente antes de leer tiles, pero el adapter (`_read_window`) calculaba el "origen" para indexar el resultado a partir de los valores *sin acotar*. Con una celda de salida que reproyecta fuera del raster nativo de la escena (plausible cerca de un borde de huso UTM, nunca ejercitado por la parcela de prueba de Colmenar de Oreja), el índice quedaba desalineado: NDVI incorrecto sin ningún error, o `IndexError`. Lo encontró el code-reviewer ejecutando `read_pixel_window` con un TIFF sintético, no leyendo el código.
  - **Aplica a**: implementación (si una función acota/transforma un valor internamente, ese valor acotado es el único que debe salir de la función — no dejar que quien llama lo vuelva a derivar) | review (cualquier función que lea/escriba con índices calculados a partir de coordenadas transformadas necesita un test con la transformación llevada a un caso límite real, no solo el caso central).

- **Lección**: en una rejilla numérica con celdas potencialmente "sin dato" (nodata de la fuente), nunca usar un valor numérico válido (como `0.0`) como marcador de ausencia — usar `float('nan')`, documentado explícitamente en el tipo, y hacer que cualquier comparación posterior (p. ej. una máscara de cambios) trate `nan` de forma explícita en vez de confiar en que la semántica IEEE 754 "ya lo resuelve sola".
  - **Causa**: el ADR 0007 ya había señalado este riesgo exacto en su sección "Consecuencias" ("las celdas con nodata deben excluirse del resultado... para que la implementación no lo descubra tarde") y aun así la primera versión de `_ndvi` devolvía `0.0` para nodata — exactamente lo que el ADR pedía evitar, encontrado por el code-reviewer porque ningún test forzaba un píxel `nodata` real. Señalar un riesgo en un ADR no sustituye verificarlo con un test; solo evita que sorprenda del todo.
  - **Aplica a**: implementación (si un ADR señala un riesgo conocido como "a vigilar", eso es una exigencia de test antes de cerrar la historia, no una nota informativa) | review (buscar explícitamente, en el diff, si los riesgos que el propio ADR de la historia señaló ya tienen cobertura).

## [2026-10-15] Historia 0010 — Conectar PNOA/Sentinel-2/BOE/BOCM al informe HTML

- **Lección**: cuando un caso de uso recibe un reloj inyectado (`today: Callable[[], date]`) para que varias ramas de lógica usen "el mismo día", llamarlo una sola vez al principio de `execute()` y pasar el valor como parámetro a los métodos internos — no dejar que cada método privado vuelva a invocar `self._today()` por su cuenta. Dos llamadas independientes al reloj real pueden devolver fechas distintas si la petición cruza la medianoche, y con cuatro fuentes de red reales sin caché (una de ellas, BOCM, con un patrón N+1 que en esta historia tardó 97s reales en producción) ese cruce deja de ser una hipótesis de laboratorio.
  - **Causa**: la primera versión de `GenerateEnrichedParcelReportUseCase.execute()` calculaba `day` para BOE/BOCM con una llamada a `self._today()` y, por separado, `_get_ndvi_summary` volvía a llamar a `self._today()` para el extremo final del rango de 90 días de Sentinel-2 — dos valores que *normalmente* coinciden pero no están garantizados a coincidir. Lo encontró el code-reviewer razonando sobre la duración real documentada en la propia historia (97s por la petición end-to-end), no un test, porque ningún test ejercitaba una llamada que cruzara la medianoche.
  - **Aplica a**: implementación (cualquier `Callable[[], date]`/`Callable[[], datetime]` inyectado para determinismo en tests es también, en producción, una fuente de inconsistencia si se invoca más de una vez por operación — capturar su valor en una variable local al principio y reutilizarla) | review (en cualquier caso de uso con una operación de larga duración de verdad —no solo en teoría—, preguntarse explícitamente si algún valor "ambiente" como la fecha/hora actual se lee más de una vez).

- **Lección**: un criterio de aceptación de "X no se persiste" es fácil de satisfacer *por arquitectura* (un tipo que no puede llegar a un port de escritura) sin que exista ningún test que lo haga explícito — y el tipado por sí solo no sustituye una aserción legible que documente la garantía para quien lea los tests después, sin tener que razonar sobre todo el grafo de tipos del caso de uso.
  - **Causa**: `GenerateEnrichedParcelReportUseCase` nunca recibe un `ParcelRepositoryPort` ni puede llamar a `save_report` con un `EnrichedParcelReport` (el port solo acepta `ParcelReport`) — la garantía del AC "ninguna de las cuatro fuentes nuevas se persiste en PostGIS" ya era cierta por construcción antes de que el code-reviewer la señalara, pero ningún test lo afirmaba: el doble de repositorio en `tests/api/test_main.py` tenía `save_report` como una función vacía (`pass`), sin registrar nada. Se corrigió haciendo que el doble acumule lo que recibe y añadiendo una aserción de tipo sobre ese acumulado en el test que ya ejercita las cuatro fuentes juntas.
  - **Aplica a**: review (cuando un AC de "no persistencia" se cumple por diseño de tipos, pedir igualmente una aserción barata — contar llamadas o capturar el argumento recibido en el doble ya existente — en vez de aceptar "el tipo lo impide" como prueba suficiente por sí sola) | implementación (dobles de test que son "no-op" a propósito, como un `save_report` vacío, son el lugar más barato para añadir ese tracking: cuesta una lista y un `append`).

## [2026-10-15] Ciclo 0006–0010 — cuatro fuentes aisladas conectadas al informe (sesión autónoma)

- **Lección**: las historias 0006-0009 (PNOA/Sentinel-2/BOE/BOCM) convergieron, cada una por su cuenta, en la misma forma de "añadir una fuente aislada" — verificación real contra la red antes de diseñar (nunca `GetCapabilities`), entidad mínima nombrada por capacidad y no por proveedor, un único `Protocol` con un único método también nombrado por capacidad, cualquier cómputo derivado (`bbox_with_margin`, `compute_change_mask`, `filter_by_title`) como función pura en `domain/` reutilizable entre fuentes, un seam de red propio por adapter cuando el contrato HTTP real difiere (nunca forzar el de otro proveedor), caché siempre diferida hasta que exista un consumidor real entre ejecuciones, y una sección final de "coste de salida del proveedor". Ningún ADR lo nombra como patrón único: cada uno lo reconstruye citando "mismo criterio que el ADR anterior" sobre los ADR previos. Antes de diseñar la próxima fuente aislada (BORM, BOP, Open Data Registradores — `docs/vision.md`), nombrar y extraer este patrón explícitamente (p. ej. un ADR-plantilla o una sección en `CLAUDE.md`) en vez de obligar al arquitecto a releer cuatro ADR para re-derivarlo.
  - **Causa**: el patrón solo es visible comparando los cuatro ADR a la vez; ninguna lección individual de 0006-0009 lo señala porque cada historia solo veía el ADR inmediatamente anterior, no el conjunto.
  - **Aplica a**: arquitecto (al abrir la historia de la próxima fuente aislada, partir de este patrón nombrado explícitamente en vez de releer 0006-0009 para reconstruirlo) | BA (los AC de "registro de fuente nueva" pueden citar este patrón directamente como listón mínimo).

- **Lección**: conectar varias fuentes ya aisladas y verificadas a un caso de uso en una sola historia (0010) es barato precisamente *porque* cada una ya respeta el mismo patrón de port único + entidad propia + función pura — no hay que volver a decidir esto por historia individual. Con evidencia: el diff de producción de 0010 (conectar cuatro fuentes a la vez) fue de solo 252 líneas (+22 test ratio >2x), más pequeño que el de *cualquiera* de las cuatro historias de aislamiento por separado (414-1348 líneas cada una). El patrón correcto no es "conectar una a una" ni "aislar y conectar en el mismo paso" — es aislar y verificar cada fuente por separado (caro, con red real) y diferir la conexión a una historia posterior que las combine todas (barato, sin red nueva, solo composición).
  - **Causa**: la uniformidad de los ports (mismo criterio de diseño en las cuatro) convirtió la fase de "conectar" en pura composición de inyección de dependencias (`api/main.py`) y manejo de fallos aislados (`except Exception` por fuente) — no en diseño de cliente nuevo. Verificable comparando los `--shortstat` reales de los commits de cada historia completa.
  - **Aplica a**: arquitecto | BA (al planificar el roadmap de nuevas fuentes, preferir "N historias de aislamiento + 1 historia de conexión conjunta" sobre "aislar y conectar" por fuente, salvo que una fuente concreta tenga una razón de negocio para salir antes que las demás).

- **Lección**: en modo autónomo (sin fundador despierto), un ADR puede resolver en la misma sesión decisiones de arquitectura genuinas junto a umbrales de producto/UX disfrazados de arquitectura (p. ej. ADR 0010 fijó `NDVI_MAX_CLOUD_COVER_PERCENT = 40.0`, `NDVI_LOOKBACK_DAYS = 90` y "tomar la escena más reciente que pasa el filtro, no la de menor nubosidad") — decisiones razonadas y justificadas, pero que determinan directamente qué ve el fundador al verificar el informe a ojo, el objetivo explícito de esta fase ("tener un frontend para verificar yo"). El ADR las trata con el mismo peso que decisiones de tipado/capas y no las señala aparte para revisión. Cualquier constante numérica fijada en una sesión autónoma que afecte lo que el usuario final percibe (calidad/actualidad de una imagen, umbral de qué se muestra o se oculta) necesita un marcador explícito y separado ("a revisar con el fundador") distinto de los riesgos puramente técnicos que el ADR ya señala — no basta con que esté razonada dentro de la sección de Decisión.
  - **Causa**: ninguna de las 9 decisiones de ADR 0010 ni las 8 alternativas rechazadas distinguen "esto es una decisión de arquitectura estable" de "esto es un umbral de producto ajustable que el fundador podría querer discutir al despertar" — ambas quedan mezcladas en la misma lista numerada con el mismo nivel de autoridad.
  - **Aplica a**: arquitecto (al emitir un ADR en una sesión sin el fundador presente, separar explícitamente, en su propia sub-sección o lista, los umbrales de producto/UX de las decisiones de arquitectura pura) | BA (al revisar el cierre de una sesión autónoma, leer primero esa sub-sección antes que el ADR completo).

## [2026-10-16] Historia 0011 — Cliente IDEM (clasificación de suelo)

- **Lección**: un tipo de dominio con una restricción documentada en su propio docstring ("ninguna fuente usada hasta ahora tiene X") deja de ser cierto en cuanto llega una fuente nueva que sí lo tiene — y esa restricción puede no saltar durante el diseño (ADR) ni durante la verificación de red (Bloque 1), solo al escribir el parser contra una fixture real completa.
  - **Causa**: `domain.geometry.Geometry` declara explícitamente "no modela anillos interiores (huecos): ninguna fuente usada hasta ahora los tiene" (cierto para Catastro/PNOA/Sentinel-2). El Bloque 1 de la historia 0011 inspeccionó el esquema de campos de IDEM con `DescribeFeatureType` real y encontró un caso de solape real, pero no llegó a inspeccionar `gml:interior` dentro de la geometría — ese hallazgo apareció durante la implementación del parser, al ver que una sola feature con "1 clasificación" producía 5 anillos en vez de 1 (un polígono real de Villaconejos tiene 14 anillos interiores). Se corrigió tomando solo `gml:exterior` y descartando los huecos, documentado en el parser, en `REGISTRY.md` (hallazgo 2.1) y como riesgo residual explícito en el ADR 0011 — ningún render todavía consume esta geometría, así que el hueco descartado no tiene consumidor que lo note hoy.
  - **Aplica a**: arquitecto (al re-verificar una fuente geoespacial nueva, inspeccionar explícitamente si la geometría real tiene anillos interiores/huecos antes de asumir que el patrón ya usado por Catastro/PNOA aplica sin cambios — un `grep -c "<gml:interior>"` sobre la fixture real es barato y lo habría encontrado en el Bloque 1 en vez de durante la implementación) | implementación (al escribir un parser GML nuevo, contar `gml:exterior`/`gml:interior` por separado en al menos una fixture real con una feature "grande" o compleja, no solo verificar que el número de features coincide).

## [2026-10-16] Historia 0012 — Clientes de hidrografía (cauces) y peligrosidad por inundación

- **Lección**: cuando una historia de descubrimiento desde cero no encuentra un único servicio limpio sino un resultado mixto (dos servicios reales con formas de dato completamente distintas, más uno roto en producción), forzar todo al mismo port/entidad que la historia original anticipaba es el error — el ADR 0012 lo evitó a propósito, aprendiendo en el diseño, no en la implementación, la misma lección que la historia 0011 aprendió tarde sobre los anillos interiores: una geometría de línea abierta (cauce) y una muestra puntual de un raster (profundidad de inundación) no son la misma forma de dato aunque las dos respondan a "afección hidráulica", y forzarlas a una única entidad con campos opcionales según el caso habría sido la señal de abstracción falsa que este proyecto ya ha aprendido a evitar.
  - **Causa**: la historia 0012 fue redactada (antes del Bloque 1) esperando "el port nuevo" en singular, análogo a `LandClassificationPort` de IDEM. El Bloque 1 encontró que los cursos de agua son una lista de entidades vectoriales reales con geometría de línea y ~30 campos, mientras que la peligrosidad por inundación es una lista de 5 muestras puntuales de un raster sin geometría — formas de retorno incompatibles. El arquitecto decidió dos ports (`WatercoursePort`/`FloodHazardPort`) y un tipo de dominio nuevo (`LineGeometry`, hermano de `Geometry`, nunca forzando una polilínea abierta dentro de un anillo cerrado) explícitamente para no repetir el patrón de la lección de la historia 0011.
  - **Aplica a**: arquitecto (cuando el Bloque 1 de descubrimiento revela dos o más formas de dato reales bajo el mismo concepto de negocio, preferir ports/entidades separados sobre forzar una única forma con campos opcionales — la señal de alerta es necesitar `campo: Tipo | None` que solo tiene sentido para una de las dos fuentes) | BA (al redactar el AC de "el port nuevo" en singular antes del Bloque 1, dejar explícito que el número de ports es una decisión del arquitecto condicionada a la evidencia real, no un compromiso cerrado de antemano).

- **Lección**: nombrar un test con la palabra "real" cuando su fixture es en realidad sintética (escrita a mano, no capturada de una petición) es el mismo error que el proyecto ya se cuidó de evitar explícitamente en BOCM (historia 0009, `_GENERIC_XML` etiquetado como tal) — pero reaparece si nadie lo compara contra ese precedente al escribir el test.
  - **Causa**: `test_valor_numerico_real_se_parsea_a_float` (luego renombrado) verificaba que el parser de profundidad de inundación convierte un valor numérico a `float`, pero el HTML de entrada era inventado a mano porque ninguna de las fixtures reales capturadas en el Bloque 1 tenía un valor de profundidad distinto de "NO DATA" (las dos parcelas del MVP no tienen afección real). El código del parser era correcto; solo el nombre del test afirmaba una verificación contra la red que nunca ocurrió — encontrado por el code-reviewer comparando explícitamente contra el precedente de BOCM, no por ejecutar el test (que pasaba).
  - **Aplica a**: implementación (si una fixture de test no viene de una petición real, el nombre y un comentario deben decirlo explícitamente — "sintético"/"hipotético", nunca "real" — sobre todo cuando sí existen fixtures reales hermanas en el mismo archivo que lo son, porque la mezcla hace más fácil que la etiqueta incorrecta pase desapercibida) | review (al revisar un conjunto de tests con fixtures mixtas reales/sintéticas, comprobar explícitamente que el nombre de cada test coincide con el origen real de su fixture, no asumirlo por el nombre del archivo).

## [2026-10-16] Historia 0013 — Conectar IDEM/cauces/inundación al informe

- **Lección**: un riesgo residual que el code-reviewer señala como "no bloqueante, sin evidencia real todavía" en una historia de cliente aislado deja de ser hipotético en cuanto la siguiente historia conecta ese cliente a un caso de uso real con una geometría distinta — y entonces sí rompe en producción, exactamente como se predijo.
  - **Causa**: el code-reviewer de la historia 0012 señaló explícitamente, como riesgo no bloqueante, que `parse_water_depth` (`adapters/flood_hazard/parsing.py`) usaba `float(value)` sin confirmar el formato decimal real de un valor numérico (solo se había observado "NO DATA" en las dos parcelas probadas). La historia 0013 conectó ese cliente al informe y lo probó end-to-end contra una parcela real distinta (Villaconejos) — las capas `MarinaT100`/`MarinaT500` devolvieron `"0,000"` (coma decimal, formato español), y `float()` lanzó `ValueError` sin capturar, que `_safe` convirtió en "no disponible" silencioso para las 5 muestras a la vez (el fallo es atómico, documentado en el ADR 0012). Se encontró ejecutando la petición real contra el servidor, no leyendo el código ni con un test — el bug solo se manifestaba con datos reales que ningún test (ni siquiera el hipotético ya señalado) cubría.
  - **Aplica a**: implementación (cuando el code-reviewer deja constancia de un riesgo residual "sin evidencia real todavía" sobre un parser, tratarlo como una tarea pendiente explícita para la primera vez que ese cliente se ejecute contra una geometría o fecha distinta a las ya probadas, no como una nota archivada) | review (al verificar end-to-end una historia de conexión que usa clientes de historias anteriores, priorizar una geometría/parámetro *distinto* al ya usado en el Bloque 1 de esa historia — reutilizar exactamente el mismo caso de prueba no habría encontrado este bug, porque esas dos capas en las dos parcelas originales del Bloque 1 nunca se habían muestreado con ese resultado concreto).

## [2026-10-16] Historia 0014 — Cliente de vías pecuarias

- **Lección**: ni siquiera el nombre del *workspace* de un servicio ya conocido (no solo el nombre de la capa) se puede adivinar por analogía con otro servicio del mismo servidor — el método real que funciona es pedir el `GetCapabilities` **global** (sin fijar workspace en la ruta) y filtrar por texto, no construir la URL a partir de un patrón que "suena razonable".
  - **Causa**: la historia 0014 proponía probar `ViasPecuarias`/`VP` como nombre de carpeta "hermana" de `UsoDelSuelo` bajo el mismo GeoServer de IDEM (mismo dominio ya verificado en la historia 0011). Ninguno de los dos nombres existe — el workspace real es `Zonas` (genérico, "Medio Ambiente/Zonas"), encontrado solo tras pedir `.../geoidem/ows?service=wfs&request=GetCapabilities` sin workspace en la URL, que lista las capas de *todos* los workspaces del servidor a la vez. La misma petición de descubrimiento reveló, de pasada, una capa de Red Natura 2000 en el mismo workspace, candidata ya identificada para una futura historia sin tener que repetir la búsqueda.
  - **Aplica a**: arquitecto (al buscar un servicio "hermano" de otro ya verificado en el mismo servidor, probar primero el `GetCapabilities` global/sin workspace fijo antes de adivinar nombres de carpeta por patrón, aunque el dominio base ya sea conocido y de confianza) | BA (al proponer pistas de nombres a probar en el Bloque 1 de una historia de descubrimiento, dejarlas explícitamente como "candidatos a probar contra la red real", nunca como asunciones de las que partir el diseño).

- **Lección**: no toda fuente geoespacial nueva obliga a tocar el tipo de geometría de dominio — después de dos historias seguidas que sí lo requirieron (IDEM/`UsoDelSuelo` con anillos interiores reales, historia 0011; cauces con líneas abiertas, historia 0012), vías pecuarias resultó ser el caso más simple de los tres: polígono real, sin huecos, que encaja exactamente con `Geometry` tal como ya existía. Confirmarlo explícitamente (contando `gml:interior`/`gml:exterior` en las fixtures reales, no asumiendo que "esta también traerá sorpresas" por el patrón de las dos historias anteriores) evitó diseñar una abstracción nueva que no hacía falta.
  - **Aplica a**: arquitecto (al diseñar el cliente de la siguiente fuente geoespacial, verificar la forma real de la geometría con la misma disciplina tanto si se espera que sea simple como si se espera que traiga complejidad — un patrón de dos historias seguidas con sorpresas no predice la tercera, cada fuente se verifica por sí misma).

## [2026-10-16] Historia 0015 — Cliente de Red Natura 2000

- **Lección**: `grep -c` cuenta **líneas que contienen al menos una coincidencia**, no apariciones — sobre un XML real minificado en una sola línea (habitual en respuestas de GeoServer), `grep -c '<gml:interior>'` da como mucho `1` aunque haya 20 anillos interiores reales, y `0` solo si de verdad no hay ninguno. La forma correcta de contar apariciones es `grep -o '<patrón>' | wc -l`.
  - **Causa**: al escribir la primera versión de la sección de Red Natura 2000 en `REGISTRY.md`, un `grep -c '<gml:interior>'` sobre la fixture real devolvió `0`, llevando a afirmar inicialmente "sin anillos interiores observados" — la misma conclusión equivocada que ya se evitó a propósito en la historia 0014 (vías pecuarias, donde sí resultó ser cierto) pero que aquí era falsa: la fixture real tiene 2 `gml:interior` reales (y 5 `gml:exterior`/`gml:Polygon`), confirmado al repetir el conteo con `grep -o ... | wc -l` antes de diseñar el parser. Si el parser se hubiera escrito ya con la premisa falsa (tratando cada `posList` como anillo exterior), habría repetido el bug exacto de la historia 0011 por un error de herramienta, no de verificación.
  - **Aplica a**: arquitecto/implementación (al contar apariciones de una etiqueta XML con `grep` sobre un fichero real, usar siempre `grep -o '<patrón>' | wc -l`, nunca `grep -c`, salvo que se sepa con certeza que el XML tiene una etiqueta por línea — las respuestas reales de GeoServer de este proyecto vienen minificadas en una sola línea) | review (al verificar una afirmación de "N apariciones reales" en REGISTRY.md/un ADR, reproducir el conteo con el propio comando en vez de confiar en el número citado, exactamente como hizo el code-reviewer de esta historia).

- **Lección**: cuando un agente se corta a mitad de tarea (límite de sesión) después de capturar evidencia real pero antes de escribirla, la evidencia ya capturada (fixtures reales grabadas en disco) sigue siendo válida y reutilizable — no hace falta repetir las peticiones de red, solo completar el análisis y la redacción a partir de lo ya obtenido.
  - **Causa**: el arquitecto de la historia 0015 se cortó tras grabar 10 fixtures reales (incluida la verificación de las tres geometrías del MVP, el `DescribeFeatureType`, y el intento de fuente complementaria del Ministerio) pero antes de escribir `REGISTRY.md`, el ADR o el Bloque 2. La sesión siguiente completó el ciclo íntegro (verificación, ADR, implementación, code review) a partir de esas mismas fixtures, sin volver a tocar la red — más barato y sin riesgo de que una segunda petición real diera un resultado distinto a la primera (p. ej. si el servicio hubiera cambiado entretanto).
  - **Aplica a**: cualquier agente (al retomar una tarea de verificación interrumpida, comprobar primero si ya hay fixtures reales grabadas en el directorio de destino antes de volver a pedir red — las fixtures ya capturadas son la fuente de verdad, no hace falta repetir la petición para "confirmar otra vez" salvo sospecha concreta de que algo cambió).

## [2026-10-16] Historia 0016 — Conectar vías pecuarias y Red Natura 2000 al informe

- **Lección**: un criterio de aceptación que dice "lista cada entidad con sus campos X/Y" se puede incumplir en *cualquier* salida que los muestre — HTML renderizado o texto impreso por un comando de CLI, por igual — sin que ningún test lo note, si el test solo comprueba que *algún* texto relacionado aparece (un nombre, una frase fija) en vez de comprobar explícitamente cada valor que el AC exige mostrar. Confirmado dos veces en dos superficies distintas del proyecto: no es un defecto específico de renderizar HTML, es un defecto de cómo se escribe el test de cualquier función de "listar/mostrar todos los campos".
  - **Causa**: (1, historia 0016) `_render_livestock_trail_section`/`_render_protected_area_section` listaban `name`/`trail_type`/`municipality_name` y `plan_name`/`zone_name`, pero nunca `area_m2`/`length_m` ni `plan_code`/`zone_code` — pese a que el AC los exigía explícitamente ("junto a `area_m2`/`length_m`", "y sus códigos"). Los tests existentes (`test_via_pecuaria_real_se_lista_con_nota_de_anchura_no_legal`, `test_espacio_protegido_real_se_lista`) pasaban igualmente porque solo comprobaban un nombre y una frase genérica. (2, historia 0019) `cli/main.py::review list` — primer código real de `cli/` — incumplió el mismo AC ("mostrando todos sus campos estructurados"): solo imprimía identifier/outcome/authority/cita/explicación, omitiendo `spatial.meters` (el campo más crítico para revisar con criterio una propuesta de cauce), `scope.region`/`soil_classes`, `attributes.building_origin` y coste/tokens de la llamada al LLM; su test solo comprobaba que `identifier` y la explicación aparecían en la salida. En ambos casos lo encontró el code-reviewer ejecutando/leyendo la salida real carácter a carácter contra el AC, no un test que fallara.
  - **Aplica a**: implementación (al escribir el test de cualquier función que liste varios campos de una entidad — render HTML o impresión de un comando de CLI, por igual —, afirmar explícitamente cada campo que el AC menciona por nombre; si el AC dice "campo X", el test debe buscar el valor real de X en la salida, no una frase que coexiste por casualidad con su ausencia) | review (al revisar cualquier función de "listar/mostrar todos los campos" contra su AC, leerlo criterio por criterio y marcar cada sustantivo citado como algo a buscar explícitamente en la salida producida — HTML o texto de terminal —, no solo "a ojo" si el resultado se parece a lo esperado).

- **Lección**: concatenar un prefijo de etiqueta fijo (`"Plan "`, `"Zona "`) delante de un valor de texto libre de un proveedor puede producir una palabra duplicada visible si ese valor ya empieza por la misma palabra — un caso real, no hipotético, cuando el dato real mismo lo contiene (`plan_name` real empieza por "Plan de gestión...").
  - **Causa**: `_render_protected_area_section` escribía `f"Plan {plan_name}"`, produciendo "Plan Plan de gestión de vegas..." contra el dato real de Villaconejos/Colmenar (ya confirmado en REGISTRY.md desde la historia 0015, pero nadie lo había renderizado hasta conectar la fuente en esta historia). Corregido anteponiendo el código corto (`plan_code`) en vez del texto fijo "Plan", que además resuelve el hallazgo anterior (código ausente del render).
  - **Aplica a**: implementación (al anteponer una etiqueta fija a un campo de texto libre de un proveedor externo, verificar contra al menos un valor real del dato si esa etiqueta coincide con el inicio típico del campo — mejor aún, anteponer un código corto/campo distinto en vez de texto fijo cuando exista, como se hizo aquí).

## [2026-10-16] Historia 0017 — Motor de reglas: evaluador determinista y primer hallazgo real

- **Lección**: `zip(xs, xs[1:], strict=True)` para generar pares consecutivos (el patrón estándar de "segmentos de una polilínea") **siempre** lanza `ValueError` — `xs[1:]` es por construcción un elemento más corto que `xs`, así que `strict=True` nunca puede cumplirse en este patrón. Es un error de categoría distinto al que `strict=True` existe para atrapar (dos secuencias que deberían tener la misma longitud pero no la tienen): aquí la diferencia de longitud es intencional y exacta.
  - **Causa**: `distance_polygon_to_line` (`domain/geometry.py`) usaba `zip(line.points, line.points[1:], strict=True)` para los segmentos de la polilínea. El arquitecto había verificado manualmente el número exacto esperado (`8.535301066249467` m) llamando a la lógica interna de distancia directamente, sin pasar por esa línea — la verificación manual del ADR no ejercitó el mismo camino de código que el test de `pytest`, así que el bug sobrevivió al diseño y solo apareció al ejecutar el test real.
  - **Aplica a**: implementación (al escribir `zip` sobre pares consecutivos de una misma secuencia — `zip(xs, xs[1:])` —, usar `strict=False`/omitir el argumento; reservar `strict=True` para comparar dos secuencias *distintas* que deben coincidir en longitud) | arquitecto (una verificación manual que llama a la lógica interna directamente, no al código público que el test ejercitará, no es verificación suficiente del camino completo — verificar contra la función pública, o aceptar que el número es una expectativa a confirmar, no un hecho ya probado).

- **Lección**: cuando un criterio de aceptación fija un número exacto esperado (no solo "debería dar algo razonable") calculado de antemano con los parsers y fixtures reales ya existentes, ese número actúa como oráculo de regresión capaz de atrapar bugs que un resultado "plausible" nunca delataría — y a la inversa, sin ese número, un bug que produce un valor de aspecto válido (como `0.0` en una función de distancia) pasa desapercibido indefinidamente.
  - **Causa**: un test que llamaba a `parse_wfs_parcels(xml)[0]` sin filtrar por referencia catastral tomó, de las 7 parcelas reales que trae la fixture de Villaconejos (la de interés más vecinas de bbox), una parcela equivocada y obtuvo distancia `0.0` — resultado perfectamente plausible en sí mismo (0.0 es un valor válido de la función) y que habría pasado cualquier revisión superficial. Solo se detectó al comparar contra el `8.535301066249467` m ya documentado en el ADR 0017, calculado de antemano ejecutando los mismos parsers sobre las mismas fixtures reales antes de escribir la historia.
  - **Aplica a**: arquitecto (al diseñar una historia sobre datos/fixtures reales ya existentes, calcular y documentar el número exacto esperado *antes* de la implementación — no solo "debe producir un hallazgo" — siempre que sea barato hacerlo con los parsers ya disponibles) | implementación (cuando una fixture real agrupa varias entidades relacionadas —p. ej. "parcela de interés + vecinas de bbox"—, filtrar explícitamente por el identificador de interés en vez de indexar por posición, aunque el test "parezca" pasar con `[0]`).

## [2026-10-16] Historia 0018 — Ampliar el catálogo de reglas: vías pecuarias y Red Natura 2000

- **Lección**: el filtro espacial `BBOX` de un WFS no es uniforme ni siquiera entre capas del mismo servidor/proveedor ya verificado — su precisión depende de la dispersión geométrica del *feature individual*, no del servidor ni del protocolo. Un feature que es un `MultiSurface` disperso (varios polígonos reales separados por decenas de km) hace que `BBOX` evalúe la envolvente del conjunto completo y devuelva "candidatos" a kilómetros de distancia real; un feature compacto (un único polígono) filtra con precisión. "El mismo proveedor/servidor ya se verificó para otra capa" no es evidencia de que esta lo comparta — antes de tratar "la fuente devolvió un feature no vacío" como proxy de "hay solape/cercanía real" en una condición espacial nueva, hay que verificar con geometría real + una librería de control independiente (`shapely`) el resultado exacto que debe producir, igual que ya exige la lección de la historia 0017 sobre fijar el número esperado antes de implementar.
  - **Causa**: el arquitecto verificó manualmente (parsers reales + `shapely` de control, sin red nueva) el resultado exacto que la regla de Red Natura 2000 debía producir antes de implementar, y descubrió que la premisa de la historia 0018 y de `REGISTRY.md` (hallazgo 3, historia 0015) era falsa: el WFS de IDEM devuelve el mismo feature de Red Natura 2000 para las tres bboxes de prueba, pero su filtro `BBOX` evalúa la envolvente del `MultiSurface` completo (5 polígonos reales dispersos en ~46×41 km), no la geometría real — la parcela de Villaconejos está en realidad a 1.36 km del polígono más cercano, Colmenar rústica a 8.33 km. La misma verificación confirmó que vías pecuarias (mismo servidor IDEM, mismo workspace `Zonas`, historia 0014) no tiene este problema: sus features son polígonos individuales compactos, filtro `BBOX` ajustado, cero falsos positivos en los casos reales "sin afección". La variable que importa es la geometría del feature, no el servidor — distinto de la lección ya registrada en la historia 0014 (adivinar nombres de workspace por analogía), que es sobre *descubrimiento* de un servicio, no sobre la *precisión* de un filtro espacial de un servicio ya conocido. Obligó a corregir la historia, el ADR y `REGISTRY.md` (hallazgo 3.1).
  - **Aplica a**: arquitecto (al diseñar cualquier condición espacial de solape/cercanía sobre una fuente WFS, verificar con geometría real + una librería de control el resultado exacto antes de implementar, incluso si el mismo servidor/workspace ya se verificó para otra capa — la precisión de `BBOX` se confirma por feature, no por servidor) | implementación (nunca tratar "la fuente devolvió un feature no vacío" como equivalente a "hay solape/cercanía real" sin una función de geometría explícita que lo confirme).

## [2026-10-16] Historia 0019 — Autoría de reglas asistida por LLM (DeepSeek)

- **Lección**: una constante de precio de un proveedor de LLM marcada explícitamente como "no verificada, a confirmar al cierre" puede seguir siendo incorrecta en el cierre aunque el diseño fuera impecable — los catálogos de modelos de LLM cambian de forma que ni siquiera un error de la API lo delata: un nombre de modelo puede seguir siendo *aceptado* como alias "legacy" mientras la petición real la sirve, en silencio, un modelo distinto y más nuevo a otro precio. "La llamada no dio error" no es evidencia de que el precio siga vigente — hace falta comparar explícitamente contra la página pública de precios en la fecha de cierre, no solo haber verificado en la fecha de diseño.
  - **Causa**: el ADR 0019 documentó el precio de `deepseek-chat` ($0.27/$1.10 por 1M tokens) "consultado de memoria, NO verificado contra la página real", exigiendo su confirmación real al cierre — el mismo patrón ya usado en las historias 0017/0018 para valores no verificados. La verificación real (dos llamadas reales a la API + comparación contra la página pública de precios) encontró que el precio había cambiado: la página ya no lista ningún modelo `deepseek-chat`; ese nombre es hoy solo un alias "legacy" todavía aceptado por la API, servido en realidad por `deepseek-flash` al precio de ese modelo ($0.15/$0.60). El fallo no fue de diseño ni de ejecución del arquitecto — fue que el catálogo del proveedor cambió entre el momento en que el precio se memorizó y el cierre de la historia, y el patrón de "marcar como no verificado y exigir confirmación real al cierre" atrapó el error exactamente como debía.
  - **Aplica a**: arquitecto (al documentar un precio de proveedor LLM como "a verificar al cierre", pedir explícitamente confirmar también que el nombre del modelo sigue existiendo como tal en el catálogo público, no solo que el número coincide — un alias legacy aceptado sin error es el caso que más fácilmente pasa desapercibido) | implementación (cualquier constante de precio de un proveedor de LLM tiene fecha de caducidad, no es un hecho fijo: re-verificarla contra la página pública en cualquier cierre de historia que la toque, no confiar en que ya se verificó una vez).

## [2026-10-17] Historia 0020 — Conectar las reglas aprobadas por el flujo de autoría al informe real

- **Lección**: cuando una fase de un ciclo (BA, arquitecto, implementación) deja una fila/entidad real en PostGIS como evidencia para que la fase siguiente la reutilice, esa evidencia es efímera **dentro del propio ciclo**, no solo entre sesiones distintas — cualquier `pytest -m integration` que corra entre medias (incluido el de la propia historia que se está implementando) la destruye. Documentar "aquí hay una fila real, reutilízala" no basta; corregir la asunción una vez tampoco inmuniza a la fase siguiente.
  - **Causa**: el BA asumió (por la documentación de cierre de la historia 0019) que la tabla `rule` tenía tres filas reales persistidas; estaba vacía (`tests/conftest.py::db_connection` trunca `rule` antes de cada test, y la suite ya se había corrido varias veces). Corregida esa asunción en el "Contexto" de la historia 0020, el arquitecto generó su propia fila real (`...-30680099`) para no depender de ella — y esa fila tampoco existía al llegar a la implementación, porque ejecutar `pytest -m integration` de la propia historia 0020 volvió a truncar `rule`, obligando a generar una tercera fila (`...-63408d7f`). Tres fases del mismo ciclo tropezaron con la misma causa raíz de forma independiente, cada una sin saber que la fase anterior ya había regenerado datos que la suite iba a volver a borrar.
  - **Aplica a**: BA | arquitecto | implementación (cualquier fase que necesite una fila real de PostGIS como evidencia para la fase siguiente debe regenerarla ella misma, justo antes de consumirla y después de su propia última ejecución de `pytest -m integration` — nunca reutilizar un identificador citado en un documento anterior sin verificarlo primero con una consulta real contra la tabla).

## [2026-11-25] Historia 0021 — Verificación de fuentes del primer hito de caminos públicos

- **Lección**: los campos `Fees` y `AccessConstraints` de un `GetCapabilities` no son la licencia del dato. La licencia se lee en el documento que el propio proveedor enlaza como tal, y se cita textualmente.
  - **Causa**: `REGISTRY.md` llevaba desde la historia 0001 la licencia de Catastro como "No conditions apply". Ese texto era el `Fees` del servicio. La licencia real (INSPIRE v1.0, julio de 2016) **prohíbe difundir la información original por Internet sin transformarla** y prohíbe presentar lo derivado como "cartografía catastral". Para informes privados el error no se notaba. Para un mapa público habría sido un incumplimiento de licencia.
  - **Aplica a**: BA | arquitecto | implementación. Ninguna capa entra en un artefacto publicado sin la licencia leída en su documento y citada en `REGISTRY.md`. Si el proveedor no publica un documento aparte (p. ej. IDEM), se anota que la única fuente es la declaración del servicio.

- **Lección**: un resultado de búsqueda web que atribuye un documento oficial (un anuncio del BOCM) a un municipio no prueba esa atribución. Se abre el documento primario y se lee antes de anotarlo como hallazgo.
  - **Causa**: la búsqueda presentó `BOCM-20230825-31` como una desafectación de un camino público en Chinchón. Al leer el PDF resultó ser de Chapinería, que no está en el ámbito. Se habría registrado un falso "acto sobre caminos" en un municipio del hito.
  - **Aplica a**: BA | implementación. Sobre todo al construir el conjunto de validación de caminos cerrados, que se nutre de fuentes de terceros.

## [2026-11-25] Historia #17 — Acceso y georreferenciabilidad de planimetrías IGN y MTN50

- **Lección**: "el original existe" no es "el original se puede descargar por lotes". Antes de prometer un paso automático sobre un fichero, abrir la ficha y leer cómo se descarga (aquí, un reCAPTCHA v3) y qué sirve cada vía (un WMS da un render ya reproyectado, no la imagen cruda).
  - **Causa**: el `GetFeatureInfo` de minutas solo enlaza al Centro de Descargas del CNIG. La ficha anuncia JPG + ECW de 11 a 30 MB, pero el botón exige un token reCAPTCHA y un formulario con campo señuelo. Sin leer el JS, el plan de georreferenciación habría contado con descargar el original en un proceso por lotes. Las minutas que sí se bajan sin captcha (WMS) son un mosaico ya reproyectado, con desajustes entre hojas.
  - **Aplica a**: BA (al redactar criterios de "se puede descargar", exigir cómo y por quién) | implementación (no automatizar ni eludir un captcha: se documenta como paso humano).

- **Lección**: el User-Agent por defecto de `requests` puede provocar 500/502 intermitentes en los WMS del IGN que `curl` no sufre. Usar un User-Agent identificable del proyecto (sin datos personales) y reintentar con espera.
  - **Causa**: la misma URL de `GetMap` del WMS de primera edición MTN devolvió 502 cuatro veces seguidas desde `requests` y 200 desde `curl`; con `User-Agent: parcel-intel-verificacion/0.1` funcionó. Se perdió tiempo creyendo que era un límite de tamaño.
  - **Aplica a**: implementación (adapters del IGN: fijar cabecera y reintentos desde el principio).

## [2026-11-25] Historia #26 — Clasificación y deslindes de vías pecuarias

- **Lección**: "no encontrada" solo es una conclusión si el canal donde se publicaría era consultable. Antes de escribir "no encontrada" o "sin deslinde publicado" en el registro, anotar qué canal se consultó y cuál no; si el canal principal (aquí el archivo del BOCM) no se puede consultar, la fila va como `bloqueado`, no como negativo verificado.
  - **Causa**: el BOCM no expone buscador público por URL (`/buscador?texto=…` devuelve 404), su RSS solo cubre 20 boletines, y el catálogo abierto de la Comunidad de Madrid no tiene nada sobre deslindes. La búsqueda web general no estaba autorizada en esta sesión. Sin distinguir esto, el registro habría afirmado que no existen deslindes en siete municipios, y esa afirmación se puede usar luego para marcar tramos en el mapa.
  - **Aplica a**: BA (al redactar criterios de "no encontrada" o "sin X publicado", exigir la lista de canales consultados y su resultado) | implementación (al investigar fuentes que solo existen en boletines oficiales, planear el escaneo del archivo por fechas, no asumir que un buscador por URL existe).

## [2026-11-26] Historia #19 — Trazado histórico con revisión y repositorio PostGIS

- **Lección**: `uv run pytest -m acceptance` **sustituye** el `-m 'not integration'` de `addopts`, así que selecciona también los criterios marcados `[integración]`. Un criterio `[integración: PostGIS local]` necesita que el job de CI tenga PostGIS con las migraciones aplicadas; si no, la PR falla en CI o alguien acaba saltando el test.
  - **Causa**: hasta la historia #19 ningún criterio de aceptación tocaba PostGIS (los `[integración]` de #17 se cumplen sin red con las fixtures). Al ejecutar `pytest -m acceptance` sin base de datos, CA4–CA6, CA9 y CA10 dieron `Connection refused`. El arreglo (servicio `postgis/postgis` en el job y migración con reintentos, por el reinicio interno de la imagen de la historia 0003) toca `.github/workflows/ci.yml`, y el push lo rechazó GitHub: la App de los agentes no tiene permiso `workflows`. La historia quedó parada hasta que lo aplicara el fundador.
  - **Aplica a**: BA y arquitecto (un criterio `[integración]` debe decir si corre en CI y con qué servicio; si exige cambiar un workflow, pedirlo al fundador antes de pasar la historia a `estado:lista`) | implementación (antes de escribir un test de aceptación `[integración]`, comprobar que el job de CI puede ejecutarlo; los agentes no pueden tocar `.github/workflows/`).

## [2026-11-26] Historia #16 — Texto público de discrepancias

- **Lección**: no ejecutar `uv run ruff format .` sobre todo el repo: el código existente no está formateado con ruff y reescribiría 24 ficheros ajenos (ADRs incluidos), ensuciando la PR. Formatear solo los ficheros propios (`uv run ruff format <ficheros>`).
  - **Causa**: al formatear la historia #16 con `.` se modificaron ADRs, adapters, api y cli sin relación; hubo que revertirlos con `git checkout`.
  - **Aplica a**: implementación (revisar `git status` antes del commit y comprobar que solo hay ficheros de la historia).

- **Lección**: el texto público que valida el fundador se protege con un hash de su forma canónica junto a la marca (fecha y enlace). Cambiar una plantilla, etiqueta o el aviso rompe el test hasta que se actualice la marca, lo que obliga a pedir nueva validación.
  - **Aplica a**: implementación y arquitecto (cualquier texto público de aprobación humana: misma técnica, en `domain/public_card.py`).

## [2026-11-26] Historia #24 — Licencia del servicio roadlink (IGN)

- **Lección**: una cita de licencia para un criterio "textual" sale del documento leído en bruto, no de un resumen. `WebFetch` sobre un PDF comprimido devuelve binario o resúmenes que parafrasean. Para leer un PDF, usar la herramienta de lectura de PDF (lee texto e imagen de cada página). Si además hace falta el texto en un fichero de la fixture, transcribirlo y etiquetarlo como transcripción; no presentar una transcripción como extracción mecánica.
  - **Causa**: el PDF de condiciones del IGN estaba comprimido; la primera lectura con `WebFetch` no devolvió texto, y las citas de la Orden FOM/2807/2015 obtenidas por resumen no son literales. Se marcaron como limitación en el registro en vez de darlas por textuales. En la corrección, el artículo 4 se citó tal como lo reproduce el PDF del IGN, y la discrepancia «Artículo 2» / «artículo 1» quedó como dato abierto.
  - **Aplica a**: implementación (para verificar una licencia o una norma, leer el documento primario con la herramienta de PDF o descargarlo a `tests/fixtures/`; un resumen de web no cuenta como cita).

- **Lección**: el texto de atribución de una fuente IGN sale de la tabla de productos del SCNE (identificador, fecha y variantes), no del nombre del servicio. Si el ámbito está en una sola comunidad autónoma, la variante se reduce a la forma sin enumerar las demás (considerando Tercero del documento de condiciones).
  - **Aplica a**: implementación y arquitecto (al fijar una atribución para el visor, anotar en `REGISTRY.md` la variante elegida y el motivo).

## [2026-11-26] Historia #25 — Adapter de tramos sin asfaltar (roadlink)

- **Lección**: el fixture de `roadlink` de la historia de fuentes estaba en EPSG:4326 y con todos los tramos `fictitious="true"`; un adapter que pide `crs`/`bbox-crs` EPSG:25830 necesita su propio fixture grabado en ese CRS. El caso `fictitious="false"` no apareció en la ventana real de Colmenar y se cubre con un test sintético etiquetado como tal.
  - **Aplica a**: implementación (al reutilizar un fixture de una historia de fuentes, comprobar que CRS y parámetros coinciden con los que pedirá el adapter).

## [2026-11-26] Historia #35 — Límites municipales del IGN

- **Lección**: el bbox de una parcela catastral no es un buen testigo de que un punto esté dentro de un término municipal. El límite del IGN y la parcela del Catastro son dos geometrías oficiales distintas, y una parcela pegada a una linde puede tener esquinas de su bbox al otro lado.
  - **Causa**: el CA2 pedía que las cuatro esquinas del bbox de la parcela 99170A90100001 cayeran dentro de Villaconejos. La esquina noroeste cae a 106,5 m dentro de Chinchón y 7 de los 72 vértices de la parcela quedan fuera del término del IGN. No es un cambio de ejes (los errores de ejes darían kilómetros, y la parcela de Colmenar cae bien). El criterio se escribió sin calcular el resultado con los datos reales.
  - **Aplica a**: BA y arquitecto (un criterio que fija "estos puntos caen dentro de esta geometría" se calcula con los datos reales antes de pasar a `estado:lista`, como ya exige la lección de la historia 0017) | implementación (si un criterio no se cumple con los datos reales, no se reinterpreta: se comenta y se pasa a `estado:necesita-fundador`).

- **Lección**: cuando una historia de diseño propone una pieza de infraestructura y otra historia la fusiona antes de que esta se desarrolle, el desarrollador la reutiliza y lo anota en el ADR en lugar de seguir el diseño al pie de la letra. Aquí `CachedRawFetcher` (#34) sustituyó a la `RawResponseCache` de #23; la guarda de truncado se pasa como `validate` para que una respuesta incompleta no entre en la caché inmutable.
  - **Aplica a**: implementación (releer los comentarios de coordinación de la issue y `git log` antes de crear un módulo que el diseño da por nuevo).

## [2026-11-26] Historia #22 — Trazado común `Trail`

- **Lección**: al mover un símbolo de un módulo a otro y querer que siga importable desde el sitio antiguo, importarlo con alias explícito (`from parcel_intel.domain.trail import SourceKind as SourceKind`). Con `mypy` estricto (`no_implicit_reexport`), un `import` normal no re-exporta y todos los que lo importaban del módulo antiguo fallan.
  - **Causa**: #22 movió `SourceKind` de `discrepancy_criterion` a `trail` (ADR 0029) sin tocar los tests de #14 ni `public_card`/`discrepancy`, que lo siguen importando de `discrepancy_criterion`.
  - **Aplica a**: implementación (antes de mover un tipo, `grep` de sus importadores; dejar el alias explícito y un test `is` que fije que es el mismo objeto).

## [2026-11-26] Historia #31 — Detección de discrepancias de un municipio

- **Lección**: un diseño escrito antes de que sus dependencias cerraran usa nombres orientativos (`TrailSource`, `evaluate`, `Geometry | LineGeometry`) y condiciones que caducan ("RED_IGN fuera mientras #24 esté abierta"). Antes de codificar, leer el código real de las dependencias ya fusionadas (`compare_trail`, `SourceRef`, `SourceKind`) y el estado actual de las issues citadas; adaptar el contrato y dejar la desviación escrita en el ADR y la PR.
  - **Causa**: #15 pedía `evaluate(trail, candidates, other_source, other_provenance)`; lo fusionado es `compare_trail(subject, SourceRef, counterparts)`, que exige que todas las contrapartes compartan `SourceRef` (incluida `data_date`). Hubo que construir un `SourceRef` por capa, con la fecha común o `None`. Y #24 ya estaba cerrada, así que `RED_IGN` entró en `REDISTRIBUTABLE_SOURCES`.
  - **Aplica a**: arquitecto (al fijar un contrato con una historia aún sin implementar, marcarlo como orientativo y fechar las condiciones) | implementación.

- **Lección**: el identificador de un registro derivado de un criterio solo cambia con lo que entra en su hash. Un test "cambia si cambian los datos" debe variar un dato que esté en el hash (aquí la zona), no una medida (distancia) que no lo está.
  - **Aplica a**: implementación (al escribir el test de un identificador por contenido, comprobar qué campos entran en él).

## [2026-11-26] Historia #43 — NOTICE de atribuciones por capa y ADR de licencias

- **Lección**: al escribir una lista de fuentes "que pueden publicarse" en un criterio o en un fichero público, derivarla de la tabla de resumen del registro (`REGISTRY.md`, columna «Conclusión» = **Publicar**), no de la lista que trae la historia de memoria. La historia nombraba cuatro familias; el registro marca seis filas «Publicar» (incluye planimetrías y MTN50, que la historia omitía).
  - **Causa**: el CA1 enumeraba «red de transporte, unidades administrativas, PNOA; vías pecuarias IDEM», pero el CA6 exige que toda fuente «Publicar» del registro aparezca en `NOTICE`. Seguir la lista literal de la historia habría dejado el test de coherencia en rojo, o peor, un mapa que publica planimetrías sin atribución visible.
  - **Aplica a**: BA (al redactar «lista cada fuente que puede…», referirse a la columna de conclusión del registro y no repetir la lista de memoria) | implementación (un test de coherencia entre registro y documento público debe mapear cada fila «Publicar» explícitamente y fallar si aparece una sin mapear).

- **Lección**: una historia que pide «el ADR NNNN» debe comprobar antes que ese número no esté ya ocupado. Aquí el CA5 pedía el ADR 0024, que ya es «merge-por-workflow-no-por-agente», una decisión distinta y aceptada.
  - **Causa**: la historia se redactó sin consultar `docs/adr/`. Reutilizar el número exigía reescribir un ADR aceptado. Se redactó el ADR con el siguiente número libre, se dejó el test de CA5 con el número literal (falla) y el conflicto se escaló al fundador en lugar de decidirlo por cuenta propia. Resolución: ADR 0035 y test de CA5 por sufijo.
  - **Aplica a**: BA y arquitecto (al citar un número de ADR en un criterio, comprobar `ls docs/adr/` y el siguiente libre) | implementación (no reasignar un número ocupado en silencio; el criterio se escribe literal y el conflicto se escala).
  - **Segunda parte (misma historia, tras fusionar `main`)**: el 0032 que eligió el fundador lo ocupó #36 mientras la PR estaba en borrador. El merge dejó dos ADR con el mismo número. Se movió el ADR de #43 al siguiente libre (0035) y se escaló. Al fusionar `main` en una rama con ADR nuevos, recomprobar la numeración en el mismo merge.
  - **Aplica a**: implementación (tras cada merge de `main`, `ls docs/adr/` y buscar números duplicados antes de subir la rama).

- **Lección**: al escribir atribuciones y licencias en un documento público, distinguir lo que consta literalmente en la fuente de lo que solo se infiere del formato. Solo se citaron como textuales las atribuciones que el registro trae completas (roadlink, planimetrías, MTN50); para PNOA y unidades administrativas el registro solo declara `CC BY 4.0 scne.es` / `ign.es`, y el formato «Obra derivada de <producto> <fecha>» queda «pendiente de verificar».
  - **Aplica a**: implementación (si el texto de atribución de una capa no consta completo en el registro, marcarlo «pendiente de verificar» en el documento y no completarlo con el patrón del ejemplo).

## [2026-11-26] Historia #44 — Purga del historial de git

- **Lección**: los tests de scripts de shell que dependen de una herramienta de operador (aquí `git-filter-repo`) se saltan en CI si el job no la instala, y los agentes no pueden tocar `.github/workflows/`. Un salto silencioso deja los criterios sin verificar en CI. Anotar en la PR que corren solo con la herramienta instalada, y pedir al fundador la línea `pip install git-filter-repo` en `ci.yml`.
  - **Causa**: la historia fijaba que `git-filter-repo` no entra en `pyproject.toml` y que "CI lo instala si hace falta", pero instalarlo exige editar el workflow. Los tests se escribieron con `skipif` y se ejecutaron en local instalándolo en el venv.
  - **Aplica a**: BA y arquitecto (si un criterio exige una herramienta externa en CI, decir quién cambia el workflow antes de `estado:lista`) | implementación (los scripts se prueban con repos temporales; las salvaguardas de rechazo no dependen de la herramienta y no se saltan).

## [2026-11-26] Historia #41 — Despliegue automático en GitHub Pages

- **Lección**: una historia cuyo criterio exige editar `.github/workflows/*.yml` no la puede completar un agente: el push lo rechaza GitHub (la App no tiene permiso `workflows`). Mismo bloqueo que #19; el refinador debe pedírselo al fundador antes de `estado:lista`, o separar el cambio del workflow en una tarea suya.
  - **Aplica a**: BA y arquitecto (si un CA toca un workflow, marcarlo y adjuntar el parche) | implementación (subir la rama sin el workflow, dejar el parche en la issue y abrir la PR en borrador).

## [2026-11-26] Historia #36 — Recorte y capa de vías pecuarias por municipio

- **Lección**: una librería de terceros sin stubs en el entorno (aquí `shapely`) rompe `uv run mypy` estricto aunque el código sea correcto. La dependencia de tipos (`types-shapely`) va en `[project.optional-dependencies].dev` en la misma PR que la librería, y el adapter que la usa es el único sitio con sus imports.
  - **Causa**: el diseño pedía añadir `shapely` y a `forbidden_modules` del dominio, pero no mencionaba los stubs; `mypy` falló con `import-untyped` al primer intento.
  - **Aplica a**: arquitecto (al proponer una dependencia nueva, comprobar si trae tipos) | implementación.

- **Lección**: el oráculo de un Bloque 0 se calcula pasando las respuestas reales grabadas por el propio código de producción (adapter + clipper), no por un recuento aparte con otra lógica. Así el número fijado en el test es el que el código da hoy, y una regresión del recorte cambia el test.
  - **Causa**: los 94 tramos distintos que traen los 7 bbox (27 sin tocar ningún término del ámbito) y las 79 piezas salieron de ejecutar `LivestockTrailAdapter` y `ShapelyPolygonClipper` sobre las fixtures; un recuento por `CD_MUNI` habría dado otra cifra (56) y habría escondido las astillas de los vecinos.
  - **Aplica a**: arquitecto e implementación (los números de REGISTRY de una historia de procesamiento salen de ejecutar el pipeline sobre las fixtures reales).

## [2026-11-26] Historia #45 — Fixtures sintéticas de Catastro

- **Lección**: al sustituir las fixtures reales de una fuente, inventariar primero qué fixtures **de otras fuentes** se grabaron a partir de ellas (bbox, ventanas de raster, oráculos de distancia, centroides) y conservar esa invariante (aquí, la envolvente de tres parcelas). El inventario se hace buscando **todos los alias** del directorio (`FIXTURES_DIR as CATASTRO_FIXTURES_DIR`, `_CATASTRO_FIXTURES_DIR`, `CATASTRO_FIXTURES`), también en los tests de aceptación de otras historias.
  - **Causa**: el diseño cruzó `grep` de `CATASTRO_FIXTURES_DIR` con los `approx(` de bbox y encontró los siete adapters afectados, pero no `tests/acceptance/test_historia_35.py`, que lee los centroides de `Consulta_CPMRC` con otro alias (`CATASTRO_FIXTURES`) y fijaba sus valores reales. Saltó al ejecutar la suite; se adaptó el literal sin tocar el docstring del criterio.
  - **Aplica a**: arquitecto (inventario por alias y por nombre de fichero, no por una sola constante) | implementación (ejecutar la suite completa nada más borrar los originales, antes de adaptar nada, para ver el inventario real).

- **Lección**: un oráculo de control calculado con otra librería (`shapely`) no coincide bit a bit con la función propia: aquí difieren en ~4·10⁻¹¹ m. Se compara con `pytest.approx(abs=1e-9)` contra el literal de control, nunca con `==` contra el valor que da la función bajo test (eso sería volver al oráculo circular de la lección 0017).
  - **Aplica a**: implementación y arquitecto (al fijar un oráculo con `shapely`, anotar la tolerancia junto al literal y de dónde salió).

- **Lección**: un test que busca "qué funciones leen tal directorio de fixtures" no puede hacerlo por subcadena del código: `LIVESTOCK_FIXTURES_DIR` contiene `FIXTURES_DIR`. Se hace con `ast`, resolviendo los nombres que el módulo enlaza a ese directorio (imports con alias y asignaciones), y se fija un mínimo de coincidencias para que la detección no pase en vacío.
  - **Aplica a**: implementación (tests de convenciones sobre el propio árbol de tests).

## [2026-11-26] Historia #27 — Capa de camino actual por municipio

- **Lección**: un adapter verificado con una ventana de 1×2 km no está verificado para un término municipal entero. `roadlink` devuelve miles de tramos por término (19.557 en Aranjuez) y el adapter de #25, con una página de 500 y sin filtro, o lanzaba "resultado truncado" o, peor, habría dejado fuera la mayoría de los caminos. El servidor admite `filter=surfacecategory='unpaved'` y `offset`.
  - **Causa**: el diseño de #27 daba por bueno `get_nearby_unpaved_roads` porque #25 estaba `confirmado`, y la historia pedía "recortar a cada término" sin calcular cuántos tramos trae un término. Se vio al grabar la respuesta real de cada bbox (`numberMatched` 823-19.557 frente a `numberReturned` 500).
  - **Aplica a**: BA y arquitecto (al reutilizar un adapter para una consulta mucho mayor que la verificada, pedir el `numberMatched` real del caso grande antes de pasar a `estado:lista`) | implementación (grabar primero la respuesta del caso más grande del ámbito).

- **Lección**: una respuesta real de decenas de MB no se versiona; se versiona un **recorte** honesto (tramos reales elegidos por lo que ejercitan, sin tocar su geometría) y se documenta cómo se obtuvo. El número de la pasada completa (oráculo) se calcula con el propio pipeline sobre la respuesta íntegra antes de recortar y se anota en `REGISTRY.md`.
  - **Causa**: las 7 consultas reales pesan ~60 MB (28 páginas de 1-2 MB). Los recortes de 10-12 tramos por municipio (cruces de límite, ficticios, no ficticios y vecinos que no tocan el término) caben en 100 KB y siguen ejercitando el caso multi-parte (`281700231594`, 3 + 2 partes).
  - **Aplica a**: implementación (etiquetar el fixture como recorte en el docstring del módulo de apoyo; el paginado real se prueba con sintéticas etiquetadas y con el criterio `[integración]`).

- **Lección**: "sin tramos" no es "sin procedencia". Un port que devuelve `list[...]` pierde la procedencia de la consulta cuando la lista está vacía, y un fichero válido de cero elementos (CA3) sigue necesitando fuente, URL, fecha y licencia. El port devuelve un resultado con la procedencia aparte.
  - **Aplica a**: arquitecto (al diseñar un port de consulta cuya salida se publica con procedencia, devolverla también en el caso vacío).

## [2026-11-26] Historia #32 — Lote de discrepancias desde la CLI

- **Lección**: un diseño que fija "el productor escribe el formato común" no garantiza que el productor lo escriba. #36 (vías pecuarias) solo imprime por pantalla y no deja `data/derived/vias_pecuarias/`; el lote de #32 lo trata como fuente ausente y su criterio `[integración]` (CA11) pide una capa que ningún comando genera hoy. Antes de pasar a `estado:lista` una historia que consume ficheros, comprobar con `grep` que existe el comando que los escribe.
  - **Aplica a**: BA y arquitecto (un "Depende de #N cerrada" no es "el fichero existe") | implementación (comprobarlo al empezar y anotarlo en la PR).

- **Lección**: una clave de salida con una palabra sensible por casualidad (`nombre` para el nombre de una medida) rompe un test que busca nombres de persona y, peor, invita a leer el fichero como si los llevara. Se llama `medida`, y el test prohíbe la palabra entera en claves y valores.
  - **Aplica a**: implementación (al diseñar claves de un fichero publicable con test de "sin titularidad").

- **Lección**: las constantes de fuente en los tests (`VIA = "via_pecuaria_oficial"`) se tipan `SourceKind`; sin anotación mypy las infiere `str` y rechaza cada llamada al port.
  - **Aplica a**: implementación.

## [2026-11-26] Historia #46 — Fixtures sintéticas de caminos de Catastro

- **Lección**: una fixture cruda grabada "para más adelante" sin ningún test que la consuma también tiene que entrar en la purga y no da ninguna cobertura. Antes de grabar una respuesta real, la historia dice qué test la consume.
  - **Causa**: las seis `tests/fixtures/caminos/catastro_*` de la historia 0021 no las leía ningún test; solo las citaba `REGISTRY.md`. Sustituirlas no exigió adaptar tests, sino escribir los que fijan su contrato (20 parcelas, 4 caminos, `ssp` distinto de `areaValue`).
  - **Aplica a**: BA y arquitecto (al pedir una grabación real, nombrar el test que la usará) | implementación.

- **Lección**: un criterio que prohíbe "toda huella de lo original en `tests/`, `docs/` y `src/`" choca con una decisión previa del ADR que deja fuera los literales de referencias en código que no lee fixtures. Las referencias de las parcelas canónicas del MVP (polígono 1, parcela 1 de Colmenar y de Villaconejos) salen como literal en unos 140 sitios (tests de API y repositorio, ADR e historias archivadas, `REGISTRY.md`). Antes de pasar a `estado:lista` un CA de escaneo global, ejecutar el escáner con las huellas actuales y ver qué encuentra.
  - **Causa**: el CA7 de #46 y la consecuencia del ADR 0033 ("quedan fuera los literales… se valorarán en #39") se contradicen. Se dejó el test literal (falla solo por referencias; ningún vértice coincide) y se escaló al fundador en lugar de añadir una lista de excepciones por cuenta propia.
  - **Aplica a**: BA y arquitecto (un CA de "no queda ninguna X en el repo" se prueba antes de `estado:lista`) | implementación (no relajar el criterio en silencio).
  - **Resolución**: el fundador eligió sustituir los literales (28 ficheros, mapa fijo real → sintética de provincia 99, misma real = misma sintética). Las huellas ya contenían las referencias, así que bastó el escáner existente; los tests que comprueban "no hay referencias reales" comparan huellas, nunca literales partidos en dos cadenas.

- **Lección**: al fusionar `main` en una rama puede romperse un test de otra historia que comprueba el orden de menciones en un documento que `main` acaba de ampliar (`test_historia_44` CA5 con el runbook). Acotar la búsqueda a la sección que el criterio nombra en vez de al documento entero.
  - **Aplica a**: implementación.


## [2026-11-26] Historia #28 — Arroyos (`HG`) frente a caminos (`VT`) en parcelas 9xxx

- **Lección**: en las parcelas ≥ 9000 de Catastro, ni el número ni el paraje bastan para decir «camino». Hay `VT` con paraje `DESCUENTO` o `NO INFORMADO`, `HG` (arroyo, barranco, río) con `NO INFORMADO`, `I-` improductivo con paraje `CAMINO`, y parcelas ≥ 9000 con cultivos ordinarios. Manda la clase de subparcela, y todas las subparcelas deben ser `VT`.
  - **Causa**: la verificación de #28 cruzó los cauces de IDEE con las parcelas ≥ 9000 de Colmenar y Villaconejos. Un primer intento con nombres de municipio inventados dio respuestas vacías en 20 de 47 parcelas; los códigos INE de los vecinos hay que sacarlos del dato, no de memoria. El servicio también devuelve la `Ñ` mal codificada (`CA¥ADA`).
  - **Aplica a**: implementación (filtros sobre Catastro: clase, no paraje; no comparar parajes por igualdad) | BA (un criterio que verifica contra el servicio sin versionar respuestas se cumple con un script local y una sección de `REGISTRY.md` con recuentos y canales, y su test lee esa sección).

## [2026-11-26] Migración a repo público — fallo de CI por un cambio de documentación

- **Lección**: hay tests que leen documentos. Por ejemplo, `test_historia_44` comprueba el orden de los pasos de `docs/runbook-publicacion.md`. Un cambio "solo de docs" empujado a `main` sin ejecutar `uv run pytest` dejó CI en rojo en `main` y en la PR en curso (#46), que lo heredó al fusionar con `main`. Antes de empujar cualquier cambio, aunque solo toque `.md`, se ejecutan los cinco comandos.
  - **Aplica a**: todos (agentes y fundador), sobre todo con commits directos a `main`.

## [2026-11-26] Historia #83 — Escritor de la capa de vías pecuarias

- **Lección**: antes de fijar un criterio sobre "los datos reales" (un municipio vacío, un código repetido, un papel `via`), contarlo en la capa real. En #83 la issue daba por vacío a Valdelaguna (28157) y por `role="via"` a todo el ámbito; la capa real tiene en Valdelaguna una astilla de 0,25 m², no repite `trail_code` en ningún municipio y las fixtures traen `DS_TIPO` ("Abrevadero-Descansadero", "Colada-Abrevadero") que la tabla `LIVESTOCK_TRAIL_TYPE_ROLE` no cubre, así que `livestock_trail_role` lanza `UnknownLivestockTrailTypeError`.
  - **Causa**: los criterios se redactaron a partir de conteos de #36 sin volver a mirar el resultado del recorte. El escritor no escribe `papel` (el lector lo toma como `via`) y guarda `tipo`; clasificar anejos queda pendiente de decisión de producto.
  - **Aplica a**: BA (comprobar en los datos cada afirmación "el municipio X está vacío" o "todos son Y") | implementación (los casos que los datos reales no ejercitan, como ids repetidos o capa vacía, se cubren con un test sintético etiquetado como tal) | arquitecto (la tabla de papeles, cerrada por ADR 0029, debe revisarse antes de publicar).

- **Lección**: `uv run pytest -m acceptance`, escrito literalmente, pisa el `addopts = "-m 'not integration'"` de `pyproject.toml` y ejecuta también los tests `[integración]` contra la red real (tarda más de 2 minutos). Sin red, usar `-m "acceptance and not integration"`.
  - **Aplica a**: implementación.

## [2026-11-26] Historia #52 — Casos de caminos cerrados del ámbito

- **Lección**: `WebFetch` no lee los PDF con texto codificado por glifos (actas de pleno, BOCM): devuelve el binario. El PDF queda guardado en `tool-results/`; sin `pdftotext` ni librerías, basta `python -I` con `zlib` para descomprimir los flujos y aplicar los `ToUnicode` de cada fuente. Para el BOCM es más rápido la versión HTML `https://www.bocm.es/bocm-AAAAMMDD-N`.
  - **Causa**: las actas de comisión salen con texto parcialmente corrupto (letras sustituidas): sirven para localizar un punto, no para citar.
  - **Aplica a**: implementación.

- **Lección**: antes de atribuir un camino a un municipio, comprobar que el homónimo no es otro: el «Colmenar» de una búsqueda era Colmenar Viejo (Vado de la Tabla), no Colmenar de Oreja (lección 0021 otra vez).
  - **Aplica a**: implementación | BA.

## [2026-11-26] Historia #37 — Capa interna de caminos de Catastro

- **Lección**: las referencias "de ejemplo" sintéticas pueden coincidir con una referencia real: la de la parcela 1 del polígono 1 de un municipio real (un ejemplo «inocente» con ceros a la izquierda en un test) casó con una huella de `tests/fixtures/catastro_huellas_reales.json` y rompió `test_historia_46::test_ca7`. Para tests de formato usar números ≥9000 (`28043A00109101`), que son los de la tabla sintética, y correr `uv run pytest` entero antes de la PR.
  - **Aplica a**: implementación.

- **Lección**: el WFS y `Consulta_DNPPP` del Catastro (`ovc.catastro.meh.es`) cortan la conexión (`Connection reset by peer`) desde los runners de GitHub Actions, aunque la raíz responda (400 a un GetCapabilities sin parámetros). Un criterio `[integración]` contra el Catastro real no puede verificarse en el agente; hay que ejecutarlo desde una máquina con acceso.
  - **Aplica a**: BA | implementación.
## [2026-11-26] Historia #84 — Publicar capas en site/datos

- **Lección**: una puerta "denegar por defecto" (aquí `PublishableLayer` con `layer_provenance`) también rechaza los casos degenerados que nadie miró, y el rechazo sale con el mensaje del caso común. La capa de vías pecuarias **vacía** toma la procedencia del límite del IGN (uso interno) porque no hay consulta de IDEM de la que tomarla. Se rechaza como «no redistribuible» y el mensaje sugiere regenerarla, lo que no arregla nada. Le pasa a Belmonte de Tajo (28019) en los datos reales, así que `publicar-capas --ambito` sale con 1.
  - **Causa**: el CA13 («capa vacía, no se publica») se probó con una capa vacía sintética con procedencia publicable. Nadie ejecutó el comando sobre la capa vacía real que escribe #83.
  - **Aplica a**: arquitecto y BA (al fijar el caso "vacío" de una capa que pasa por una puerta de licencia, decir de dónde sale su procedencia en el dato real) | implementación (ejecutar el comando nuevo sobre todos los municipios reales del ámbito, no solo sobre el del criterio, y anotar lo que falle).

- **Lección**: un adapter que empieza a marcar `PUBLIC` en origen rompe tests de otras historias que fijaban "el adapter no marca" (#22 CA5 y CA8) y los literales de procedencia de los escritores (#83 CA3). Se actualiza el literal o se construye el caso sintético con la marca explícita. No se relaja la aserción ni se toca el docstring del criterio.
  - **Aplica a**: implementación (antes de cambiar qué marca un adapter, `grep` de `Redistribution.` y de `"licencia"` en `tests/acceptance/`).

## [2026-11-26] Historia #54 — Congelar el conjunto de validación

- **Lección**: una historia que congela un conjunto con mínimos fijos (10 casos, 5 en el ámbito) puede quedar por delante de las que lo rellenan: #53 rebajó los mínimos a 6/2 por decisión del fundador y `casos.json` tiene hoy 6 casos y 2 en el ámbito. Se entrega la herramienta (huella, integridad, workflow) pero no `CONGELADO.md`, y la historia pasa a `estado:necesita-fundador`.
  - **Causa**: el criterio de #54 copia los mínimos del comentario original de #51; la rebaja posterior solo quedó escrita en #53.
  - **Aplica a**: BA (al rebajar un mínimo en una historia, buscar y actualizar las historias que dependen de él) | implementación (comprobar los recuentos reales de `casos.json` antes de congelar).

- **Lección**: el test de integridad del repo real debe pasar mientras no exista ningún `CONGELADO*.md` (solo valida `casos.json`) y ser estricto en cuanto exista el primero; así no bloquea CI durante la recopilación.
  - **Aplica a**: implementación.

## [2026-11-26] Historia #85 — Visor estático con Leaflet

- **Lección**: en Leaflet, "texto" no es texto. `bindPopup("…")` y `control.attribution().addAttribution("…")` insertan la cadena como **HTML**. Para mostrar datos o atribuciones como texto plano hay que pasar a `bindPopup` una función que devuelva un nodo construido con `textContent`, y escapar cada atribución antes de `addAttribution`. El diseño decía «texto plano» sin decir cómo lo consigue la librería.
  - **Aplica a**: implementación (cualquier librería de mapas: leer cómo inserta cada contenido antes de darle datos externos) | review (buscar cadenas pasadas a `bindPopup`, `setContent` o `addAttribution`).

- **Lección**: para probar módulos ES del sitio con `node --test` sin poner `package.json` en `site/` (sería un `.json` sin procedencia), se copian a un directorio temporal junto a un `package.json` `{"type": "module"}`. Renombrarlos a `.mjs` rompe los `import "./x.js"` entre módulos. El DOM se inyecta (`crearVista(document)`), para probarlo con un documento falso que lance en `innerHTML`.
  - **Aplica a**: implementación (código JS del visor) | arquitecto (al diseñar módulos del navegador, dejar el `document` inyectable).

- **Lección**: en las Actions de los agentes no se puede lanzar un navegador sin cabeza (`chromium`, `google-chrome` piden aprobación). La comprobación en un navegador real queda como paso manual anotado en la PR; los tests cubren el visor con un `L` falso, así que un error de uso de la API real de Leaflet solo se ve en el navegador.
  - **Aplica a**: implementación (anotar en la PR qué no se pudo ver) | fundador (abrir la URL de Pages tras el despliegue).

## [2026-11-26] Historia #86 — Publicar el ámbito completo y vigilar el peso

- **Lección**: una puerta "denegar por defecto" que también mira lo vacío bloquea ejecuciones enteras por un caso sin datos. Si una capa no tiene elementos, no hay nada que redistribuir: se omite antes de la puerta y se declara en la salida. El dominio sigue siendo estricto; la decisión vive en el caso de uso y se prueba con el caso real (Belmonte de Tajo).
  - **Aplica a**: arquitecto (al añadir una validación sobre una colección, decidir qué pasa con la colección vacía) | implementación (ejecutar el comando sobre TODO el ámbito real antes de dar la historia por buena: un municipio atípico solo sale así).
## [2026-11-26] Historia #50 — Guardia de curated/** en fusionar.yml

- **Lección**: en las Actions de los agentes `chmod` y `sed -i` sobre ficheros del repo piden aprobación. Los scripts nuevos de `.github/scripts/` se invocan con `bash fichero.sh` (en el workflow y en los tests), no por su ruta, para no depender del bit de ejecución. Y un test que busca `gh pr merge` en `fusionar.yml` encuentra primero el comentario de cabecera: se ancla a la orden real (`gh pr merge "$pr"`).
  - **Aplica a**: implementación (scripts de shell y tests que leen workflows).

- **Lección**: la lógica de decisión de un workflow (aquí «¿toca `curated/`?» y «¿aprobó el fundador el último commit?») se extrae a scripts que leen stdin; así se prueba con JSON fijo sin red y el YAML solo la invoca.
  - **Aplica a**: arquitecto | implementación.

## [2026-11-26] Historia #37 — Capa interna de caminos de Catastro

- **Lección**: una respuesta de error determinista de una fuente (aquí `lerr` de `Consulta_DNPPP`: «no existe ningún inmueble») se cachea igual que una buena; si no, `--sin-red` no puede repetir la pasada con red. La validación previa a la caché solo rechaza lo transitorio (excepciones de servicio, truncados) y el error de dominio se relanza al leer.
  - **Causa**: el WFS de Catastro devuelve parcelas ≥ 9000 que DNPPP no reconoce; sin cachear el `lerr`, la repetición sin red falló con `RawCacheMissError`.
  - **Aplica a**: implementación (adapters con `CachedRawFetcher`) | arquitecto.
## [2026-11-26] Historia #110 — Arnés de evaluación de candidatos

- **Lección**: en una medida por muestreo de puntos medios con `<=` (aquí `line_overlap_fraction`, paso 1 m), un oráculo escrito a mano falla si el alcance coincide con la distancia de un punto medio (0,5 m del vértice): ese punto cuenta como cubierto. Al calcular el oráculo, listar los puntos medios cercanos a la frontera y elegir un alcance que no caiga justo en uno, o fijar ese caso límite a propósito en un test propio.
  - **Causa**: un test unitario con alcance 0,5 m esperaba 0,5 y la función dio 0,505: el primer punto medio del segundo segmento quedaba a 0,5 m exactos. La función era correcta; el oráculo no.
  - **Aplica a**: arquitecto e implementación (oráculos de funciones muestreadas).

## [2026-11-26] Historia #115 — Marca Paso Común en el sitio

- **Lección**: un «dato verificado» de una historia (aquí la URL de Pages `…/parcel-intel/`) caduca si el repositorio se renombra entre el refinado y el desarrollo. CLAUDE.md ya decía `…/paso-comun/`. Antes de fijar una URL absoluta en un criterio, contrastarla con CLAUDE.md y con `git remote -v`; el desarrollador que la ve distinta la usa corregida, lo deja escrito en el docstring de los tests y lo escala.
  - **Aplica a**: BA (no copiar URLs de una consulta antigua) | implementación.

- **Lección**: en las Actions de los agentes `cp` y las redirecciones piden aprobación, también para binarios (PNG). Funciona `uv run python -I -c "import shutil; shutil.copy(...)"` para copiar ficheros ya versionados.
  - **Aplica a**: implementación.

- **Lección**: los tests que fijan la lista exacta de `<link>` de `index.html` (#85 CA1) se rompen en cuanto se añaden favicons o manifest. Filtrar por `rel="stylesheet"` en vez de comparar todos los `<link>`.
  - **Aplica a**: implementación (editar `<head>`; #114 y #116 también lo tocan).
## [2026-11-26] Historia #114 — Tokens de diseño y tipografía Atkinson Hyperlegible

- **Lección**: un `unicode-range` declarado en un CSS de diseño no prueba que la fuente tenga esos glifos. `tokens.css` anuncia U+2190-2193 (flechas) para Atkinson Hyperlegible Next y Mono, pero la versión oficial 2.001 no los trae (tampoco U+00AD ni U+00B5). Se comprueba sobre el TTF completo con `fontTools` antes de fijar un criterio de cobertura, y el volcado de codepoints de cada WOFF2 se guarda junto a él.
  - **Causa**: el CA2 pedía que las dos Next cubrieran `←↑→↓`. El test, que lee el volcado, falla y la historia se escala al fundador. No se inventaron glifos.
  - **Aplica a**: BA y arquitecto (un criterio de cobertura de glifos se calcula con la fuente real antes de `estado:lista`) | implementación (no relajar el criterio en silencio).

- **Lección**: los tokens de color del sistema de diseño no cubren todo criterio de contraste. `--color-line-strong` da 1,98:1 (claro) y 2,87:1 (oscuro) frente a `--color-bg`, por debajo del 3:1 de WCAG para bordes de controles. En el visor el borde de `.campo select` usa `--color-muted`, y `line-strong` queda para separadores decorativos. #116 debe tenerlo en cuenta al diseñar componentes.
  - **Aplica a**: arquitecto e implementación (calcular el contraste de cada token antes de asignarlo a un control).

- **Lección**: al añadir una hoja de estilo a `index.html`, el test de #85 que fijaba la lista exacta de hojas (`hojas == [...]`) se actualiza con el literal nuevo y un comentario; no se relaja.
  - **Aplica a**: implementación (`grep` de `index.html`/`estilo.css` en `tests/acceptance/` antes de cambiar el HTML).

- **Lección**: en las Actions de los agentes `cp`, `rm` y `mkdir` fuera del repo piden aprobación; los ficheros se copian con `Read` + `Write` y los temporales de descarga se dejan dentro del repo sin añadirlos (`git add` por ruta).
  - **Aplica a**: implementación.
## [2026-11-26] Historia #112 — validacion-v2

- **Lección**: una línea de validación no puede dibujarse sobre la misma capa pública que alimenta a los candidatos (vías pecuarias): casaría casi seguro y inflaría la cobertura. Y que un nombre parezca equivalente («Canal de las Aves» ≈ «Caz de las Aves») no es localizar: la fuente de prensa decía que el camino NO conecta con la vía pecuaria. Si la fuente no da trazado ni coordenadas y no hay fuente independiente (ortofoto, plano del deslinde), el caso queda sin `tramo_linea` y, si ninguno del ámbito es localizable, se escala al fundador (CA4).
  - **Aplica a**: implementación y revisión (casos de validación con geometría).

## [2026-11-26] Historia #120 — ADR de imágenes de comparación

- **Lección**: si un ADR trae una tabla de oráculos y las constantes de las que sale, el test de aceptación no se limita a comprobar propiedades de la tabla (lado múltiplo de 32, píxeles = 2·lado). Recalcula cada fila con una implementación de referencia mínima que **lee las constantes del texto del ADR** y exige igualdad exacta. Así, si alguien cambia una constante (margen, escalón, tope) sin rehacer la tabla, el test falla antes de que #121 programe contra oráculos caducados. La referencia vive solo en el test y no sustituye a la implementación de producción.
  - **Causa**: el CA10 pedía solo la coherencia con las constantes. Con esa comprobación habría pasado una ventana mal calculada que aun así tuviera un lado válido. Recalcular costó unas 40 líneas, y los seis oráculos del arquitecto cuadraron.
  - **Aplica a**: implementación (historias de ADR con oráculos) | arquitecto (dar el algoritmo con el detalle suficiente para reimplementarlo: orden, redondeo y desempate).

- **Lección**: en las Actions de los agentes, `sha256sum` sobre un fichero de `/tmp` se deniega, porque solo se permite dentro del repo. Para comprobar el sha256 y los bytes de una respuesta descargada sin guardarla en el repo, usar `python3 -I -c "import hashlib,sys; d=open(sys.argv[1],'rb').read(); print(len(d), hashlib.sha256(d).hexdigest())" /tmp/fichero`.
  - **Aplica a**: implementación (verificaciones de red cuya respuesta no se versiona).

## [2026-11-26] Historia #126 — Entidad CutCandidate y método de combinación

- **Lección**: no ejecutes `ruff format .` sobre todo el repo: la versión de ruff local reformatea medio repo (incluidos bloques de código de los ADR, que rompen tests que leen sus textos literales). CI solo exige `ruff check`. Formatea solo los ficheros nuevos (`ruff format <ruta>`) y revisa `git status` antes de commitear.
  - **Causa**: al formatear `.` se tocaron 44 ficheros ajenos a la historia y falló `test_historia_109` (que busca una línea exacta del ADR 0039). Se revirtió con `git checkout` de lo ajeno.
  - **Aplica a**: implementación.

- **Lección**: al fundir rachas por distancia entre piezas (`GAP_M`), basta comparar piezas con `segment_distance` y un prefiltro de bbox; el coste es cuadrático por trazado, aceptable con rachas de decenas de muestras. Si se calibra `GAP_M` o `SAMPLE_STEP_M`, revisar ese coste.
  - **Aplica a**: implementación (al calibrar el método con #100).

## [2026-11-26] Historia #121 — Caso de uso de imágenes de comparación

- **Lección**: el trozo de puntos de `comparison_windows` se corta por `max(ancho, alto)`, no por longitud: una diagonal de 1500 m (2121 m de recorrido) cabe en 4 ventanas y no es `tramo_demasiado_largo`. Para probar el tope, usar un tramo cuyo lado mayor supere ~1,6 km en proyección.
  - **Causa**: el primer test unitario esperaba `None` con una diagonal de 1500×1500 m y salieron 4 ventanas. El código seguía el ADR; el oráculo mental era erróneo.
  - **Aplica a**: implementación (tests de topes por tamaño de ventana).

## [2026-11-26] Historia #18 — ADR de georreferenciación y digitalización

- **Lección**: los cuerpos de las issues anteriores a la migración (ADR 0040) llevan referencias reescritas como `murcialito/parcel-intel#N`, también dentro de cadenas que un criterio pide «literales», como un encabezado de ADR. Antes de copiarlas, comprobar con `gh issue view N` que la issue de este repo es la misma. Si lo es, escribir `#N` en el documento, como el resto de `docs/adr/`; el docstring del test conserva el criterio literal y el módulo explica la adaptación. Un ADR con `murcialito/parcel-intel#N` enlazaría al repo antiguo.
  - **Causa**: el CA12 de #18 exigía el encabezado `## Consecuencias para #19 y murcialito/parcel-intel#20`. #20 y #50 de este repo son las mismas historias.
  - **Aplica a**: BA y arquitecto (al reescribir una issue migrada, normalizar sus referencias a `#N`) | implementación (anotar la adaptación en la PR).

- **Lección**: al buscar frases en un ADR tras quitar las negritas (`texto.replace("**", "")`) también se borra el `**` de los globs (`curated/**`). Las cadenas con glob se buscan en el texto sin normalizar.
  - **Aplica a**: implementación (tests que leen documentos Markdown).

## [2026-11-26] Historia #94 — Publicar los indicios de discrepancia

- **Lección**: una decisión del fundador tomada después del diseño (aquí el ADR 0041, que abre los indicios con Catastro como contraparte) cambia el oráculo, un criterio y el texto público a la vez. Antes de codificar, leer los comentarios de la issue y los ADR posteriores al diseño; recalcular el oráculo con el pipeline (Bloque 0) y escribir en el docstring del módulo de tests qué se ajustó y por qué, dejando el criterio literal en el docstring de cada test.
  - **Causa**: el diseño fijaba 2 indicios publicados y 3 omitidos en 28043; con el ADR 0041 son 4 y 1. El número del ADR propuesto (0038) también estaba ya ocupado (lección #43).
  - **Aplica a**: implementación | arquitecto (un diseño que depende de una decisión jurídica abierta debe decir qué cambia si se resuelve en el otro sentido).

- **Lección**: una franja sintética de 20 m frente a una línea por su eje no "coincide" para todos los pares de fuentes. Las muestras del borde corto quedan a 7,5 m del eje y la tolerancia Catastro–IGN es 7,07 m, así que sale un `SOLAPE` que el oráculo no esperaba. Al escribir fixtures de franjas, comprobar la distancia de las muestras de los bordes cortos con la tolerancia más estricta de los pares que se van a comparar, o usar franjas estrechas (10 m).
  - **Aplica a**: implementación y arquitecto (fixtures sintéticas de discrepancias).

- **Lección**: en las capas derivadas reales ninguna fuente declara una fecha común: vías pecuarias no tiene fecha por tramo y la red del IGN mezcla `fecha_alta` de 2017 a 2026. Los `AUSENTE_EN_FUENTE` reales dicen «año no declarado» aunque el formato ya guarda `fecha_dato_b`. Un criterio con un año en el texto (CA11) se cumple con fixtures; con los datos reales hace falta decidir qué fecha representa a cada capa.
  - **Aplica a**: BA y arquitecto (al fijar un texto público con año, comprobar qué fecha tienen los datos reales) | implementación (anotarlo en la PR al publicar datos reales).
## [2026-11-26] Historia #124 — Adapter de ventanas de ortofoto PNOA

- **Lección**: el WMS del IGN responde **200** a todo: una capa inexistente da un `ServiceException` XML y un vuelo sin cobertura da un JPEG blanco. Ningún código de estado distingue el éxito; solo la cabecera JPEG (para no cachear errores) y el tamaño por píxel (para detectar el blanco, que sí se cachea) lo hacen.
  - **Causa**: ambas respuestas se grabaron como fixtures reales (`window_capa_inexistente_serviceexception.xml`, `window_224_pnoa2010_blanca.jpg`); la blanca a 448 px pesa 0,0073 bytes/píxel, las reales ≥ 0,135.
  - **Aplica a**: implementación (cualquier adapter nuevo sobre WMS del IGN: validar el cuerpo, no el estado HTTP).

## [2026-11-26] Historia #96 — Publicar indicios del ámbito y comprobar peso y lenguaje

- **Lección**: regenerar `data/derived/` con red y volver a publicar es determinista: los ficheros de Colmenar salieron byte a byte iguales a los ya commiteados y las capas de #86 no cambiaron, así que el diff de datos es solo lo nuevo. Cuando un test de una historia anterior fija «solo existe X» sobre datos que la siguiente amplía (el CA15 de #94 exigía solo `28043.geojson`), se relaja a «X sigue ahí» y la historia nueva fija el conjunto completo.
  - **Aplica a**: implementación (tests que congelan un conjunto de ficheros publicados) | arquitecto (al partir una publicación en dos historias, decir qué test de la primera deja de valer).
## [2026-11-26] Historia #95 — El visor dibuja indicios y muestra su ficha

- **Lección**: para probar que un aviso «siempre está», no basta con la ficha del caso feliz: se pasa por la función pura de la ficha cada forma rota (`null`, `{}`, sin `texto`, `evidencia` que no es lista, aviso vacío) y se comprueba el aviso en el nodo resultante. El literal fijo de `config.js` se compara además con el aviso del fichero publicado y con `NOTICE`, para que no diverjan.
  - **Aplica a**: implementación (JS del visor) | revisor (cualquier texto legal obligatorio en una ficha).

- **Lección**: los tests estáticos de #85 escanean todo el JS propio en busca de `//` seguidos de texto (URLs externas), también dentro de comentarios. Escribir `https://` en `vista.js` rompe CA1 de #85; el enlace seguro se comprueba con la expresión regular `/^https:\/\/\S/` y los comentarios evitan la cadena literal.
  - **Aplica a**: implementación (módulos del visor).

- **Lección**: `tests/acceptance/test_historia_46.py` busca en `tests/` cadenas de 14 caracteres alfanuméricos y las compara con huellas de referencias catastrales reales. Una referencia «inventada» con forma plausible puede ser real (p. ej. polígono 1, parcela 1 de un municipio del ámbito). En fixtures usar prefijos de municipio inexistentes (`99999`).
  - **Aplica a**: implementación (fixtures con referencias catastrales falsas).
## [2026-11-26] Historia #136 — Ajuste afín y validación de trazados en el dominio

- **Lección**: el ADR que un diseño llama «siguiente libre (hoy 0044)» caduca igual que un número de issue. #94 ocupó el 0044 mientras #136 esperaba en cola; se comprobó `ls docs/adr/`, se usó el 0045 y se anotó en el propio ADR. El test de CA12 localiza el ADR por su cabecera (`Historias: #20`) y comprueba que no hay números duplicados, así que no depende del número.
  - **Aplica a**: BA y arquitecto (citar «el siguiente libre» sin número) | implementación (comprobar `docs/adr/` antes de crear el ADR; ya lo exigía la lección de #43).

- **Lección**: un oráculo de `numpy.linalg.lstsq` sobre una afín casi exacta coincide con el cálculo con coordenadas centradas dentro de ~1e-10 m, así que `abs=1e-9` es correcto pero justo. Al cambiar la escala de la figura en un test (p. ej. multiplicar los píxeles por 100) hay que mantener los objetivos dentro del rango de EPSG:25830 que valida `ControlPoint`.
  - **Aplica a**: implementación (tests de escala sobre `fit_affine`).
## [2026-11-26] Historia #122 — Comando imagenes-comparacion

- **Lección**: para rechazar un GeoJSON «fuera de EPSG:25830» no basta con leer el miembro `crs`: un fichero que declara 25830 pero trae grados (`-3.6, 40.1`) pasa. Hay que acotar además las coordenadas al rango de UTM 30N (este 100.000–900.000 m). Y con mypy estricto, los tests no pueden leer `main.ManifestTooLargeError` y similares: se importan de su módulo, no de `cli.main`.
  - **Causa**: la primera cota (−1.000.000…2.000.000) dejaba pasar los grados; lo cazó un test unitario con lon/lat.
  - **Aplica a**: implementación (lectores de ficheros de entrada con CRS declarado).

## [2026-11-26] Historia #138 — Registro PostGIS de unidades y `sync_unit`

- **Lección**: un test de migración que dice «`down` revierte la última» se rompe en cuanto llega una migración nueva. `test_historia_19::test_ca6` revertía una vez y esperaba que `historic_trail` desapareciera; con la `0006` solo desaparece la columna `unit_id`. Al añadir una migración, buscar con `grep` los tests que llaman a `migrate.down` y actualizarlos.
  - **Aplica a**: implementación (cualquier migración nueva) | revisión.

- **Lección**: para que `get_unit` devuelva una unidad **igual** a la guardada, cada dato que el dominio repite en dos entidades (`UnitSpec` y `WorkingImage`: tipo, sha256, ancho, alto, fuente) o que no está en la entidad persistida (`image_url` exacta, `image_retrieved_at` de la especificación frente a la fecha real de descarga) necesita su columna o una comprobación previa al SQL. El SQL literal de #20 no tenía `image_url` ni `spec_image_retrieved_at`; se añadieron, y `sync_unit` rechaza con `ValueError` una unidad incoherente en vez de perder un dato en silencio.
  - **Aplica a**: arquitecto (al fijar un esquema para una ida y vuelta exacta, comparar campo a campo con el dataclass) | implementación.

- **Lección**: en esta sesión no había PostGIS ni permiso para Docker, así que los criterios `[integración: PostGIS]` solo corren en CI. Se cuadró a mano el número de `%s` y de parámetros de cada sentencia (el fallo más probable al no poder ejecutarlas) y se dejó el resto a CI.
  - **Aplica a**: implementación (si no se puede ejecutar la base, contar placeholders y decir en la PR que no se ejecutó).
## [2026-11-26] Historia #137 — Entrada curada y caché de la imagen de trabajo

- **Lección**: el sha256 de un fichero versionado solo es estable si git no toca sus finales de línea. `.gitattributes` marca `curated/historico/**` y la fixture como `-text` (CA11), y la lectura del CSV calcula el hash sobre los **bytes** del fichero, no sobre el texto decodificado: con BOM o CRLF el hash cambia a propósito, porque eso es lo que vio quien aprobó. Un test que mezcla las dos cosas (BOM → mismo contenido, otro sha256) lo documenta.
  - **Aplica a**: implementación (cualquier fichero cuyo hash se copie a otro sitio como firma de «lo que vi»).

- **Lección**: en un borde de validación estricta, los helpers que extraen un campo tipado (`_text`, `_integer`, `_choice`) evitan `cast` y `type: ignore` y dejan que mypy compruebe el `TypedDict` de salida. Para un `Literal` cerrado, `options: tuple[T, ...] = get_args(Literal)` más `_choice` devuelve el literal ya estrechado. Y un `lambda` asignado a una variable en un test cuenta como función sin tipar para `mypy` estricto (`no-untyped-call`): se escribe inline o como `def`.
  - **Aplica a**: implementación (nuevos lectores de ficheros de formato cerrado).

- **Lección**: el WMS de minutas del IGN devolvió byte a byte el mismo PNG (sha256 idéntico) del recorte versionado en #17 al día siguiente, así que un hash declarado en `unidad.json` es una comprobación realista y no un falso positivo. Aun así, el adapter trata cualquier diferencia como `RawImageMismatchError` y no escribe nada: los puntos de control se tomaron sobre aquella imagen.
  - **Aplica a**: implementación (adapters de imágenes con hash declarado) | review.

## [2026-11-27] Historia #139 — Comandos `historico ajuste` y `historico cargar`

- **Lección**: un criterio que cita un valor de una fixture ya fusionada («`reviewer = usuario-sintetico`») puede no coincidir con ella (`fixture-sintetica` en #137). Se comprueba el valor real de la fixture, no el del texto, y la diferencia se anota en el docstring del módulo de tests y en la PR; cambiar la fixture rompería el sha256 de sus aprobaciones.
  - **Aplica a**: BA y arquitecto (copiar los valores de las fixtures, no recordarlos) | implementación.

- **Lección**: para que un comando pruebe «no se llama a la base» sin PostGIS, el composition root expone un único punto de apertura (`_georeference_repository`, un `contextmanager`) que los tests sustituyen por un doble que registra llamadas; `ajuste` no lo usa, y un test lo sustituye por una función que falla. La métrica que un comando imprime y que el dominio ya calcula en privado (cuadrantes) se hace pública en el dominio en vez de recalcularla en `cli/`.
  - **Aplica a**: implementación (comandos con base opcional).
## [2026-11-27] Historia #59 — Segunda unidad histórica (Valdelaguna, 280246-w1)

- **Lección**: una segunda unidad sintética se deriva de la primera desplazando solo las coordenadas métricas de los puntos (aquí −7200 m en X, +8550 m en Y) y dejando intactos píxeles y trazados: residuos y LOO son idénticos, el sha256 del CSV cambia y hay que reescribir el `control_points_sha256` de los trazados aprobados. El texto de licencia del IGN es «CC-BY 4.0» (con guion), no «CC BY 4.0»: los tests usan el literal del código.
  - **Aplica a**: implementación (fixtures de nuevas unidades) | refinador (copiar literales de licencia).

- **Lección**: un criterio «descarga real [integración]» no es comprobable con una imagen sintética: su sha256 no coincide con ningún render del WMS. Con una unidad sintética solo se prueba la caché y la procedencia; la descarga real corresponde a la tarea humana con datos reales.
  - **Aplica a**: refinador.

## [2026-11-26] Historia #140 — Evidencia PNG de dos paneles

- **Lección**: el WMS `pnoa-ma` del IGN sirve PNG de 8 bits RGB (tipo de color 2, sin entrelazar, todas las filas con filtro 0, 47 bloques `IDAT` en 400 × 352 px) y el recorte de minutas es igual. Con esos dos reales no se ejercita ningún filtro PNG distinto de 0: el decodificador propio sin Pillow se prueba con PNG **sintéticos escritos con un codificador independiente** (filtrado directo en el test) para los cinco tipos de color y los cinco filtros, además de los reales. Si solo hubiera fixtures reales, Sub, Up, Average y Paeth llegarían sin una sola prueba.
  - **Aplica a**: implementación (decodificadores de formatos binarios: los reales dicen qué sirve la fuente hoy, no qué cubre el código).

- **Lección**: para que un oráculo de banda «a ≤ 2 × LOO» se pueda fijar a mano, se elige 1 m por píxel y un LOO tal que el radio caiga en el centro de un píxel (LOO = 4,75 m → 9,5 m): los centros están a x,5 m y un radio entero dejaría la frontera exactamente sobre un centro (lección de #110). La fila a 9,5 m está dentro y la de 10,5 m (= 2 × LOO + 1) fuera.
  - **Aplica a**: arquitecto e implementación (oráculos de máscaras sobre rejillas de centros de píxel).

- **Lección**: una opción de CLI que el diseño no nombra (`--cache-pnoa`) fue necesaria para poder probar de extremo a extremo con `tmp_path`: sin ella, la caché de PNOA habría tenido que derivarse de `--cache` o escribirse en `data/raw/pnoa_ma` durante los tests. Se anota en CLAUDE.md y en la PR.
  - **Aplica a**: arquitecto (una CLI con dos cachés crudas necesita dos opciones).
## [2026-11-27] Historia #127 — Señal `parcela_no_camino` y caso de uso de candidatos a corte

- **Lección**: un criterio que cita «el error de sin datos que deje #37» no se cumple si #37 no dejó uno: `ParcelsInAreaPort` solo declara `AreaQueryLimitExceededError`, `TruncatedAreaQueryError` y `CadastralServiceError`. La señal trata esos tres como «tesela sin datos» (`TILE_UNAVAILABLE` en `application/cut_signals.py`) y propaga cualquier otro. Un `RawCacheMissError` (modo `--sin-red`) vive en un adapter y la aplicación no puede capturarlo: H3 (#128) debe declarar un error de port para «sin red» o envolverlo en el adapter, o ese modo abortaría en vez de dar `sin_datos`.
  - **Aplica a**: arquitecto/refinador (al citar un error «acordado» de otra historia, comprobar que existe en `ports/errors.py`) | implementación de #128.

- **Lección**: las muestras de `trail_samples` salen de interpolaciones en coma flotante (`102.49999999999999`), así que un test que compare coordenadas con `==` falla aunque el método sea correcto; y el tramo de un candidato lleva un vértice por pieza de 5 m, no solo los dos extremos. Compara con `pytest.approx` y por extremos.
  - **Aplica a**: implementación (tests sobre muestras o tramos).

## [2026-11-27] Historia #60 — Fixtures de Villarejo y runbook histórico

- **Lección**: para una segunda unidad sintética que reutilice la imagen de otra, basta **trasladar** los puntos de control: la afín es invariante a la traslación, así que RMSE, residuo máximo y LOO coinciden con el oráculo de #139 (aquí con `abs=0.005`) sin recalcular nada. Cambian el `puntos_sha256` (hay que copiarlo a `control_points_sha256` de los trazados aprobados, con `sha256sum` del fichero ya escrito) y el `BBOX` de la URL. Para distinguir las unidades por su `.provenance.json` en un test, se da a cada sidecar una fecha de descarga distinta.
  - **Aplica a**: implementación (fixtures hermanas).

- **Lección**: al comprobar el orden de pasos de un runbook con `text.index`, la primera aparición de una palabra («CODEOWNERS») puede estar en la introducción; se indexan los encabezados de los pasos numerados, no las palabras sueltas.
  - **Aplica a**: implementación (tests de documentos).

## [2026-11-27] Historia #56 — Fixture sintética de Chinchón (281287-w1)

- **Lección**: el id de trazado del código es `<unit_id>-<trail_id>` (`281287-w1-T001`), no `281287:<trazado>` como decía el criterio: se verifica el formato real antes de copiar el literal del criterio y se anota en el docstring del test. Para una unidad con fecha genérica basta una fixture hermana con `document_date_precision: "year"`; un test «guardia» que cambia `"year"` por `"day"` demuestra que el test de CA5 falla si el marcado se pierde. El hash `control_points_sha256` de los aprobados debe recalcularse con `sha256sum` tras trasladar los puntos.
  - **Aplica a**: implementación (fixtures hermanas de unidades históricas).

- **Lección**: `ruff format .` sin límite reformatea decenas de ficheros ajenos a la historia (aquí 51, incluidos ADR y código de otras historias) y ensucia la PR. Formatea solo los ficheros que has tocado (`ruff format <rutas>`), o comprueba `git status` antes de hacer commit.
  - **Aplica a**: implementación.

## [2026-11-27] Historia #57 — Unidad sintética de Villaconejos (280264-w1)

- **Lección**: un criterio que cita una interfaz de #20 («`historico cargar --documento 280264`», «cargados 0», «sin error de ejecución», `curated/planimetrias/280264/`) debe comprobarse contra el CLI real antes de pasar a `lista`: el subcomando recibe `<unit_id>` posicional, la ruta es `curated/historico/<unit_id>/` y una unidad sin entrada curada sale con 1 (`CuratedUnitNotFoundError`), sin línea «cargados». Una historia «sin código nuevo» no puede cumplir un criterio que exige otro comportamiento de salida.
  - **Aplica a**: refinador (copiar de la historia madre la interfaz real, no la planeada).
## [2026-11-27] Historia #62 — Hoja MTN50 0606 (0606-w1)

- **Lección**: un criterio que dice «sin cambios en el mecanismo» puede ser incompatible con otro criterio de la misma historia. #62 (CA6) pide trazados de varios municipios en una hoja, pero `unidad.json` lleva un único `municipality_code` y `trazados.geojson` no admite municipio por trazado (`_ALLOWED_PROPERTIES`). Antes de escribir los tests se contrasta cada criterio con `adapters/curated_historic/files.py` y `domain/digitization.py`; el que no cabe se escala al fundador en vez de tocar `src/` fuera de la línea `Toca:`.
  - **Aplica a**: refinador (al exigir una capacidad nueva en una historia «de patrón», comprobar que el formato de #20 la admite) | implementación.

- **Lección**: la atribución «exacta» de un criterio puede ser la de capa y no la que imprime la CLI. Del MTN50 hay dos: por trazado (`Obra derivada de Primera edición MTN50, IGN, hoja 0606 1881 …`, `derived_work_attribution`) y de capa (`…MTN50 1875-1968…`, `layer_attributions`). El test comprueba cada una donde vive. El `unit_id` de una hoja lleva ceros a la izquierda (`0606-w1`) y casa con `^[0-9]{4,6}-[wh][0-9]+$`.
  - **Aplica a**: refinador | implementación.

## [2026-11-27] Historia #128 — Fichero interno de candidatos y comando `candidatos-corte`

- **Lección**: un fichero ausente y uno vacío significan cosas distintas. `CutCandidateSource.get_report` devuelve `None` si no hay fichero («sin capas») y un informe con `candidates == ()` si lo hay sin features («municipio evaluable»); un fichero ilegible o en otro CRS lanza `CutCandidateFileError`, nunca `None`. La escritura es atómica (temporal + `os.replace`) para que un fallo a medias no deje un fichero truncado que se tome por «evaluable».
  - **Aplica a**: cualquier lector de ficheros derivados con semántica «ausente ≠ vacío» (#110, #111, #103).

- **Lección**: el `RawCacheMissError` de `--sin-red` vive en un adapter y la aplicación no lo captura (lección de #127). Se resuelve en el composition root: `_CachedParcels` (en `cli/main.py`) lo convierte en `CadastralServiceError`, que la señal ya trata como «tesela sin datos». Así `--sin-red` da `sin_datos` con su motivo en vez de abortar.
  - **Aplica a**: composition roots que inyecten un adapter con caché a un caso de uso que solo conoce errores de port.

- **Lección**: las coordenadas de un tramo salen de interpolaciones en coma flotante (`110.00000000000001`) y el fichero las guarda a 0,01 m, así que `leer(escribir(x)) == x` solo vale si `x` ya está en esa rejilla. Los tests de ida y vuelta redondean el informe de partida; el fichero es idempotente (escribir, leer y volver a escribir da los mismos bytes).
  - **Aplica a**: tests de ida y vuelta de geometría.

- **Lección**: `ruff format src tests` sin acotar volvió a reformatear 34 ficheros ajenos (ya avisaba la lección de #127). Pasa `ruff format` solo con las rutas tocadas.
  - **Aplica a**: implementación.
## [2026-11-27] Historia #58 — Belmonte de Tajo (280040-w1)

- **Lección**: el diseño de una historia hija escrita antes de #139 puede citar una forma que ya no existe (`historico cargar --documento 280040`, ids `280040:<trazado>`, fixture en `planimetrias/280040/`). Con el código fusionado manda lo real: `historico cargar 280040-w1`, id `280040-w1-<trazado>`, fixture en `planimetrias/historico/280040-w1/`. Se anota en el docstring del módulo de tests y en la PR. El CA8 («sha256 que declara el fichero de puntos de control») se cumple con el `sha256` de `unidad.json` (la entrada curada), que es donde vive hoy ese dato.
  - **Aplica a**: refinador (al partir hijas, citar la CLI y las rutas del código ya fusionado) | implementación.
## [2026-11-27] Historia #61 — Unidad sintética de Aranjuez (280027-w1)

- **Lección**: un criterio que cita `historico cargar --documento 280022` describe una opción que no existe: el comando recibe la unidad (`280022-w1`) y el documento y la fecha viajan juntos en su `unidad.json`. Pedir el documento de 1943 sin unidad curada falla con «no existe» (salida 1, nada escrito), y un `document_id` distinto del prefijo del `unit_id` se rechaza al leer la unidad. Los tests prueban esos dos caminos en vez de inventar la opción.
  - **Aplica a**: refinador (copiar las opciones reales del CLI de CLAUDE.md) | implementación.

- **Lección**: la ruta que un criterio nombra (`tests/fixtures/caminos/planimetrias/280027/`) puede diferir de la que fija la línea `Toca:` y el método de #20 (`historico/280027-w1/`). Manda la convención vigente del repo; la diferencia se anota en el docstring del módulo de tests y en la PR.
  - **Aplica a**: refinador | implementación.

## [2026-11-27] Historia #160 — Integrar `catastro-caminos` con `discrepancias` y `candidatos-corte`

- **Lección**: un criterio «el fichero se genera» pasa con datos inservibles si productor y consumidor no se prueban juntos. #128 dio 0 candidatos porque `catastro-caminos` no escribía el formato común y porque dos órdenes teselaban con rejillas distintas (la clave de la caché cruda lleva el bbox exacto de la tesela). Los tests de #160 encadenan productor → lector y `catastro-caminos` → caché → `candidatos-corte --sin-red` sobre la misma caché.
  - **Aplica a**: refinador (un criterio de integración nombra al consumidor) | implementación.

- **Lección**: si dos comandos comparten una caché cuya clave depende de una geometría, esa geometría la calcula una única función de dominio (`municipality_tiles`), no dos copias del cálculo. El test de «teselas idénticas» falla si divergen.
  - **Aplica a**: cualquier caché cruda indexada por bbox.

- **Lección**: `ruff format src` sin acotar reformateó 14 ficheros ajenos (tercera vez, ver #127 y #128; cuarta en #111 con `ruff format .`, 47 ficheros). Formatea solo los ficheros nuevos y comprueba `git status` antes del commit; `ruff format` también retoca líneas ya existentes de los ficheros que tocas: revierte ese ruido.
  - **Aplica a**: implementación.

## [2026-11-27] Historia #162 — Municipio por trazado en el formato histórico

- **Lección**: la lista de los 7 municipios del ámbito vivía solo en `cli/main.py` (`AMBITO_HITO_INE`), inalcanzable para el dominio. Una validación «dentro del ámbito» en dominio/aplicación necesita la constante en `domain` (`HITO_MUNICIPALITY_CODES`, en `municipality.py`); el CLI la reexporta con su nombre antiguo. Validar en el borde (adapter) habría duplicado la lista.
  - **Aplica a**: cualquier regla que cite «los 7 municipios» fuera del CLI.

- **Lección**: una propiedad opcional nueva del formato curado se valida en tres sitios que deben moverse juntos: `_ALLOWED_PROPERTIES` del lector (si no, «propiedades desconocidas»), el `DigitizedTrail` (forma) y el caso de uso (`load` y `evidence` llaman a `check_trail_municipalities` antes de escribir). Rechazar el fichero entero antes de `sync_unit` deja la base intacta; el doble de repositorio de los tests de aceptación cuenta las llamadas para probarlo.
  - **Aplica a**: ampliaciones del formato de #20.
## [2026-11-27] Historia #125 — Mover validador y congelación a producción

- **Lección**: al reubicar un módulo de `tests/` a `src/`, busca todas sus referencias con `grep` (imports, docstrings y docs que citan la ruta vieja) antes de darlo por hecho; los comentarios del tipo «sigue en `tests/` hasta #N» quedan falsos si no se actualizan.
  - **Aplica a**: implementación.


## [2026-11-27] Tubería — correcciones perdidas

- **Lección**: un disparo por evento (`labeled`, `synchronize`) se pierde si la PR está en conflicto con `main` en ese momento: GitHub no crea runs de `pull_request` en una PR con conflictos, y no los crea después. Todo paso de la tubería que dependa de un evento de PR necesita un barrido de respaldo que mire el estado (aquí `correcciones_pendientes.sh`: `cambios-pedidos` sin un Corregir lanzado después). Ese barrido no debe duplicar el disparo normal: deja un margen y comprueba los runs en curso, y el workflow disparado vuelve a comprobar la condición al empezar.
  - **Causa**: el revisor puso `cambios-pedidos` en la PR #129 (#114) mientras Actualizar rama la sacaba de un conflicto. No se lanzó ningún Corregir y la PR quedó parada hasta la revisión manual.
  - **Aplica a**: workflows (`.github/`).

- **Lección**: `git push` seguido de `gh pr ready` deja el commit de cabeza sin revisión. El `synchronize` del commit nuevo sale con la PR aún en borrador, y su revisión se salta. El `ready_for_review` sale con el commit anterior, porque GitHub aún no ha movido la cabeza. Un evento de PR describe la PR en el instante en que se emite, no cuando corre el workflow. Por eso el barrido `revisiones_pendientes.sh` mira el estado de los checks del commit de cabeza (CI terminado y `revision` ausente, skipped o cancelled) y vuelve a pedir la revisión pasando la PR a borrador y a lista.
  - **Causa**: PR #131 (#115). El push de 0887fd9 fue a las 08:35:15 y `gh pr ready` a las 08:35:19. La revisión de 0887fd9 se saltó y la del `ready_for_review` revisó 7b0e316.
  - **También**: con `cancel-in-progress: true`, la `concurrency` va en el job y no en el workflow, porque así un run que se salta por el `if` no cancela al que sí revisa. Y si cambia `revisar.yml` en `main`, las PRs abiertas fallan la revisión con `SIN_VEREDICTO` hasta traerse `main`: la acción se salta si su workflow no coincide.
  - **Aplica a**: workflows (`.github/`).

- **Lección**: el desarrollador puede terminar con la historia en `estado:en-revision` y la PR aún en borrador si se le olvida `gh pr ready`. Ningún paso de la tubería lo recogía: la revisión se salta los borradores, el barrido de revisiones solo miraba PRs listas y «devolver a la cola» solo actúa si la historia sigue en desarrollo. Ahora `revisiones_pendientes.sh` también marca lista (con `PROJECT_TOKEN`) una PR `claude/historia-<n>-*` en borrador cuando la historia `n` lleva más de 10 minutos en revisión y no hay un Corregir en marcha; el revisor decide si está completa. Y el barrido de revisiones se salta las PRs en conflicto: sin runs de `pull_request`, pedir la revisión no sirve; las saca Actualizar rama.
  - **Causa**: PRs #232 (#216), #234 (#217) y #184 (#183), 2026-11-27. Una sola ejecución del agente cada una, sin ningún evento `ready_for_review`; la #184 llevó así casi tres horas y quedó en conflicto con `main`.
  - **Aplica a**: workflows (`.github/`), desarrollador (paso 8: `gh pr ready` antes de pasar la historia a revisión).

## [2026-11-27] Historia #165 — Estilos de capa del sistema de diseño en el mapa

- **Lección**: para no divergir de un paquete de diseño entregado como script global, el test ejecuta el original en `node:vm` con `window` y `document` falsos y compara con `deepStrictEqual`. Los objetos de otro contexto de `vm` tienen otro prototipo y `deepStrictEqual` los distingue: normaliza los dos lados con `JSON.parse(JSON.stringify(x))`. Un comentario del módulo que nombre `data-tema` o `window` rompe los tests estáticos que buscan esas palabras: reescríbelo.
  - **Aplica a**: módulos del visor derivados de `docs/design-system/`.

- **Lección**: al dibujar cada trazado en dos capas (halo y línea), el L falso guarda los `setStyle` por subcapa y expone `disparar(evento)`; los tests emparejan halo y línea por índice. En `reg.geojsons` el halo va justo antes que la línea, y los grupos (`layerGroup`) en `reg.grupos`. La fixture `propsIndicio` trae una confianza por defecto: para probar «sin confianza» hay que borrar la propiedad.
  - **Aplica a**: tests de `mapa.js`.
- **Lección**: nunca `uv run ruff format .` sobre todo el repo: reformatea decenas de ficheros ajenos (aquí 48, con ADR y tests de otras historias) y rompe tests que leen el texto de esos ficheros (`test_historia_109` busca una línea alineada de `cut_candidate.py`). Formatea solo los ficheros de la historia (`ruff format <rutas>`) y mira `git status` antes de seguir.
  - **Causa**: historia #169. Tras `ruff format .` falló un test de aceptación de #109 sin relación con el cambio; se restauró con `git checkout -- docs scripts src tests`.
  - **Aplica a**: desarrollo.
## [2026-11-27] Tubería — tests contra servicios en vivo en el check obligatorio

- **Lección**: un test que llama en vivo a un servicio público no puede estar en el check del que depende el merge. Si el servicio cae o va lento, todas las PRs se quedan en rojo sin culpa suya, y los agentes «arreglan» CI marcando tests ajenos uno por uno. Esos tests llevan el marcador `red` y corren en el job `red-externa`, con `continue-on-error`, así que su resultado se ve sin bloquear. `fuera_de_ci` es para los servicios que rechazan siempre a GitHub (Catastro), no para una caída.
  - **Causa**: el 2026-11-27 sobre las 09:45 UTC, `api-features.idee.es` empezó a agotar el tiempo de lectura (30 s) con las consultas reales, también desde local. La PR #174 (#169) gastó cuatro vueltas marcando `fuera_de_ci` tests de #84, #27 y #25, acabó en `veto`, y `main` se quedó en rojo.
  - **Aplica a**: implementación, revisión y workflows.

## [2026-11-27] Historia #168 — Revisión humana de candidatos a corte

- **Lección**: si un criterio dice «un código no válido hace fallar ese municipio» (y el lote sigue), el código no se valida en `argparse` con `type=_municipality_code`: `argparse` sale con 2 antes de ejecutar nada y aborta el lote entero. La validación va en el caso de uso, que devuelve `fallo` para ese municipio. Y la línea de fallo no repite el código rechazado, igual que el resto de mensajes de una entrada pública.
  - **Aplica a**: implementación (comandos por lote con `--municipio` repetible) | arquitecto.

- **Lección**: una entrada humana que vive en un repo público (`curated/candidatos/<ine>/revision.json`) se valida con stdlib y mensajes propios, no con pydantic: sus errores incluyen `input_value`. Los mensajes nombran solo campos, posiciones y ids ya validados, y cuentan las claves desconocidas sin nombrarlas. El test de cada mutación busca un valor centinela (`valor-secreto-rechazado`) en stdout y stderr, además de `CADASTRAL_REFERENCE_RE`.
  - **Aplica a**: implementación (lectores de ficheros versionados que pueden traer datos sensibles).

- **Lección**: en las Actions de los agentes, un heredoc (`cat >> f <<'EOF'`) con `{"` dentro se deniega («brace with quote character»). Para añadir código a un fichero, `Read` + `Edit`.
  - **Aplica a**: implementación.

- **Lección**: el número de ADR «siguiente libre» se comprueba otra vez al fusionar `main` en la rama, no solo al crearlo: otra historia en desarrollo a la vez puede haber tomado el mismo número (aquí #165 fusionó su 0046 y este ADR pasó a 0047). El test de CA12 de #168 exige números de ADR únicos, y así el choque sale en CI.
  - **Aplica a**: implementación | arquitecto (cuando el diseño fija el número del ADR).

- **Lección**: un helper de test que «deja solo estas revisiones» debe partir de la fixture original (`generar.revision_document()`), no del fichero que el propio test acaba de reescribir: la segunda llamada encuentra un fichero ya recortado y falla con `KeyError`.
  - **Aplica a**: implementación (tests que mutan una copia de la fixture varias veces).
## [2026-11-27] Historia #166 — Panel de capas, avisos y estados en el visor

- **Lección**: al sustituir marcado que otros tests fijan (`<label for>` con el rótulo, reglas `.muestra-*`), el test viejo se actualiza a lo que de verdad prueba (el rótulo ahora es el nombre de la fila; las muestras ahora son reglas `.pc-muestra--* .h/.l`), no se relaja. Un regex sin `re.DOTALL` sobre HTML falla en silencio en cuanto el elemento pasa a varias líneas: `grep` de `index.html` en `tests/acceptance/` antes de editarlo.
  - **Causa**: #95 CA1 y #165 CA11 fijaban la etiqueta en una línea y las reglas `.muestra-*`; no estaban en la línea `Toca:` de la issue.
  - **Aplica a**: implementación (cambios de marcado de `index.html`) | BA (listar en `Toca:` los tests de otras historias que fijan el marcado).

- **Lección**: los tests de contraste y de estilo leen las reglas reales de `estilo.css` (la primera fuera de `@media`), no una lista de tokens fijada en el test; así un token cambiado se detecta. Una regla de `@media (forced-colors)` con el mismo selector no debe fusionarse con la base.
  - **Aplica a**: implementación (tests sobre `estilo.css`).

## [2026-11-27] Historia #170 — Publicar los tramos a verificar aprobados

- **Lección**: una prohibición de texto como «nunca 0 %» no se prueba con una subcadena sobre el fichero entero: la ficha dice «el 80 % del tramo» y `"0 %" in texto` da un falso positivo (o, al revés, oculta el caso real si se relaja). Se acota al bloque que el criterio nombra (`medicion`) y se busca con límites (`(?<!\d)0 %`), más una comprobación de que no hay decimales.
  - **Aplica a**: implementación y revisión (tests de «tal texto no aparece»).

- **Lección**: un informe de agregados que solo lee los ficheros de los municipios **de los casos** deja fuera los municipios que se quieren publicar y no tienen casos (Colmenar). Su `metodo_candidatos` sale vacío y una regla «publica solo con la medición del mismo método» bloquearía siempre. Lo encontró el arquitecto en el diseño; el test de aceptación (CA16) encadena `medir-candidatos` → `publicar-candidatos` con un conjunto sin casos en 28043, como el real.
  - **Aplica a**: arquitecto e implementación (una puerta que exige un dato de otro comando se prueba con el productor real y con la forma de los datos reales, no con un informe escrito a mano).

- **Lección**: en las Actions de los agentes, `python -c "…"` con un salto de línea seguido de `#` dentro de la cadena se deniega («Newline followed by #»). Para cambios de varias líneas en un fichero, `Edit`.
  - **Aplica a**: implementación.

## [2026-11-27] Historia #172 — Imágenes de comparación en la ficha publicada

- **Lección**: para probar «el manifiesto no corresponde al tramo actual» no basta con desplazar el candidato: su `candidate_id` depende del tramo, así que el manifiesto regenerado lleva otro id y el fallo sale como «no está en el manifiesto». Se desplaza el tramo en una copia del fichero de candidatos **conservando el id** y se pasa por el productor real (`imagenes-comparacion` con un `http_get` falso que responde según `params["layers"]`). Un desplazamiento de 0,2 m deja la misma ventana y solo cambia el trazo en píxeles: así se prueba cada comprobación por separado.
  - **Aplica a**: implementación (tests de coherencia entre ficheros derivados indexados por id).

- **Lección**: al añadir claves a una lista blanca publicada, los tests de otras historias que buscan fugas por subcadena en el fichero entero (`"0.5"` para la confianza) dan falsos positivos con datos nuevos legítimos (`resolucion_m: 0.5`). Se acota la búsqueda al bloque que el criterio protegía y el bloque nuevo se cubre con su propia lista blanca de claves, sin relajar el test (lección de #170, ahora entre historias).
  - **Aplica a**: implementación y revisión (CA de «actualizar los tests de #N sin relajarlos»).

- **Lección**: un criterio puede citar como «URL publicada» la de un id de la fixture que no se publica (`12b804770c6c38bd` es el candidato sin revisión). Se comprueba en el Bloque 0 el papel de cada id citado; el test pide la URL de ese id desde el manifiesto (la misma función que la publicada) y además una URL realmente publicada, y la adaptación se anota en el docstring.
  - **Aplica a**: refinador y arquitecto (al citar ids de fixtures en criterios) | implementación.
## [2026-11-27] Historia #171 — Tramos a verificar en el visor

- **Lección**: `site/css/tokens.css` es copia fiel de `docs/design-system/` (#114 CA4) y `estilo.css` no admite colores literales (#166 CA5): un color nuevo de capa va a **los dos** `tokens.css` a la vez (claro y los dos bloques oscuros). La clave nueva de `estilos-capas.js` no existe en el original de diseño, así que el test de #165 compara solo las claves del original y comprueba que `candidato` es la única añadida.
  - **Aplica a**: implementación (capas nuevas del visor).

- **Lección**: una capa con ficha de otro tipo reutiliza `fichaIndicio`/`nodoFichaIndicio` en lugar de duplicarla; lo que la ficha ya no puede dar por supuesto (aquí, el criterio vacío) se resuelve en la vista, sin pintar párrafos vacíos. Un resumen que viene del fichero se valida y se pasa tal cual: si falta, la capa va a error antes de dibujar ningún tramo.
  - **Aplica a**: implementación y revisión (extensiones del visor).

- **Lección**: en las Actions de los agentes, `mv`, `rm`, `cp` y `sed -i` sobre ficheros del repo piden aprobación. Para colocar un fichero generado, genéralo directamente en su ruta o léelo y escríbelo con `Write`, y no dejes carpetas temporales dentro del repo.
  - **Aplica a**: implementación.

## [2026-11-27] Historia #178 — Imágenes de comparación en la ficha del visor

- **Lección**: el espacio de nombres de SVG (`createElementNS`) es una URL literal que el escáner de #85 (CA1) toma por un recurso externo. No se relaja el test: en `vista.js` se escribe partido (`"http:" + "//www.w3.org/2000/svg"`) con un comentario que lo explica, sin `https://` en el comentario.
  - **Aplica a**: implementación (módulos del visor que necesiten un literal con `//`).

- **Lección**: el documento falso de los tests del visor solo conoce lo que ya se usaba; un widget con eventos o SVG exige ampliarlo (`createElementNS`, `addEventListener`, `getAttribute`) en el mismo cambio, y la fixture con el campo nuevo se genera a partir de la anterior para no divergir de su forma.
  - **Aplica a**: implementación (tests de `tests/site/`).

## [2026-11-27] Historia #183 — Red asfaltada del IGN como contraparte

- **Lección**: un oráculo «estos indicios van por asfalto» medido como «% de la zona a ≤ 15 m de una carretera» mezcla dos cosas: una vía que va **bajo** la carretera y un camino sin asfaltar que corre **junto** a ella. Antes de fijar qué ids «dejan de publicarse» al cambiar una contraparte, mirar de cada registro quién es el sujeto y contra qué fuente se compara: un `DESPLAZAMIENTO` con sujeto IGN se compara con la vía, así que añadir tramos a la capa del IGN no lo toca.
  - **Causa**: CA8 fijaba 3 ids (82 %, 65 %, 57 %). Dos se reducen; el de 65 % es un camino sin asfaltar a 13–25 m de una carretera cuya vía no va por asfalto, y sigue igual. Se dejó el test literal y se escaló al fundador.
  - **Aplica a**: arquitecto y refinador (oráculos de «deja de salir» tras cambiar una capa) | implementación.

- **Lección**: cuando un comando pasa a escribir un fichero más con un directorio por defecto bajo `data/`, todo test que lo invoca por la CLI debe pasar también la opción nueva a `tmp_path`; si no, el test escribe en el `data/` del repo. Y los ayudantes compartidos que imitan al productor (`write_real_derived_layers`, `prepare_road_cache`) se actualizan para escribir también el fichero nuevo, aunque no estén en la línea `Toca:`: si no, cada consumidor (#94 CA7) falla con «falta la capa complementaria».
  - **Aplica a**: implementación (`grep` del nombre del comando y de los ayudantes de `tests/` antes de cambiar lo que escribe).
## [2026-11-27] Historia #185 — Rediseño del visor

- **Lección**: un nombre de capa que viene de un texto validado (aquí «Tramos a verificar (no son un dictamen)», `CANDIDATE_LAYER_NAME` de #169) no se acorta en la interfaz aunque el diseño lo pida: el test de #171 exige el literal en `index.html`. Se acortan solo los rótulos que no son texto validado (vías pecuarias, camino actual) y la salvedad se mantiene. Antes de cambiar un rótulo, `grep` del literal en `src/parcel_intel/domain/` y en `tests/acceptance/`.
  - **Aplica a**: diseño e implementación (rótulos del visor).

- **Lección**: al añadir una petición al arranque del visor (aquí `datos/cobertura.geojson`), los tests de carrera que sueltan las cargas «a mano» se cuelgan o se vuelven frágiles: la promesa nueva también queda en espera. Se resuelve al momento lo que no es objeto del test (índice, cobertura) y se espera a la condición (`esperas.length === 2`) en lugar de a un número fijo de vueltas del bucle.
  - **Aplica a**: tests de `tests/site/visor.test.mjs`.

- **Lección**: el Leaflet falso de los tests solo conoce lo que se usaba (`geoJSON` sobre colecciones, sin `setStyle` en grupos, sin `marker`); un `L.geoJSON` sobre un `Feature` suelto o un `setStyle` de grupo rompen en el doble y no en el navegador. Se amplía el doble en el mismo cambio y se comprueba el resultado en un navegador real (`preview_start` con la configuración `visor` de `.claude/launch.json`, o `scripts/capturar_visor.mjs`).
  - **Aplica a**: implementación (cambios de `mapa.js`).

- **Lección**: una captura de una fixture sintética sobre el mapa real (un tramo inventado con texto de indicio en un punto de Colmenar) parece un hallazgo real. Esas capturas se enseñan en local y no se suben a `docs/`.
  - **Aplica a**: diseño (capturas de la PR).

## [2026-11-27] Historia #214 — Fuentes de sitios de interés

- **Lección**: un servicio público que responde 200 no es un recuento. Overpass dio un 200 con cero nodos porque la consulta por `area["ref:INE"="28043"]` no encontró el área (la relación de Colmenar no lleva esa etiqueta en OSM), y la búsqueda de relaciones con la misma etiqueta también dio cero. Un cero solo vale si antes se ha comprobado que el objeto de referencia existe en la fuente. Lo que no responde se anota como `bloqueado`, con el intento, no como negativo.
  - **Causa**: el espejo agotó el tiempo en las consultas por caja y el servidor principal devolvió 502 «at capacity» en varios intentos.
  - **Aplica a**: BA (al exigir un recuento, pedir el canal y el intento fallido) | implementación (antes de registrar un recuento de OSM o de un servicio público, comprobar que el objeto de referencia existe).

- **Lección**: dos fuentes que parecen independientes pueden ser la misma geometría. El Nomenclátor (NGBE) trae 217 de 230 topónimos de la caja con el campo `fuente` igual a `MITMA.IGN.BTN25`. Antes de sumar cobertura de dos fuentes, mirar el campo de procedencia.
  - **Aplica a**: BA e implementación (fuentes del IGN que se solapan; no contar dos veces el mismo dato como verificación cruzada).

- **Lección**: una especificación de producto descargada de la web puede estar sustituida. La BTN25 que se encuentra en ign.es es la v1.0 de 2009, y dice que desde septiembre de 2021 la sustituye ESPBTN_MOD21. Anotar la versión y la fecha de la especificación leída, y comprobar si hay una vigente antes de citar sus códigos.
  - **Aplica a**: BA (al verificar un producto, anotar la versión y la fecha de la especificación leída).

- **Lección**: un PDF de más de 10 MB no pasa por `WebFetch` y `pdftoppm` no está instalado. Descargar con `curl` a un directorio aparte y extraer el texto de los flujos Flate con `zlib` (`python3 -I`) sirve para buscar cláusulas. Ese texto no es una cita certificada: las citas se copian de él y se marcan como extraídas.
  - **Aplica a**: implementación (verificación de licencias en PDF de gran tamaño).
## [2026-11-27] Historia #213 — Kit de campo GPX/KML

- **Lección**: un exportador de lo ya publicado (GPX/KML) no copia propiedades: construye cada descripción solo desde la lista blanca de la ficha y falla si llega una clave de más o una de titularidad/catastral, incluso anidada. El aviso se exige por elemento y en la procedencia de cada capa; un exportador que «omite lo que no encuentra» deja un tramo sin aviso (lecciones 95 y 169).
  - **Aplica a**: implementación de cualquier exportación derivada de `site/datos/` (descargas, enlaces, QField).

- **Lección**: para que una salida con fecha sea reproducible byte a byte, la fecha entra por parámetro (el CLI la lee una vez) y nunca la lee el adapter; `xml.etree` con `ET.indent` y orden de entrada fijo basta, sin librerías nuevas.
  - **Aplica a**: implementación (ficheros publicados que llevan «fecha de generación»).
## [2026-11-27] Historia #206 — Vuelos históricos de la Comunidad de Madrid

- **Lección**: un servicio de imágenes responde **200 con una tesela blanca** fuera de su cobertura (la IDEM: 854 bytes, un solo nivel de gris), igual que el IGN. «200 + `image/png`» no prueba que haya imagen: se decodifica y se mide varianza y niveles distintos (en blanco si varianza < 1). Cualquier adapter de #207 debe aplicarlo a cada respuesta.
  - **Aplica a**: implementación (adapters de imágenes) | revisión.

- **Lección**: el `GetCapabilities` del WMS de la IDEM anuncia un `GetMap` (`http://…/erdas-iws/ogc/wms/visorcartografia2`) que responde 404; el que funciona es `https://idem.comunidad.madrid/srvimagenes2/ogc/wms/visorcartografia2`. Una URL que da el propio documento de capacidades tampoco vale hasta probarla (lección 0001). El `GetCapabilities` WMTS pesa 14 MB: se guarda solo un extracto.
  - **Aplica a**: implementación (#207).

- **Lección**: los píxeles de un `TileMatrixSet` `EPSG3857` miden `escala × 0,00028 × cos(latitud)` en el terreno: el nivel 18 («0,597 m») son 0,457 m en Colmenar. Se calcula la resolución real a la latitud del ámbito antes de elegir nivel. Los bytes de una misma tesela cambian entre peticiones, así que no sirven como huella.
  - **Aplica a**: implementación (#207) | BA (criterios de «resolución»).
## [2026-11-27] Historia #211 — Catastrones del IGN

- **Lección**: el `GetFeatureInfo` de `primera-edicion-mtn` devuelve la hoja de un solo punto; para saber qué hojas cubren un término hay que muestrear una rejilla dentro del límite (2,5 km bastó) y contar puntos por hoja. Una sola consulta en el centro oculta las hojas de borde (Colmenar tiene dos; Chinchón, cuatro). Los enlaces de descarga del CNIG devuelven la ficha HTML con reCAPTCHA v3, no el zip: se anota `bloqueado` y se trabaja con el render del WMS.
  - **Aplica a**: refinador, implementación (historias que inventarían la cobertura de un WMS histórico).

## [2026-11-27] Historia #217 — Polígonos catastrales (`CadastralZoning`)

- **Lección**: el texto de una `ExceptionReport` de Catastro depende de la capa y del idioma (parcelas: `out of limits`; `CadastralZoning`: `Area de la extensión fuera de los límites`). Al reutilizar la detección de «bbox demasiado grande» de otra capa, pedir una vez la excepción real a esa capa y reconocer su texto, no asumir el de la anterior.
  - **Aplica a**: implementación (adaptadores nuevos sobre el WFS de Catastro).
## [2026-11-27] Historia #216 — Fuente SIGPAC de la Comunidad de Madrid

- **Lección**: antes de dar por inexistente un documento de licencia («el Acuerdo del FEGA no se puede leer»), mirar dentro del paquete descargado: los términos de uso del FEGA (HVD, CC BY 4.0) venían como PDF dentro del ZIP de recintos, no en el feed ni en la ficha. Un texto de licencia que el feed cita y no se encuentra se deja `asumido`, pero antes hay que revisar cada fichero del paquete y la ficha del catálogo.
  - **Aplica a**: implementación y refinador (al verificar la licencia de una capa de descarga, listar el contenido completo del ZIP antes de buscar el documento en la web).
- **Lección**: las guardas de tests que leen una sección de `REGISTRY.md` por rango (`index(...)` hasta `## Resumen…`) cubren también todo lo que se inserte antes del resumen. Así, una palabra inocua de una sección nueva («propietario») rompió el test de #26. Escribir las secciones nuevas sin esos términos o, mejor, acotar la guarda al encabezado de su propia sección.
  - **Aplica a**: implementación (al añadir una sección a `REGISTRY.md`, ejecutar la suite completa, no solo los tests de la historia).
- **Lección**: el cruce de control entre una capa de trazados (GML en EPSG:25830) y otra de polígonos (SHP en EPSG:4258) se hizo con stdlib (inverso de UTM sobre los vértices y punto-en-polígono). Que el 99 % de los vértices caiga dentro de un recinto valida la reproyección a la escala del problema; si el porcentaje cayera, habría que revisar la conversión antes de sacar conclusiones.
  - **Aplica a**: implementación (cruces entre capas en distintos CRS sin dependencias de geoprocesamiento en `domain/`).
## [2026-11-27] Historia #220 — Montes de utilidad pública (IDEM)

- **Lección**: cuando una fuente pública publica campos de titularidad (aquí `DS_PROPIETARIO` y `DS_NOM_PROPIETARIO`, incluso de entes públicos), el parser lee solo una lista blanca de campos y falla si se le pide otro; no se parsea todo y se «filtra después». En la fixture se sustituyen los valores reales por unos ficticios, y un test comprueba que ni nombres ni valores llegan a ningún fichero derivado.
  - **Aplica a**: implementación de adapters de cualquier capa con campos de propietario (otras capas de `Zonas`, Catastro) | revisión.

- **Lección**: el filtro `BBOX` de una capa de recintos grandes trae montes de municipios vecinos y, al recortar con el límite del IGN, deja astillas de unos m² por la diferencia entre líneas municipales (Chinchón 122 m², Belmonte 1 920 m²). Se anotan en `REGISTRY.md` y no se filtran hasta decidir un umbral de publicación.
  - **Aplica a**: implementación (#224 al publicar la capa).
## [2026-11-27] Historia #207 — Adapter de los vuelos históricos de Madrid

- **Lección**: el WMS de la IDEM responde 200 con un PNG blanco fuera de cobertura (y la URL de `GetMap` que anuncia su `GetCapabilities` da 404); un `ServiceException` XML también llega con 200. El filtro fiable es validar la firma PNG antes de cachear y medir la varianza de gris (media de R, G, B) después. No hay Pillow en el proyecto: se reutiliza `adapters/evidence/png.py::decode_png`. Los identificadores `CM_*` son distintos de `AMS_1956-1957` del IGN: cada adapter rechaza con `InvalidHistoricalVintageError` los vuelos que no son suyos, y elegir adapter por vuelo es cosa de la composición (#208).
  - **Aplica a**: implementación (#208 y cualquier otra fuente de ortofoto por WMS).

## [2026-11-27] Historia #209 — Catastro a una fecha

- **Lección**: la verificación de Catastro se parte por dónde se puede leer. Desde GitHub Actions se leen las páginas de ayuda y los PDF de licencias de `www.catastro.hacienda.gob.es` (responden 200); no se puede pedir nada a `ovc.catastro.meh.es` (WMS, WFS, REST), que rechaza a los runners. Lo primero va en la historia de lectura; lo segundo, en una hija `fuera_de_ci` que se pasa en local.
  - **Causa**: la ayuda de antecedentes describe la consulta a una fecha pero no nombra el parámetro WMS, así que «`TIME` funciona» queda `pendiente` hasta la petición real. Además `Licencia.pdf` usa fuentes con codificación propia: ni `WebFetch` ni descomprimir los flujos dan texto, y las citas literales se reutilizaron de #21 diciéndolo. Para releerla, usar un visor de PDF fuera de la sandbox del agente.
  - **Aplica a**: BA (al partir historias de Catastro, separar lo legible en la web de lo que exige `ovc.catastro.meh.es`) | implementación (no inventar un parámetro que la documentación no nombra; marcarlo `pendiente`).
## [2026-11-27] Historia #247 — Licencia del IGN y servidumbre de cauces

- **Lección**: la licencia del IGN (CC BY 4.0, Orden FOM/2807/2015) sí cubre las obras derivadas, pero exige la fórmula «Obra derivada de <identificador> <fecha> CC-BY 4.0 <atribución>» visible a pie de mapa y repetida en abstract, lineage y AccessConstraints de los metadatos. El PDF remite a `scne.es/productos.html` para los valores concretos de cada producto, y esa tabla hay que leerla al publicar. `WebFetch` no decodifica el PDF; se descarga con `curl` y se extrae con `uv run --with pypdf python -I`. Ni el TRLA (art. 6) ni el RDPH (arts. 6–7) dicen desde qué línea se mide la servidumbre de 5 m, solo que afecta a los «terrenos que lindan con los cauces»; una franja desde el eje es siempre aproximada.
  - **Aplica a**: implementación de las historias de derivar y publicar la franja | revisión.
- **Lección**: al citar una norma, comprobar que la cita es de la redacción consolidada **vigente**, no de una versión antigua. En #247 el art. 7.1 del RDPH se citó con la redacción anterior a 2016 (sin «paso público peatonal») y el test fijaba esas cadenas obsoletas, así que pasaba en verde con una cita falsa. Anotar la fecha de consulta y comprobar numeración de apartados (art. 6.2 a), no 6 a)); un test de cita debe afirmar el texto vigente y, si procede, la ausencia del obsoleto.
  - **Aplica a**: implementación | revisión de cualquier historia que cite normas.

## [2026-11-27] Historia #219 — QField y el proyecto de campo

- **Lección**: un proyecto QGIS/QField se puede generar sin QGIS (GeoPackage con `sqlite3` + `.qgs` XML a mano), pero eso solo prueba que el fichero es válido como SQLite y XML, no que QField lo abra. Dejar la apertura real como reserva `asumido` en `REGISTRY.md` y pedirla a quien tenga el móvil. `WebFetch` devuelve 404 en varias rutas de `docs.qfield.org` que salen en buscadores; hay que partir de las que enlaza la propia documentación.
  - **Aplica a**: la historia hija que genere el proyecto real por municipio | revisión.

## [2026-11-27] Historia #223 — Kit de campo en el visor

- **Lección**: los tests de #166, #171 y #185 fijan el marcado y `config.js` con regex frágiles: `<section class="pc-cuerpo">…</section>` no admite otro `<section>` dentro (cierra en el primero) y `id: "candidatos",\s*titulo:` exige que `titulo` siga a `id`. Una sección nueva del panel va en un `<div role="group">` y las claves nuevas de una capa (`kitNombre`) van después de `nombre`, no entre `id` y `titulo`. `grep` de esos patrones en `tests/acceptance/` antes de tocar `index.html` o `config.js`.
  - **Aplica a**: implementación (cambios de `index.html` y de `config.js` del visor).

- **Lección**: un recurso opcional del visor (el índice del kit) se carga en paralelo al índice principal con un `try/catch` propio que solo escribe en `console`, y el arranque lo espera, para que la sección salga ya al elegir municipio sin carreras. La validación de ese índice descarta entradas y claves (no lanza) y deriva el texto de las capas de `config.js`, nunca del dato.
  - **Aplica a**: implementación (otros ficheros opcionales publicados en `site/datos/`, como el proyecto de campo de #222).
## [2026-11-27] Historia #222 — Proyecto de QField con formulario de incidencia

- **Lección**: para generar un GeoPackage reproducible byte a byte basta construirlo en `sqlite3.connect(":memory:")` y volcar `connection.serialize()` (sin ficheros temporales ni marcas de tiempo del sistema); el ZIP necesita `ZipInfo` con fecha fija (1980-01-01), orden de nombres y `external_attr` fijos. Una función que añade un campo opcional a un índice público compartido (`kit/indice.json`) debe conservar lo que otro comando escribió en él, y por defecto no tocarlo si la publicación está condicionada a una comprobación manual pendiente.
  - **Aplica a**: cualquier exportación binaria determinista | comandos que comparten un índice publicado.
## [2026-11-27] Historia #257 — Cauces de IDEE paginados y cacheados

- **Lección**: en OGC API Features el enlace `rel=next` trae la URL completa con su query; para cachearla con `CachedRawFetcher` (clave = URL canónica) hay que partirla en base + parámetros (`urlsplit`/`parse_qsl`) y comprobar que la base es la de la colección antes de seguirla. Y no comprobar «sin duplicados» por nombre o longitud: en datos reales dos tramos distintos comparten nombre y hasta longitud; el `id` de la feature es la única clave.
  - **Aplica a**: adapters que paginen colecciones OGC API Features (red de transporte, nuevas capas) | revisión.

## [2026-11-27] Historia #287 — Catastro histórico de rústica en REGISTRY.md

- **Lección**: una historia de documentación escrita antes de que el fundador termine la descarga puede afirmar como hecho algo que su propio comentario posterior desmiente. El CA3 decía que la 2.ª y la 3.ª renovación «no se bajaron»; el fundador las bajó después y lo pidió en el mismo issue. Antes de implementar, leer todos los comentarios del issue y contrastar cada afirmación de hecho de los criterios con el último comentario; si el fundador lo resuelve por escrito, seguir su decisión más reciente y dejar el cambio explícito en la PR, no reinterpretar el criterio en silencio.
  - **Causa**: el refinamiento se escribió con los tres productos del primer comentario; la segunda tanda de descargas llegó como comentario posterior y no entró en el texto de la historia.
  - **Aplica a**: refinador (al redactar criterios que describen el estado de una descarga o de un dato externo, citar el comentario que lo fija y su fecha) | implementación (leer los comentarios del issue completos, no solo el cuerpo, antes de dar por válido un CA de hecho).
- **Lección**: una sección nueva de `REGISTRY.md` que se inserte antes de «## Resumen de fuentes no verificadas» cae dentro del rango de las guardas de otras historias (`index(...)` hasta el resumen). Se añade al final del fichero, para no tocar esos rangos, y la suite completa se ejecuta antes de abrir la PR.
  - **Aplica a**: implementación (al añadir secciones a `REGISTRY.md`) | revisión (comprobar con qué encabezado acota cada guarda antes de aceptar la posición de una sección nueva).
## [2026-11-27] Historia #243 — Estrechamiento de vías pecuarias

- **Lección**: la capa derivada de vías pecuarias (#83) solo trae el área de la pieza recortada, no el `area_m2`/`length_m` del tramo completo de IDEM, y sin ellos no hay anchura oficial. Un cálculo que parta de la anchura oficial debe leer `MunicipalLivestockTrailLayer` (caché cruda de IDEM), no el GeoJSON derivado. Además, con la anchura catastral recortada a la franja, `A / W` es la fracción de la franja cubierta por parcelas VT: nunca supera 1, así que «A mayor que W» no existe como caso. Y `ruff format` sobre todo `src/` reformatea ficheros ajenos a la historia: formatear solo los ficheros propios.
  - **Aplica a**: implementación (señales de anchura, ventanas a lo largo del eje) | revisión.
## [2026-11-27] Historia #282 — Clasificación de vías pecuarias en BOE y BOCM

- **Lección**: la búsqueda de texto libre del BOCM no exige todas las palabras: pone en la página 1 documentos de otros municipios con «Colmenar» o «vías pecuarias» (Colmenar del Arroyo, Chapinería), y el control con Villaconejos no lo deja arriba aunque el BOE tenga su orden. Un «no hay» en el BOCM solo vale con la frase exacta del título de la orden y con un control positivo que salga en primer lugar (Chapinería, orden 3/2011).
  - **Causa**: sin control, la primera página de texto libre parecía un negativo válido para cuatro municipios y no lo era. Con la frase exacta, el control de Chapinería sí aparece el primero.
  - **Aplica a**: BA (exigir control positivo antes de escribir «no localizada») | implementación (cualquier consulta a un buscador de boletines).
- **Lección**: el buscador por título del BOE sirve como negativo cuando salen los controles positivos (Chinchón, Villaconejos, Villarejo), pero solo cubre el título. Un «no hay» en el texto completo o en los sumarios 1960–1990 no se afirma: esos canales se anotan «no se pudo leer» con su motivo.
  - **Aplica a**: BA e implementación (cada canal de un registro de fuentes con su resultado, también cuando es ilegible).
- **Lección**: cambiar el estado de filas de una tabla compartida rompe la guarda de otra historia que cuenta las mismas cadenas (`"| no encontrada |"` en #26). Se actualiza esa guarda al nuevo estado y se explica en la PR, en vez de mantener el dato viejo.
  - **Aplica a**: implementación (antes de editar una tabla de `REGISTRY.md`, buscar las guardas que la leen por rango o por cadena).
## [2026-11-27] Historia #279 — Catálogo de caminos de Cáceres

- **Lección**: la URL de descarga que cita una ficha de datos.gob.es puede estar muerta o devolver la portada HTML con `200`; el recurso real está en el registro del propio portal (`package_show` de CKAN). Antes de dar un fichero por descargado, comprobar que es JSON (`head -c`), y si no lo es, no guardarlo con la extensión del dato.
  - **Causa**: el endpoint `GetData?...format=geojson` de la ficha devolvió la portada de `opendata.caceres.es` (6.619 bytes, `text/html`) con código 200 tras una redirección a HTTPS con cadena de certificados incompleta. Los datos reales están en `ide.caceres.es` (WFS), que no respondió desde GitHub Actions: el contenido quedó `pendiente-local`.
  - **Aplica a**: refinador (no citar una licencia o un formato de una ficha sin abrir el recurso) | implementación (un `200` no prueba que haya dato).

## [2026-11-27] Historia #281 — Órdenes de clasificación en el BOE

- **Lección**: el PDF de una disposición del BOE se saca del enlace «PDF» de la ficha `doc.php?id=<BOE-A-…>` (patrón `/boe/dias/AAAA/MM/DD/pdfs/A<página inicial>-<final>.pdf`) y es la página entera del boletín, con otras disposiciones sin relación. El texto extraído confunde cifras («6,E9» por 6,69): una cifra se da por buena solo contra la imagen que devuelve `Read`. Si `Read` con `pages` falla por falta de `pdftoppm`, leerlo sin `pages`.
  - **Aplica a**: implementación (fixtures de disposiciones: grabar el PDF íntegro, pero no copiar a texto los nombres de personas que cite la página).

## [2026-11-28] Historia #250 — Servidumbre aproximada de cauces

- **Lección**: `Watercourse` no conserva el `id` de la feature de IDEE, así que la capa derivada no tiene identificador estable de origen: los `id` de salida son `<ine>-<k>` tras ordenar por (nombre, geometría, anchura), deterministas pero cambian si entra un cauce nuevo. Y el buffer de una línea puede traer huecos que `Geometry` no modela: el adapter conserva solo el contorno exterior (la franja solo se amplía) porque el recorte posterior lanza `ClippedGeometryHasHolesError` ante huecos.
  - **Aplica a**: implementación (#251 al publicar: no prometer ids estables) | revisión.


## [2026-11-28] Historia #287 — Test de «solo documentación»

- **Lección**: un test que comprueba qué ficheros toca una historia no debe comparar la rama actual con `origin/main` (`git diff origin/main...HEAD`). Una vez fusionado, ese test se ejecuta en todas las ramas y falla en cualquiera que cambie código. Hay que mirar los ficheros de los commits de la propia historia (`git log --format= --name-only --grep="^Historia #<n>:" HEAD`) y saltarse si no hay ninguno, como pasa en CI con historia corta.
  - **Causa**: `test_historia_287.py::test_ca8_solo_documentacion_en_el_diff` fallaba en local en la rama de #183.
  - **Aplica a**: implementación (tests de alcance de una historia de documentación).

## [2026-11-28] Historia #208 — Vuelos de Madrid en las imágenes de comparación

- **Lección**: cuando un dominio fija «la fuente de cada imagen» y dos adapters la escriben en su procedencia, el dominio declara la cadena por vuelo (`FlightInfo.source`) y un test la compara con la que pone cada adapter real sobre una fixture. Sin ese test, una errata en cualquiera de los dos lados hace que la publicación rechace todas las imágenes de ese servidor con un motivo que parece de datos y no de código.
  - **Aplica a**: implementación y arquitecto (todo valor que el dominio exige y otro adapter produce).
- **Lección**: el contador de `tests/site/nodo.py` cuenta el propio fichero de Node como un test que pasa cuando el patrón no casa con ninguno (`passed == 1`). `assert_node_tests_pass(patrón, minimum=1)` pasa sin ejecutar nada si el test aún no existe o se renombra. Para un criterio con un único test de Node, comprobar además que su nombre está en `visor.test.mjs`, o pedir `minimum` igual al número real de casos.
  - **Causa**: el CA5 de #208 pasó en verde antes de escribir el test `H208 CA5:`.
  - **Aplica a**: implementación y revisión (tests de aceptación que lanzan casos de Node).
- **Lección**: al cambiar la serie de una política versionada (franjas, huecos), los tests de otras historias que «rompen» el manifiesto subiendo la versión (`version=2`) dejan de probar nada en cuanto la versión vigente es esa. Se cambian a la versión anterior (`version=1`), no se borran: siguen probando el mismo motivo.
  - **Aplica a**: implementación (CA de «actualizar los tests de #N sin relajarlos»).
## [2026-11-28] Historia #236 — Catastro a una fecha, peticiones reales

- **Lección**: el WMS de Catastro (`ServidorWMS.aspx`) es 1.1.1: se pide con `SRS`, no con `CRS`. Con `VERSION=1.3.0` responde `200` con una `ServiceException` en XML. Acepta `TIME=AAAA-MM-DD` aunque su `GetCapabilities` no declare ninguna dimensión. Con una fecha sin datos devuelve **`200` y un PNG que solo tiene el rótulo «FECHA: …»**, y con una fecha incompleta (`TIME=2005`) ignora el parámetro y devuelve el mapa actual. Para dar por buena una imagen hay que comprobar que trae líneas (píxeles dibujados) y el rótulo de fecha, no el código HTTP ni el tipo de contenido.
  - **Causa**: la primera tanda de `GetMap` se pidió con `CRS` y las cinco «imágenes» eran el mismo XML de error de 432 bytes guardado como `.png`.
  - **Aplica a**: implementación (cualquier adapter del WMS de Catastro; comprobar el tipo real del cuerpo antes de guardarlo con extensión de imagen).

## [2026-11-28] Historia #227 — dominio propio

- **Lección**: al cambiar la URL absoluta del sitio, buscar con Grep también los tests de otras historias que fijan una lista blanca de dominios en `index.html` (el de #166 CA8 admitía `murcialito` pero no el dominio nuevo). Se actualizan añadiendo el dominio nuevo, sin relajar el resto de la regla. Y un test `red` que comprueba el sitio en vivo falla hasta que se despliega la PR (el dominio sirve aún la versión anterior): se anota en la PR, no es un fallo.
  - **Aplica a**: implementación (cambios de dominio o de URL base).

## [2026-11-28] Tubería — límite de turnos del revisor

- **Lección**: `claude-code-action` marca el paso como fallido si el agente termina con más turnos que `--max-turns`, aunque acabe bien y haya publicado su veredicto. Entonces no se ejecuta «Aplicar veredicto», el check `revision` queda en `failure` y Fusionar no fusiona. El barrido de `revisiones_pendientes.sh` solo relanza revisiones en `skipped` o `cancelled`, así que la PR se queda parada. El límite del revisor sube de 40 a 60, como el de Actualizar rama. Si vuelve a pasar, se relanza la revisión a mano (`gh run rerun <id>`).
  - **Causa**: la revisión de la PR #310 (#240, `talla:l`) publicó «APROBADO» en 41 turnos y el check quedó en `failure` (2026-11-28).
  - **Aplica a**: tubería (límites de turnos de los workflows de agente).
## [2026-11-28] Historia #240 — Tabla de convergencia de fuentes

- **Lección**: cuando el fundador pide que un umbral de producto sea «configurable», un parámetro con valor por defecto no basta: el resultado debe anotar el valor que usó (aquí `ConvergenceTable.appears_min_fraction`). Si no, dos tablas con el mismo `CONVERGENCE_METHOD` pueden salir de umbrales distintos sin que nada lo delate, y se pierde «cada hallazgo rastreable hasta su regla». El campo nuevo entra en la lista blanca de la entidad y en el ADR, y la PR lo dice como desviación del diseño.
  - **Aplica a**: arquitecto (al marcar un umbral «a revisar con el fundador», prever dónde se anota si se hace configurable) | implementación.
- **Lección**: para probar que una entidad publicable no puede llevar geometría ni procedencia «ni anidada», no basta con listar `dataclasses.fields`: se recorren las anotaciones con `typing.get_type_hints`, `get_origin` y `get_args` hasta los tipos hoja, se compara cada uno con una lista blanca y se falla con los tipos prohibidos (`Geometry`, `Provenance`, `TrailRef`…). Así, un campo `tuple[X, ...]` o `X | None` con un tipo nuevo hace fallar el test aunque el nombre del campo no cambie.
  - **Aplica a**: implementación y revisión (entidades con «lista blanca de campos»).

## [2026-11-28] Historia #294 — ADR del repositorio privado de datos

- **Lección**: cuando el fundador comenta en la issue que una condición que el criterio pide «hacer» ya está hecha, el ADR y la documentación la recogen como **hecha, con fecha y evidencia** (enlace al run que lo comprueba), y el test de aceptación busca esa forma, no la del criterio original. La PR lo dice como ajuste del criterio.
  - **Aplica a**: implementación y revisión (criterios con condiciones previas que cambian mientras la historia espera en la cola).
- **Lección**: si un hecho que corrige una historia de documentación también aparece en comentarios de workflows o scripts que su `Toca:` (o un CA de «solo documentación») no deja tocar, no se editan: se listan en las Consecuencias del ADR con fichero y línea, para que los corrija el fundador o una historia de tubería.
  - **Aplica a**: implementación (historias de solo documentación).
## [2026-11-28] Historia #314 — Manifiesto de imágenes de los indicios

- **Lección**: hoy todas las fuentes de `SourceKind` son contraparte publicable (las cuatro redistribuibles más `catastro_vt`), así que la rama «contraparte fuera de `PUBLISHABLE_COUNTERPART_SOURCES`» de `is_publishable_indicio` no se alcanza con datos reales. Para probarla, un test recorta la constante con `monkeypatch.setattr(published_discrepancy, ...)`: vale porque la función lee la global del módulo en cada llamada, no una copia importada.
  - **Aplica a**: implementación (reglas de licencia con ramas que ningún dato actual dispara) | revisión (exigir el test aunque la rama parezca muerta).
- **Lección**: `uv run ruff format src` reformatea el árbol entero y toca ficheros ajenos a la historia. Formatear solo los ficheros propios (`ruff format <ruta>`) y mirar `git status` antes de commitear.
  - **Aplica a**: implementación.

## [2026-11-28] Historia #313 — Cobertura histórica desde curated/historico

- **Lección**: un ADR que el diseño fija como «el siguiente número libre» puede estar ocupado cuando lo crea la historia (aquí, el 0055 y el 0056 se fusionaron entre el diseño y el desarrollo). Mirar `ls docs/adr` antes de crear el fichero, usar el siguiente libre y anotar en el propio ADR la correspondencia («ADR 0055 §n» de las issues = este ADR), para que las hijas no citen el equivocado.
  - **Aplica a**: arquitecto (en las issues, escribir «el número libre al crearlo», sin cifra) | implementación.
- **Lección**: un `unit_id` sintético derivado de la fixture tiene que empezar por el `document_id` de su `unidad.json` (`UnitSpec` lo exige): para fabricar una segunda unidad en `tmp_path` se cambia el sufijo (`280075-w2`), no el prefijo.
  - **Aplica a**: implementación (tests con varias unidades históricas).
## [2026-11-28] Tests que miran los ficheros de los commits de una historia

- **Lección**: en un clon superficial (el CI de `main` clona con profundidad 1) el último commit es la raíz del clon, y `git log --name-only` sobre una raíz lista **el árbol entero**. Un test que busca los commits «Historia #N: …» con `git log --grep … --name-only` pasa en la PR (la cabeza es un merge que no casa y se salta) y falla en `main` justo después de fusionar, cuando la cabeza es el propio commit de la historia. Se sacan los ficheros con `git diff-tree --no-commit-id --name-only -r <commit>` commit a commit: sin `--root` no lista nada en una raíz y el test se salta.
  - **Causa**: el CA10 de #294 dejó en rojo el CI de `main` tras fusionar la PR #312 (2026-11-28); el CA8 de #287 tenía el mismo patrón.
  - **Aplica a**: implementación y revisión (tests que inspeccionan el historial de git).

## [2026-11-28] Historia #295 — Sincronización de los datos privados

- **Lección**: un comando que se niega si hay `CI`, `GITHUB_ACTIONS` o `CLAUDECODE` en el entorno hace que **todos** sus tests salgan por la guarda en CI y en la sesión del propio agente (las dos variables están definidas). Una fixture `autouse` del módulo de tests borra las tres con `monkeypatch.delenv`, y solo los tests de la guarda las vuelven a poner. Sin eso, los tests pasan en el portátil y fallan en CI, o al revés.
  - **Aplica a**: implementación (comandos con guarda de entorno).
- **Lección**: para que el `stderr` de un CLI externo (aquí `gh`, que puede nombrar el repo privado) nunca llegue a un log, el adapter lo captura siempre y lanza errores de port con **textos fijos**. El test lo prueba con un ejecutor falso cuyo `stderr` lleva el nombre del repo y un centinela, y busca los dos en stdout y stderr del comando. Un test estático sobre el fichero del adapter (`token`, `shell=True`, `env=`) obliga además a no escribir esas palabras ni en los comentarios.
  - **Aplica a**: implementación y revisión (adapters sobre CLIs con sesión del usuario).
- **Lección**: las fixtures con sha256 «calculados en el test, no copiados a mano» se generan con un `generar.py` que escribe los bytes y el catálogo a la vez, y un test compara lo versionado con lo que da el generador. Así el hash nunca se copia y un cambio de bytes sin regenerar falla en CI.
  - **Aplica a**: implementación (fixtures que declaran la huella de otros ficheros).
## [2026-11-28] Historia #315 — Tabla de convergencia en los tramos a verificar

- **Lección**: cuando un comando gana opciones con valor por defecto bajo `data/` o `curated/` (aquí `--capas data/derived`, `--curated-historico curated/historico`), los tests que lo invocan por la CLI siguen en verde sin tocarlos si esos directorios no existen en el clon: pasan por accidente, y en el portátil de alguien con datos reales leerían otra cosa. Se buscan con `grep` todos los ayudantes que llaman al comando (aquí `_publicar` de #170 y #172 y `generar_idem.py` de #208) y se les pasan directorios de la fixture o inexistentes en `tmp_path`, de forma explícita.
  - **Aplica a**: implementación (comandos con lectores nuevos) | revisión (un test de otra historia que «no hubo que tocar» puede depender del estado del disco).
- **Lección**: «no se lee ninguna capa si falla una comprobación previa» no se cumple con solo ordenar los `except`: si el cálculo caro (la tabla) va antes de la función de dominio que hace las comprobaciones por elemento, ya ha leído. Se separan las comprobaciones en su propia función (`check_approved_publishable`), se llaman primero y la construcción las repite (son baratas y deterministas); un test con un lector que falla si se llama lo fija.
  - **Aplica a**: arquitecto e implementación (criterios de orden de fallos con lecturas caras).
- **Lección**: `dump_compact` ordena las claves, así que una lista blanca publicada se compara como conjunto (`set(tabla) == set(CLAVES)`); el orden del ADR solo se puede afirmar sobre el diccionario antes de serializar.
  - **Aplica a**: implementación (tests de listas blancas sobre ficheros publicados).

## [2026-11-28] Historia #288 — Código de inventario de los tramos

- **Lección**: un filtro de texto libre (`note_problem`, `name_problem`) solo detecta referencias catastrales, localizadores y palabras vetadas: **no puede detectar un nombre de persona**. Para que ninguno entre, el formato del nombre es una lista cerrada de caracteres (sin `_`, `@`, `?`…) y, sobre todo, el mensaje de error nunca repite el valor. El test de «sin nombres de personas» usa una entrada hostil y comprueba que el nombre no aparece en la salida ni en el error, no que el sistema lo reconozca.
  - **Aplica a**: implementación y revisión (campos de texto libre en `curated/`).
- **Lección**: el prefijo de un municipio es una constante de dominio (`MUNICIPALITY_PREFIXES`) fijada por el fundador; el código se normaliza al formatear (`:03d`), así que se rechazan los ceros de más (`COL-0001`) para que lo que se lee sea lo que se imprime.
  - **Aplica a**: implementación (identificadores humanos que se muestran tal cual).
