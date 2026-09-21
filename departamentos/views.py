from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from django.http import HttpResponse

from .forms import (
    CargoForm,
    DepartamentoForm,
    GastoEdificioForm,
    GastoFijoForm,
    GenerarRecibosForm,
    ImportarExcelForm,
    MesAnioForm,
    PagoForm,
    ProveedorForm,
    TarifaAguaForm,
)
from .models import (
    Cargo, Concepto, Departamento, GastoEdificio, GastoFijo, LecturaMedidor,
    Pago, Proveedor, TarifaAgua,
)
from .servicios import (
    crear_cargo,
    deuda_anterior,
    deudores,
    desglose_gastos_mes,
    generar_cargos_mes,
    importar_lecturas_excel,
    prorrateo_mes,
    registrar_lectura,
    registrar_pago,
    saldo_departamento,
    ultima_lectura,
)


def _periodo_desde_get(request):
    """Lee mes/anio del querystring; devuelve (form, periodo|None)."""
    form = MesAnioForm(request.GET or None)
    if request.GET and form.is_valid():
        return form, form.periodo
    return form, None


# --------------------------------------------------------- Balance (financiero)

MESES_ES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
            'Agosto', 'Setiembre', 'Octubre', 'Noviembre', 'Diciembre']


def _balance_ingresos(anio, mes):
    """Ingresos del mes = pagos recibidos, agrupados por concepto (de sus
    imputaciones). Devuelve (total, filas[{nombre,movs,monto}], movimientos)."""
    pagos = (Pago.objects.filter(fecha_pago__year=anio, fecha_pago__month=mes)
             .select_related('departamento').prefetch_related('imputaciones__cargo__concepto'))
    agg = defaultdict(lambda: {'monto': Decimal('0.00'), 'pagos': set()})
    movs = defaultdict(list)
    total = Decimal('0.00')
    for p in pagos:
        total += p.monto
        reparto = defaultdict(lambda: Decimal('0.00'))
        imp_tot = Decimal('0.00')
        for i in p.imputaciones.all():
            reparto[i.cargo.concepto.nombre] += i.monto
            imp_tot += i.monto
        if p.monto - imp_tot > 0:
            reparto['A cuenta'] += p.monto - imp_tot
        for nom, monto in reparto.items():
            agg[nom]['monto'] += monto
            agg[nom]['pagos'].add(p.id)
            movs[nom].append({'fecha': p.fecha_pago.strftime('%d/%m/%Y'),
                              'ref': p.departamento.numero_domicilio,
                              'monto': f'{monto:.2f}', 'nota': p.metodo or ''})
    filas = sorted(({'nombre': k, 'movs': len(v['pagos']), 'monto': v['monto']}
                    for k, v in agg.items()), key=lambda x: x['monto'], reverse=True)
    return total, filas, movs


def _balance_egresos(anio, mes):
    """Egresos del mes = gastos fijos (recurrentes) + gastos del mes, agrupados
    por descripción. Devuelve (total, filas, movimientos)."""
    agg = defaultdict(lambda: {'monto': Decimal('0.00'), 'n': 0})
    movs = defaultdict(list)
    for g in GastoFijo.objects.filter(activo=True):
        agg[g.descripcion]['monto'] += g.monto; agg[g.descripcion]['n'] += 1
        movs[g.descripcion].append({'fecha': '', 'ref': 'Fijo', 'monto': f'{g.monto:.2f}', 'nota': ''})
    for g in GastoEdificio.objects.filter(periodo__year=anio, periodo__month=mes):
        agg[g.descripcion]['monto'] += g.monto; agg[g.descripcion]['n'] += 1
        movs[g.descripcion].append({'fecha': '', 'ref': 'Del mes', 'monto': f'{g.monto:.2f}', 'nota': ''})
    total = sum((v['monto'] for v in agg.values()), Decimal('0.00'))
    filas = sorted(({'nombre': k, 'movs': v['n'], 'monto': v['monto']}
                    for k, v in agg.items()), key=lambda x: x['monto'], reverse=True)
    return total, filas, movs


@login_required
def balance(request):
    """Balance financiero del edificio (ingresos vs egresos), mensual o anual.

    Solo agrega/organiza datos existentes (pagos = ingresos, gastos = egresos);
    no modifica modelos, servicios ni cálculos.
    """
    from datetime import date
    hoy = date.today()
    modo = 'anual' if request.GET.get('modo') == 'anual' else 'mensual'
    try:
        anio = int(request.GET.get('anio') or hoy.year)
        mes = int(request.GET.get('mes') or hoy.month)
    except ValueError:
        anio, mes = hoy.year, hoy.month

    def barras(d):
        mx = max(d.values()) if d else Decimal('1')
        return sorted(({'nombre': k, 'monto': v, 'pct': int(v / mx * 100) if mx else 0}
                       for k, v in d.items()), key=lambda x: x['monto'], reverse=True)

    ctx = {'modo': modo, 'mes': mes, 'anio': anio,
           'periodo_form': MesAnioForm(initial={'mes': mes, 'anio': anio})}

    if modo == 'anual':
        meses, drawer = [], {}
        tot_ing = tot_egr = Decimal('0.00')
        dist_ing, dist_egr = defaultdict(lambda: Decimal('0.00')), defaultdict(lambda: Decimal('0.00'))
        for m in range(1, 13):
            ing, ingf, _ = _balance_ingresos(anio, m)
            egr, egrf, _ = _balance_egresos(anio, m)
            tot_ing += ing; tot_egr += egr
            for f in ingf:
                dist_ing[f['nombre']] += f['monto']
            for f in egrf:
                dist_egr[f['nombre']] += f['monto']
            saldo = ing - egr
            meses.append({'mes': m, 'nombre': MESES_ES[m], 'ingresos': ing, 'egresos': egr,
                          'saldo': saldo, 'tone': 'success' if saldo >= 0 else 'danger'})
            drawer[str(m)] = {
                'titulo': f'{MESES_ES[m]} {anio}', 'ingresos': f'{ing:.2f}',
                'egresos': f'{egr:.2f}', 'saldo': f'{saldo:.2f}',
                'ing_filas': [{'nombre': x['nombre'], 'movs': x['movs'], 'monto': f'{x["monto"]:.2f}'} for x in ingf],
                'egr_filas': [{'nombre': x['nombre'], 'movs': x['movs'], 'monto': f'{x["monto"]:.2f}'} for x in egrf]}
        max_v = max([m_['ingresos'] for m_ in meses] + [m_['egresos'] for m_ in meses] + [Decimal('1')])
        for m_ in meses:
            m_['ing_pct'] = int(m_['ingresos'] / max_v * 100) if max_v else 0
            m_['egr_pct'] = int(m_['egresos'] / max_v * 100) if max_v else 0
        saldo_anual = tot_ing - tot_egr
        ctx.update(meses=meses, tot_ing=tot_ing, tot_egr=tot_egr, saldo_anual=saldo_anual,
                   saldo_tone='success' if saldo_anual >= 0 else 'danger',
                   dist_ing=barras(dist_ing), dist_egr=barras(dist_egr), drawer_json=drawer)
    else:
        ing, ingf, ingm = _balance_ingresos(anio, mes)
        egr, egrf, egrm = _balance_egresos(anio, mes)
        saldo = ing - egr
        mx = max(ing, egr, Decimal('1'))
        drawer = {}
        for f in ingf:
            drawer['ing::' + f['nombre']] = {'titulo': 'Ingresos · ' + f['nombre'], 'movs': ingm[f['nombre']]}
        for f in egrf:
            drawer['egr::' + f['nombre']] = {'titulo': 'Egresos · ' + f['nombre'], 'movs': egrm[f['nombre']]}
        ctx.update(ing_total=ing, egr_total=egr, saldo=saldo,
                   saldo_tone='success' if saldo >= 0 else 'danger',
                   ing_filas=ingf, egr_filas=egrf,
                   ing_pct=int(ing / mx * 100), egr_pct=int(egr / mx * 100),
                   drawer_json=drawer)

    return render(request, 'departamentos/balance.html', ctx)


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

    # Cargos del mes agrupados por departamento y por concepto.
    cargos = (Cargo.objects.filter(periodo=periodo)
              .select_related('departamento', 'concepto').prefetch_related('imputaciones'))
    por_dep = {}
    for c in cargos:
        fila = por_dep.setdefault(c.departamento_id, {
            'departamento': c.departamento,
            'AGUA': Decimal('0.00'), 'AREA_COMUN': Decimal('0.00'),
            'MANTENIMIENTO': Decimal('0.00'), 'COCHERA': Decimal('0.00'),
            'extras': Decimal('0.00'), 'total': Decimal('0.00'),
            'pagado': Decimal('0.00'), 'saldo': Decimal('0.00'),
        })
        clave = c.concepto.codigo if c.concepto.codigo in fila else 'extras'
        fila[clave] += c.monto
        fila['total'] += c.monto
        fila['pagado'] += c.total_imputado
        fila['saldo'] += c.saldo

    filas = sorted(por_dep.values(), key=lambda f: f['departamento'].numero_domicilio)
    tot_facturado = sum((f['total'] for f in filas), Decimal('0.00'))
    tot_pagado = sum((f['pagado'] for f in filas), Decimal('0.00'))
    tot_agua = sum((f['AGUA'] for f in filas), Decimal('0.00'))
    tot_m3 = sum((l.m3_consumidos for l in LecturaMedidor.objects.filter(periodo=periodo)),
                 Decimal('0.00'))

    datos = prorrateo_mes(periodo)
    gastos = desglose_gastos_mes(periodo)

    return render(request, 'departamentos/balance_mes.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,
        'tot_facturado': tot_facturado, 'tot_pagado': tot_pagado,
        'tot_pendiente': tot_facturado - tot_pagado,
        'tot_agua': tot_agua, 'tot_m3': tot_m3,
        'datos': datos, 'gastos': gastos,
    })


# ------------------------------------------------- Detalle / pagos por depto

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
    return render(request, 'departamentos/departamento_detalle.html', {
        'departamento': departamento, 'cargos': cargos, 'pagos': pagos,
        'pago_form': PagoForm(), 'cargo_form': CargoForm(),
        'facturado': facturado, 'cobrado': cobrado, 'saldo': facturado - cobrado,
    })


@login_required
def recibo_boleta(request, pk):
    """Boleta del departamento para un mes (cargos del mes + deuda anterior)."""
    departamento = get_object_or_404(Departamento, pk=pk)
    _, periodo = _periodo_desde_get(request)
    if periodo is None:
        from datetime import date
        hoy = date.today()
        periodo = date(hoy.year, hoy.month, 1)

    cargos = list(departamento.cargos.filter(periodo=periodo)
                  .select_related('concepto').prefetch_related('imputaciones'))
    total_mes = sum((c.monto for c in cargos), Decimal('0.00'))
    anterior = deuda_anterior(departamento, periodo)
    return render(request, 'departamentos/recibo_boleta.html', {
        'departamento': departamento, 'periodo': periodo, 'cargos': cargos,
        'total_mes': total_mes, 'deuda_anterior': anterior,
        'total_pagar': total_mes + anterior,
    })


@login_required
def registrar_pagos(request):
    """Registro de pagos en lote (atómico): masivo (varios deptos, mismo concepto
    y monto) o individual (fila por fila con depto/concepto/monto propios)."""
    if request.method == 'POST':
        modo = request.POST.get('modo')
        try:
            with transaction.atomic():
                n = 0
                if modo == 'masivo':
                    concepto = get_object_or_404(Concepto, pk=request.POST.get('concepto'))
                    monto = Decimal(request.POST.get('monto') or '0')
                    metodo = request.POST.get('metodo', '')
                    ids = request.POST.getlist('deptos')
                    if monto <= 0 or not ids:
                        raise ValueError('Elige un monto y al menos un departamento.')
                    for did in ids:
                        dep = get_object_or_404(Departamento, pk=did)
                        registrar_pago(dep, monto, concepto=concepto, metodo=metodo)
                        n += 1
                elif modo == 'individual':
                    deptos = request.POST.getlist('row_depto')
                    conceptos = request.POST.getlist('row_concepto')
                    montos = request.POST.getlist('row_monto')
                    metodos = request.POST.getlist('row_metodo')
                    if not deptos:
                        raise ValueError('Agrega al menos un pago a la lista.')
                    for i, did in enumerate(deptos):
                        dep = get_object_or_404(Departamento, pk=did)
                        concepto = get_object_or_404(Concepto, pk=conceptos[i])
                        monto = Decimal(montos[i] or '0')
                        if monto <= 0:
                            raise ValueError('Todos los montos deben ser mayores a 0.')
                        metodo = metodos[i] if i < len(metodos) else ''
                        registrar_pago(dep, monto, concepto=concepto, metodo=metodo)
                        n += 1
                else:
                    raise ValueError('Modo inválido.')
            messages.success(request, f'{n} pago(s) registrados correctamente.')
            return redirect('deudores')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'No se pudo registrar: {e}')

    return render(request, 'departamentos/registrar_pagos.html', {
        'departamentos': Departamento.objects.filter(activo=True),
        'conceptos': Concepto.objects.filter(activo=True),
    })


@login_required
def registrar_cargos(request):
    """Asigna cargos (multas, moras, derramas) en lote: masivo (mismo concepto y
    monto a varios deptos) o individual (fila por fila). Atómico."""
    form = MesAnioForm(request.POST or None)
    if request.method == 'POST':
        if not form.is_valid():
            messages.error(request, 'Elige un mes y año válidos.')
            return redirect('registrar_cargos')
        periodo = form.periodo
        modo = request.POST.get('modo')
        try:
            with transaction.atomic():
                n = 0
                if modo == 'masivo':
                    concepto = get_object_or_404(Concepto, pk=request.POST.get('concepto'))
                    monto = Decimal(request.POST.get('monto') or '0')
                    desc = request.POST.get('descripcion', '')
                    ids = request.POST.getlist('deptos')
                    if monto <= 0 or not ids:
                        raise ValueError('Elige un monto y al menos un departamento.')
                    for did in ids:
                        dep = get_object_or_404(Departamento, pk=did)
                        crear_cargo(dep, concepto, periodo, monto, desc)
                        n += 1
                elif modo == 'individual':
                    deptos = request.POST.getlist('row_depto')
                    conceptos = request.POST.getlist('row_concepto')
                    montos = request.POST.getlist('row_monto')
                    descs = request.POST.getlist('row_desc')
                    if not deptos:
                        raise ValueError('Agrega al menos un cargo a la lista.')
                    for i, did in enumerate(deptos):
                        dep = get_object_or_404(Departamento, pk=did)
                        concepto = get_object_or_404(Concepto, pk=conceptos[i])
                        monto = Decimal(montos[i] or '0')
                        if monto <= 0:
                            raise ValueError('Todos los montos deben ser mayores a 0.')
                        desc = descs[i] if i < len(descs) else ''
                        crear_cargo(dep, concepto, periodo, monto, desc)
                        n += 1
                else:
                    raise ValueError('Modo inválido.')
            messages.success(request, f'{n} cargo(s) asignados para {periodo:%m/%Y}.')
            return redirect('deudores')
        except (ValueError, InvalidOperation) as e:
            messages.error(request, f'No se pudo asignar: {e}')

    return render(request, 'departamentos/registrar_cargos.html', {
        'departamentos': Departamento.objects.filter(activo=True),
        'conceptos': Concepto.objects.filter(activo=True),
        'periodo_form': MesAnioForm(),
    })


@login_required
def estilos(request):
    """Guía visual (styleguide) del Design System: tokens y componentes."""
    return render(request, 'departamentos/estilos.html', {})


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
        if g.get('inquilino') == 'si':
            qs = qs.filter(tiene_inquilino=True)
        elif g.get('inquilino') == 'no':
            qs = qs.filter(tiene_inquilino=False)
        if g.get('cochera') == 'si':
            qs = qs.filter(cochera_monto__gt=0)
        elif g.get('cochera') == 'no':
            qs = qs.filter(cochera_monto=0)
        if g.get('estado') == 'activo':
            qs = qs.filter(activo=True)
        elif g.get('estado') == 'inactivo':
            qs = qs.filter(activo=False)
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


# ------------------------------------------------------- Proveedores

@login_required
def proveedores(request):
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'agregar':
            form = ProveedorForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, 'Proveedor agregado.')
            else:
                messages.error(request, 'Revisa los datos del proveedor.')
        elif accion == 'eliminar':
            Proveedor.objects.filter(pk=request.POST.get('proveedor_id')).delete()
            messages.success(request, 'Proveedor eliminado.')
        return redirect('proveedores')

    return render(request, 'departamentos/proveedores.html', {
        'proveedores': Proveedor.objects.all(),
        'form': ProveedorForm(),
    })


class ProveedorUpdateView(LoginRequiredMixin, UpdateView):
    model = Proveedor
    form_class = ProveedorForm
    template_name = 'departamentos/proveedor_form.html'
    success_url = reverse_lazy('proveedores')
    extra_context = {'titulo': 'Editar proveedor'}

    def form_valid(self, form):
        messages.success(self.request, 'Proveedor actualizado.')
        return super().form_valid(form)


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
                # Personalizado: aplicar solo a los deptos marcados (vacío = todos).
                ids = request.POST.getlist('deptos')
                if ids:
                    gasto.departamentos.set(Departamento.objects.filter(pk__in=ids))
                messages.success(request, 'Gasto agregado.')
            else:
                messages.error(request, 'Revisa los datos del gasto.')
        elif accion == 'eliminar':
            GastoEdificio.objects.filter(pk=request.POST.get('gasto_id'), periodo=periodo).delete()
            messages.success(request, 'Gasto eliminado.')
        return redirect(f"{request.path}?mes={periodo.month}&anio={periodo.year}")

    gastos = GastoEdificio.objects.filter(periodo=periodo).prefetch_related('departamentos')
    datos = prorrateo_mes(periodo)
    return render(request, 'departamentos/gastos_mes.html', {
        'periodo_form': form, 'periodo': periodo, 'gastos': gastos,
        'gasto_form': GastoEdificioForm(), 'datos': datos,
        'departamentos': Departamento.objects.filter(activo=True),
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
            resumen = generar_cargos_mes(periodo, form.cleaned_data['dia_vencimiento'])
            if resumen['n'] == 0:
                messages.error(request, 'No hay departamentos activos.')
            else:
                messages.success(
                    request,
                    f"Cargos de {periodo:%m/%Y}: {resumen['creados']} creados, "
                    f"{resumen['actualizados']} actualizados, "
                    f"{resumen['omitidos_pagados']} ya pagados.")
            return redirect(f'{reverse("balance_mes")}?mes={periodo.month}&anio={periodo.year}')
    else:
        form = GenerarRecibosForm()
    return render(request, 'departamentos/generar_recibos.html', {'form': form})


# ------------------------------------------------------- Deudores

@login_required
def vista_deudores(request):
    """Deudores (dashboard). Solo prepara/organiza datos para la UI; no cambia
    ningún cálculo (usa servicios.deudores()). Enriquecido con estado, historial
    y desglose con nombres legibles para el rediseño."""
    from datetime import date, timedelta
    from django.utils import timezone

    form, periodo = _periodo_desde_get(request)
    if periodo is None:
        hoy_ = date.today()
        periodo = date(hoy_.year, hoy_.month, 1)
        form = MesAnioForm(initial={'mes': periodo.month, 'anio': periodo.year})

    hoy = timezone.localdate()
    pronto = hoy + timedelta(days=7)
    nombres = dict(Concepto.objects.values_list('codigo', 'nombre'))
    meses_es = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
                'Agosto', 'Setiembre', 'Octubre', 'Noviembre', 'Diciembre']

    def estado_de(cargos_impagos):
        if any(c.fecha_vencimiento < hoy for c in cargos_impagos):
            return 'vencido', 'Vencido', 'danger'
        if any(hoy <= c.fecha_vencimiento <= pronto for c in cargos_impagos):
            return 'por_vencer', 'Por vencer', 'warning'
        return 'pendiente', 'Pendiente', 'info'

    filas, drawer_json = [], []
    tot_general = tot_vencido = tot_por_vencer = Decimal('0.00')
    por_concepto = {}

    for f in deudores(periodo):
        dep = f['departamento']
        impagos = [c for c in dep.cargos.all() if c.saldo > 0]
        estado, estado_label, estado_tone = estado_de(impagos)
        vencido = sum((c.saldo for c in impagos if c.fecha_vencimiento < hoy), Decimal('0.00'))
        porvencer = sum((c.saldo for c in impagos if hoy <= c.fecha_vencimiento <= pronto), Decimal('0.00'))
        tot_general += f['total']; tot_vencido += vencido; tot_por_vencer += porvencer

        # Desglose con nombres legibles (ordenado por monto desc)
        desglose = sorted(
            ({'nombre': nombres.get(cod, cod), 'monto': monto}
             for cod, monto in f['por_concepto'].items()),
            key=lambda x: x['monto'], reverse=True)
        for cod, monto in f['por_concepto'].items():
            por_concepto[cod] = por_concepto.get(cod, Decimal('0.00')) + monto

        # Historial de deuda por mes (para el drawer)
        hist = {}
        for c in impagos:
            hist[c.periodo] = hist.get(c.periodo, Decimal('0.00')) + c.saldo
        historial = [{'mes': f'{meses_es[p.month]} {p.year}', 'monto': f'{m:.2f}'}
                     for p, m in sorted(hist.items())]

        filas.append({
            'id': dep.pk, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
            'total': f['total'], 'mes': f['mes'], 'anterior': f['anterior'],
            'desglose': desglose, 'estado': estado, 'estado_label': estado_label,
            'estado_tone': estado_tone,
            'total_tone': 'danger' if estado == 'vencido' else '',
            'conceptos_cod': ' '.join(f['por_concepto'].keys()),
            'url_detalle': reverse('departamento_detalle', args=[dep.pk]),
        })
        drawer_json.append({
            'id': dep.pk, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
            'total': f'{f["total"]:.2f}', 'estado_label': estado_label, 'estado_tone': estado_tone,
            'desglose': [{'nombre': d['nombre'], 'monto': f'{d["monto"]:.2f}'} for d in desglose],
            'historial': historial, 'url': reverse('departamento_detalle', args=[dep.pk]),
        })

    n_deud = len(filas)
    promedio = (tot_general / n_deud) if n_deud else Decimal('0.00')
    tot_pendiente_resto = tot_general - tot_vencido - tot_por_vencer

    # Deuda por concepto (para la mini-gráfica del resumen)
    max_c = max(por_concepto.values()) if por_concepto else Decimal('1')
    grafico = sorted(
        ({'nombre': nombres.get(cod, cod), 'monto': monto,
          'pct': int(monto / max_c * 100) if max_c else 0}
         for cod, monto in por_concepto.items()),
        key=lambda x: x['monto'], reverse=True)

    # Top deudores (departamentos con más deuda) — filas ya vienen ordenadas desc.
    max_t = filas[0]['total'] if filas else Decimal('1')
    top_deudores = [{'id': f['id'], 'numero': f['numero'], 'total': f['total'],
                     'estado_tone': f['estado_tone'],
                     'pct': int(f['total'] / max_t * 100) if max_t else 0}
                    for f in filas[:8]]

    return render(request, 'departamentos/deudores.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,
        'conceptos': list(Concepto.objects.order_by('orden').values_list('codigo', 'nombre')),
        'kpi': {'total': tot_general, 'n_deudores': n_deud, 'vencido': tot_vencido,
                'promedio': promedio},
        'resumen': {'total': tot_general, 'vencido': tot_vencido, 'por_vencer': tot_por_vencer,
                    'pendiente': tot_pendiente_resto},
        'grafico': grafico, 'top_deudores': top_deudores, 'drawer_json': drawer_json,
    })


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
