"""Deudores (dashboard): KPIs, tabla con desglose y drawer de detalle."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from ..forms import MESES_ES
from ..models import Concepto
from ..servicios import deudores
from .comun import periodo_de_request

CERO = Decimal('0.00')


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

        # Historial de deuda por mes (para el drawer)
        hist = {}
        for c in impagos:
            hist[c.periodo] = hist.get(c.periodo, CERO) + c.saldo
        historial = [{'mes': f'{MESES_ES[p.month]} {p.year}', 'monto': f'{m:.2f}'}
                     for p, m in sorted(hist.items())]

        url = reverse('departamento_detalle', args=[dep.pk])
        filas.append({
            'id': dep.pk, 'numero': dep.numero_domicilio, 'propietario': dep.propietario,
            'total': f['total'], 'mes': f['mes'], 'anterior': f['anterior'],
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

    return render(request, 'departamentos/deudores/deudores.html', {
        'periodo_form': form, 'periodo': periodo, 'filas': filas,
        'conceptos': list(Concepto.objects.order_by('orden').values_list('codigo', 'nombre')),
        'kpi': {'total': tot_general, 'n_deudores': n_deud, 'vencido': tot_vencido,
                'promedio': (tot_general / n_deud) if n_deud else CERO},
        'resumen': {'total': tot_general, 'vencido': tot_vencido, 'por_vencer': tot_por_vencer,
                    'pendiente': tot_general - tot_vencido - tot_por_vencer},
        'grafico': grafico, 'top_deudores': top_deudores, 'drawer_json': drawer_json,
    })
