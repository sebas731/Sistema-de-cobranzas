"""Recibos: boleta por depto/mes, recibo snapshot (estilo foto) y generación mensual.

Ambas vistas pintan el MISMO documento (recibos/documento.html, réplica del
recibo físico). Cada una arma el contexto `r` desde su fuente de datos:
- recibo_ver: el snapshot Recibo (lecturas, desglose SEDAPAL, A/B).
- recibo_boleta: el snapshot del mes si existe; si no, los cargos del mes.
Las secciones sin datos en el sistema quedan en None y no se muestran.
"""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import GenerarRecibosForm
from ..models import (
    ConfiguracionCondominio, Departamento, LecturaMedidor, PeriodoFacturacion, Recibo,
    TarifaAguaVersion,
)
from ..servicios import datos_boleta, generar_cargos_mes
from ..servicios.comun import ordenar_tramos
from .comun import periodo_de_request


def _estructura_tarifaria(version):
    """Tramos de la tarifa con el rango legible ('0 a 10', '50 a más')."""
    def m3(d):  # 10.00 → '10', 2.50 → '2.5'
        s = f'{d:f}'
        return s.rstrip('0').rstrip('.') if '.' in s else s

    tramos, piso = [], Decimal('0')
    for t in ordenar_tramos(version.tramos.all(), 'consumo_hasta'):
        rango = f'{m3(piso)} a más' if t.consumo_hasta is None else f'{m3(piso)} a {m3(t.consumo_hasta)}'
        tramos.append({'rango': rango, 'agua': t.precio_agua, 'saneamiento': t.precio_saneamiento})
        if t.consumo_hasta is not None:
            piso = t.consumo_hasta
    return tramos


def _tarifa_vigente(periodo):
    return (TarifaAguaVersion.objects.filter(activo=True, vigencia_desde__lte=periodo)
            .filter(Q(vigencia_hasta__isnull=True) | Q(vigencia_hasta__gte=periodo)).first())


def _edificio(per, agua_comun):
    """Detalle consumo edificio (A/B) solo si el periodo lo tiene cargado."""
    if not per or not (per.sedapal_total_a or per.consumo_dptos_b):
        return None
    # Separador de miles, como en la boleta física (S/24,760.60).
    return {'a': f'{per.sedapal_total_a:,.2f}', 'b': f'{per.consumo_dptos_b:,.2f}',
            'diferencia': f'{per.diferencia_ab:,.2f}', 'agua_comun': agua_comun}


def _render(request, r, volver):
    config = ConfiguracionCondominio.vigente()
    medios = list(config.medios_pago.filter(activo=True)) if config else []
    return render(request, 'departamentos/recibos/documento.html', {
        'r': r, 'config': config, 'volver': volver,
        'banco': next((m for m in medios if m.tipo == 'BANCO'), None),
        'billeteras': [m for m in medios if m.tipo in ('PLIN', 'YAPE')],
    })


def _contexto_snapshot(recibo):
    per = recibo.periodo_fac
    lineas = list(recibo.lineas.select_related('concepto'))
    agua_comun = next((l.monto for l in lineas
                       if l.concepto and l.concepto.codigo == 'AREA_COMUN'), Decimal('0.00'))
    tarifa = per.tarifa_agua
    return {
        'departamento': recibo.departamento, 'periodo': per.periodo,
        'lecturas': {
            'anterior_fecha': recibo.lectura_anterior_fecha, 'anterior': recibo.lectura_anterior_valor,
            'actual_fecha': recibo.lectura_actual_fecha, 'actual': recibo.lectura_actual_valor,
            'm3': recibo.m3_consumidos,
        },
        'lineas': [{'etiqueta': l.etiqueta, 'monto': l.monto} for l in lineas],
        'descuento': recibo.descuento_mes_anterior, 'saldo_favor': recibo.saldo_favor,
        'total_mes': recibo.total_mes, 'deuda': recibo.deuda_anterior, 'total_pagar': recibo.total_pagar,
        'tarifa': tarifa, 'tramos': _estructura_tarifaria(tarifa) if tarifa else [],
        'facturacion': {
            'potable': recibo.agua_potable, 'saneamiento': recibo.agua_saneamiento,
            'cargo_fijo': recibo.agua_cargo_fijo, 'igv': recibo.agua_igv,
            'total': next((l.monto for l in lineas if l.grupo == 'AGUA'), Decimal('0.00')),
        },
        'edificio': _edificio(per, agua_comun),
        'emision': per.fecha_emision, 'vencimiento': per.fecha_vencimiento,
        'restriccion': per.fecha_restriccion,
    }


def _contexto_cargos(departamento, periodo):
    """Recibo desde los cargos del mes (datos_boleta), sin recalcular nada."""
    b = datos_boleta(departamento, periodo)
    per = PeriodoFacturacion.objects.filter(periodo=periodo).select_related('tarifa_agua').first()
    lectura = LecturaMedidor.objects.filter(departamento=departamento, periodo=periodo).first()
    tarifa = (per.tarifa_agua if per and per.tarifa_agua_id else None) or _tarifa_vigente(periodo)
    agua_comun = next((i['monto'] for i in b['items'] if i['codigo'] == 'AREA_COMUN'), Decimal('0.00'))
    return {
        'departamento': departamento, 'periodo': periodo,
        'lecturas': lectura and {
            'anterior_fecha': lectura.fecha_anterior, 'anterior': lectura.lectura_anterior,
            'actual_fecha': lectura.fecha_actual, 'actual': lectura.lectura_actual,
            'm3': lectura.m3_consumidos,
        },
        'lineas': [{'etiqueta': i['nombre'], 'monto': i['monto']} for i in b['items']],
        'descuento': None, 'saldo_favor': None,
        'total_mes': b['total_mes'], 'deuda': b['anterior'], 'total_pagar': b['total_pagar'],
        'tarifa': tarifa, 'tramos': _estructura_tarifaria(tarifa) if tarifa else [],
        'facturacion': None,  # el desglose SEDAPAL solo existe en el snapshot
        'edificio': _edificio(per, agua_comun),
        'emision': b['emision'] or (per and per.fecha_emision),
        'vencimiento': b['vencimiento'] or (per and per.fecha_vencimiento),
        'restriccion': per and per.fecha_restriccion,
    }


@login_required
def recibo_boleta(request, pk):
    """Recibo del departamento para un mes: snapshot si se emitió, si no los cargos."""
    departamento = get_object_or_404(Departamento, pk=pk)
    _, periodo = periodo_de_request(request)
    recibo = (Recibo.objects.select_related('departamento', 'periodo_fac__tarifa_agua')
              .filter(departamento=departamento, periodo_fac__periodo=periodo).first())
    r = _contexto_snapshot(recibo) if recibo else _contexto_cargos(departamento, periodo)
    return _render(request, r, request.META.get('HTTP_REFERER')
                   or reverse('departamento_detalle', args=[departamento.pk]))


@login_required
def recibo_ver(request, pk):
    """Recibo de mantenimiento (réplica de la boleta física) desde el snapshot."""
    recibo = get_object_or_404(
        Recibo.objects.select_related('departamento', 'periodo_fac__tarifa_agua'), pk=pk)
    return _render(request, _contexto_snapshot(recibo),
                   reverse('departamento_detalle', args=[recibo.departamento.pk]))


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
    return render(request, 'departamentos/recibos/generar.html', {'form': form})
