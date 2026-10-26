"""Proveedores / contactos del edificio."""
from django.contrib.auth.decorators import login_required
from django.urls import reverse_lazy

from ..forms import ProveedorForm
from ..models import Proveedor
from .comun import EditarView, lista_con_alta


@login_required
def proveedores(request):
    return lista_con_alta(request, form_class=ProveedorForm, queryset=Proveedor.objects.all(),
                          template='departamentos/proveedores/lista.html', contexto='proveedores',
                          campo_id='proveedor_id', nombre='Proveedor')


class ProveedorUpdateView(EditarView):
    model = Proveedor
    form_class = ProveedorForm
    template_name = 'departamentos/proveedores/form.html'
    success_url = reverse_lazy('proveedores')
    extra_context = {'titulo': 'Editar proveedor'}
    success_message = 'Proveedor actualizado.'
