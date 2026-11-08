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
from django.templatetags.static import static
from django.urls import reverse

from ..forms import MESES_ES, BorrarRecibosForm, GenerarRecibosForm
from ..models import (
    CategoriaServicio, ConfiguracionCondominio, Departamento, LecturaMedidor, PeriodoFacturacion, Recibo,
    TarifaAgua,
)
from ..servicios import borrar_generacion_mes, datos_boleta, desglose_gastos_mes, generar_cargos_mes
from ..servicios.comun import ordenar_tramos, redondear
from .comun import periodo_de_request


def _estructura_tarifaria(tramos):
    """Tramos de la tarifa con el rango legible ('0 a 10', '50 a más')."""
    def m3(d):  # 10.00 → '10', 2.50 → '2.5'
        s = f'{d:f}'
        return s.rstrip('0').rstrip('.') if '.' in s else s

    out, piso = [], Decimal('0')
    for t in ordenar_tramos(tramos, 'consumo_limite'):   # campo de TarifaAgua
        rango = f'{m3(piso)} a más' if t.consumo_limite is None else f'{m3(piso)} a {m3(t.consumo_limite)}'
        out.append({'rango': rango, 'precio': t.tarifa})  # un solo precio
        if t.consumo_limite is not None:
            piso = t.consumo_limite

    return out

def _tarifa_vigente():
    return (TarifaAgua.objects.filter(activo=True))


def _edificio(per, agua_comun):
    """Detalle consumo edificio (A/B) solo si el periodo lo tiene cargado."""
    if not per or not (per.sedapal_total_a or per.consumo_dptos_b):
        return None
    # Separador de miles, como en la boleta física (S/24,760.60).
    return {'a': f'{per.sedapal_total_a:,.2f}', 'b': f'{per.consumo_dptos_b:,.2f}',
            'diferencia': f'{per.diferencia_ab:,.2f}', 'agua_comun': agua_comun}


def _logo_url(logo):
    """El logo de la configuración es una ruta bajo /static (img/logo.jpg) o una URL."""
    if not logo or logo.startswith(('http://', 'https://', '/')):
        return logo
    return static(logo)


def _aviso(config, r):
    """Texto del aviso; `{vencimiento}` se reemplaza por la fecha de pago del recibo (05-09)."""
    texto = config.texto_aviso if config else ''
    venc = r.get('vencimiento')
    return texto.replace('{vencimiento}', f'{venc:%d-%m}' if venc else '')


def _render(request, r, volver):
    config = ConfiguracionCondominio.vigente()
    medios = list(config.medios_pago.filter(activo=True)) if config else []
    return render(request, 'departamentos/recibos/documento.html', {
        'r': r, 'config': config, 'volver': volver,
        'logo_url': _logo_url(config.logo if config else ''), 'aviso': _aviso(config, r),
        'banco': next((m for m in medios if m.tipo == 'BANCO'), None),
        'billeteras': [m for m in medios if m.tipo in ('PLIN', 'YAPE')],
        'meses': [(i, MESES_ES[i]) for i in range(1, 13)],
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
        'total_gastos': None,   # el snapshot no guarda el desglose de gastos
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
    tramos = _tarifa_vigente()
    agua_comun = next((i['monto'] for i in b['items'] if i['codigo'] == 'AREA_COMUN'), Decimal('0.00'))
    
    # 1. Extraer individualmente cada concepto si existe en b['items']
    multa = next((i['monto'] for i in b['items'] if 'MULTA' in i['nombre'].upper()), Decimal('0.00'))
    cuota_extraordinaria = next((i['monto'] for i in b['items'] if 'EXTRAORDINARIA' in i['nombre'].upper()), Decimal('0.00'))
    mora = next((i['monto'] for i in b['items'] if 'MORA' in i['nombre'].upper()), Decimal('0.00'))
    cochera = next((i['monto'] for i in b['items'] if 'COCHERA' in i['nombre'].upper()),Decimal('0.00'))
    # 2. Crear la variable combinada de mora + multa
    mora_mas_multa = mora + multa

    # 3. Filtrar las líneas generales para que no se dupliquen o se mezclen donde no deben
    excluir_en_agua = ['MULTA', 'MORA', 'EXTRAORDINARIA','MANTENIMIENTO','COCHERA']
    lineas_filtradas = [
        {'etiqueta': i['nombre'], 'monto': i['monto'], 'total':i['monto']*89} 
        for i in b['items'] 
        if not any(exc in i['nombre'].upper() for exc in excluir_en_agua)
    ]
    
    # Gastos del mes en sus dos grupos del recibo, según el tipo de servicio de
    # cada gasto. Las cuotas que se cobran aparte van en "Extras", no aquí.
    dg = desglose_gastos_mes(periodo)
    servicios_torre, servicios_condominio = [], []
    for g in dg['general'] + dg['adicionales']:
        if g['aparte']:
            continue
        item = {'nombre': g['descripcion'], 'monto': g['por_depto'], 'monto_completo': g['total']}
        (servicios_torre if g['tipo_servicio'] == CategoriaServicio.TORRE else servicios_condominio).append(item)

    # 4. El subtotal del recibo representa el mes base del recibo, no el total
    # completo con mora/multa/cuota extraordinaria, que van en líneas separadas
    # más abajo. Tomamos la fuente real del mes y quitamos solo esos rubros.
    sub_total = redondear(b['total_mes'] - mora - multa - cuota_extraordinaria)
    # Barra "Total gastos": suma de las filas de los dos grupos de gastos del recibo.
    total_gastos = redondear(sum((g['monto'] for g in servicios_torre + servicios_condominio),
                                 Decimal('0')) + cochera)

    return {
        'agua_total': b['agua_total'],
        'servicios_torre': servicios_torre,
        'servicios_condominio': servicios_condominio,   # la cochera va al final de este grupo
        # Nuevas variables disponibles para el template:
        'multa': multa,
        'cuota_extraordinaria': cuota_extraordinaria,
        'mora': mora,
        'mora_mas_multa': mora_mas_multa,
        'sub_total': sub_total,
        'total_gastos': total_gastos,
        'cochera': cochera,
        'departamento': departamento, 
        'periodo': periodo,
        'lecturas': lectura and {
            'anterior_fecha': lectura.fecha_anterior, 'anterior': lectura.lectura_anterior,
            'actual_fecha': lectura.fecha_actual, 'actual': lectura.lectura_actual,
            'm3': lectura.m3_consumidos,
        },
        'lineas': lineas_filtradas,
        'descuento': None, 'saldo_favor': None,
        'total_mes': b['total_mes'], 'deuda': b['anterior']+mora, 'total_pagar': b['total_pagar'],
        'tarifa': tramos.first(), 'tramos': _estructura_tarifaria(tramos),
        'facturacion': None,
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
def recibos_mes(request):
    """Recibos de TODOS los departamentos activos de un mes en una sola página,
    uno por hoja, para imprimir / guardar en PDF de una vez."""
    form, periodo = periodo_de_request(request)
    config = ConfiguracionCondominio.vigente()
    medios = list(config.medios_pago.filter(activo=True)) if config else []
    snapshots = {r.departamento_id: r for r in Recibo.objects
                 .select_related('departamento', 'periodo_fac__tarifa_agua')
                 .filter(periodo_fac__periodo=periodo)}
    recibos = []
    for dep in Departamento.objects.filter(activo=True).order_by('numero_domicilio'):
        rec = snapshots.get(dep.pk)
        r = _contexto_snapshot(rec) if rec else _contexto_cargos(dep, periodo)
        recibos.append({'r': r, 'aviso': _aviso(config, r)})
    return render(request, 'departamentos/recibos/mes.html', {
        'recibos': recibos, 'periodo': periodo, 'config': config,
        'logo_url': _logo_url(config.logo if config else ''),
        'banco': next((m for m in medios if m.tipo == 'BANCO'), None),
        'billeteras': [m for m in medios if m.tipo in ('PLIN', 'YAPE')],
        'meses': [(i, MESES_ES[i]) for i in range(1, 13)],
    })


@login_required
def generar_recibos(request):
    if request.POST.get('accion') == 'borrar':
        return _borrar_generacion(request)
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

    # Borrar: con "Ver qué se borra" (GET b-mes) se muestra la vista previa y se confirma.
    borrar = BorrarRecibosForm(request.GET if 'b-mes' in request.GET else None, prefix='b')
    previa = None
    if borrar.is_bound and borrar.is_valid():
        previa = {'periodo': borrar.periodo, **borrar_generacion_mes(
            borrar.periodo, borrar.cleaned_data['incluir_manuales'], borrar.cleaned_data['incluir_pagados'])}
    return render(request, 'departamentos/recibos/generar.html', {
        'form': form, 'borrar': borrar, 'previa': previa})


def _borrar_generacion(request):
    borrar = BorrarRecibosForm(request.POST, prefix='b')
    if not borrar.is_valid():
        messages.error(request, 'Elige un mes válido para borrar.')
        return redirect('generar_recibos')
    periodo, datos = borrar.periodo, borrar.cleaned_data
    r = borrar_generacion_mes(periodo, datos['incluir_manuales'], datos['incluir_pagados'], aplicar=True)
    texto = f"Boletas de {periodo:%m/%Y}: se borraron {r['n']} cargo(s) de {r['deptos']} depto(s) (S/ {r['total']:.2f})."
    if r['pagos_liberados']:
        texto += f" S/ {r['pagos_liberados']:.2f} de pagos quedaron como saldo a favor."
    if r['conservados']:
        texto += f" Se conservaron {r['conservados']} cargo(s) con pagos."
    messages.success(request, texto)
    return redirect('generar_recibos')
