"""Helpers compartidos por las vistas."""
import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.generic import UpdateView

from ..forms import MesAnioForm


def periodo_de_request(request):
    """(form, periodo) desde mes/anio del querystring; si no vienen o no son
    válidos, el mes actual."""
    form = MesAnioForm(request.GET or None)
    if request.GET and form.is_valid():
        return form, form.periodo
    hoy = date.today()
    return MesAnioForm(initial={'mes': hoy.month, 'anio': hoy.year}), date(hoy.year, hoy.month, 1)


def redirigir_al_periodo(request, periodo):
    """Vuelve a la misma pantalla en el mes indicado."""
    return redirect(f'{request.path}?mes={periodo.month}&anio={periodo.year}')


def leer_json(request):
    """Cuerpo JSON del request como dict, o None si es inválido."""
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None


def json_invalido():
    return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)


def lista_con_alta(request, *, form_class, queryset, template, contexto, campo_id, nombre):
    """Pantalla de lista con formulario de alta y botón eliminar (POST accion=
    agregar|eliminar). `nombre` se usa en los mensajes ('Proveedor', 'Gasto fijo')."""
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'agregar':
            form = form_class(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, f'{nombre} agregado.')
            else:
                messages.error(request, f'Revisa los datos del {nombre.lower()}.')
        elif accion == 'eliminar':
            queryset.model.objects.filter(pk=request.POST.get(campo_id)).delete()
            messages.success(request, f'{nombre} eliminado.')
        return redirect(request.path)
    return render(request, template, {contexto: queryset, 'form': form_class()})


class EditarView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    """UpdateView con login obligatorio y mensaje de éxito (`success_message`)."""
