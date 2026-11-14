"""Configuración del condominio editable desde el propio sistema."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from ..forms import ConfiguracionForm
from ..models import ConfiguracionCondominio


@login_required
def configuracion(request):
    """Página de configuración: datos del condominio, inicio de administración
    (desde dónde es deuda anterior) y firmas de los reportes."""
    cfg = ConfiguracionCondominio.vigente()
    if cfg is None:
        cfg = ConfiguracionCondominio.objects.create(nombre='Condominio')
    if request.method == 'POST':
        form = ConfiguracionForm(request.POST, instance=cfg)
        if form.is_valid():
            form.save()
            messages.success(request, 'Configuración guardada.')
            return redirect('configuracion')
        messages.error(request, 'Revisa los campos marcados.')
    else:
        form = ConfiguracionForm(instance=cfg)
    return render(request, 'departamentos/configuracion/configuracion.html',
                  {'form': form, 'cur': 'configuracion'})
