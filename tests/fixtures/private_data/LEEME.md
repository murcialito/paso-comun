# Fixtures sintéticas del repo privado de datos (historia #295)

**Todo es sintético.** Nada de esto sale del repo privado real (`ADR 0055`): ningún agente tiene acceso a él. Año 2099, URL `example.invalid`, sin referencias catastrales ni nombres de personas.

- `repo/` imita la raíz del repo privado: `catalogo/<fuente>.json`, `procedencia/<fuente>/<id>.procedencia.json` y `derivados/...` (lo que vive en git).
- `adjuntos/<etiqueta>/<adjunto>` son los bytes de los adjuntos de release.
- Dos catálogos (`catastro_historico`, `catastro_inspire`), con una entrada `release` y otra `git` vigentes en cada uno. `catastro_historico` trae además una entrada sustituida (`sustituido_por`) con el mismo `destino` que la que la sustituye.

Los sha256 y los tamaños del catálogo se calculan a partir de los bytes en `generar.py`; no se copian a mano. Para regenerar todo:

```bash
uv run python -m tests.fixtures.private_data.generar
```

`tests/adapters/private_data/test_fixtures.py` comprueba que lo versionado es lo que da el generador.
