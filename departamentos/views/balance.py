"""Balance financiero del edificio (ingresos vs egresos), mensual o anual."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..forms import MESES_ES
from ..models import Concepto, GastoEdificio, GastoFijo, Pago

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


def _tono(saldo):
    return 'success' if saldo >= 0 else 'danger'


def _filas_texto(filas):
    return [{'nombre': x['nombre'], 'movs': x['movs'], 'monto': f'{x["monto"]:.2f}'} for x in filas]


def _mes_anterior(anio, mes):
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _variacion(actual, anterior, sube_es_bueno=True):
    """Flecha frente al periodo anterior: {dir: up|down|flat, monto, pct, tono}."""
    dif = actual - anterior
    if dif == 0:
        return {'dir': 'flat', 'monto': f'{CERO:.2f}', 'pct': None, 'tono': 'muted'}
    bueno = (dif > 0) == sube_es_bueno
    pct = int(abs(dif) / abs(anterior) * 100) if anterior else None
    return {'dir': 'up' if dif > 0 else 'down', 'monto': f'{abs(dif):.2f}', 'pct': pct,
            'tono': 'success' if bueno else 'danger'}


def _dona(filas, maximo=5):
    """Parte-del-todo para la dona: los `maximo` conceptos más grandes y el resto en
    «Otros» (el color sigue al orden de tamaño). [{nombre, monto(str), pct}]."""
    total = sum((f['monto'] for f in filas), CERO)
    top, resto = filas[:maximo], filas[maximo:]
    partes = [(f['nombre'], f['monto']) for f in top]
    if resto:
        partes.append(('Otros', sum((f['monto'] for f in resto), CERO)))
    return [{'nombre': n, 'monto': f'{m:.2f}', 'pct': round(float(m / total * 100), 1) if total else 0}
            for n, m in partes if m > 0]


def _serie(meses):
    """[(anio, mes, etiqueta)] -> [{label, ingresos, egresos}] para las barras."""
    out = []
    for a, m, label in meses:
        ing, _, _ = _balance_ingresos(a, m)
        egr, _, _ = _balance_egresos(a, m)
        out.append({'label': label, 'ingresos': f'{ing:.2f}', 'egresos': f'{egr:.2f}'})
    return out


def _ultimos_meses(anio, mes, n=6):
    meses = []
    for _ in range(n):
        meses.append((anio, mes, f'{MESES_ES[mes][:3]} {str(anio)[2:]}'))
        anio, mes = _mes_anterior(anio, mes)
    return meses[::-1]


def _tabla_ingresos(anio, mes=None):
    """Filas de la tabla «Ingresos» como el Excel de la clienta: un renglón por
    concepto y el mantenimiento separado por el mes de la deuda que se cobró
    (Mto. Mayo, Mto. Junio…). Los conceptos manuales activos salen aunque estén
    en 0; lo pagado sin imputar va en «A cuenta». [{concepto, monto}] (str)."""
    filtro = {'fecha_pago__year': anio, **({'fecha_pago__month': mes} if mes else {})}
    pagos = Pago.objects.filter(**filtro).prefetch_related('imputaciones__cargo__concepto')
    filas = {}  # clave de orden -> [etiqueta, monto]
    for c in Concepto.objects.filter(activo=True, es_recurrente=False):
        filas[(c.orden, c.nombre, None)] = [c.nombre, CERO]
    a_cuenta = CERO
    for p in pagos:
        imputado = CERO
        for i in p.imputaciones.all():
            c, per = i.cargo.concepto, i.cargo.periodo
            if c.codigo == Concepto.MANTENIMIENTO:
                etiqueta = f'Mto. {MESES_ES[per.month]}' + ('' if per.year == anio else f' {per.year}')
                clave = (c.orden, c.nombre, per)
            else:
                etiqueta, clave = c.nombre, (c.orden, c.nombre, None)
            filas.setdefault(clave, [etiqueta, CERO])[1] += i.monto
            imputado += i.monto
        a_cuenta += max(p.monto - imputado, CERO)
    orden = sorted(filas, key=lambda k: (k[0], k[1], k[2] or date.min))
    out = [{'concepto': filas[k][0], 'monto': f'{filas[k][1]:.2f}'} for k in orden]
    if a_cuenta:
        out.append({'concepto': 'A cuenta', 'monto': f'{a_cuenta:.2f}'})
    return out


def _tabla_egresos(filas):
    """Filas de la tabla «Egresos»: un renglón por gasto (descripción)."""
    return [{'concepto': f['nombre'], 'monto': f'{f["monto"]:.2f}'} for f in filas]


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
    saldo_anual = tot_ing - tot_egr
    prev_ing = sum((_balance_ingresos(anio - 1, m)[0] for m in range(1, 13)), CERO)
    prev_egr = sum((_balance_egresos(anio - 1, m)[0] for m in range(1, 13)), CERO)
    ing_filas = _ordenar_filas({k: {'monto': v} for k, v in dist_ing.items()}, lambda v: 0)
    egr_filas = _ordenar_filas({k: {'monto': v} for k, v in dist_egr.items()}, lambda v: 0)
    return dict(meses=meses, ing_total=tot_ing, egr_total=tot_egr, saldo=saldo_anual,
                saldo_tone=_tono(saldo_anual), ing_filas=ing_filas, egr_filas=egr_filas,
                var_ing=_variacion(tot_ing, prev_ing), var_egr=_variacion(tot_egr, prev_egr, False),
                var_saldo=_variacion(saldo_anual, prev_ing - prev_egr),
                comparado_con=str(anio - 1), drawer_json=drawer,
                tablas={'ing': _tabla_ingresos(anio), 'egr': _tabla_egresos(egr_filas),
                        'ing_total': f'{tot_ing:.2f}', 'egr_total': f'{tot_egr:.2f}'},
                graficas={'ing': _dona(ing_filas), 'egr': _dona(egr_filas),
                          'serie': [{'label': x['nombre'][:3], 'ingresos': f'{x["ingresos"]:.2f}',
                                     'egresos': f'{x["egresos"]:.2f}', 'mes': x['mes']} for x in meses]})


def _contexto_mensual(anio, mes):
    ing, ingf, ingm = _balance_ingresos(anio, mes)
    egr, egrf, egrm = _balance_egresos(anio, mes)
    saldo = ing - egr
    drawer = {}
    for f in ingf:
        drawer['ing::' + f['nombre']] = {'titulo': 'Ingresos · ' + f['nombre'], 'movs': ingm[f['nombre']]}
    for f in egrf:
        drawer['egr::' + f['nombre']] = {'titulo': 'Egresos · ' + f['nombre'], 'movs': egrm[f['nombre']]}
    pa, pm = _mes_anterior(anio, mes)
    prev_ing, prev_egr = _balance_ingresos(pa, pm)[0], _balance_egresos(pa, pm)[0]
    return dict(ing_total=ing, egr_total=egr, saldo=saldo, saldo_tone=_tono(saldo),
                ing_filas=ingf, egr_filas=egrf, drawer_json=drawer,
                var_ing=_variacion(ing, prev_ing), var_egr=_variacion(egr, prev_egr, False),
                var_saldo=_variacion(saldo, prev_ing - prev_egr),
                comparado_con=MESES_ES[pm].lower(),
                tablas={'ing': _tabla_ingresos(anio, mes), 'egr': _tabla_egresos(egrf),
                        'ing_total': f'{ing:.2f}', 'egr_total': f'{egr:.2f}'},
                graficas={'ing': _dona(ingf), 'egr': _dona(egrf),
                          'serie': _serie(_ultimos_meses(anio, mes))})


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

    if not 1 <= mes <= 12:
        mes = hoy.month
    if modo == 'anual':
        prev, sig = {'anio': anio - 1}, {'anio': anio + 1}
        etiqueta = str(anio)
    else:
        pa, pm = _mes_anterior(anio, mes)
        sa, sm = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
        prev, sig = {'anio': pa, 'mes': pm}, {'anio': sa, 'mes': sm}
        etiqueta = f'{MESES_ES[mes]} {anio}'
    ctx = {'modo': modo, 'mes': mes, 'anio': anio, 'etiqueta': etiqueta,
           'prev': prev, 'sig': sig, 'hoy': {'anio': hoy.year, 'mes': hoy.month}}
    ctx.update(_contexto_anual(anio) if modo == 'anual' else _contexto_mensual(anio, mes))
    return render(request, 'departamentos/balance/balance.html', ctx)
