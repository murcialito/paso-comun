"""Formas de una referencia catastral y de un localizador de parcela (historias #94 y #316).

Viven aquí, y no en `published_discrepancy`, para que `published_convergence` las use sin
importar `published_discrepancy`, que desde #316 importa `published_convergence` (ciclo).
`published_discrepancy` las reexporta con su nombre de siempre. Solo stdlib.
"""

import re

CADASTRAL_REFERENCE_RE = re.compile(
    r"\b\d{5}[A-Z]\d{8}(?:\d{4}[A-Z]{2})?\b"  # rústica: 14 o 20 caracteres
    r"|\b\d{7}[A-Z]{2}\d{4}[A-Z](?:\d{4}[A-Z]{2})?\b"  # urbana: 14 o 20 caracteres
)
"""Forma de una referencia catastral. Última barrera del sink y del test de CA3."""

PARCEL_LOCATOR_RE = re.compile(r"[?&](?:poligono|polígono|parcela|refcat)=", re.IGNORECASE)
"""Parámetros de URL que localizan una parcela concreta (ADR 0041 §4: no se publican
números de polígono ni de parcela). También es última barrera del sink."""
