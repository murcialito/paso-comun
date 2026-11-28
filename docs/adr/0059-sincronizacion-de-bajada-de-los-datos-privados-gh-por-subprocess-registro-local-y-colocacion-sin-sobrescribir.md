# ADR 0059 — Sincronización de bajada de los datos privados: `gh` por subprocess, registro local y colocación sin sobrescribir

- Estado: aceptada
- Fecha: 2026-11-28
- Historia: #295 (feature #293; sigue en #320)
- Relacionados: ADR 0055 (repo privado, catálogo y acceso), ADR 0028 y la caché cruda de #34 (inmutabilidad), ADR 0022 y 0024 (agentes en Actions)

> Numeración: el diseño de #295 lo proponía como «ADR 0057». Ese número lo ocupó #313 (tabla de convergencia) mientras esta historia esperaba en la cola, y el 0058 lo ocupó #102 (privacidad de los avisos con foto) mientras la PR estaba abierta. Donde la issue #295 o #320 dicen «ADR 0057 §n», léase este ADR 0059 §n.

## Contexto

El ADR 0055 fija dónde están los datos internos (repo privado `murcialito/paso-comun-datos`, adjuntos de release), el esquema del catálogo (§3) y que la sincronización es un comando local que usa el `gh` del fundador, que no maneja credenciales, que sale con 0 y un mensaje genérico si no hay acceso, y que se niega en CI (§5). Deja abierto cómo se llama a `gh`, cómo se distingue «sin acceso» de un fallo, dónde queda la procedencia en local, cómo se coloca un fichero sin sobrescribir y qué hacer con un fichero que el fundador ya tiene.

## Decisión

1. **Dos ports de una capacidad.** `PrivateDataStore` (leer los catálogos; descargar un objeto por su ubicación a una ruta) y `PrivateDataArea` (la zona local `data/`: preparación, estado, lectura de una procedencia pequeña, colocación y registro). El catálogo y su validación viven en `domain/private_catalog.py` con dataclasses congeladas y stdlib; el caso de uso `SyncPrivateDataUseCase` solo orquesta. No se reutiliza `CachedRawFetcher` (clave por URL, cuerpo en memoria); se conserva su contrato: inmutable, sha256 comprobado y escritura atómica.
2. **Validar todo antes de tocar nada.** Todos los catálogos se leen, se validan por entrada y en conjunto (ids únicos, `sustituido_por` existente, `destino` vigente único, ningún fichero vigente dentro de un tar vigente) antes de la primera descarga. Los mensajes nombran fichero, posición y campo, nunca el valor; las claves desconocidas se cuentan. Un valor con forma de referencia catastral o una clave de titularidad, en el catálogo o en la procedencia, invalida el conjunto.
3. **`gh` por subprocess, sin shell y sin entorno propio.** Solo tres formas de llamada: `gh api --method GET repos/<repo>/contents/catalogo`, la misma con `-H "Accept: application/vnd.github.raw+json"` para un fichero de git, y `gh release download <etiqueta> --repo <repo> --pattern <adjunto> --output <ruta>`. Sin `shell=True` y sin pasar un entorno propio: `gh` se autentica con su sesión o con su propia lectura de `GH_TOKEN`. El código no lee ni pasa ninguna credencial. El `stderr` de `gh` se captura siempre, solo sirve para clasificar el error y nunca se imprime. Requiere `gh` ≥ 2.28.
4. **«Sin acceso» solo al leer los catálogos.** `gh` ausente, `HTTP 401/403/404` o «gh auth login» → `PrivateDataUnavailable`: el comando imprime «sin acceso al repo de datos privados; nada que hacer» y sale con 0. Cualquier otro fallo (red, 5xx, tiempo) sale con 1 con un mensaje genérico. Un fallo durante las descargas es un fallo de esa entrada, no «sin acceso».
5. **Guarda de entorno.** Si están definidas `CI`, `GITHUB_ACTIONS` o `CLAUDECODE`, el comando no instancia nada, no llama a `gh` y sale con 1. `CLAUDECODE` es defensa añadida para sesiones de Claude Code en local; la regla de que ningún agente lo ejecuta (ADR 0055 §5) sigue siendo la norma.
6. **Colocación sin sobrescribir.** Cada objeto se descarga a una zona de preparación dentro de `data/privados/.preparacion/` (mismo sistema de ficheros), se comprueban tamaño y sha256 y solo entonces se enlaza en `data/<destino>` con `os.link`, que falla si ya existe. Sin enlaces físicos, copia a un temporal hermano y `os.replace` tras comprobar que no existe. La zona se borra siempre.
7. **Lo que ya está, está.** Un `destino` presente con el sha256 y el tamaño del catálogo cuenta como «ya estaba» y no se descarga; uno distinto es un conflicto que falla la entrada sin tocarla. Así el portátil del fundador, que ya tiene los originales, no vuelve a bajar nada.
8. **Registro local de procedencia.** `data/privados/<id>.json` guarda la entrada del catálogo y su procedencia del repo privado, en JSON canónico y sin fecha de sincronización (repetir da los mismos bytes). Es inmutable como lo demás. No se escribe nada junto al fichero de `destino`.
9. **Lote que sigue.** Un fallo en una entrada no frena las demás; el comando sale con 1 si alguna falla. Las entradas sustituidas, las de fuentes no pedidas (`--fuente`) y, hasta #320, las de `formato: tar` o con `partes` se cuentan como saltadas con su motivo.
10. **Composition root solo en `cli/`**, con el nombre del repo como constante de `cli/main.py`. Ni `api/` ni ningún workflow lo instancia; un test lo comprueba sobre `.github/`.

### Ajustes de la implementación respecto al diseño de #295

- `PrivateDataArea` lleva además `read_small(path)`: el caso de uso no lee ficheros por su cuenta, así que la procedencia descargada a la zona de preparación la lee el adapter, con un tope de 1 MiB.
- El registro de una entrada solo lleva las claves opcionales que tienen valor (`sustituido_por: null` no aparece). Como las entradas sustituidas se saltan antes de mirar el registro, rellenar `sustituido_por` en el catálogo no invalida registros vigentes.
- La defensa contra enlaces simbólicos alcanza también a los directorios intermedios bajo `data/`: si `data/raw` (o cualquier tramo de la ruta de `destino`) es un enlace simbólico, por ejemplo a un disco externo, todas sus entradas fallan como «conflicto con lo que ya hay en local». Es a propósito: un enlace podría sacar la escritura fuera de `data/`. Quien guarde los datos en otro disco debe montar o apuntar `--datos` a un directorio real, no enlazar subdirectorios.
- Los catálogos se listan con `gh api` sin `--paginate`: vale mientras haya menos de 1000 ficheros en `catalogo/`. Revisarlo en #320 o si el repo privado se acerca a ese número.

## Consecuencias

- La copia se restaura en cualquier máquina con `gh auth login` y `uv run python -m parcel_intel.cli.main datos-privados sincronizar`, sin credenciales nuevas.
- El test de integración contra el repo real solo lo corre el fundador (`integration`, `fuera_de_ci`, opt-in con `PASO_COMUN_DATOS_PRIVADOS=1`). Necesita en el repo privado una entrada pequeña `prueba-sincronizacion`.
- La clasificación de errores depende del texto de `stderr` de `gh` (`HTTP 404`…). Si `gh` cambia sus mensajes, «sin acceso» pasaría a salir con 1: falla de forma visible, no filtra nada.
- `tar` y `partes` quedan para #320 sobre estos mismos ports.
