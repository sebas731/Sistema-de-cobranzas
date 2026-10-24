"""Balance financiero del edificio (ingresos vs egresos), mensual o anual."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..forms import MESES_ES, MesAnioForm
from ..models import GastoEdificio, GastoFijo, Pago

CERO = Decimal('0.00')


def _ordenar_filas(agg, n):
    """[{nombre, movs, monto}] ordenadas por monto desc; `n(v)` = nº de movimientos."""
    return sorted(({'nombre': k, 'movs': n(v), 'monto': v['monto']} for k, v in agg.items()),
                  key=lambda x: x['monto'], reverse=True)


def _balance_ingresos(anio, mes):
    """Ingresos del mes = pagos recibidos, agrupados por concepto (de sus
    imputaciones). Devuelve (total, filas[{nombre,movs,monto}], movimientos)."""
    pagos = (Pago.objects.filter(fecha_pago__year=anio, fecha_pago__month=mes)
             .select_related('departamento').prefetch_related('imputaciones__cargo__concepto'))
    agg = defaultdict(lambda: {'monto': CERO, 'pagos': set()})
    movs = defaultdict(list)
    total = CERO
    for p in pagos:
        total += p.monto
        reparto = defaultdict(lambda: CERO)
        for i in p.imputaciones.all():
            reparto[i.cargo.concepto.nombre] += i.monto
        sobrante = p.monto - sum(reparto.values(), CERO)
        if sobrante > 0:
            reparto['A cuenta'] += sobrante
        for nom, monto in reparto.items():
            agg[nom]['monto'] += monto
            agg[nom]['pagos'].add(p.id)
            movs[nom].append({'fecha': p.fecha_pago.strftime('%d/%m/%Y'),
                              'ref': p.departamento.numero_domicilio,
                              'monto': f'{monto:.2f}', 'nota': p.metodo or ''})
    return total, _ordenar_filas(agg, lambda v: len(v['pagos'])), movs


def _balance_egresos(anio, mes):
    """Egresos del mes = gastos fijos (recurrentes) + gastos del mes, agrupados
    por descripción. Devuelve (total, filas, movimientos)."""
    agg = defaultdict(lambda: {'monto': CERO, 'n': 0})
    movs = defaultdict(list)
    for ref, gastos in (('Fijo', GastoFijo.objects.filter(activo=True)),
                        ('Del mes', GastoEdificio.objects.filter(periodo__year=anio, periodo__month=mes))):
        for g in gastos:
            agg[g.descripcion]['monto'] += g.monto
            agg[g.descripcion]['n'] += 1
            movs[g.descripcion].append({'fecha': '', 'ref': ref, 'monto': f'{g.monto:.2f}', 'nota': ''})
    total = sum((v['monto'] for v in agg.values()), CERO)
    return total, _ordenar_filas(agg, lambda v: v['n']), movs


def _pct(valor, maximo):
    return int(valor / maximo * 100) if maximo else 0


def _barras(d):
    mx = max(d.values()) if d else Decimal('1')
    return sorted(({'nombre': k, 'monto': v, 'pct': _pct(v, mx)} for k, v in d.items()),
                  key=lambda x: x['monto'], reverse=True)


def _tono(saldo):
    return 'success' if saldo >= 0 else 'danger'


def _filas_texto(filas):
    return [{'nombre': x['nombre'], 'movs': x['movs'], 'monto': f'{x["monto"]:.2f}'} for x in filas]


def _contexto_anual(anio):
    meses, drawer = [], {}
    tot_ing = tot_egr = CERO
    dist_ing, dist_egr = defaultdict(lambda: CERO), defaultdict(lambda: CERO)
    for m in range(1, 13):
        ing, ingf, _ = _balance_ingresos(anio, m)
        egr, egrf, _ = _balance_egresos(anio, m)
        tot_ing += ing
        tot_egr += egr
        for f in ingf:
            dist_ing[f['nombre']] += f['monto']
        for f in egrf:
            dist_egr[f['nombre']] += f['monto']
        saldo = ing - egr
        meses.append({'mes': m, 'nombre': MESES_ES[m], 'ingresos': ing, 'egresos': egr,
                      'saldo': saldo, 'tone': _tono(saldo)})
        drawer[str(m)] = {
            'titulo': f'{MESES_ES[m]} {anio}', 'ingresos': f'{ing:.2f}',
            'egresos': f'{egr:.2f}', 'saldo': f'{saldo:.2f}',
            'ing_filas': _filas_texto(ingf), 'egr_filas': _filas_texto(egrf)}
    max_v = max([x['ingresos'] for x in meses] + [x['egresos'] for x in meses] + [Decimal('1')])
    for x in meses:
        x['ing_pct'] = _pct(x['ingresos'], max_v)
        x['egr_pct'] = _pct(x['egresos'], max_v)
    saldo_anual = tot_ing - tot_egr
    return dict(meses=meses, tot_ing=tot_ing, tot_egr=tot_egr, saldo_anual=saldo_anual,
                saldo_tone=_tono(saldo_anual), dist_ing=_barras(dist_ing),
                dist_egr=_barras(dist_egr), drawer_json=drawer)


def _contexto_mensual(anio, mes):
    ing, ingf, ingm = _balance_ingresos(anio, mes)
    egr, egrf, egrm = _balance_egresos(anio, mes)
    saldo = ing - egr
    mx = max(ing, egr, Decimal('1'))
    drawer = {}
    for f in ingf:
        drawer['ing::' + f['nombre']] = {'titulo': 'Ingresos · ' + f['nombre'], 'movs': ingm[f['nombre']]}
    for f in egrf:
        drawer['egr::' + f['nombre']] = {'titulo': 'Egresos · ' + f['nombre'], 'movs': egrm[f['nombre']]}
    return dict(ing_total=ing, egr_total=egr, saldo=saldo, saldo_tone=_tono(saldo),
                ing_filas=ingf, egr_filas=egrf,
                ing_pct=int(ing / mx * 100), egr_pct=int(egr / mx * 100), drawer_json=drawer)


@login_required
def balance(request):
    """Balance financiero del edificio (ingresos vs egresos), mensual o anual.

    Solo agrega/organiza datos existentes (pagos = ingresos, gastos = egresos);
    no modifica modelos, servicios ni cálculos.
    """
    hoy = date.today()
    modo = 'anual' if request.GET.get('modo') == 'anual' else 'mensual'
    try:
        anio = int(request.GET.get('anio') or hoy.year)
        mes = int(request.GET.get('mes') or hoy.month)
    except ValueError:
        anio, mes = hoy.year, hoy.month

    ctx = {'modo': modo, 'mes': mes, 'anio': anio,
           'periodo_form': MesAnioForm(initial={'mes': mes, 'anio': anio})}
    ctx.update(_contexto_anual(anio) if modo == 'anual' else _contexto_mensual(anio, mes))
    return render(request, 'departamentos/balance.html', ctx)
