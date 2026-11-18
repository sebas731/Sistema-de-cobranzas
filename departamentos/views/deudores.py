"""Deudores (dashboard): KPIs, tabla con desglose y drawer de detalle."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from ..forms import MESES_ES
from ..models import Concepto, Departamento
from ..servicios import agrupar_deuda, deudores, sumar_meses
from .comun import periodo_de_request

CERO = Decimal('0.00')

# Conceptos con columna propia en "Deudas actuales"; el resto (mantenimiento,
# agua, área común, cochera...) es el "general" de cada mes.
_APARTE = {'PUERTAS': 'puertas', 'CAMARA': 'camara', 'ASCENSORES': 'ascensores',
           'CUOTA': 'cuota', 'MULTA': 'multa', 'MORA': 'mora'}


def _num_key(numero):
    try:
        return (0, int(numero))
    except (TypeError, ValueError):
        return (1, str(numero))


def _mes(p):
    return f'{MESES_ES[p.month]} {p.year}'


@login_required
def deudas_actuales(request):
    """Tabla de deudas por depto al estilo del Excel «DEUDORES»: una fila por
    deudor con el saldo por concepto, y el general del mes (mant+agua+área+
    cochera) separado por mes (anterior + 3 meses recientes). Se actualiza con
    los cargos reales, se filtra por texto y se exporta a PDF (imprimir)."""
    form, ref = periodo_de_request(request)
    meses = [sumar_meses(ref, -2), sumar_meses(ref, -1), ref]   # ej. jul, ago, set
    q = request.GET.get('q', '').strip().lower()
    # "Solo este mes": cuenta únicamente los cargos del periodo de referencia
    # (ej. elegir octubre → solo las deudas generadas en octubre).
    solo_mes = request.GET.get('solo') == '1'

    deps = (Departamento.objects.filter(activo=True)
            .prefetch_related('cargos__concepto', 'cargos__imputaciones'))
    cols = ['puertas', 'camara', 'ascensores', 'ant', 'm0', 'm1', 'm2',
            'cuota', 'multa', 'mora', 'total']
    filas = []
    for dep in deps:
        r = {k: CERO for k in cols}
        r['depto'] = dep
        for c in dep.cargos.all():
            s = c.saldo
            if s <= 0:
                continue
            if solo_mes and c.periodo != ref:
                continue
            r['total'] += s
            col = _APARTE.get(c.concepto.codigo)
            if col:
                r[col] += s
            elif c.periodo == meses[0]:
                r['m0'] += s
            elif c.periodo == meses[1]:
                r['m1'] += s
            elif c.periodo == meses[2]:
                r['m2'] += s
            else:   # general anterior (o posterior) = acumulado
                r['ant'] += s
        if r['total'] > 0:
            filas.append(r)

    if q:
        filas = [f for f in filas
                 if q in f['depto'].numero_domicilio.lower()
                 or q in (f['depto'].propietario or '').lower()]
    filas.sort(key=lambda f: _num_key(f['depto'].numero_domicilio))
    tot = {k: sum((f[k] for f in filas), CERO) for k in cols}
    mes_labels = [MESES_ES[m.month] for m in meses]

    if request.GET.get('formato') == 'xlsx':
        return _exportar_deudas_xlsx(filas, tot, ref, mes_labels, solo_mes)

    return render(request, 'departamentos/deudores/deudas_actuales.html', {
        'periodo_form': form, 'ref': ref, 'filas': filas, 'tot': tot, 'n': len(filas),
        'q': request.GET.get('q', ''), 'solo_mes': solo_mes,
        'mes_labels': mes_labels,
    })


def _exportar_deudas_xlsx(filas, tot, ref, mes_labels, solo_mes):
    """Deudas actuales a Excel con el mismo desglose de la tabla."""
    from django.http import HttpResponse
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    enc = ['Depto', 'Propietario', 'Puertas', 'Cámara', 'Ascensor', 'Anterior',
           mes_labels[0], mes_labels[1], mes_labels[2], 'Cuota', 'Multa', 'Mora', 'TOTAL']
    campos = ['puertas', 'camara', 'ascensores', 'ant', 'm0', 'm1', 'm2', 'cuota', 'multa', 'mora', 'total']

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Deudas'
    titulo = f'Deudas de {MESES_ES[ref.month]} {ref.year}' if solo_mes else f'Deudas actuales (ref. {MESES_ES[ref.month]} {ref.year})'
    ws.append([titulo])
    ws.append(enc)
    verde = PatternFill('solid', fgColor='2E7D32')
    for cell in ws[2]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = verde
        cell.alignment = Alignment(horizontal='center')
    for f in filas:
        ws.append([f['depto'].numero_domicilio, f['depto'].propietario or '']
                  + [float(f[k]) for k in campos])
    ws.append(['TOTAL', ''] + [float(tot[k]) for k in campos])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    ws.freeze_panes = 'A3'
    for i, ancho in enumerate((10, 26, 10, 10, 10, 11, 11, 11, 11, 10, 10, 10, 12), start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = ancho
    for row in ws.iter_rows(min_row=3, min_col=3):
        for cell in row:
            cell.number_format = '0.00'

    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    nombre = f'deudas_{ref.year}_{ref.month:02d}{"_mes" if solo_mes else ""}.xlsx'
    resp['Content-Disposition'] = f'attachment; filename="{nombre}"'
    wb.save(resp)
    return resp


def _estado(impagos, hoy, pronto):
    if any(c.fecha_vencimiento < hoy for c in impagos):
        return 'vencido', 'Vencido', 'danger'
    if any(hoy <= c.fecha_vencimiento <= pronto for c in impagos):
        return 'por_vencer', 'Por vencer', 'warning'
    return 'pendiente', 'Pendiente', 'info'


@login_required
def vista_deudores(request):
    """Deudores (dashboard). Solo prepara/organiza datos para la UI; no cambia
    ningún cálculo (usa servicios.deudores()). Enriquecido con estado, historial
    y desglose con nombres legibles para el rediseño."""
    form, periodo = periodo_de_request(request)
    hoy = timezone.localdate()
    pronto = hoy + timedelta(days=7)
    nombres = dict(Concepto.objects.values_list('codigo', 'nombre'))

    filas, drawer_json = [], []
    hay_posterior = False
    tot_general = tot_vencido = tot_por_vencer = CERO
    por_concepto = {}

    for f in deudores(periodo):
        dep = f['departamento']
        impagos = [c for c in dep.cargos.all() if c.saldo > 0]
        estado, estado_label, estado_tone = _estado(impagos, hoy, pronto)
        tot_general += f['total']
        tot_vencido += sum((c.saldo for c in impagos if c.fecha_vencimiento < hoy), CERO)
        tot_por_vencer += sum((c.saldo for c in impagos if hoy <= c.fecha_vencimiento <= pronto), CERO)

        # Desglose con nombres legibles (ordenado por monto desc)
        desglose = sorted(({'nombre': nombres.get(cod, cod), 'monto': monto}
                           for cod, monto in f['por_concepto'].items()),
                          key=lambda x: x['monto'], reverse=True)
        for cod, monto in f['por_concepto'].items():
            por_concepto[cod] = por_concepto.get(cod, CERO) + monto

        # Deuda por antigüedad: el mes y los 2 anteriores sueltos, lo más antiguo acumulado.
        grupo = agrupar_deuda(impagos, periodo)
        historial = ([{'mes': f'Acumulado (hasta {_mes(grupo["hasta"])})', 'monto': f'{grupo["acumulado"]:.2f}'}]
                     if grupo['acumulado'] else [])
        historial += [{'mes': _mes(p), 'monto': f'{m:.2f}'} for p, m in grupo['meses'] if m]
        if grupo['posterior']:
            historial.append({'mes': f'Posterior a {_mes(periodo)}', 'monto': f'{grupo["posterior"]:.2f}'})
        hay_posterior = hay_posterior or grupo['posterior'] > 0

        url = reverse('departamento_detalle', args=[dep.pk])
        filas.append({
            'id': dep.pk, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
            'total': f['total'], 'mes': f['mes'], 'anterior': f['anterior'],
            'acumulado': grupo['acumulado'],
            'por_mes': [{'mes': MESES_ES[p.month][:3] + f' {p.year}', 'monto': m} for p, m in grupo['meses']],
            'posterior': grupo['posterior'],
            'desglose': desglose, 'estado': estado, 'estado_label': estado_label,
            'estado_tone': estado_tone,
            'total_tone': 'danger' if estado == 'vencido' else '',
            'conceptos_cod': ' '.join(f['por_concepto'].keys()),
            'url_detalle': url,
        })
        drawer_json.append({
            'id': dep.pk, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
            'total': f'{f["total"]:.2f}', 'estado_label': estado_label, 'estado_tone': estado_tone,
            'desglose': [{'nombre': d['nombre'], 'monto': f'{d["monto"]:.2f}'} for d in desglose],
            'historial': historial, 'url': url,
        })

    n_deud = len(filas)

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

    ref = agrupar_deuda([], periodo)
    return render(request, 'departamentos/deudores/deudores.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,
        # Columnas por antigüedad: "Acumulado (hasta junio)", julio, agosto, septiembre.
        'col_hasta': _mes(ref['hasta']), 'col_meses': [MESES_ES[p.month][:3] + f' {p.year}' for p, _ in ref['meses']],
        'hay_posterior': hay_posterior,
        'conceptos': list(Concepto.objects.order_by('orden').values_list('codigo', 'nombre')),
        'kpi': {'total': tot_general, 'n_deudores': n_deud, 'vencido': tot_vencido,
                'promedio': (tot_general / n_deud) if n_deud else CERO},
        'resumen': {'total': tot_general, 'vencido': tot_vencido, 'por_vencer': tot_por_vencer,
                    'pendiente': tot_general - tot_vencido - tot_por_vencer},
        'grafico': grafico, 'top_deudores': top_deudores, 'drawer_json': drawer_json,
    })
