"""Cobranza: registrar pagos y asignar multas/cargos."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from ..forms import MESES_ES, CargoMasivoForm, RegistroPagoForm
from ..models import Cargo, Departamento
from ..servicios import cargos_pendientes, crear_cargo, registrar_pago


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
    """Registrar UN pago con un formulario corto. El front muestra una vista previa
    de a qué deudas se aplicará, pero la autoridad es el back: aquí se pone la fecha
    (hoy) y registrar_pago vuelve a aplicar la regla. Lo que sobre queda a favor."""
    form = RegistroPagoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        pago = registrar_pago(d['departamento'], d['monto'], concepto=d['concepto'],
                              periodo=d['periodo'])
        texto = f'Pago de S/ {pago.monto:.2f} registrado para el depto {pago.departamento.numero_domicilio}.'
        if pago.saldo_a_favor > 0:
            texto += f' Quedan S/ {pago.saldo_a_favor:.2f} a favor.'
        messages.success(request, texto)
        return redirect('registrar_pagos')
    return render(request, 'departamentos/cobranza/registrar_pagos.html', {
        'form': form, 'deudas': _deudas_para_pago(),
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
