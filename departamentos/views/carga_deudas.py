"""Carga inicial de deudas: grilla editable (RevoGrid) con la forma de la hoja
DEUDAS ACTUALES. Muestra la deuda migrada ya guardada; el Excel solo llena la
grilla para revisar; "Guardar" deja los cargos de migración iguales a la grilla.

GET  → página.     POST multipart accion=leer_excel → filas del Excel (no guarda).
POST JSON accion=guardar → guarda por depto.   accion=quitar_todo → borra la carga
(solo los cargos de migración sin pagos).
"""
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from ..forms import MESES_ES
from ..models import Cargo, Concepto, Departamento, Pago
from ..servicios import a_decimal, sembrar_conceptos
from ..servicios.comun import CERO
from ..servicios.migracion import (
    CAMPOS_CONCEPTO, OTROS, SERVICIOS, cobrado_migracion, deuda_migrada, fila_vacia,
    guardar_deuda_migrada, leer_excel_deudas, meses_columna, total_fila,
)
from .comun import json_invalido, leer_json


def _corte_por_defecto():
    """Mes anterior al primer mes facturado por el sistema (si se factura desde
    septiembre, el corte es agosto). Si ya hay carga inicial, su último mes."""
    ultimo_mig = (Cargo.objects.filter(origen=Cargo.Origen.MIGRACION)
                  .order_by('-periodo').values_list('periodo', flat=True).first())
    if ultimo_mig:
        return ultimo_mig
    primero = (Cargo.objects.filter(origen=Cargo.Origen.SISTEMA, concepto__codigo=Concepto.MANTENIMIENTO)
               .order_by('periodo').values_list('periodo', flat=True).first())
    base = primero or date.today().replace(day=1)
    return date(base.year - 1, 12, 1) if base.month == 1 else date(base.year, base.month - 1, 1)


def _fecha(texto):
    """'2026-08-31' → date; None si no es válido."""
    try:
        return date.fromisoformat(str(texto))
    except (TypeError, ValueError):
        return None


def _corte(texto):
    """'2026-08' → date(2026, 8, 1); None si no es válido."""
    try:
        anio, mes = str(texto).split('-')[:2]
        return date(int(anio), int(mes), 1)
    except (TypeError, ValueError):
        return None


def _texto_mes(periodo):
    return f'{MESES_ES[periodo.month]} {periodo.year}'


def _meses_json(meses):
    return [{'clave': k, 'nombre': MESES_ES[int(k[5:])].upper()} for k in meses]


def _f(v):
    return float(v) if v is not None else 0.0


def _a_json(r, dep=None):
    return {
        'pk': dep.pk if dep else None, 'dpto': r['dpto'],
        'propietario': dep.propietario if dep else '',
        **{c: _f(r[c]) for c, _, _ in CAMPOS_CONCEPTO},
        'd_ant': _f(r['d_ant']), 'meses': {k: _f(v) for k, v in r['meses'].items()},
        'detalle': {'meses': {k: _f(v) for k, v in r['detalle']['meses'].items()},
                    'sin_periodo': _f(r['detalle']['sin_periodo'])},
        'cobradas': {k: _f(v) for k, v in r.get('cobradas', {}).items()},
        'total': _f(r['total']), 'pagado': _f(r.get('pagado')),
        'errores': list(r.get('errores', [])), 'advertencias': list(r.get('advertencias', [])),
    }


def _a_fila(d, meses):
    """Fila JSON de la grilla → fila con Decimal (misma forma que el parser)."""
    dec = lambda v: a_decimal(v, CERO) or CERO
    det = d.get('detalle') or {}
    r = fila_vacia(str(d.get('dpto') or ''), meses)
    for c, _, _ in CAMPOS_CONCEPTO:
        r[c] = dec(d.get(c))
    r['meses'] = {k: dec(v) for k, v in (d.get('meses') or {}).items()}
    r['detalle'] = {'meses': {k: dec(v) for k, v in (det.get('meses') or {}).items()},
                    'sin_periodo': dec(det.get('sin_periodo'))}
    r['d_ant'] = dec(d.get('d_ant'))
    r['cobradas'] = {k: dec(v) for k, v in (d.get('cobradas') or {}).items()}
    return r


def _migrado():
    """Resumen de lo ya guardado como carga inicial (para los KPI): deuda pendiente
    cargada, lo cobrado de la carga inicial (ingreso) y lo pagado después."""
    cargos = Cargo.objects.filter(origen=Cargo.Origen.MIGRACION).prefetch_related('imputaciones__pago')
    cobrado = sum((cobrado_migracion(c) for c in cargos), CERO)
    return {'n': len(cargos), 'total': _f(sum((c.monto for c in cargos), CERO) - cobrado),
            'cobrado': _f(cobrado),
            'pagado': _f(sum((c.total_imputado for c in cargos), CERO) - cobrado)}


def _fecha_cobro_por_defecto(corte):
    """Fecha de los pagos de la carga inicial ya guardados; si no hay, el último día del corte."""
    guardada = (Pago.objects.filter(origen=Cargo.Origen.MIGRACION).order_by('-fecha_pago')
                .values_list('fecha_pago', flat=True).first())
    if guardada:
        return guardada
    siguiente = date(corte.year + 1, 1, 1) if corte.month == 12 else date(corte.year, corte.month + 1, 1)
    return siguiente - timedelta(days=1)


def _filas_grilla(corte, meses):
    migradas = deuda_migrada(corte, meses)
    filas = []
    for dep in Departamento.objects.filter(activo=True).order_by('numero_domicilio'):
        r = migradas.pop(dep.numero_domicilio, None) or fila_vacia(dep.numero_domicilio, meses)
        filas.append(_a_json(r, dep))
    for r in migradas.values():   # deuda migrada de deptos inactivos: también se ve
        r['advertencias'].append('Departamento inactivo.')
        filas.append(_a_json(r, Departamento.objects.filter(numero_domicilio=r['dpto']).first()))
    return filas


def _leer_excel(request, corte, meses):
    archivo = request.FILES.get('archivo')
    if not archivo:
        return JsonResponse({'ok': False, 'error': 'Elige el archivo .xlsx.'}, status=400)
    try:
        anio = int(request.POST.get('anio') or corte.year)
        libro = leer_excel_deudas(archivo, anio)
    except Exception as e:  # archivo dañado o no es .xlsx
        return JsonResponse({'ok': False, 'error': f'No se pudo leer el Excel: {e}'}, status=400)

    # Si el Excel llega a meses posteriores al corte (trae JULIO y AGOSTO y el corte
    # era junio), el corte pasa al último mes del Excel: así coinciden las columnas.
    cambio_corte = False
    if libro['meses'] and _corte(libro['meses'][-1]) > corte:
        corte, cambio_corte = _corte(libro['meses'][-1]), True
        meses = meses_columna(corte)
        libro['advertencias'].insert(0, f'El Excel llega hasta {_texto_mes(corte)}: el mes de corte '
                                        f'pasó a {_texto_mes(corte)}.')

    deps = {d.numero_domicilio: d for d in Departamento.objects.all()}
    por_dpto = {r['dpto']: r for r in libro['filas']}
    for dpto, c in libro['cobradas'].items():
        r = por_dpto.get(dpto)
        if r is None:
            r = por_dpto[dpto] = fila_vacia(dpto, libro['meses'])
            libro['filas'].append(r)
        r['cobradas'] = dict(c['meses'])
        r['advertencias'] += c['advertencias']
        for k in c['meses']:
            if k > meses[-1]:
                r['errores'].append(f'Cobrado {k[5:]}/{k[:4]} es posterior al corte ({corte:%m/%Y}).')
    filas = []
    for r in libro['filas']:
        # Meses del Excel que no son columnas de la grilla → detalle de D. ANTERIORES.
        for k in list(r['meses']):
            if k > meses[-1]:
                r['errores'].append(f'El mes {k[5:]}/{k[:4]} es posterior al corte ({corte:%m/%Y}).')
            elif k not in meses:
                v = r['meses'].pop(k)
                r['detalle']['meses'][k] = r['detalle']['meses'].get(k, CERO) + v
                r['d_ant'] += v
        for k in meses:
            r['meses'].setdefault(k, CERO)
        dep = deps.get(r['dpto'])
        if dep is None:
            r['errores'].append(f'El depto {r["dpto"]} no existe en el sistema.')
        r['total'] = total_fila(r)
        filas.append(_a_json(r, dep))
    return JsonResponse({
        'ok': True, 'filas': filas, 'hojas': libro['hojas'], 'meses_excel': libro['meses'],
        'total_control': _f(libro['total_control']) if libro['total_control'] is not None else None,
        'total_calculado': _f(libro['total_calculado']),
        'errores': libro['errores'], 'advertencias': libro['advertencias'],
        'cobradas_control': _f(libro['cobradas_control']) if libro['cobradas_control'] is not None else None,
        'cobradas_total': _f(libro['cobradas_total']),
        'corte': f'{corte:%Y-%m}', 'corte_texto': _texto_mes(corte), 'meses': _meses_json(meses),
        # Con otro corte cambian las columnas: se envía también lo ya guardado con ese corte.
        'filas_sistema': _filas_grilla(corte, meses) if cambio_corte else None,
    })


def _guardar(payload, corte, meses):
    sembrar_conceptos()
    fecha_cobro = _fecha(payload.get('fecha_cobro'))
    deps = {d.pk: d for d in Departamento.objects.all()}
    resumen = {'creados': 0, 'actualizados': 0, 'borrados': 0, 'deptos': 0}
    errores = []
    for d in payload.get('rows', []):
        dep = deps.get(d.get('pk'))
        if dep is None:
            errores.append({'dpto': d.get('dpto'), 'error': 'El depto no existe en el sistema.'})
            continue
        r = _a_fila(d, meses)
        suma_det = sum(r['detalle']['meses'].values(), CERO) + r['detalle']['sin_periodo']
        if suma_det != r['d_ant']:
            errores.append({'dpto': dep.numero_domicilio, 'pk': dep.pk,
                            'error': f'D. ANTERIORES {r["d_ant"]:.2f} ≠ su detalle {suma_det:.2f}.'})
            continue
        try:
            c, a, b = guardar_deuda_migrada(dep, r, corte, fecha_cobro)
        except ValueError as e:
            errores.append({'dpto': dep.numero_domicilio, 'pk': dep.pk, 'error': str(e)})
            continue
        resumen['creados'] += c
        resumen['actualizados'] += a
        resumen['borrados'] += b
        resumen['deptos'] += 1
    return JsonResponse({'ok': True, 'resumen': resumen, 'errores': errores,
                         'filas': _filas_grilla(corte, meses), 'migrado': _migrado()})


def _quitar_todo(corte, meses):
    Pago.objects.filter(origen=Cargo.Origen.MIGRACION).delete()
    qs = Cargo.objects.filter(origen=Cargo.Origen.MIGRACION).prefetch_related('imputaciones')
    con_pagos = [c for c in qs if c.total_imputado > 0]
    borrados = qs.exclude(pk__in=[c.pk for c in con_pagos]).delete()[0]
    return JsonResponse({'ok': True, 'borrados': borrados, 'con_pagos': len(con_pagos),
                         'filas': _filas_grilla(corte, meses), 'migrado': _migrado()})


@login_required
def carga_deudas(request):
    corte = _corte(request.GET.get('corte')) or _corte_por_defecto()
    meses = meses_columna(corte)

    if request.method == 'POST':
        if request.content_type.startswith('multipart/'):
            return _leer_excel(request, corte, meses)
        payload = leer_json(request)
        if payload is None:
            return json_invalido()
        if payload.get('accion') == 'quitar_todo':
            return _quitar_todo(corte, meses)
        return _guardar(payload, corte, meses)

    return render(request, 'departamentos/carga_deudas/carga.html', {
        'corte': corte, 'corte_valor': f'{corte:%Y-%m}',
        'corte_texto': _texto_mes(corte),
        'meses_json': _meses_json(meses),
        'filas_json': _filas_grilla(corte, meses),
        'servicios_json': [{'prop': c, 'nombre': n} for c, _, n in SERVICIOS],
        'otros_json': [{'prop': c, 'nombre': n} for c, _, n in OTROS],
        'migrado': _migrado(),
        'fecha_cobro': f'{_fecha_cobro_por_defecto(corte):%Y-%m-%d}',
        'anio_hoy': date.today().year,
    })
