"""Inicio (tarjetas), styleguide y buscador rápido de departamento."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from ..models import Departamento


@login_required
def inicio(request):
    """Pantalla de arranque: panel de trabajo limpio con tarjetas grandes hacia
    las secciones del sistema."""
    return render(request, 'departamentos/inicio/inicio.html', {
        'n_deptos': Departamento.objects.filter(activo=True).count(),
    })


@login_required
def estilos(request):
    """Guía visual (styleguide) del Design System: tokens y componentes."""
    return render(request, 'departamentos/inicio/estilos.html', {})


@login_required
def buscar_departamento(request):
    q = request.GET.get('q', '').strip()
    if q:
        dep = Departamento.objects.filter(numero_domicilio__iexact=q).first()
        if dep:
            return redirect('departamento_detalle', pk=dep.pk)
        messages.error(request, f'No existe el departamento "{q}".')
    return redirect('balance')
