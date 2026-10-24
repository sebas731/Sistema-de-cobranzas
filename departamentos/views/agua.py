"""Agua: lecturas del mes, facturas de agua (RevoGrid) y tarifas."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from ..forms import MESES_ES, TarifaAguaForm
from ..models import Departamento, TarifaAgua
from ..servicios import a_decimal, lectura_del_mes, registrar_lectura
from ..servicios.comun import ordenar_tramos
from .comun import json_invalido, leer_json, periodo_de_request, redirigir_al_periodo


@login_required
def lecturas_mes(request):
    form, periodo = periodo_de_request(request)

    if request.method == 'POST':
        guardadas = 0
        for dep in Departamento.objects.filter(activo=True):
            lectura_actual = a_decimal(request.POST.get(f'actual_{dep.id}', '').strip())
            if lectura_actual is not None:
                registrar_lectura(dep, periodo, lectura_actual)
                guardadas += 1
        messages.success(request, f'{guardadas} lectura(s) guardadas.')
        return redirigir_al_periodo(request, periodo)

    filas = []
    for dep in Departamento.objects.filter(activo=True):
        actual, anterior = lectura_del_mes(dep, periodo)
        filas.append({
            'dep': dep, 'anterior': anterior,
            'actual': actual.lectura_actual if actual else '',
            'm3': actual.m3_consumidos if actual else None,
        })
    return render(request, 'departamentos/lecturas_mes.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,
    })


def _guardar_facturas(payload, periodo):
    """Guarda las lecturas de la tabla y renombra el depto si cambió su número."""
    guardadas = 0
    with transaction.atomic():
        for r in payload.get('rows', []):
            dep = Departamento.objects.filter(pk=r.get('pk')).first()
            if not dep:
                continue
            nuevo = str(r.get('numero') or '').strip()
            if (nuevo and nuevo != dep.numero_domicilio
                    and not Departamento.objects.filter(numero_domicilio=nuevo).exclude(pk=dep.pk).exists()):
                dep.numero_domicilio = nuevo
                dep.save(update_fields=['numero_domicilio'])
            actual = a_decimal(r.get('actual'))
            if actual is not None:
                registrar_lectura(dep, periodo, actual)
                guardadas += 1
    return JsonResponse({'ok': True, 'guardadas': guardadas})


def _f(v):
    return float(v) if v is not None and v != '' else None


def _mes_corto(anio, mes):
    return f'{MESES_ES[mes][:3]} {anio}'


@login_required
def facturas_agua(request):
    """Tabla editable (RevoGrid) de lecturas/consumo de agua por departamento.

    Se editan en celda: nº de departamento, lectura actual, m³ y el cálculo.
    GET renderiza el grid; POST (JSON) guarda las lecturas y el nº de depto.
    """
    form, periodo = periodo_de_request(request)

    if request.method == 'POST':
        payload = leer_json(request)
        return json_invalido() if payload is None else _guardar_facturas(payload, periodo)

    filas = []
    for dep in Departamento.objects.filter(activo=True):
        actual, anterior = lectura_del_mes(dep, periodo)
        filas.append({'pk': dep.id, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
                      'anterior': _f(anterior),
                      'actual': _f(actual.lectura_actual) if actual else None,
                      'm3': _f(actual.m3_consumidos) if actual else None, 'costo': None})

    tramos = ordenar_tramos(TarifaAgua.objects.filter(activo=True), 'consumo_limite')
    previo = (periodo.year - 1, 12) if periodo.month == 1 else (periodo.year, periodo.month - 1)

    return render(request, 'departamentos/facturas_agua.html', {
        'periodo_form': form, 'periodo': periodo,
        'filas_json': filas, 'n_filas': len(filas),
        'tramos_json': [{'limite': _f(t.consumo_limite), 'tarifa': _f(t.tarifa)} for t in tramos],
        # Etiquetas informativas de mes (periodo actual y el anterior) para las columnas.
        'mes_actual': _mes_corto(periodo.year, periodo.month), 'mes_anterior': _mes_corto(*previo),
    })


@login_required
def tarifas(request):
    return render(request, 'departamentos/tarifas.html', {
        'tarifas': TarifaAgua.objects.all(),
    })


class _TarifaMixin(LoginRequiredMixin):
    model = TarifaAgua
    form_class = TarifaAguaForm
    template_name = 'departamentos/tarifa_form.html'
    success_url = reverse_lazy('tarifas')


class TarifaCreateView(_TarifaMixin, CreateView):
    extra_context = {'titulo': 'Nuevo tramo de tarifa'}


class TarifaUpdateView(_TarifaMixin, UpdateView):
    extra_context = {'titulo': 'Editar tramo de tarifa'}
