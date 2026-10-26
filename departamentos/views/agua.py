"""Agua: historial de lecturas (solo vista), facturas de agua (RevoGrid) y tarifas."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from ..forms import MESES_ES, TarifaAguaForm
from ..models import Departamento, LecturaMedidor, TarifaAgua
from ..servicios import a_decimal, lectura_del_mes, registrar_lectura, validar_lectura
from ..servicios.comun import ordenar_tramos
from .comun import json_invalido, leer_json, periodo_de_request


@login_required
def lecturas_mes(request):
    """Historial de lecturas de agua, SOLO VISTA: una fila por depto y una columna
    por mes del año elegido (lectura actual + m³ consumidos). Las lecturas se
    cargan en Facturas de agua o con Importar Excel."""
    hoy = date.today()
    try:
        anio = int(request.GET.get('anio') or hoy.year)
    except ValueError:
        anio = hoy.year

    lecturas = {(l.departamento_id, l.periodo.month): l
                for l in LecturaMedidor.objects.filter(periodo__year=anio)}
    meses_con_datos = {m for _, m in lecturas}
    tot_mes = [Decimal('0.00')] * 12
    filas = []
    for dep in Departamento.objects.filter(activo=True):
        celdas, total = [], Decimal('0.00')
        for m in range(1, 13):
            l = lecturas.get((dep.id, m))
            if l:
                total += l.m3_consumidos
                tot_mes[m - 1] += l.m3_consumidos
            celdas.append({'lectura': l.lectura_actual if l else None, 'm3': l.m3_consumidos if l else None})
        filas.append({'dep': dep, 'celdas': celdas, 'total': total})

    return render(request, 'departamentos/agua/lecturas_mes.html', {
        'anio': anio, 'anio_prev': anio - 1, 'anio_sig': anio + 1, 'anio_hoy': hoy.year,
        'meses': [{'n': m, 'nombre': MESES_ES[m][:3], 'con_datos': m in meses_con_datos,
                   'total': tot_mes[m - 1]} for m in range(1, 13)],
        'filas': filas, 'total_anio': sum(tot_mes, Decimal('0.00')),
        'n_lecturas': len(lecturas),
    })


def _guardar_facturas(payload, periodo):
    """Guarda las lecturas de la tabla y renombra el depto si cambió su número.

    Solo toca las lecturas que cambiaron (o cuya lectura anterior quedó desfasada
    del mes previo). Guarda las válidas; las que rompen el orden del medidor no se
    guardan y se devuelven en `errores` (con su pk) para marcarlas en la tabla."""
    guardadas, errores = 0, []
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
            if actual is None:
                continue
            guardada, anterior = lectura_del_mes(dep, periodo)
            if (guardada and guardada.lectura_actual == actual
                    and (anterior is None or guardada.lectura_anterior == anterior)):
                continue
            error = validar_lectura(dep, periodo, actual)
            if error:
                errores.append({'pk': dep.pk, 'depto': dep.numero_domicilio, 'error': error})
                continue
            registrar_lectura(dep, periodo, actual)
            guardadas += 1
    return JsonResponse({'ok': True, 'guardadas': guardadas, 'errores': errores})


def _f(v):
    return float(v) if v is not None and v != '' else None


def _mes_corto(anio, mes):
    return f'{MESES_ES[mes][:3]} {anio}'


# Opciones de "meses a ver" en Facturas de agua (actual + anteriores).
MESES_VISIBLES = (2, 3, 6, 12)


def _mes_menos(periodo, k):
    """Día 1 del mes que está `k` meses antes de `periodo`."""
    n = periodo.year * 12 + periodo.month - 1 - k
    return date(n // 12, n % 12 + 1, 1)


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

    # Cuántos meses se ven (actual + anteriores). Los anteriores al mes previo son solo consulta.
    try:
        n_meses = int(request.GET.get('meses') or 3)
    except ValueError:
        n_meses = 3
    n_meses = n_meses if n_meses in MESES_VISIBLES else 3
    historial = [_mes_menos(periodo, k) for k in range(n_meses - 1, 1, -1)]  # del más antiguo al de hace 2 meses
    lecturas_hist = {(l.departamento_id, l.periodo): l.lectura_actual
                     for l in LecturaMedidor.objects.filter(periodo__in=historial)}

    filas = []
    for dep in Departamento.objects.filter(activo=True):
        actual, anterior = lectura_del_mes(dep, periodo)
        # m³ contra la lectura del mes previo (la misma que muestra la columna "anterior").
        m3 = None
        if actual:
            m3 = max(actual.lectura_actual - anterior, Decimal('0')) if anterior is not None else Decimal('0')
        fila = {'pk': dep.id, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
                'anterior': _f(anterior),
                'actual': _f(actual.lectura_actual) if actual else None,
                'm3': _f(m3), 'costo': None}
        for p in historial:
            fila[f'h{p:%Y%m}'] = _f(lecturas_hist.get((dep.id, p)))
        filas.append(fila)

    tramos = ordenar_tramos(TarifaAgua.objects.filter(activo=True), 'consumo_limite')
    previo = _mes_menos(periodo, 1)

    return render(request, 'departamentos/agua/facturas.html', {
        'periodo_form': form, 'periodo': periodo,
        'filas_json': filas, 'n_filas': len(filas),
        'tramos_json': [{'limite': _f(t.consumo_limite), 'tarifa': _f(t.tarifa)} for t in tramos],
        # Etiquetas de mes para las columnas: historial (solo vista), anterior y actual.
        'historial_json': [{'prop': f'h{p:%Y%m}', 'nombre': f'Lect. {_mes_corto(p.year, p.month)}'}
                           for p in historial],
        'meses_opciones': MESES_VISIBLES, 'n_meses': n_meses,
        'mes_actual': _mes_corto(periodo.year, periodo.month), 'mes_anterior': _mes_corto(previo.year, previo.month),
    })


@login_required
def tarifas(request):
    return render(request, 'departamentos/agua/tarifas.html', {
        'tarifas': TarifaAgua.objects.all(),
    })


class _TarifaMixin(LoginRequiredMixin):
    model = TarifaAgua
    form_class = TarifaAguaForm
    template_name = 'departamentos/agua/tarifa_form.html'
    success_url = reverse_lazy('tarifas')


class TarifaCreateView(_TarifaMixin, CreateView):
    extra_context = {'titulo': 'Nuevo tramo de tarifa'}


class TarifaUpdateView(_TarifaMixin, UpdateView):
    extra_context = {'titulo': 'Editar tramo de tarifa'}
