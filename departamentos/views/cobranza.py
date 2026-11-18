"""Cobranza: registrar pagos y asignar multas/cargos."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from ..forms import MESES_ES, CargoMasivoForm, RegistroPagoForm
from ..models import Cargo, Departamento, Pago
from ..servicios import MESES_SUELTOS, cargos_pendientes, crear_cargo, registrar_pago, sumar_meses

CERO = Decimal('0.00')


def _deudas_para_pago():
    """{pk: {numero, propietario, deudas[]}} de los deptos activos, con sus cargos
    pendientes del más antiguo al más nuevo. El formulario de pago lo usa para
    autocompletar, llenar los selectores y armar la vista previa sin pedir nada más."""
    datos = {}
    for dep in (Departamento.objects.filter(activo=True)
                .prefetch_related('cargos__concepto', 'cargos__imputaciones')):
        datos[str(dep.pk)] = {
            'numero': dep.numero_domicilio,
            'propietario': dep.propietario,
            'deudas': [{'concepto': str(c.concepto_id), 'motivo': c.concepto.nombre,
                        'periodo': c.periodo.isoformat(),
                        'mes': f'{MESES_ES[c.periodo.month]} {c.periodo.year}',
                        'saldo': str(c.saldo)}
                       for c in cargos_pendientes(dep)],
        }
    return datos


@login_required
def registrar_pagos(request):
    """Registrar pagos con un formulario corto: uno, o el mismo importe para varios
    deptos a la vez (ej. la cuota extraordinaria). El front muestra una vista previa
    de a qué deudas se aplicará, pero la autoridad es el back: aquí se pone la fecha
    (hoy) y registrar_pago vuelve a aplicar la regla. Lo que sobre queda a favor."""
    form = RegistroPagoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            pagos = [registrar_pago(dep, d['monto'], concepto=d['concepto'], periodo=d['periodo'])
                     for dep in d['departamentos']]
        numeros = ', '.join(p.departamento.numero_domicilio for p in pagos)
        if len(pagos) == 1:
            texto = f'Pago de S/ {d["monto"]:.2f} registrado para el depto {numeros}.'
        else:
            texto = (f'{len(pagos)} pagos de S/ {d["monto"]:.2f} registrados '
                     f'(total S/ {d["monto"] * len(pagos):.2f}): {numeros}.')
        favor = [p for p in pagos if p.saldo_a_favor > 0]
        if favor:
            texto += ' Quedan a favor: ' + ', '.join(
                f'{p.departamento.numero_domicilio} S/ {p.saldo_a_favor:.2f}' for p in favor) + '.'
        messages.success(request, texto)
        return redirect('registrar_pagos')
    # Para mostrar la deuda por antigüedad: el mes actual y los 2 anteriores sueltos,
    # lo más antiguo en un solo "acumulado" (solo vista; el pago sigue saldando lo más antiguo).
    hoy = timezone.localdate().replace(day=1)
    inicio = sumar_meses(hoy, -MESES_SUELTOS)
    hasta = sumar_meses(inicio, -1)
    return render(request, 'departamentos/cobranza/registrar_pagos.html', {
        'form': form, 'deudas': _deudas_para_pago(),
        'antiguedad': {'inicio': inicio.isoformat(), 'actual': hoy.isoformat(),
                       'hasta': f'{MESES_ES[hasta.month]} {hasta.year}'},
    })


def _num_key(numero):
    """Orden natural del número de departamento (508, 103…), con texto al final."""
    try:
        return (0, int(numero))
    except (TypeError, ValueError):
        return (1, str(numero))


def _label_pago(p):
    """Motivo visible de un pago: su concepto, o el de sus imputaciones, o 'A cuenta'."""
    if p.concepto_id:
        return p.concepto.nombre
    nombres = sorted({i.cargo.concepto.nombre for i in p.imputaciones.all()})
    return ' / '.join(nombres) if nombres else 'A cuenta'


@login_required
def pagos_departamento(request):
    """Tabla de pagos agrupados por departamento (reporte: solo lee, no modifica).

    Filtros opcionales por mes/año (GET). Por cada depto lista sus pagos
    (fecha, motivo, método, nota, monto) y un subtotal; abajo el total general.
    """
    qs = (Pago.objects.select_related('departamento', 'concepto')
          .prefetch_related('imputaciones__cargo__concepto')
          .order_by('departamento__numero_domicilio', 'fecha_pago', 'id'))
    sel_mes = (request.GET.get('mes') or '').strip()
    sel_anio = (request.GET.get('anio') or '').strip()
    if sel_anio.isdigit():
        qs = qs.filter(fecha_pago__year=int(sel_anio))
    if sel_mes.isdigit():
        qs = qs.filter(fecha_pago__month=int(sel_mes))

    grupos, total = {}, CERO
    for p in qs:
        dep = p.departamento
        g = grupos.setdefault(dep.pk, {
            'numero': dep.numero_domicilio, 'propietario': dep.propietario or 'Sin propietario',
            'pagos': [], 'subtotal': CERO})
        g['pagos'].append({'fecha': p.fecha_pago, 'label': _label_pago(p),
                           'metodo': p.metodo, 'nota': p.nota, 'monto': p.monto})
        g['subtotal'] += p.monto
        total += p.monto

    grupos = sorted(grupos.values(), key=lambda x: _num_key(x['numero']))
    n_pagos = sum(len(g['pagos']) for g in grupos)
    anios = sorted({f.year for f in Pago.objects.values_list('fecha_pago', flat=True)}, reverse=True)

    return render(request, 'departamentos/cobranza/pagos_departamento.html', {
        'grupos': grupos, 'total': total, 'n_pagos': n_pagos, 'n_deptos': len(grupos),
        'meses': [(i, MESES_ES[i]) for i in range(1, 13)], 'anios': anios,
        'sel_mes': sel_mes, 'sel_anio': sel_anio,
    })


@login_required
def registrar_cargos(request):
    """Asignar un mismo cargo (multa, mora, cuota…) a uno o varios deptos en un mes,
    con un formulario corto. Si un depto ya tenía ese concepto en ese mes, se
    actualiza el monto (crear_cargo es idempotente) y se avisa. Todo el lote en un
    transaction.atomic. No quita cargos: eso se hace desde el detalle del depto."""
    form = CargoMasivoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        periodo, concepto, deptos = form.periodo, d['concepto'], list(d['departamentos'])
        ya_tenian = set(Cargo.objects.filter(concepto=concepto, periodo=periodo, departamento__in=deptos)
                        .values_list('departamento__numero_domicilio', flat=True))
        with transaction.atomic():
            for dep in deptos:
                crear_cargo(dep, concepto, periodo, d['monto'], d['descripcion'].strip())
        mes = f'{MESES_ES[periodo.month].lower()} {periodo.year}'
        n = len(deptos)
        texto = (f'Se asignó «{concepto.nombre}» de S/ {d["monto"]:.2f} a {n} '
                 f'departamento{"s" if n != 1 else ""} (total S/ {d["monto"] * n:.2f}). '
                 f'Saldrá en el recibo de {mes}.')
        if ya_tenian:
            texto += (f' Ya tenían «{concepto.nombre}» en ese mes y se actualizó su monto: '
                      f'{", ".join(sorted(ya_tenian))}.')
        messages.success(request, texto)
        return redirect('registrar_cargos')
    return render(request, 'departamentos/cobranza/registrar_cargos.html', {'form': form})


# ------------------------------------------------ Cobranza (pantalla principal)

MEDIOS_PAGO = ['Yape', 'Plin', 'Transferencia', 'Depósito', 'Efectivo']


def _deudas_por_depto(deps):
    """{pk: {numero, propietario, total, deudas[{id, concepto, mes, saldo, orden}]}}
    con los cargos pendientes del más antiguo al más nuevo (para el panel de cobro)."""
    datos = {}
    for dep in deps:
        pend = cargos_pendientes(dep)
        datos[str(dep.pk)] = {
            'numero': dep.numero_domicilio, 'propietario': dep.propietario,
            'total': str(sum((c.saldo for c in pend), CERO)),
            'deudas': [{'id': c.pk, 'concepto': c.concepto.nombre, 'codigo': c.concepto.codigo,
                        'mes': f'{MESES_ES[c.periodo.month]} {c.periodo.year}',
                        'detalle': c.descripcion, 'saldo': str(c.saldo)} for c in pend],
        }
    return datos


@login_required
def cobranza(request):
    """Pantalla principal de cobranza (como la hoja «MANT. OCT»): una fila por
    depto con su deuda por columna y un panel lateral para registrar el cobro
    marcando exactamente QUÉ deudas paga el vecino."""
    from .comun import periodo_de_request
    from .deudores import filas_deudas
    form, ref = periodo_de_request(request)
    meses = [sumar_meses(ref, -2), sumar_meses(ref, -1), ref]
    deps = list(Departamento.objects.filter(activo=True)
                .prefetch_related('cargos__concepto', 'cargos__imputaciones'))
    solo_deuda = request.GET.get('todos') != '1'
    filas = filas_deudas(ref, meses, incluir_sin_deuda=not solo_deuda, deps=deps)
    ultimo = {}
    for p in Pago.objects.filter(departamento__in=deps).order_by('fecha_pago', 'id'):
        ultimo[p.departamento_id] = p
    for f in filas:
        f['ultimo'] = ultimo.get(f['depto'].pk)
        if f['total'] <= 0:
            f['estado'], f['estado_cls'] = 'Al día', 'aldia'
        elif f['ultimo'] and f['ultimo'].fecha_pago >= ref:
            f['estado'], f['estado_cls'] = 'Parcial', 'parcial'
        else:
            f['estado'], f['estado_cls'] = 'Debe', 'debe'
    from .deudores import COLS_DEUDA
    tot = {k: sum((f[k] for f in filas), CERO) for k in COLS_DEUDA}
    cobrado_mes = sum((p.monto for p in Pago.objects.filter(
        fecha_pago__year=ref.year, fecha_pago__month=ref.month)), CERO)
    return render(request, 'departamentos/cobranza/cobranza.html', {
        'periodo_form': form, 'ref': ref, 'filas': filas, 'tot': tot,
        'mes_labels': [MESES_ES[m.month] for m in meses], 'solo_deuda': solo_deuda,
        'n_deudores': sum(1 for f in filas if f['total'] > 0),
        'cobrado_mes': cobrado_mes, 'deudas_json': _deudas_por_depto(deps),
        'medios': MEDIOS_PAGO, 'hoy': timezone.localdate(),
    })


@login_required
def cobrar(request):
    """POST JSON {depto, items:[{id, monto}], fecha, medio, operacion, nota, excedente}
    → registra el cobro con cobrar_cargos. Responde JSON {ok, mensaje} o {ok:false, error}."""
    from datetime import date

    from django.http import JsonResponse

    from ..servicios import cobrar_cargos
    from .comun import leer_json
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido.'}, status=405)
    d = leer_json(request)
    if d is None:
        return JsonResponse({'ok': False, 'error': 'Datos inválidos.'}, status=400)
    dep = Departamento.objects.filter(pk=d.get('depto'), activo=True).first()
    if dep is None:
        return JsonResponse({'ok': False, 'error': 'Elige un departamento.'}, status=400)
    medio = (d.get('medio') or '').strip()
    operacion = (d.get('operacion') or '').strip()
    if medio not in MEDIOS_PAGO:
        return JsonResponse({'ok': False, 'error': 'Elige cómo pagó (Yape, Plin, ...).'}, status=400)
    if medio != 'Efectivo' and not operacion:
        return JsonResponse({'ok': False, 'error': 'Falta el N° de operación.'}, status=400)
    try:
        fecha = date.fromisoformat(d.get('fecha') or '') if d.get('fecha') else None
        items = [(int(i['id']), Decimal(str(i['monto']))) for i in d.get('items') or []]
        excedente = Decimal(str(d.get('excedente') or '0'))
        pago = cobrar_cargos(dep, items, fecha=fecha, metodo=medio, operacion=operacion,
                             nota=d.get('nota') or '', excedente=excedente)
    except (ValueError, KeyError, ArithmeticError) as e:
        return JsonResponse({'ok': False, 'error': str(e) or 'Datos inválidos.'}, status=400)
    texto = f'Cobro de S/ {pago.monto:.2f} registrado para el depto {dep.numero_domicilio} ({medio}).'
    if pago.saldo_a_favor > 0:
        texto += f' Quedan S/ {pago.saldo_a_favor:.2f} a favor.'
    messages.success(request, texto)
    return JsonResponse({'ok': True, 'mensaje': texto})
