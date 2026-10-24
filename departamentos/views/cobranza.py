"""Cobranza: registrar pagos y asignar multas/cargos (tabla editable)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render

from ..forms import MESES_ES, RegistroPagoForm
from ..models import Cargo, Concepto, Departamento
from ..servicios import a_decimal, cargos_pendientes, crear_cargo, registrar_pago
from .comun import json_invalido, leer_json, periodo_de_request


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
    return render(request, 'departamentos/registrar_pagos.html', {
        'form': form, 'deudas': _deudas_para_pago(),
    })


def _guardar_cargos(payload, periodo):
    """Aplica el lote de la tabla: monto > 0 asigna el cargo; vacío/0 lo quita
    (si no tiene pagos). Atómico."""
    concepto = Concepto.objects.filter(pk=payload.get('concepto')).first()
    if not concepto:
        return JsonResponse({'ok': False, 'error': 'Elige un concepto.'}, status=400)
    asignados = quitados = 0
    with transaction.atomic():
        for r in payload.get('rows', []):
            dep = Departamento.objects.filter(pk=r.get('pk'), activo=True).first()
            if not dep:
                continue
            monto = a_decimal(r.get('monto'), Decimal('0'))
            if monto > 0:
                crear_cargo(dep, concepto, periodo, monto, (r.get('descripcion') or '').strip())
                asignados += 1
                continue
            for c in Cargo.objects.filter(departamento=dep, concepto=concepto, periodo=periodo):
                if c.total_imputado == 0:
                    c.delete()
                    quitados += 1
    return JsonResponse({'ok': True, 'asignados': asignados, 'quitados': quitados})


@login_required
def registrar_cargos(request):
    """Multas / cargos como TABLA EDITABLE (RevoGrid). Se elige un concepto y un
    periodo; en la tabla se edita el monto y la descripción por departamento.
    Fila con monto > 0 = se asigna el cargo; monto vacío/0 = se quita (si no
    tiene pagos). Guardado en lote (JSON), atómico."""
    form, periodo = periodo_de_request(request)

    if request.method == 'POST' and request.content_type == 'application/json':
        payload = leer_json(request)
        return json_invalido() if payload is None else _guardar_cargos(payload, periodo)

    try:
        concepto_id = int(request.GET.get('concepto') or 0)
    except ValueError:
        concepto_id = 0
    if not concepto_id:
        activos = Concepto.objects.filter(activo=True)
        c = activos.filter(codigo='MULTA').first() or activos.first()
        concepto_id = c.id if c else 0
    concepto_sel = Concepto.objects.filter(pk=concepto_id).first()

    existentes = {c.departamento_id: c for c in
                  Cargo.objects.filter(concepto=concepto_sel, periodo=periodo)} if concepto_sel else {}
    filas = []
    for dep in Departamento.objects.filter(activo=True):
        e = existentes.get(dep.id)
        filas.append({'pk': dep.id, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
                      'monto': float(e.monto) if e else None, 'descripcion': e.descripcion if e else ''})

    return render(request, 'departamentos/registrar_cargos.html', {
        'periodo_form': form, 'periodo': periodo,
        'conceptos': list(Concepto.objects.filter(activo=True).values('id', 'nombre')),
        'concepto_id': concepto_id, 'concepto_nombre': concepto_sel.nombre if concepto_sel else '',
        'filas_json': filas, 'n_filas': len(filas),
    })
