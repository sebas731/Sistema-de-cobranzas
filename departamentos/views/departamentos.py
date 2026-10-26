"""Departamentos: lista con filtros, alta, edición y estado de cuenta."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from ..forms import CargoForm, DepartamentoForm, PagoForm
from ..models import Departamento
from ..servicios import crear_cargo, registrar_pago
from .comun import EditarView


class DepartamentoCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Departamento
    form_class = DepartamentoForm
    template_name = 'departamentos/departamentos/form.html'
    success_url = reverse_lazy('departamento_lista')
    success_message = 'Departamento registrado.'


class DepartamentoUpdateView(EditarView):
    model = Departamento
    form_class = DepartamentoForm
    template_name = 'departamentos/departamentos/form.html'
    extra_context = {'titulo': 'Editar Departamento'}
    success_message = 'Departamento actualizado.'

    def get_success_url(self):
        return reverse_lazy('departamento_detalle', kwargs={'pk': self.object.pk})


# Filtros de la lista: parámetro GET -> {valor: filtro del queryset}.
_FILTROS_SI_NO = {
    'inquilino': {'si': Q(tiene_inquilino=True), 'no': Q(tiene_inquilino=False)},
    'cochera': {'si': Q(cochera_monto__gt=0), 'no': Q(cochera_monto=0)},
    'estado': {'activo': Q(activo=True), 'inactivo': Q(activo=False)},
}


class DepartamentoListView(LoginRequiredMixin, ListView):
    model = Departamento
    template_name = 'departamentos/departamentos/lista.html'
    context_object_name = 'departamentos'
    paginate_by = 15

    def get_queryset(self):
        qs = Departamento.objects.all()
        g = self.request.GET
        q = g.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(numero_domicilio__icontains=q) | Q(nombres__icontains=q)
                | Q(apellidos__icontains=q) | Q(celular__icontains=q)
                | Q(celular_inquilino__icontains=q))
        if g.get('tipo') in dict(Departamento.TipoDomicilio.choices):
            qs = qs.filter(tipo_domicilio=g['tipo'])
        for param, opciones in _FILTROS_SI_NO.items():
            if g.get(param) in opciones:
                qs = qs.filter(opciones[g[param]])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        ctx['querystring'] = params.urlencode()
        ctx['filtros'] = {k: self.request.GET.get(k, '')
                          for k in ('q', 'tipo', 'inquilino', 'cochera', 'estado')}
        ctx['tipos'] = Departamento.TipoDomicilio.choices
        return ctx


@login_required
def departamento_detalle(request, pk):
    """Estado de cuenta del depto: cargos, pagos, y registro de pago/cargo."""
    departamento = get_object_or_404(Departamento, pk=pk)

    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'pago':
            form = PagoForm(request.POST)
            if form.is_valid():
                registrar_pago(departamento, form.cleaned_data['monto'],
                               concepto=form.cleaned_data['concepto'],
                               metodo=form.cleaned_data['metodo'])
                destino = form.cleaned_data['concepto'] or 'la deuda más antigua'
                messages.success(request, f'Pago registrado (aplicado a {destino}).')
            else:
                messages.error(request, 'Revisa el monto del pago.')
        elif accion == 'cargo':
            form = CargoForm(request.POST)
            if form.is_valid():
                crear_cargo(departamento, form.cleaned_data['concepto'], form.periodo,
                            form.cleaned_data['monto'], form.cleaned_data['descripcion'])
                messages.success(request, 'Cargo agregado.')
            else:
                messages.error(request, 'Revisa los datos del cargo.')
        return redirect('departamento_detalle', pk=departamento.pk)

    cargos = list(departamento.cargos.select_related('concepto').prefetch_related('imputaciones'))
    pagos = list(departamento.pagos.select_related('concepto').prefetch_related('imputaciones'))
    facturado = sum((c.monto for c in cargos), Decimal('0.00'))
    cobrado = sum((c.total_imputado for c in cargos), Decimal('0.00'))
    return render(request, 'departamentos/departamentos/detalle.html', {
        'departamento': departamento, 'cargos': cargos, 'pagos': pagos,
        'pago_form': PagoForm(), 'cargo_form': CargoForm(),
        'facturado': facturado, 'cobrado': cobrado, 'saldo': facturado - cobrado,
    })
