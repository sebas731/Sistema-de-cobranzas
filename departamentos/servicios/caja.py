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
    return apertura + pagos_prev + ing_prev - egr_prev


def _ingresos_por_concepto(anio, mes, inicio, sig):
    """Pagos del mes agrupados por concepto (Mantenimiento por mes de la deuda,
    meses anteriores, cuotas, etc.) + otros ingresos + por identificar."""
    pagos = (Pago.objects.filter(fecha_pago__gte=inicio, fecha_pago__lt=sig)
             .prefetch_related('imputaciones__cargo__concepto'))
    agg = defaultdict(lambda: CERO)
    a_cuenta = CERO
    for p in pagos:
        imputado = CERO
        for i in p.imputaciones.all():
            c, per = i.cargo.concepto, i.cargo.periodo
            if c.codigo == Concepto.MANTENIMIENTO:
                etq = 'Mantenimiento del mes' if (per.year, per.month) == (anio, mes) else 'Mantenimiento meses anteriores'
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

    otros_total = _suma(otros)
    por_ident_total = sum((m.monto for m in por_ident), CERO)
    egresos_total = _suma(egresos_qs)
    ingresos_total = pagos_total + otros_total + por_ident_total
    s_fin = s_ini + ingresos_total - egresos_total

    # Egresos agrupados por rubro, con su detalle.
    por_rubro = defaultdict(lambda: {'monto': CERO, 'movs': []})
    rubros_label = dict(MovimientoCaja.Rubro.choices)
    for m in egresos_qs:
        etq = rubros_label.get(m.rubro, 'Sin rubro')
        por_rubro[etq]['monto'] += m.monto
        por_rubro[etq]['movs'].append(m)
    egresos_filas = [{'rubro': k, 'monto': v['monto'], 'movs': v['movs']}
                     for k, v in sorted(por_rubro.items(), key=lambda x: -x[1]['monto'])]

    return {
        'anio': anio, 'mes': mes,
        'saldo_inicial': s_ini,
        'ingresos': _ingresos_por_concepto(anio, mes, inicio, sig),
        'ingresos_total': ingresos_total,
        'pagos_total': pagos_total,
        'por_identificar': por_ident,
        'por_identificar_total': por_ident_total,
        'egresos': egresos_filas,
        'egresos_total': egresos_total,
        'saldo_final': s_fin,
    }


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
