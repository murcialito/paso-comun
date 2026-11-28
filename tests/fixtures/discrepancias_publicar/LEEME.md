# Fixtures de la historia #94 — SINTÉTICAS

Todo lo de este directorio está **inventado a mano** (lección 0012): ni un metro de
geometría ni una procedencia son reales. Cada capa lo dice en su `aviso` y en su
`procedencia`. Las coordenadas están desplazadas a la zona de Colmenar de Oreja
(origen 465000, 4440000 en EPSG:25830) para que la reproyección a EPSG:4326 dé
longitudes y latitudes creíbles. No se tocan las fixtures de #32 (`../discrepancias/`).

`entrada/` imita `data/derived/` en el formato común de capa derivada (ADR 0030), con
`nombre_municipio` y la marca `redistribucion` (ADR 0036):

| Fichero | Contenido |
| --- | --- |
| `vias_pecuarias/28043.geojson` | `VP-1` `Polygon` (franja de 200 × 10 m), `VP-2` `MultiPolygon` de 2 partes, `VP-3` `LineString`; `fecha_dato` 2014-01-01, `publicable` |
| `camino_actual/28043.geojson` | `R-1` sobre el eje de `VP-1`, `R-3` paralela a `VP-3` a 12,5 m, `R-2` lejos; `fecha_dato` 2025-03-15, `publicable` |
| `catastro_caminos/28043.geojson` | `99043A00109001` igual a `VP-1`, `99043A00109002` sin equivalente; referencias rústicas sintéticas de provincia 99; `solo_uso_interno` |
| `catastro_caminos/28045.geojson`, `camino_actual/28045.geojson` | un camino de Catastro y uno del IGN a 1 km: todo indicio tiene Catastro en el lado A (caso vacío de CA8) |
| `camino_asfaltado/<ine>.geojson` | red asfaltada **vacía** junto a cada `camino_actual/` (historia #183): `discrepancias` exige los dos ficheros, y vacía no cambia el oráculo |

Las franjas miden 10 m de ancho y no 20 como en #32: con 20 m, las muestras del borde
corto de `99043A00109001` quedan a 7,5 m del eje de `R-1` (tolerancia Catastro–IGN 7,07
m) y saldría un `SOLAPE` con Catastro en el lado A que no ejercita nada nuevo.

## Oráculo de 28043

Fijado a mano antes de implementar (lección 0017) y confirmado ejecutando
`discrepancias` y `publicar-discrepancias` sobre estas capas (Bloque 0). Con la
corrección del ADR 0041, los indicios con Catastro como contraparte **sí** se publican.

`discrepancias` escribe 5 registros (`AUSENTE_EN_FUENTE`=4, `DESPLAZAMIENTO`=1).
`publicar-discrepancias` publica **4**:

- `AUSENTE_EN_FUENTE` vías pecuarias → IGN (`VP-2`, `MultiPolygon` de 2 partes): «Indicio
  a verificar: según vías pecuarias (IDEM), 2014 existe un trazado en este tramo que no
  aparece en red de transporte del IGN, 2025.»;
- `AUSENTE_EN_FUENTE` vías pecuarias → Catastro (`VP-2` y `VP-3`);
- `DESPLAZAMIENTO` IGN → vías pecuarias (`R-3`): «… difieren unos 12 m en este tramo.»
  (`f"{12.5:.0f}"` da `"12"`); confianza `baja` (0,082).

Omite **1**: el `AUSENTE_EN_FUENTE` Catastro → IGN de `99043A00109002` (lado A de
Catastro, zona redactada). Contraparte: 0.

28045 no publica nada y omite 1 (lado A).

## Uso en #316 (tabla de convergencia)

Desde #316, `publicar-discrepancias` lee también el informe **interno** que `discrepancias`
escribe junto al publicable, el manifiesto de `imagenes-comparacion-indicios` y estas mismas
capas como capas de la tabla (`--capas`). Los tests no versionan ni el manifiesto ni las
imágenes: los genera en `tmp_path` el productor real, con un `http_get` falso
(`tests/indicio_publication_support.py`). La cobertura histórica sale vacía (directorios que
no existen). Los cuatro indicios publicados de 28043 llevan su tabla; el oráculo de #94 no
cambia.
