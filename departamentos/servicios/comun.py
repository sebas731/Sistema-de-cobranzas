"""Utilidades compartidas por los servicios: redondeo, fechas, parseo y tramos."""
from datetime import date
from decimal import InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, Decimal

CENTIMOS = Decimal('0.01')
CERO = Decimal('0.00')

# --- Estrategias de redondeo configurables (siempre con Decimal) ---
TRUNCAR_DECIMAS = 'truncar_decimas'
REDONDEAR_DECIMAS = 'redondear_decimas'
TRUNCAR_CENTIMOS = 'truncar_centimos'
REDONDEAR_CENTIMOS = 'redondear_centimos'

_ESTRATEGIAS = {
    TRUNCAR_DECIMAS: (Decimal('0.1'), ROUND_DOWN),
    REDONDEAR_DECIMAS: (Decimal('0.1'), ROUND_HALF_UP),
    TRUNCAR_CENTIMOS: (CENTIMOS, ROUND_DOWN),
    REDONDEAR_CENTIMOS: (CENTIMOS, ROUND_HALF_UP),
}


def redondear(valor):
    """Redondeo comercial (medio-arriba) al céntimo."""
    return Decimal(valor).quantize(CENTIMOS, rounding=ROUND_HALF_UP)


def aplicar_redondeo(valor, estrategia=TRUNCAR_DECIMAS):
    """Redondea un importe según la estrategia elegida y lo deja a 2 decimales.

    - truncar_decimas (por defecto): trunca a la décima (47.13 → 47.10).
    - redondear_decimas: medio-arriba a la décima.
    - truncar_centimos: trunca al céntimo.
    - redondear_centimos: medio-arriba al céntimo.
    """
    if estrategia not in _ESTRATEGIAS:
        raise ValueError(f'Estrategia de redondeo desconocida: {estrategia}')
    paso, modo = _ESTRATEGIAS[estrategia]
    return Decimal(valor).quantize(paso, rounding=modo).quantize(CENTIMOS)


def sumar_meses(periodo, k):
    """Día 1 del mes que está `k` meses después (k negativo: antes) de `periodo`."""
    n = periodo.year * 12 + periodo.month - 1 + k
    return date(n // 12, n % 12 + 1, 1)


def fecha_vencimiento(periodo, dia=5):
    """Día `dia` del mes siguiente al periodo (por defecto, el 5, como el Excel)."""
    if periodo.month == 12:
        return date(periodo.year + 1, 1, dia)
    return date(periodo.year, periodo.month + 1, dia)


def a_decimal(valor, defecto=None):
    """Convierte texto/número a Decimal (acepta coma decimal); `defecto` si no se puede."""
    if valor is None or str(valor).strip() == '':
        return defecto
    try:
        return Decimal(str(valor).replace(',', '.'))
    except (InvalidOperation, ValueError):
        return defecto


def ordenar_tramos(tramos, limite):
    """Ordena tramos de tarifa por su tope (`limite` = nombre del campo); el
    tramo sin tope (None) queda al final."""
    return sorted(tramos, key=lambda t: (getattr(t, limite) is None, getattr(t, limite) or Decimal('0')))


def escalonar(m3, tramos, limite):
    """Reparte `m3` entre tramos acumulativos: produce (tramo, m³ dentro del tramo).

    Cada m³ se cobra a la tarifa del tramo en que cae; el tramo sin tope cubre
    todo el excedente.
    """
    piso = Decimal('0')
    for tramo in ordenar_tramos(tramos, limite):
        if m3 <= piso:
            break
        tope = getattr(tramo, limite)
        tope = m3 if tope is None else tope
        cantidad = min(m3, tope) - piso
        if cantidad > 0:
            yield tramo, cantidad
        piso = tope
