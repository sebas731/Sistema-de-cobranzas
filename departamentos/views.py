from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from django.http import HttpResponse

from .forms import (
    DepartamentoForm,
    GastoEdificioForm,
    GastoFijoForm,
    GenerarRecibosForm,
    ImportarExcelForm,
    MesAnioForm,
    PagoForm,
    TarifaAguaForm,
)
from .models import Departamento, GastoEdificio, GastoFijo, Recibo, TarifaAgua
from .servicios import (
    desglose_adicionales,
    desglose_gastos_mes,
    generar_recibos_mes,
    importar_lecturas_excel,
    marcar_vencidos,
    prorrateo_mes,
    registrar_lectura,
    registrar_pago,
    ultima_lectura,
)


def _periodo_desde_get(request):
    """Lee mes/anio del querystring; devuelve (form, periodo|None)."""
    form = MesAnioForm(request.GET or None)
    if request.GET and form.is_valid():
        return form, form.periodo
    return form, None


# --------------------------------------------------------- Balance (home)

@login_required
def balance(request):
    marcar_vencidos()
    filas = []
    tot_fact = tot_cob = tot_saldo = Decimal('0.00')
    deptos = Departamento.objects.filter(activo=True).prefetch_related('recibos__pagos')
    for dep in deptos:
        recibos = list(dep.recibos.all())
        facturado = sum((r.total for r in recibos), Decimal('0.00'))
        cobrado = sum((r.total_pagado for r in recibos), Decimal('0.00'))
        saldo = facturado - cobrado
        pendientes = sum(1 for r in recibos if r.estado != Recibo.Estado.PAGADO)
        tot_fact += facturado
        tot_cob += cobrado
        tot_saldo += saldo
        filas.append({'departamento': dep, 'facturado': facturado, 'cobrado': cobrado,
                      'saldo': saldo, 'pendientes': pendientes})
    filas.sort(key=lambda f: f['saldo'], reverse=True)
    return render(request, 'departamentos/balance.html', {
        'filas': filas, 'total_facturado': tot_fact,
        'total_cobrado': tot_cob, 'total_saldo': tot_saldo,
    })


# ------------------------------------------------------- Balance del mes

@login_required
def balance_mes(request):
    """Balance de un mes: totales (pagos, agua), gastos itemizados y por depto."""
    form, periodo = _periodo_desde_get(request)
    if periodo is None:  # sin mes en la URL: usar el mes actual
        from datetime import date
        hoy = date.today()
        periodo = date(hoy.year, hoy.month, 1)
        form = MesAnioForm(initial={'mes': periodo.month, 'anio': periodo.year})

    recibos = list(
        Recibo.objects.filter(periodo=periodo)
        .select_related('departamento').prefetch_related('pagos'))

    tot_facturado = sum((r.total for r in recibos), Decimal('0.00'))
    tot_pagado = sum((r.total_pagado for r in recibos), Decimal('0.00'))
    tot_agua = sum((r.monto_agua for r in recibos), Decimal('0.00'))
    tot_m3 = sum((r.m3_consumidos for r in recibos), Decimal('0.00'))

    datos = prorrateo_mes(periodo)
    gastos = desglose_gastos_mes(periodo)

    return render(request, 'departamentos/balance_mes.html', {
        'periodo_form': form, 'periodo': periodo, 'recibos': recibos,
        'tot_facturado': tot_facturado, 'tot_pagado': tot_pagado,
        'tot_pendiente': tot_facturado - tot_pagado,
        'tot_agua': tot_agua, 'tot_m3': tot_m3,
        'datos': datos, 'gastos': gastos,
    })


# ------------------------------------------------- Detalle / pagos por depto

@login_required
def departamento_detalle(request, pk):
    departamento = get_object_or_404(Departamento, pk=pk)

    if request.method == 'POST' and request.POST.get('accion') == 'pago':
        recibo = get_object_or_404(Recibo, pk=request.POST.get('recibo_id'),
                                   departamento=departamento)
        form = PagoForm(request.POST)
        if form.is_valid():
            registrar_pago(recibo, form.cleaned_data['monto'], form.cleaned_data['metodo'])
            messages.success(request, f'Pago registrado en el recibo de {recibo.periodo:%m/%Y}.')
        else:
            messages.error(request, 'Revisa el monto del pago.')
        return redirect('departamento_detalle', pk=departamento.pk)

    recibos = list(departamento.recibos.prefetch_related('pagos'))
    # Adjunta a cada recibo el desglose de su columna "gastos adicionales" (para el '?').
    for r in recibos:
        items = desglose_adicionales(r.periodo)
        if r.mora and r.mora > 0:
            items = items + [{'descripcion': 'Mora', 'monto': r.mora, 'compartido': False}]
        r.extras_items = items
    facturado = sum((r.total for r in recibos), Decimal('0.00'))
    cobrado = sum((r.total_pagado for r in recibos), Decimal('0.00'))
    return render(request, 'departamentos/departamento_detalle.html', {
        'departamento': departamento, 'recibos': recibos, 'pago_form': PagoForm(),
        'facturado': facturado, 'cobrado': cobrado, 'saldo': facturado - cobrado,
    })


@login_required
def recibo_boleta(request, pk):
    """Vista tipo boleta con todos los cobros del departamento en ese mes."""
    recibo = get_object_or_404(Recibo.objects.select_related('departamento'), pk=pk)
    items = desglose_adicionales(recibo.periodo)
    return render(request, 'departamentos/recibo_boleta.html', {
        'recibo': recibo, 'departamento': recibo.departamento, 'adicionales_items': items,
    })


@login_required
def buscar_departamento(request):
    q = request.GET.get('q', '').strip()
    if q:
        dep = Departamento.objects.filter(numero_domicilio__iexact=q).first()
        if dep:
            return redirect('departamento_detalle', pk=dep.pk)
        messages.error(request, f'No existe el departamento "{q}".')
    return redirect('balance')


# ------------------------------------------------------- Departamentos

class DepartamentoCreateView(LoginRequiredMixin, CreateView):
    model = Departamento
    form_class = DepartamentoForm
    template_name = 'departamentos/departamento_form.html'
    success_url = reverse_lazy('departamento_lista')

    def form_valid(self, form):
        messages.success(self.request, 'Departamento registrado.')
        return super().form_valid(form)


class DepartamentoUpdateView(LoginRequiredMixin, UpdateView):
    model = Departamento
    form_class = DepartamentoForm
    template_name = 'departamentos/departamento_form.html'
    extra_context = {'titulo': 'Editar Departamento'}

    def get_success_url(self):
        return reverse_lazy('departamento_detalle', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Departamento actualizado.')
        return super().form_valid(form)


class DepartamentoListView(LoginRequiredMixin, ListView):
    model = Departamento
    template_name = 'departamentos/departamento_lista.html'
    context_object_name = 'departamentos'


# ------------------------------------------------------- Gastos fijos

@login_required
def gastos_fijos(request):
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'agregar':
            form = GastoFijoForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, 'Gasto fijo agregado.')
            else:
                messages.error(request, 'Revisa los datos del gasto fijo.')
        elif accion == 'eliminar':
            GastoFijo.objects.filter(pk=request.POST.get('gasto_id')).delete()
            messages.success(request, 'Gasto fijo eliminado.')
        return redirect('gastos_fijos')

    return render(request, 'departamentos/gastos_fijos.html', {
        'gastos': GastoFijo.objects.all(),
        'form': GastoFijoForm(),
    })


class GastoFijoUpdateView(LoginRequiredMixin, UpdateView):
    model = GastoFijo
    form_class = GastoFijoForm
    template_name = 'departamentos/gasto_fijo_form.html'
    success_url = reverse_lazy('gastos_fijos')
    extra_context = {'titulo': 'Editar gasto fijo'}

    def form_valid(self, form):
        messages.success(self.request, 'Gasto fijo actualizado.')
        return super().form_valid(form)


# ------------------------------------------------------- Gastos del mes

@login_required
def gastos_mes(request):
    form, periodo = _periodo_desde_get(request)
    if periodo is None:  # sin mes en la URL: usar el mes actual
        from datetime import date
        hoy = date.today()
        periodo = date(hoy.year, hoy.month, 1)
        form = MesAnioForm(initial={'mes': periodo.month, 'anio': periodo.year})

    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'agregar':
            gform = GastoEdificioForm(request.POST)
            if gform.is_valid():
                gasto = gform.save(commit=False)
                gasto.periodo = periodo
                gasto.save()
                messages.success(request, 'Gasto agregado.')
            else:
                messages.error(request, 'Revisa los datos del gasto.')
        elif accion == 'eliminar':
            GastoEdificio.objects.filter(pk=request.POST.get('gasto_id'), periodo=periodo).delete()
            messages.success(request, 'Gasto eliminado.')
        return redirect(f"{request.path}?mes={periodo.month}&anio={periodo.year}")

    gastos = GastoEdificio.objects.filter(periodo=periodo)
    datos = prorrateo_mes(periodo)
    return render(request, 'departamentos/gastos_mes.html', {
        'periodo_form': form, 'periodo': periodo, 'gastos': gastos,
        'gasto_form': GastoEdificioForm(), 'datos': datos,
    })


# ------------------------------------------------------- Lecturas del mes

@login_required
def lecturas_mes(request):
    form, periodo = _periodo_desde_get(request)
    if periodo is None:
        from datetime import date
        hoy = date.today()
        periodo = date(hoy.year, hoy.month, 1)
        form = MesAnioForm(initial={'mes': periodo.month, 'anio': periodo.year})

    if request.method == 'POST':
        guardadas = 0
        for dep in Departamento.objects.filter(activo=True):
            raw = request.POST.get(f'actual_{dep.id}', '').strip()
            if raw == '':
                continue
            try:
                lectura_actual = Decimal(raw)
            except (InvalidOperation, ValueError):
                continue
            registrar_lectura(dep, periodo, lectura_actual)
            guardadas += 1
        messages.success(request, f'{guardadas} lectura(s) guardadas.')
        return redirect(f"{request.path}?mes={periodo.month}&anio={periodo.year}")

    filas = []
    for dep in Departamento.objects.filter(activo=True):
        actual = dep.lecturas.filter(periodo=periodo).first()
        previa = ultima_lectura(dep, antes_de=periodo)
        anterior = (actual.lectura_anterior if actual
                    else (previa.lectura_actual if previa else Decimal('0.00')))
        filas.append({
            'dep': dep, 'anterior': anterior,
            'actual': actual.lectura_actual if actual else '',
            'm3': actual.m3_consumidos if actual else None,
        })
    return render(request, 'departamentos/lecturas_mes.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,
    })


# ------------------------------------------------------- Generar recibos

@login_required
def generar_recibos(request):
    if request.method == 'POST':
        form = GenerarRecibosForm(request.POST)
        if form.is_valid():
            periodo = form.periodo
            resumen = generar_recibos_mes(periodo, form.cleaned_data['dia_vencimiento'])
            marcar_vencidos()
            if resumen['n'] == 0:
                messages.error(request, 'No hay departamentos activos.')
            else:
                messages.success(
                    request,
                    f"Recibos de {periodo:%m/%Y}: {resumen['creados']} creados, "
                    f"{resumen['actualizados']} actualizados, "
                    f"{resumen['omitidos_pagados']} ya pagados.")
            return redirect('balance')
    else:
        form = GenerarRecibosForm()
    return render(request, 'departamentos/generar_recibos.html', {'form': form})


# ------------------------------------------------------- Importar Excel

@login_required
def importar_excel(request):
    resumen = None
    if request.method == 'POST':
        form = ImportarExcelForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                resumen = importar_lecturas_excel(form.cleaned_data['archivo'], form.periodo)
                messages.success(
                    request,
                    f"Importado para {form.periodo:%m/%Y}: {resumen['departamentos']} "
                    f"departamentos y {resumen['lecturas']} lecturas.")
            except Exception as e:
                messages.error(request, f'No se pudo leer el Excel: {e}')
        else:
            messages.error(request, 'Revisa el mes, el año y el archivo.')
    else:
        form = ImportarExcelForm()
    return render(request, 'departamentos/importar.html', {'form': form, 'resumen': resumen})


@login_required
def plantilla_excel(request):
    """Descarga una plantilla .xlsx con el formato que espera el sistema."""
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Datos'
    ws.append(['DEPARTAMENTO', 'LECTURA_ANTERIOR', 'LECTURA_ACTUAL', 'COCHERA'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.append(['101', 1330.54, 1334.34, 0])
    ws.append(['104', 625.27, 627.50, 5])
    ws.append(['201', 800.57, 809.94, 0])
    for col, ancho in zip('ABCD', (16, 18, 18, 10)):
        ws.column_dimensions[col].width = ancho

    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="plantilla_cobranzas.xlsx"'
    wb.save(resp)
    return resp


# ------------------------------------------------------- Tarifas de agua

@login_required
def tarifas(request):
    return render(request, 'departamentos/tarifas.html', {
        'tarifas': TarifaAgua.objects.all(),
    })


class TarifaCreateView(LoginRequiredMixin, CreateView):
    model = TarifaAgua
    form_class = TarifaAguaForm
    template_name = 'departamentos/tarifa_form.html'
    success_url = reverse_lazy('tarifas')
    extra_context = {'titulo': 'Nuevo tramo de tarifa'}


class TarifaUpdateView(LoginRequiredMixin, UpdateView):
    model = TarifaAgua
    form_class = TarifaAguaForm
    template_name = 'departamentos/tarifa_form.html'
    success_url = reverse_lazy('tarifas')
    extra_context = {'titulo': 'Editar tramo de tarifa'}
