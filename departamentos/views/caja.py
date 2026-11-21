"""Estado de caja real del edificio (ingresos, egresos y saldo por mes)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

import calendar

from ..forms import MESES_ES
from ..models import ConfiguracionCondominio, Departamento, MovimientoCaja
from ..servicios import (
    asignar_por_identificar, cobranza_mensual, cobrado_vs_pagado,
    deuda_anterior_resumen, estado_caja, estado_caja_rango, morosidad, multas_moras_resumen,
)
from ..servicios.caja import corte_admin
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
def estado_cuenta(request):
    """Estado de cuenta formal del fondo de administración y mantenimiento,
    estilo contable (Saldo inicial → Ingresos → Egresos → Saldo final).
    Imprimible a PDF. Modo mes (default) o `?modo=anio` (todo el año, con la
    fecha actual como límite si el año es el corriente)."""
    from datetime import date
    form, periodo = periodo_de_request(request)
    hoy = date.today()
    anual = request.GET.get('modo') == 'anio'
    if anual:
        # El "año" de la administración arranca en su fecha de inicio (config).
        desde = max(date(periodo.year, 1, 1), corte_admin())
        hasta = min(hoy, date(periodo.year, 12, 31))
    else:
        desde = date(periodo.year, periodo.month, 1)
        ultimo = calendar.monthrange(periodo.year, periodo.month)[1]
        hasta = min(hoy, date(periodo.year, periodo.month, ultimo))
    # Balance personalizable: fuente del agua + textos editables del documento.
    g = request.GET
    fuente_agua = 'medidores' if g.get('agua') == 'medidores' else 'sedapal'
    cfg = ConfiguracionCondominio.vigente()
    nombre = cfg.nombre if cfg else 'Condominio'
    doc = {
        'titulo': (g.get('titulo') or '').strip() or f'Balance Mensual {nombre}',
        'subtitulo': (g.get('subtitulo') or '').strip() or 'Estado de cuenta de fondo de administración y mantenimiento.',
        'lbl_saldo_ini': (g.get('lbl_saldo_ini') or '').strip() or 'Saldo en CTA',
        'lbl_ingresos': (g.get('lbl_ingresos') or '').strip() or 'INGRESOS',
        'lbl_egresos': (g.get('lbl_egresos') or '').strip() or 'EGRESOS',
        'lbl_saldo_fin': (g.get('lbl_saldo_fin') or '').strip() or 'Saldo final en CTA',
        'firmas': g.get('firmas', '1') != '0',
        'firma1': (g.get('firma1') or '').strip() or (cfg.firma_administracion if cfg and cfg.firma_administracion else 'Administración.'),
        'firma2': (g.get('firma2') or '').strip() or (cfg.firma_vigilancia if cfg and cfg.firma_vigilancia else 'Presidenta de Comité de Vigilancia.'),
    }
    datos = estado_caja_rango(desde, hasta, fuente_agua=fuente_agua)
    ingresos = list(datos['ingresos'])
    if datos['por_identificar_total']:
        ingresos.append({'concepto': 'Cuotas sin identificar', 'monto': datos['por_identificar_total']})
    return render(request, 'departamentos/caja/estado_cuenta.html', {
        'periodo_form': form, 'periodo': periodo, 'datos': datos, 'ingresos': ingresos,
        'desde': desde, 'hasta': hasta, 'anual': anual,
        'fuente_agua': fuente_agua, 'doc': doc, 'g': g,
        'config': cfg,
    })


@login_required
def detalle_cobranza(request):
    """Detalle mensual de cobranza (PDF 2): una fila por mes con emitido, cobrado,
    morosidad, % y egreso; más el bloque de deuda anterior (heredada). Imprimible.
    Modo mes (default) o `?modo=anio` (desde el inicio de la administración)."""
    from datetime import date
    form, periodo = periodo_de_request(request)
    hoy = date.today()
    anual = request.GET.get('modo') == 'anio'
    if anual:
        desde = max(date(periodo.year, 1, 1), corte_admin())
        hasta = min(hoy, date(periodo.year, 12, 31))
    else:
        desde = date(periodo.year, periodo.month, 1)
        ultimo = calendar.monthrange(periodo.year, periodo.month)[1]
        hasta = min(hoy, date(periodo.year, periodo.month, ultimo))
    filas = cobranza_mensual(desde, hasta)
    totales = {
        'emitido': sum((f['emitido'] for f in filas), Decimal('0.00')),
        'cobrado': sum((f['cobrado'] for f in filas), Decimal('0.00')),
        'morosidad': sum((f['morosidad'] for f in filas), Decimal('0.00')),
        'egreso': sum((f['egreso'] for f in filas), Decimal('0.00')),
        'saldo': sum((f['saldo'] for f in filas), Decimal('0.00')),
    }
    totales['pct'] = (totales['cobrado'] / totales['emitido'] * 100) if totales['emitido'] else Decimal('0.00')
    return render(request, 'departamentos/caja/detalle_cobranza.html', {
        'periodo_form': form, 'periodo': periodo, 'anual': anual,
        'desde': desde, 'hasta': hasta, 'filas': filas, 'totales': totales,
        'deuda_anterior': deuda_anterior_resumen(),
        'multas_moras': multas_moras_resumen(),
        'config': ConfiguracionCondominio.vigente(),
    })


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
