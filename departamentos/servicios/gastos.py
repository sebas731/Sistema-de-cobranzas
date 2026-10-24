"""Prorrateo de gastos (fijos + del mes) entre los departamentos."""
from django.db.models import Sum

from ..models import Departamento, GastoEdificio, GastoFijo
from .comun import CERO, redondear

Categoria = GastoEdificio.Categoria


def _por_depto(compartido_total, directo_total, n):
    """Monto por depto: la parte compartida se divide entre n; la parte
    'por departamento' se cobra completa a cada uno."""
    compartido = compartido_total / n if n else CERO
    return redondear(compartido + directo_total)


def prorrateo_mes(periodo):
    """Mantenimiento, área común y gastos adicionales por departamento.

    Mantenimiento y área común combinan gastos fijos (recurrentes) y gastos
    variables del mes de la MISMA categoría. Los 'gastos adicionales' (otros
    cobros) solo existen como gasto del mes y se cobran aparte.

    Cada gasto puede ser compartido (se divide entre los deptos) o por
    departamento (cada depto paga el monto completo).
    """
    n = Departamento.objects.filter(activo=True).count()
    if n == 0:
        return {'n': 0, 'mantenimiento': CERO, 'area_comun': CERO, 'adicionales': CERO,
                'total_general': CERO, 'total_agua_comun': CERO, 'total_adicional': CERO}

    fijos = GastoFijo.objects.filter(activo=True)
    # Solo gastos del mes NO dirigidos (los personalizados se aplican por depto
    # en la generación, no en el prorrateo uniforme).
    mes = GastoEdificio.objects.filter(periodo=periodo, departamentos__isnull=True)

    def suma(categoria, compartido):
        return sum((qs.filter(categoria=categoria, compartido=compartido)
                    .aggregate(s=Sum('monto'))['s'] or CERO) for qs in (fijos, mes))

    datos = {'n': n}
    for clave, total, cat in (('mantenimiento', 'total_general', Categoria.GENERAL),
                              ('area_comun', 'total_agua_comun', Categoria.AGUA_COMUN),
                              ('adicionales', 'total_adicional', Categoria.ADICIONAL)):
        compartido, directo = suma(cat, True), suma(cat, False)
        datos[clave] = _por_depto(compartido, directo, n)
        datos[total] = compartido + directo
    return datos


def desglose_gastos_mes(periodo):
    """Desglose itemizado de gastos del mes (fijos + del mes) para el balance.

    Por cada gasto devuelve su total (columna 'TORRE D' del Excel) y su aporte
    por departamento (columna 'TOTAL'), agrupado por categoría.
    """
    n = Departamento.objects.filter(activo=True).count() or 1

    def item(g, del_mes, objetivos=0):
        divisor = objetivos if objetivos else n
        share = (g.monto / divisor) if g.compartido else g.monto
        return {'descripcion': g.descripcion, 'total': g.monto,
                'por_depto': redondear(share), 'compartido': g.compartido,
                'del_mes': del_mes, 'objetivos': objetivos}

    general, agua_comun, adicionales = [], [], []
    destino = {Categoria.AGUA_COMUN: agua_comun, Categoria.ADICIONAL: adicionales}
    for g in GastoFijo.objects.filter(activo=True):
        destino.get(g.categoria, general).append(item(g, False))
    for g in GastoEdificio.objects.filter(periodo=periodo).prefetch_related('departamentos'):
        destino.get(g.categoria, general).append(item(g, True, g.departamentos.count()))

    return {'n': n, 'general': general, 'agua_comun': agua_comun, 'adicionales': adicionales}
