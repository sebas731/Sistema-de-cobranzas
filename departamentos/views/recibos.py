"""Recibos: boleta por depto/mes, recibo snapshot (estilo foto) y generación mensual."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import GenerarRecibosForm
from ..models import ConfiguracionCondominio, Departamento, Recibo
from ..servicios import datos_boleta, generar_cargos_mes
from ..servicios.comun import ordenar_tramos
from .comun import periodo_de_request


@login_required
def recibo_boleta(request, pk):
    """Boleta del departamento para un mes (cargos del mes + deuda anterior)."""
    departamento = get_object_or_404(Departamento, pk=pk)
    _, periodo = periodo_de_request(request)
    return render(request, 'departamentos/recibo_boleta.html', {
        'boleta': datos_boleta(departamento, periodo),
    })


def _estructura_tarifaria(version):
    """Tramos de la tarifa con el rango legible ('0 a 10', '50 a más')."""
    tramos, piso = [], Decimal('0')
    for t in ordenar_tramos(version.tramos.all(), 'consumo_hasta'):
        rango = f'{piso:g} a más' if t.consumo_hasta is None else f'{piso:g} a {t.consumo_hasta:g}'
        tramos.append({'rango': rango, 'agua': t.precio_agua, 'saneamiento': t.precio_saneamiento})
        if t.consumo_hasta is not None:
            piso = t.consumo_hasta
    return tramos


@login_required
def recibo_ver(request, pk):
    """Recibo de mantenimiento (réplica de la boleta física) desde el snapshot."""
    recibo = get_object_or_404(
        Recibo.objects.select_related('departamento', 'periodo_fac__tarifa_agua'), pk=pk)
    per = recibo.periodo_fac
    config = ConfiguracionCondominio.vigente()
    medios = list(config.medios_pago.filter(activo=True)) if config else []
    lineas = list(recibo.lineas.prefetch_related('sublineas').all())

    return render(request, 'departamentos/recibo_documento.html', {
        'recibo': recibo, 'per': per, 'config': config, 'medios': medios,
        'banco': next((m for m in medios if m.tipo == 'BANCO'), None),
        'billeteras': [m for m in medios if m.tipo in ('PLIN', 'YAPE')],
        'lineas': lineas,
        'tramos': _estructura_tarifaria(per.tarifa_agua) if per.tarifa_agua_id else [],
        'agua_comun': next((l.monto for l in lineas
                            if l.concepto and l.concepto.codigo == 'AREA_COMUN'), Decimal('0.00')),
        'agua_total': next((l.monto for l in lineas if l.grupo == 'AGUA'), Decimal('0.00')),
    })


@login_required
def generar_recibos(request):
    form = GenerarRecibosForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        periodo = form.periodo
        resumen = generar_cargos_mes(periodo, form.cleaned_data['dia_vencimiento'])
        if resumen['n'] == 0:
            messages.error(request, 'No hay departamentos activos.')
        else:
            messages.success(
                request,
                f"Cargos de {periodo:%m/%Y}: {resumen['creados']} creados, "
                f"{resumen['actualizados']} actualizados, "
                f"{resumen['omitidos_pagados']} ya pagados.")
        return redirect(f'{reverse("balance_mes")}?mes={periodo.month}&anio={periodo.year}')
    return render(request, 'departamentos/generar_recibos.html', {'form': form})
