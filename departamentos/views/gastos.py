"""Gastos fijos (recurrentes) y gastos del mes (tabla editable)."""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy

from ..forms import GastoFijoForm
from ..models import Departamento, GastoEdificio, GastoFijo
from ..servicios import a_decimal, prorrateo_mes
from .comun import EditarView, json_invalido, leer_json, lista_con_alta, periodo_de_request


@login_required
def gastos_fijos(request):
    return lista_con_alta(request, form_class=GastoFijoForm, queryset=GastoFijo.objects.all(),
                          template='departamentos/gastos/fijos.html', contexto='gastos',
                          campo_id='gasto_id', nombre='Gasto fijo')


class GastoFijoUpdateView(EditarView):
    model = GastoFijo
    form_class = GastoFijoForm
    template_name = 'departamentos/gastos/fijo_form.html'
    success_url = reverse_lazy('gastos_fijos')
    extra_context = {'titulo': 'Editar gasto fijo'}
    success_message = 'Gasto fijo actualizado.'


def _guardar_gastos(payload, periodo):
    """Reemplaza los gastos del periodo por las filas de la tabla. Atómico."""
    validas = {c[0] for c in GastoEdificio.Categoria.choices}
    creados = 0
    with transaction.atomic():
        GastoEdificio.objects.filter(periodo=periodo).delete()
        for r in payload.get('rows', []):
            desc = (r.get('descripcion') or '').strip()
            monto = a_decimal(r.get('monto'), Decimal('0'))
            if not desc or monto <= 0:
                continue
            g = GastoEdificio.objects.create(
                periodo=periodo, descripcion=desc, monto=monto,
                categoria=r.get('categoria') if r.get('categoria') in validas else 'GENERAL',
                compartido=bool(r.get('compartido', True)))
            ids = r.get('deptos') or []
            if ids:
                g.departamentos.set(Departamento.objects.filter(pk__in=ids, activo=True))
            creados += 1
    return JsonResponse({'ok': True, 'creados': creados})


@login_required
def gastos_mes(request):
    """Gastos del mes como TABLA EDITABLE. Cada fila es un gasto con descripción,
    monto, categoría, un interruptor compartido (÷ todos) / por depto (cada uno
    paga completo) y a qué departamentos aplica (vacío = todos). Se guarda en
    lote (JSON): reemplaza los gastos del periodo por los de la tabla."""
    form, periodo = periodo_de_request(request)

    if request.method == 'POST' and request.content_type == 'application/json':
        payload = leer_json(request)
        return json_invalido() if payload is None else _guardar_gastos(payload, periodo)

    gastos = GastoEdificio.objects.filter(periodo=periodo).prefetch_related('departamentos')
    return render(request, 'departamentos/gastos/mes.html', {
        'periodo_form': form, 'periodo': periodo, 'datos': prorrateo_mes(periodo),
        'gastos_json': [{'id': g.id, 'descripcion': g.descripcion, 'monto': float(g.monto),
                         'categoria': g.categoria, 'compartido': g.compartido,
                         'deptos': [d.id for d in g.departamentos.all()]} for g in gastos],
        'deptos_json': [{'id': d.id, 'numero': d.numero_domicilio, 'propietario': d.propietario}
                        for d in Departamento.objects.filter(activo=True)],
        'categorias': list(GastoEdificio.Categoria.choices),
    })
