"""Panel del mes (tabla tipo Excel con cobro inline) y Balance del mes."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .. import filtros as F
from ..models import Departamento, LecturaMedidor
from ..servicios import (
    COLUMNAS_MES, a_decimal, cargos_mes_por_departamento, desglose_gastos_mes,
    prorrateo_mes, registrar_pago,
)
from .comun import periodo_de_request, redirigir_al_periodo

CERO = Decimal('0.00')


def _sumar(filas, claves):
    return {k: sum((f[k] for f in filas), CERO) for k in claves}


@login_required
def panel(request):
    """Panel mensual tipo hoja de cálculo: una fila por departamento con sus
    conceptos, lo pagado y el saldo, con KPIs arriba. Desde aquí se registra un
    pago o se abre el recibo sin cambiar de pantalla. Pensado para la usuaria
    que viene de Excel: ver todo de un vistazo, sin formularios ni pestañas."""
    form, periodo = periodo_de_request(request)

    # Registrar un pago del mes sin salir del panel.
    if request.method == 'POST' and request.POST.get('accion') == 'pago':
        dep = get_object_or_404(Departamento, pk=request.POST.get('departamento'))
        monto = a_decimal(request.POST.get('monto'), Decimal('0'))
        if monto > 0:
            registrar_pago(dep, monto, metodo=request.POST.get('metodo', ''), periodo=periodo,
                           con_mora_anterior=True)
            messages.success(request, f'Pago de S/ {monto:.2f} registrado para el depto {dep.numero_domicilio}.')
        else:
            messages.error(request, 'Ingresa un monto mayor a 0.')
        return redirigir_al_periodo(request, periodo)

    campos = F.con_valores(request, [
        F.busqueda('q', 'Buscar depto o propietario...'),
        F.select('estado', 'Estado', [('', 'Todos'), ('deuda', 'Con deuda'), ('aldia', 'Al día')]),
        F.rango('total', 'Total', sufijo='S/'),
        F.rango('saldo', 'Saldo', sufijo='S/'),
    ])
    estado = request.GET.get('estado', '')
    filas = [f for f in cargos_mes_por_departamento(periodo, con_mora_anterior=True)
             if F.coincide_texto(request, 'q', f['departamento'].numero_domicilio, f['departamento'].propietario)
             and (estado != 'deuda' or f['saldo'] > 0)
             and (estado != 'aldia' or f['saldo'] <= 0)
             and F.en_rango(f['total'], request, 'total')
             and F.en_rango(f['saldo'], request, 'saldo')]


    # filas JSON

    filas_json = [  {

    'pk': item['departamento'].pk,
    'numero': item['departamento'].numero_domicilio,
    'propietario': item['departamento'].propietario,
    'mantenimiento': float(item['MANTENIMIENTO']),
    'agua': float(item['AGUA']),
    'area_comun': float(item['AREA_COMUN']),
    'cochera': float(item['COCHERA']),
    'cuota': float(item['CUOTA']),
    'multa': float(item['MULTA']),
    'mora': float(item['MORA']),
    'otros': float(item['extras'] - item['CUOTA'] - item['MULTA'] - item['MORA']),
    'total': float(item['total']),
    'pagado': float(item['pagado']),
    'saldo': float(item['saldo']),
    'cobrar': float(item['saldo']),   

    } for item in filas]

    return render(request, 'departamentos/panel/panel.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,'filas_json':filas_json,
        'tot': _sumar(filas, COLUMNAS_MES + ('extras', 'total', 'pagado', 'saldo')),
        'n_deudores': sum(1 for f in filas if f['saldo'] > 0),
        'n_pagados': sum(1 for f in filas if f['total'] > 0 and f['saldo'] <= 0),
        'n_deptos': len(filas),
        'filtros': campos, 'filter_hidden': {'mes': periodo.month, 'anio': periodo.year},
        'filter_reset_url': f'{request.path}?mes={periodo.month}&anio={periodo.year}',
    })


@login_required
def balance_mes(request):
    """Balance de un mes: totales (pagos, agua), gastos itemizados y por depto."""
    form, periodo = periodo_de_request(request)
    filas = cargos_mes_por_departamento(periodo)
    tot = _sumar(filas, ('total', 'pagado', 'AGUA'))
    tot_m3 = sum((l.m3_consumidos for l in LecturaMedidor.objects.filter(periodo=periodo)), CERO)

    return render(request, 'departamentos/panel/balance_mes.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,
        'tot_facturado': tot['total'], 'tot_pagado': tot['pagado'],
        'tot_pendiente': tot['total'] - tot['pagado'],
        'tot_agua': tot['AGUA'], 'tot_m3': tot_m3,
        'datos': prorrateo_mes(periodo), 'gastos': desglose_gastos_mes(periodo),
    })
