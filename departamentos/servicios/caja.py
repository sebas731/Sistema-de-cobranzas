"""Estado de caja real: saldo por mes a partir de pagos de vecinos y de los
MovimientoCaja (saldo inicial, otros ingresos, depósitos por identificar y
egresos pagados). NO toca la deuda ni los cargos; solo lee y suma.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from ..models import Concepto, MovimientoCaja, Pago
from .comun import sumar_meses
from .cuenta import registrar_pago

CERO = Decimal('0.00')
Ti = MovimientoCaja.Tipo


def _vigentes():
    return MovimientoCaja.objects.filter(estado=MovimientoCaja.Estado.VIGENTE)


def _suma(qs):
    return qs.aggregate(s=Sum('monto'))['s'] or CERO


# --- Gastos del mes como egresos --------------------------------------------
# Los gastos del mes (GastoEdificio: vigilancia, limpieza, luz, agua común, …)
# son el EGRESO real que paga el condominio. El recibo que se cobra a los
# vecinos por esos mismos gastos es el INGRESO. Aquí los leemos como egresos
# para el estado de caja/cuenta, sin tocar cargos ni deudas.

def _gastos_mes_qs(desde, hasta):
    """GastoEdificio de MANTENIMIENTO cuyo periodo (día 1 del mes) cae dentro del
    rango de meses [mes de `desde`, mes de `hasta`]. Excluye los 'adicionales'
    (cuotas extraordinarias, multas, etc.): esos son cobros a los vecinos, no un
    egreso de servicios del condominio."""
    from ..models import GastoEdificio
    ini = date(desde.year, desde.month, 1)
    fin = date(hasta.year, hasta.month, 1)
    return (GastoEdificio.objects.filter(periodo__gte=ini, periodo__lte=fin)
            .exclude(categoria=GastoEdificio.Categoria.ADICIONAL))


def _gastos_mes_antes(fecha):
    """Total de gastos de MANTENIMIENTO de periodos ANTERIORES al mes de `fecha`."""
    from ..models import GastoEdificio
    return _suma(GastoEdificio.objects.filter(periodo__lt=date(fecha.year, fecha.month, 1))
                 .exclude(categoria=GastoEdificio.Categoria.ADICIONAL))


def _egresos_por_mes(egresos_qs, gastos_qs):
    """Egresos agrupados por MES, etiquetados 'Egreso Julio', 'Egreso Agosto'…
    (para la vista de rango/año). El detalle de cada mes queda en 'movs'."""
    from ..forms import MESES_ES
    por_mes = defaultdict(lambda: {'monto': CERO, 'movs': []})
    for g in gastos_qs:
        k = (g.periodo.year, g.periodo.month)
        por_mes[k]['monto'] += g.monto
        por_mes[k]['movs'].append(
            {'fecha': g.periodo, 'ref': g.descripcion, 'monto': f'{g.monto:.2f}', 'nota': ''})
    for m in egresos_qs:
        k = (m.fecha.year, m.fecha.month)
        por_mes[k]['monto'] += m.monto
        por_mes[k]['movs'].append(m)
    return [{'rubro': f'Egreso {MESES_ES[mth]}', 'monto': v['monto'], 'movs': v['movs']}
            for (yr, mth), v in sorted(por_mes.items())]


def _egresos_filas(egresos_qs, gastos_qs):
    """Combina egresos de caja (MovimientoCaja, por rubro) con los gastos del mes
    (GastoEdificio, por descripción) en una sola lista de filas para la vista."""
    por_etq = defaultdict(lambda: {'monto': CERO, 'movs': []})
    rubros_label = dict(MovimientoCaja.Rubro.choices)
    for m in egresos_qs:
        etq = rubros_label.get(m.rubro, 'Sin rubro')
        por_etq[etq]['monto'] += m.monto
        por_etq[etq]['movs'].append(m)
    for g in gastos_qs:
        por_etq[g.descripcion]['monto'] += g.monto
        por_etq[g.descripcion]['movs'].append(
            {'fecha': g.periodo, 'ref': 'Gasto del mes', 'monto': f'{g.monto:.2f}', 'nota': ''})
    return [{'rubro': k, 'monto': v['monto'], 'movs': v['movs']}
            for k, v in sorted(por_etq.items(), key=lambda x: -x[1]['monto'])]


def saldo_inicial(anio, mes):
    """Saldo con el que arranca el mes = saldo final del mes anterior.

    El SALDO_INICIAL (caja recibida de la junta anterior) cuenta como apertura
    del mes en que se entregó, no como un ingreso del mes."""
    inicio = date(anio, mes, 1)
    sig = sumar_meses(inicio, 1)
    # Apertura: todo SALDO_INICIAL entregado hasta este mes inclusive.
    apertura = _suma(_vigentes().filter(tipo=Ti.SALDO_INICIAL, fecha__lt=sig))
    # Flujo neto (pagos + otros ingresos − egresos) de los meses ANTERIORES.
    pagos_prev = Pago.objects.filter(fecha_pago__lt=inicio).aggregate(s=Sum('monto'))['s'] or CERO
    ing_prev = _suma(_vigentes().filter(tipo__in=[Ti.INGRESO_OTRO, Ti.POR_IDENTIFICAR], fecha__lt=inicio))
    egr_prev = _suma(_vigentes().filter(tipo=Ti.EGRESO, fecha__lt=inicio))
    gastos_prev = _gastos_mes_antes(inicio)
    return apertura + pagos_prev + ing_prev - egr_prev - gastos_prev


def _ingresos_por_concepto(inicio, sig, ref=None):
    """Pagos del rango agrupados por concepto (+ otros ingresos). `ref=(anio,mes)`
    separa 'Mantenimiento del mes' vs 'meses anteriores'; ref=None etiqueta cada
    mantenimiento por su mes (Mto. Julio…), útil para rangos largos."""
    pagos = (Pago.objects.filter(fecha_pago__gte=inicio, fecha_pago__lt=sig)
             .prefetch_related('imputaciones__cargo__concepto'))
    agg = defaultdict(lambda: CERO)
    a_cuenta = CERO
    from ..forms import MESES_ES
    for p in pagos:
        imputado = CERO
        for i in p.imputaciones.all():
            c, per = i.cargo.concepto, i.cargo.periodo
            if c.codigo == Concepto.MANTENIMIENTO:
                if ref is None:
                    etq = f'Mto. {MESES_ES[per.month]}'
                else:
                    etq = 'Mantenimiento del mes' if (per.year, per.month) == ref else 'Mantenimiento meses anteriores'
            else:
                etq = c.nombre
            agg[etq] += i.monto
            imputado += i.monto
        a_cuenta += max(p.monto - imputado, CERO)
    if a_cuenta:
        agg['A cuenta (sin imputar)'] += a_cuenta
    # Otros ingresos y por identificar (de MovimientoCaja).
    for mov in _vigentes().filter(tipo=Ti.INGRESO_OTRO, fecha__gte=inicio, fecha__lt=sig):
        agg[mov.descripcion or 'Otro ingreso'] += mov.monto
    filas = [{'concepto': k, 'monto': v} for k, v in sorted(agg.items(), key=lambda x: -x[1])]
    return filas


def estado_caja(anio, mes):
    """Estado de caja del mes: saldo inicial, ingresos (pagos + otros), egresos
    por rubro, saldo final y lista de depósitos por identificar."""
    inicio = date(anio, mes, 1)
    sig = sumar_meses(inicio, 1)
    rango = {'fecha__gte': inicio, 'fecha__lt': sig}

    s_ini = saldo_inicial(anio, mes)

    pagos_total = Pago.objects.filter(fecha_pago__gte=inicio, fecha_pago__lt=sig).aggregate(
        s=Sum('monto'))['s'] or CERO
    otros = _vigentes().filter(tipo=Ti.INGRESO_OTRO, **rango)
    por_ident = list(_vigentes().filter(tipo=Ti.POR_IDENTIFICAR, **rango))
    egresos_qs = _vigentes().filter(tipo=Ti.EGRESO, **rango)

    gastos_qs = list(_gastos_mes_qs(inicio, inicio))

    otros_total = _suma(otros)
    por_ident_total = sum((m.monto for m in por_ident), CERO)
    egresos_total = _suma(egresos_qs) + sum((g.monto for g in gastos_qs), CERO)
    ingresos_total = pagos_total + otros_total + por_ident_total
    s_fin = s_ini + ingresos_total - egresos_total

    # Egresos: gastos del mes (lo que paga el condominio) + egresos de caja.
    egresos_filas = _egresos_filas(egresos_qs, gastos_qs)

    return {
        'anio': anio, 'mes': mes,
        'saldo_inicial': s_ini,
        'ingresos': _ingresos_por_concepto(inicio, sig, ref=(anio, mes)),
        'ingresos_total': ingresos_total,
        'pagos_total': pagos_total,
        'por_identificar': por_ident,
        'por_identificar_total': por_ident_total,
        'egresos': egresos_filas,
        'egresos_total': egresos_total,
        'saldo_final': s_fin,
    }


def estado_caja_rango(desde, hasta):
    """Estado de caja entre dos fechas (inclusive), p.ej. todo el año o desde el
    inicio hasta hoy. La caja inicial (SALDO_INICIAL) se toma como apertura
    (génesis), así SIEMPRE aparece aunque sea de mitad de periodo."""
    from datetime import timedelta
    sig = hasta + timedelta(days=1)
    # Apertura = toda la caja inicial hasta 'hasta' + flujo neto antes de 'desde'.
    s_ini = (_suma(_vigentes().filter(tipo=Ti.SALDO_INICIAL, fecha__lte=hasta))
             + (Pago.objects.filter(fecha_pago__lt=desde).aggregate(s=Sum('monto'))['s'] or CERO)
             + _suma(_vigentes().filter(tipo__in=[Ti.INGRESO_OTRO, Ti.POR_IDENTIFICAR], fecha__lt=desde))
             - _suma(_vigentes().filter(tipo=Ti.EGRESO, fecha__lt=desde))
             - _gastos_mes_antes(desde))

    rango = {'fecha__gte': desde, 'fecha__lt': sig}
    pagos_total = Pago.objects.filter(fecha_pago__gte=desde, fecha_pago__lt=sig).aggregate(
        s=Sum('monto'))['s'] or CERO
    por_ident = list(_vigentes().filter(tipo=Ti.POR_IDENTIFICAR, **rango))
    egresos_qs = _vigentes().filter(tipo=Ti.EGRESO, **rango)
    gastos_qs = list(_gastos_mes_qs(desde, hasta))
    otros_total = _suma(_vigentes().filter(tipo=Ti.INGRESO_OTRO, **rango))
    por_ident_total = sum((m.monto for m in por_ident), CERO)
    egresos_total = _suma(egresos_qs) + sum((g.monto for g in gastos_qs), CERO)
    ingresos_total = pagos_total + otros_total + por_ident_total

    egresos_filas = _egresos_por_mes(egresos_qs, gastos_qs)

    return {
        'desde': desde, 'hasta': hasta,
        'saldo_inicial': s_ini,
        'ingresos': _ingresos_por_concepto(desde, sig, ref=None),
        'ingresos_total': ingresos_total, 'pagos_total': pagos_total,
        'por_identificar': por_ident, 'por_identificar_total': por_ident_total,
        'egresos': egresos_filas, 'egresos_total': egresos_total,
        'saldo_final': s_ini + ingresos_total - egresos_total,
    }


# Mapeo descripción -> rubro, para comparar lo COBRADO (gastos fijos/del mes) con
# lo PAGADO (egresos de caja) sin tener que migrar GastoFijo/GastoEdificio.
_RUBRO_KW = [
    ('material', MovimientoCaja.Rubro.LIMPIEZA_MATERIAL),
    ('limpieza', MovimientoCaja.Rubro.LIMPIEZA_PERSONAL),
    ('vigilancia', MovimientoCaja.Rubro.VIGILANCIA),
    ('ascensor', MovimientoCaja.Rubro.ASCENSORES),
    ('administ', MovimientoCaja.Rubro.ADMINISTRATIVOS),
    ('internet', MovimientoCaja.Rubro.INTERNET),
    ('acopio', MovimientoCaja.Rubro.ACOPIO),
    ('pluz', MovimientoCaja.Rubro.LUZ), ('luz', MovimientoCaja.Rubro.LUZ),
    ('energ', MovimientoCaja.Rubro.LUZ), ('electric', MovimientoCaja.Rubro.LUZ),
    ('agua', MovimientoCaja.Rubro.AGUA), ('sedapal', MovimientoCaja.Rubro.AGUA),
    ('banc', MovimientoCaja.Rubro.BANCARIOS), ('itf', MovimientoCaja.Rubro.BANCARIOS),
    ('devoluc', MovimientoCaja.Rubro.DEVOLUCIONES),
    ('repar', MovimientoCaja.Rubro.MANTENIMIENTO), ('manten', MovimientoCaja.Rubro.MANTENIMIENTO),
]


def _rubro_de(descripcion):
    d = (descripcion or '').lower()
    for kw, rubro in _RUBRO_KW:
        if kw in d:
            return rubro
    return MovimientoCaja.Rubro.OTROS


def cobrado_vs_pagado(anio, mes):
    """Por rubro: lo COBRADO a los vecinos (gastos fijos + del mes + agua facturada)
    frente a lo PAGADO (egresos de caja). Marca en rojo las diferencias > 10%."""
    from ..models import Cargo, Concepto, GastoEdificio, GastoFijo
    inicio = date(anio, mes, 1)
    sig = sumar_meses(inicio, 1)
    labels = dict(MovimientoCaja.Rubro.choices)

    cobrado = defaultdict(lambda: CERO)
    for g in GastoFijo.objects.filter(activo=True):
        cobrado[_rubro_de(g.descripcion)] += g.monto
    for g in GastoEdificio.objects.filter(periodo=inicio):
        cobrado[_rubro_de(g.descripcion)] += g.monto
    # Agua facturada del mes = cargos AGUA + ÁREA COMÚN (ambos van a SEDAPAL).
    agua = Cargo.objects.filter(periodo=inicio,
                                concepto__codigo__in=[Concepto.AGUA, 'AREA_COMUN']
                                ).aggregate(s=Sum('monto'))['s'] or CERO
    cobrado[MovimientoCaja.Rubro.AGUA] += agua

    pagado = defaultdict(lambda: CERO)
    for m in _vigentes().filter(tipo=Ti.EGRESO, fecha__gte=inicio, fecha__lt=sig):
        pagado[m.rubro or MovimientoCaja.Rubro.OTROS] += m.monto

    filas = []
    for rubro in set(cobrado) | set(pagado):
        c, p = cobrado[rubro], pagado[rubro]
        dif = c - p
        base = max(abs(c), abs(p)) or CERO
        alerta = base > 0 and abs(dif) / base > Decimal('0.10')
        filas.append({'rubro': labels.get(rubro, rubro), 'cobrado': c, 'pagado': p,
                      'diferencia': dif, 'alerta': alerta})
    filas.sort(key=lambda x: -(abs(x['diferencia'])))
    return {'filas': filas,
            'cobrado_total': sum((f['cobrado'] for f in filas), CERO),
            'pagado_total': sum((f['pagado'] for f in filas), CERO)}


def morosidad(anio, mes):
    """Del recibo de un mes: emitido (cargos del periodo), cobrado (imputaciones a
    esos cargos), pendiente y % cobrado. Un recibo emitido es cuenta por cobrar,
    NO ingreso."""
    from ..models import Cargo, Imputacion
    periodo = date(anio, mes, 1)
    cargos = Cargo.objects.filter(periodo=periodo)
    emitido = cargos.aggregate(s=Sum('monto'))['s'] or CERO
    cobrado = Imputacion.objects.filter(cargo__periodo=periodo).aggregate(s=Sum('monto'))['s'] or CERO
    pendiente = emitido - cobrado
    pct = (cobrado / emitido * 100) if emitido else CERO
    return {'periodo': periodo, 'emitido': emitido, 'cobrado': cobrado,
            'pendiente': pendiente, 'pct': pct}


@transaction.atomic
def asignar_por_identificar(movimiento, departamento, usuario=None):
    """Asigna un depósito POR_IDENTIFICAR a un departamento: crea el Pago (misma
    fecha y monto, imputado a sus cargos) y ANULA el movimiento, todo en una
    transacción. El total de ingresos del mes no cambia (deja de contar el
    POR_IDENTIFICAR y pasa a contar el Pago, mismo monto y mes)."""
    if movimiento.tipo != Ti.POR_IDENTIFICAR:
        raise ValueError('Solo se asignan movimientos POR_IDENTIFICAR.')
    if not movimiento.vigente:
        raise ValueError('El movimiento ya fue anulado.')
    pago = registrar_pago(departamento, movimiento.monto,
                          metodo='Depósito identificado',
                          nota=f'Caja #{movimiento.pk}: {movimiento.descripcion}'[:200])
    # El ingreso se cuenta en la fecha del depósito, no en la de hoy.
    Pago.objects.filter(pk=pago.pk).update(fecha_pago=movimiento.fecha)
    pago.fecha_pago = movimiento.fecha
    movimiento.anular(f'Asignado a dpto {departamento.numero_domicilio}', usuario)
    return pago
