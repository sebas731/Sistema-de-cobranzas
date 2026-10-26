"""Boleta de cobranza (documento único web = impresión = PDF)."""
from django.utils import timezone

from ..models import Concepto
from .comun import CERO
from .cuenta import deuda_anterior

# Identidad del edificio para la boleta. EDITAR con los datos reales.
# Los campos opcionales (cuenta_pago, contacto) se muestran SOLO si tienen
# valor: se deja la estructura lista, sin inventar contenido.
EDIFICIO = {
    'nombre': 'Condominio Los Girasoles',   # EDITAR: nombre real del edificio
    'torre': 'Torre D',                      # EDITAR o dejar en '' si no aplica
    'logo_texto': 'C',                       # iniciales para el logo provisional
    'cuenta_pago': '',                       # ej. 'BCP 191-XXXXXXX-0-XX' (vacío = no se muestra)
    'contacto': '',                          # ej. 'Administración · 999 999 999' (vacío = no se muestra)
    'aviso': 'Su pago oportuno ayuda al mantenimiento y la buena convivencia del edificio.',
}


def datos_boleta(departamento, periodo):
    """Estructura de datos ÚNICA de la boleta (ReceiptData).

    Solo lee datos existentes (cargos del periodo + deuda anterior); no altera
    ningún cálculo. La consumen por igual la vista web, la impresión y el PDF.

    - Agua: un único importe (concepto AGUA). Sin lecturas ni tarifario.
    - Mantenimiento: agrupa el resto de conceptos del mes que existan realmente
      (mantenimiento, área común, gastos adicionales, cochera, multas, etc.).
    - Deuda anterior separada del total del mes.
    - Estado derivado de los datos (pagado / vencido / pendiente).
    """
    cargos = list(departamento.cargos.filter(periodo=periodo)
                  .select_related('concepto').prefetch_related('imputaciones'))

    items, mant_items, mant_total, agua_total = [], [], CERO, CERO
    saldo_mes, hay_vencido, hay_agua = CERO, False, False
    hoy = timezone.localdate()
    for c in sorted(cargos, key=lambda x: x.concepto.orden):
        if c.monto == 0:
            continue
        saldo_mes += c.saldo
        nombre = f'{c.concepto.nombre} ({c.descripcion})' if c.descripcion else c.concepto.nombre
        items.append({'codigo': c.concepto.codigo, 'nombre': nombre, 'monto': c.monto})
        if c.saldo > 0 and c.fecha_vencimiento < hoy:
            hay_vencido = True
        if c.concepto.codigo == Concepto.AGUA:
            agua_total += c.monto
            hay_agua = True
        else:
            mant_items.append({'nombre': c.concepto.nombre, 'monto': c.monto})
            mant_total += c.monto

    total_mes = mant_total + agua_total
    anterior = deuda_anterior(departamento, periodo)

    # Estado real (no inventado): pagado si no queda saldo del mes; vencido si
    # algún cargo del mes ya venció con saldo; si no, pendiente.
    if cargos and saldo_mes <= 0:
        estado, estado_tone = 'Pagado', 'success'
    elif hay_vencido:
        estado, estado_tone = 'Vencido', 'danger'
    else:
        estado, estado_tone = 'Pendiente de pago', 'warning'

    return {
        'edificio': EDIFICIO,
        'departamento': departamento,
        'periodo': periodo,
        'emision': min((c.fecha_emision for c in cargos), default=None),
        'vencimiento': min((c.fecha_vencimiento for c in cargos), default=None),
        'items': items,  # todas las líneas del mes en orden de concepto (agua incluida)
        'mant_items': mant_items,
        'mant_total': mant_total,
        'agua_total': agua_total,
        'hay_agua': hay_agua,
        'total_mes': total_mes,
        'anterior': anterior,
        'total_pagar': total_mes + anterior,
        'estado': estado,
        'estado_tone': estado_tone,
    }
