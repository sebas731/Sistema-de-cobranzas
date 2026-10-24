"""Cálculo y emisión del recibo mensual (agua SEDAPAL + conceptos + saldo)."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import (
    Concepto, Departamento, GastoEdificio, GastoFijo, LecturaMedidor, PlanCuota,
    Recibo, ReciboLinea, ReciboSubLinea,
)
from .agua import agua_comun_por_departamento, calcular_agua_sedapal
from .comun import CERO, TRUNCAR_DECIMAS, redondear
from .cuenta import deuda_anterior


def _concepto_de_gastofijo(g, conceptos):
    """Concepto al que aporta un gasto fijo: el explícito, o el deducido de la categoría."""
    if g.concepto_id:
        return g.concepto
    cod = 'AREA_COMUN' if g.categoria == GastoEdificio.Categoria.AGUA_COMUN else 'MANTENIMIENTO'
    return conceptos.get(cod)


def _linea(concepto, etiqueta, monto, grupo='CONSUMO', sub_items=(), orden_defecto=100, **extra):
    return {'concepto': concepto, 'etiqueta': etiqueta, 'monto': monto, 'grupo': grupo,
            'sub_items': list(sub_items), 'orden': concepto.orden if concepto else orden_defecto,
            **extra}


def conceptos_mensuales(departamento, periodo_fac):
    """Líneas de conceptos del mes (aparte del agua) para un depto, desde su
    configuración: gastos fijos agrupados por concepto (con sub-ítems), la
    cochera del depto y las cuotas en armadas vigentes. El agua común NO sale
    de aquí (se calcula desde A−B)."""
    periodo = periodo_fac.periodo
    conceptos = {c.codigo: c for c in Concepto.objects.all()}
    n = Departamento.objects.filter(activo=True).count() or 1

    grupos = {}  # codigo -> {'concepto', 'sub': [(etiqueta, monto)], 'total'}
    for g in GastoFijo.objects.filter(activo=True).select_related('concepto'):
        # El agua común se calcula con A−B, no desde gastos fijos.
        if not g.concepto_id and g.categoria == GastoEdificio.Categoria.AGUA_COMUN:
            continue
        c = _concepto_de_gastofijo(g, conceptos)
        if c is None:
            continue
        aporte = redondear(g.monto / n) if g.compartido else redondear(g.monto)
        grp = grupos.setdefault(c.codigo, {'concepto': c, 'sub': [], 'total': CERO})
        grp['sub'].append((g.descripcion, aporte))
        grp['total'] += aporte

    lineas = [_linea(grp['concepto'], grp['concepto'].nombre, grp['total'],
                     sub_items=grp['sub'] if len(grp['sub']) > 1 else [])
              for grp in grupos.values()]

    # Cochera (asignación por departamento).
    if departamento.cochera_monto and departamento.cochera_monto > 0:
        lineas.append(_linea(conceptos.get('COCHERA'), 'Cochera',
                             redondear(departamento.cochera_monto), orden_defecto=40))

    # Cuotas en armadas vigentes.
    planes = PlanCuota.objects.filter(activo=True).select_related('concepto').prefetch_related('departamentos')
    for plan in planes:
        objetivos = list(plan.departamentos.all())
        if objetivos and departamento not in objetivos:
            continue
        n_cuota = plan.numero_en(periodo)
        if n_cuota:
            lineas.append(_linea(plan.concepto, f'{plan.descripcion} {n_cuota}/{plan.num_cuotas}',
                                 redondear(plan.monto_cuota)))
    return lineas


def calcular_recibo(departamento, periodo_fac, *, criterio_agua_comun='iguales',
                    estrategia=TRUNCAR_DECIMAS, descuento=None):
    """Calcula (sin persistir) el recibo de un departamento para un periodo.

    Reglas: m³ = lectura actual − anterior; agua SEDAPAL escalonada; agua común
    por A−B; suma de conceptos del mes (con sub-ítems) − descuentos = total del
    mes; total a pagar = total del mes + deuda pendiente − saldo a favor.
    """
    periodo = periodo_fac.periodo
    lectura = LecturaMedidor.objects.filter(departamento=departamento, periodo=periodo).first()
    m3 = lectura.m3_consumidos if lectura else CERO

    lineas = []
    agua = None
    if periodo_fac.tarifa_agua_id:
        agua = calcular_agua_sedapal(m3, periodo_fac.tarifa_agua, estrategia)
        lineas.append(_linea(Concepto.objects.filter(codigo='AGUA').first(), 'Pago consumo Dpto.',
                             agua['total'], grupo='AGUA', orden_defecto=20, detalle_agua=agua))

    agua_comun = agua_comun_por_departamento(periodo_fac, criterio_agua_comun, estrategia, departamento)
    if agua_comun > 0:
        lineas.append(_linea(Concepto.objects.filter(codigo='AREA_COMUN').first(), 'Agua común',
                             agua_comun, orden_defecto=30))

    lineas.extend(conceptos_mensuales(departamento, periodo_fac))
    lineas.sort(key=lambda x: (x['orden'], x['etiqueta']))

    total_conceptos = sum((l['monto'] for l in lineas), CERO)
    descuento = redondear(descuento or 0)
    total_mes = total_conceptos - descuento

    deuda_pendiente = deuda_anterior(departamento, periodo)
    saldo_favor = sum((p.saldo_a_favor for p in departamento.pagos.all()), CERO)
    total_pagar = total_mes + deuda_pendiente - saldo_favor

    return {
        'departamento': departamento, 'periodo_fac': periodo_fac, 'periodo': periodo,
        'lectura': lectura, 'm3': m3, 'agua': agua, 'lineas': lineas,
        'total_conceptos': redondear(total_conceptos), 'descuento': descuento,
        'total_mes': redondear(total_mes), 'deuda_pendiente': redondear(deuda_pendiente),
        'saldo_favor': redondear(saldo_favor), 'total_pagar': redondear(total_pagar),
    }


@transaction.atomic
def emitir_recibo(departamento, periodo_fac, **kw):
    """Calcula y PERSISTE el recibo como snapshot (Recibo + líneas + sub-ítems)."""
    calc = calcular_recibo(departamento, periodo_fac, **kw)
    lectura = calc['lectura']
    agua = calc['agua'] or {}
    recibo, _ = Recibo.objects.update_or_create(
        periodo_fac=periodo_fac, departamento=departamento,
        defaults={
            'lectura_anterior_fecha': lectura.fecha_anterior if lectura else None,
            'lectura_anterior_valor': lectura.lectura_anterior if lectura else Decimal('0'),
            'lectura_actual_fecha': lectura.fecha_actual if lectura else None,
            'lectura_actual_valor': lectura.lectura_actual if lectura else Decimal('0'),
            'm3_consumidos': calc['m3'],
            'agua_potable': agua.get('agua_potable', Decimal('0')),
            'agua_saneamiento': agua.get('saneamiento', Decimal('0')),
            'agua_cargo_fijo': agua.get('cargo_fijo', Decimal('0')),
            'agua_igv': agua.get('igv', Decimal('0')),
            'total_mes': calc['total_mes'], 'deuda_anterior': calc['deuda_pendiente'],
            'descuento_mes_anterior': calc['descuento'], 'saldo_favor': calc['saldo_favor'],
            'total_pagar': calc['total_pagar'], 'emitido_en': timezone.now(),
        })
    recibo.lineas.all().delete()
    for l in calc['lineas']:
        ln = ReciboLinea.objects.create(recibo=recibo, concepto=l['concepto'], etiqueta=l['etiqueta'],
                                        monto=l['monto'], grupo=l['grupo'], orden=l['orden'])
        for i, (etiqueta, monto) in enumerate(l.get('sub_items') or [], start=1):
            ReciboSubLinea.objects.create(linea=ln, etiqueta=etiqueta, monto=monto, orden=i * 10)
    return recibo
