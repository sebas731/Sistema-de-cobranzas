"""Boleta de cobranza (documento único web = impresión = PDF)."""
from django.utils import timezone

from ..models import Concepto, LecturaMedidor
from .agua import costo_agua
from .comun import CERO, redondear
from .cuenta import deuda_anterior
from .gastos import prorrateo_mes

_RECUR_EXCEL = {'MANTENIMIENTO', 'AGUA', 'AREA_COMUN', 'COCHERA'}

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

    # Cuota/multa/mora: suma a la línea del mes lo pendiente de ESE concepto de
    # meses anteriores (ej. cuota de agosto no pagada + la de septiembre). El
    # resto de la deuda (mantenimiento, agua...) queda en "deuda anterior".
    APARTE = {'CUOTA', 'MULTA', 'MORA'}
    prev_aparte, prev_otros = {}, CERO
    for c in departamento.cargos.all():
        if c.periodo >= periodo or c.saldo <= 0:
            continue
        if c.concepto.codigo in APARTE:
            prev_aparte[c.concepto.codigo] = prev_aparte.get(c.concepto.codigo, CERO) + c.saldo
        else:
            prev_otros += c.saldo
    nombres = {c.codigo: c.nombre for c in Concepto.objects.filter(codigo__in=APARTE)}
    for codigo, saldo in prev_aparte.items():
        it = next((i for i in items if i['codigo'] == codigo), None)
        if it:
            it['monto'] += saldo
        else:
            items.append({'codigo': codigo, 'nombre': nombres.get(codigo, codigo), 'monto': saldo})

    # Totales finales (ya con lo anterior de cuota/multa/mora sumado a su línea).
    agua_total = sum((i['monto'] for i in items if i['codigo'] == Concepto.AGUA), CERO)
    mant_items = [{'nombre': i['nombre'], 'monto': i['monto']} for i in items if i['codigo'] != Concepto.AGUA]
    mant_total = sum((i['monto'] for i in items if i['codigo'] != Concepto.AGUA), CERO)
    hay_agua = agua_total > 0
    total_mes = mant_total + agua_total
    anterior = prev_otros

    # Total estilo Excel: recalcula mantenimiento+área+agua+cochera EN CRUDO y
    # redondea 1 vez (suma → redondea), en vez de sumar cargos ya redondeados.
    # Guardrail: si diverge > 5 céntimos (meses cargados a mano), no ajusta.
    pr = prorrateo_mes(periodo)
    lect = LecturaMedidor.objects.filter(departamento=departamento, periodo=periodo).first()
    m3 = lect.m3_consumidos if lect else CERO
    sys_recur = sum((c.monto for c in cargos if c.concepto.codigo in _RECUR_EXCEL), CERO)
    excel_recur = redondear(pr['mantenimiento_raw'] + pr['area_comun_raw']
                            + costo_agua(m3, redondeo=False) + (departamento.cochera_monto or CERO))
    delta = excel_recur - sys_recur
    if abs(delta) <= redondear('0.05'):
        total_mes += delta

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
