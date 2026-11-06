"""Estado de caja real del edificio (ingresos, egresos y saldo por mes)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from ..forms import MESES_ES
from ..models import Departamento, MovimientoCaja
from ..servicios import asignar_por_identificar, estado_caja
from .comun import periodo_de_request, redirigir_al_periodo


@login_required
def estado_caja_vista(request):
    """Estado de caja del mes: saldo inicial, ingresos, egresos por rubro y saldo
    final. Permite asignar un depósito por identificar a un departamento."""
    form, periodo = periodo_de_request(request)

    if request.method == 'POST' and request.POST.get('accion') == 'asignar':
        mov = get_object_or_404(MovimientoCaja, pk=request.POST.get('movimiento'),
                                tipo=MovimientoCaja.Tipo.POR_IDENTIFICAR,
                                estado=MovimientoCaja.Estado.VIGENTE)
        dep = get_object_or_404(Departamento, pk=request.POST.get('departamento'))
        asignar_por_identificar(mov, dep, usuario=request.user)
        messages.success(request, f'Depósito de S/ {mov.monto:.2f} asignado al depto {dep.numero_domicilio}.')
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
    })


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
