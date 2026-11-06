"""Estado de caja real del edificio (ingresos, egresos y saldo por mes)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from ..forms import MESES_ES
from ..models import Departamento, MovimientoCaja
from ..servicios import asignar_por_identificar, cobrado_vs_pagado, estado_caja, morosidad
from ..servicios.cuenta import deudores as servicio_deudores
from .comun import periodo_de_request, redirigir_al_periodo


def _a_decimal(txt):
    try:
        return Decimal(str(txt).replace(',', '').strip())
    except (ValueError, ArithmeticError):
        return None


@login_required
def estado_caja_vista(request):
    """Estado de caja del mes: saldo inicial, ingresos, egresos por rubro y saldo
    final. Permite asignar un depósito por identificar y registrar movimientos."""
    form, periodo = periodo_de_request(request)

    if request.method == 'POST' and request.POST.get('accion') == 'asignar':
        mov = get_object_or_404(MovimientoCaja, pk=request.POST.get('movimiento'),
                                tipo=MovimientoCaja.Tipo.POR_IDENTIFICAR,
                                estado=MovimientoCaja.Estado.VIGENTE)
        dep = get_object_or_404(Departamento, pk=request.POST.get('departamento'))
        asignar_por_identificar(mov, dep, usuario=request.user)
        messages.success(request, f'Depósito de S/ {mov.monto:.2f} asignado al depto {dep.numero_domicilio}.')
        return redirigir_al_periodo(request, periodo)

    if request.method == 'POST' and request.POST.get('accion') == 'registrar':
        monto = _a_decimal(request.POST.get('monto'))
        tipo = request.POST.get('tipo')
        fecha = request.POST.get('fecha')
        desc = (request.POST.get('descripcion') or '').strip()
        if monto and monto > 0 and tipo in MovimientoCaja.Tipo.values and fecha and desc:
            MovimientoCaja.objects.create(
                fecha=fecha, tipo=tipo, rubro=request.POST.get('rubro', ''),
                descripcion=desc[:200], monto=monto,
                comprobante=(request.POST.get('comprobante') or '').strip()[:100],
                creado_por=request.user)
            messages.success(request, f'Movimiento registrado: {desc} S/ {monto:.2f}.')
        else:
            messages.error(request, 'Revisa los datos del movimiento (fecha, tipo, monto > 0, descripción).')
        return redirigir_al_periodo(request, periodo)

    datos = estado_caja(periodo.year, periodo.month)

    if request.GET.get('formato') == 'xlsx':
        return _exportar_xlsx(datos, periodo)

    saldo_banco = request.GET.get('banco')
    try:
        diferencia = datos['saldo_final'] - Decimal(saldo_banco) if saldo_banco else None
    except (ValueError, ArithmeticError):
        diferencia = None

    return render(request, 'departamentos/caja/estado.html', {
        'periodo_form': form, 'periodo': periodo, 'datos': datos,
        'saldo_banco': saldo_banco, 'diferencia': diferencia,
        'departamentos': Departamento.objects.filter(activo=True).order_by('numero_domicilio'),
        'tipos': MovimientoCaja.Tipo.choices, 'rubros': MovimientoCaja.Rubro.choices,
        'hoy': periodo,
    })


@login_required
def cobrado_vs_pagado_vista(request):
    """Comparación por rubro: lo cobrado a los vecinos vs lo pagado de la caja."""
    form, periodo = periodo_de_request(request)
    return render(request, 'departamentos/caja/cobrado_pagado.html', {
        'periodo_form': form, 'periodo': periodo,
        'datos': cobrado_vs_pagado(periodo.year, periodo.month)})


@login_required
def morosidad_vista(request):
    """Cobranza y morosidad del recibo del mes + lista de deudores."""
    form, periodo = periodo_de_request(request)
    return render(request, 'departamentos/caja/morosidad.html', {
        'periodo_form': form, 'periodo': periodo,
        'datos': morosidad(periodo.year, periodo.month),
        'deudores': servicio_deudores(periodo)})


def _exportar_xlsx(datos, periodo):
    """Estado de caja del mes a Excel (una hoja: saldo inicial, ingresos, egresos,
    saldo final)."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = 'Estado de caja'
    ws.append([f'Estado de caja — {MESES_ES[periodo.month]} {periodo.year}'])
    ws.append([])
    ws.append(['Saldo inicial', float(datos['saldo_inicial'])])
    ws.append([])
    ws.append(['INGRESOS', ''])
    for f in datos['ingresos']:
        ws.append([f['concepto'], float(f['monto'])])
    for m in datos['por_identificar']:
        ws.append([f'Por identificar · {m.descripcion}', float(m.monto)])
    ws.append(['Total ingresos', float(datos['ingresos_total'])])
    ws.append([])
    ws.append(['EGRESOS', ''])
    for f in datos['egresos']:
        ws.append([f['rubro'], float(f['monto'])])
    ws.append(['Total egresos', float(datos['egresos_total'])])
    ws.append([])
    ws.append(['SALDO FINAL', float(datos['saldo_final'])])

    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename=caja_{periodo.year}_{periodo.month:02d}.xlsx'
    wb.save(resp)
    return resp
