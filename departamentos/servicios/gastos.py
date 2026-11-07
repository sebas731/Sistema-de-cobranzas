"""Prorrateo de gastos (fijos + del mes) entre los departamentos."""
from ..models import Departamento, GastoEdificio, GastoFijo
from .comun import CERO, redondear

Categoria = GastoEdificio.Categoria


def aporte_por_depto(monto, compartido, n):
    """Lo que paga un depto de UN gasto, a 2 decimales: compartido → monto ÷ n;
    'por departamento' → el monto completo. Todo el sistema suma estos montos ya
    redondeados (como el Excel del condominio), nunca el total sin redondear."""
    return redondear(monto / n) if compartido else redondear(monto)


def prorrateo_mes(periodo):
    """Mantenimiento, área común y gastos adicionales por departamento.

    Mantenimiento y área común combinan gastos fijos (recurrentes) y gastos
    variables del mes de la MISMA categoría. Los 'gastos adicionales' (otros
    cobros) solo existen como gasto del mes y se cobran aparte.

    Cada gasto puede ser compartido (se divide entre los deptos) o por
    departamento (cada depto paga el monto completo). El monto por depto de cada
    categoría es la suma de los aportes de sus gastos, cada uno a 2 decimales.
    """
    n = Departamento.objects.filter(activo=True).count()
    if n == 0:
        return {'n': 0, 'mantenimiento': CERO, 'area_comun': CERO, 'adicionales': CERO,
                'total_general': CERO, 'total_agua_comun': CERO, 'total_adicional': CERO}

    # Los fijos que se cobran aparte (cuota extraordinaria) no se prorratean aquí.
    fijos = GastoFijo.objects.filter(activo=True).exclude(concepto__es_recurrente=False)
    # Solo gastos del mes NO dirigidos (los personalizados se aplican por depto
    # en la generación, no en el prorrateo uniforme).
    mes = GastoEdificio.objects.filter(periodo=periodo, departamentos__isnull=True)

    datos = {'n': n}
    for clave, total, cat in (('mantenimiento', 'total_general', Categoria.GENERAL),
                              ('area_comun', 'total_agua_comun', Categoria.AGUA_COMUN),
                              ('adicionales', 'total_adicional', Categoria.ADICIONAL)):
        gastos = [g for qs in (fijos, mes) for g in qs.filter(categoria=cat)]
        datos[clave] = sum((aporte_por_depto(g.monto, g.compartido, n) for g in gastos), CERO)
        datos[total] = sum((g.monto for g in gastos), CERO)
    return datos


def fijos_aparte_por_depto():
    """{codigo_concepto: monto por depto} de los gastos fijos activos que se cobran
    aparte (concepto no recurrente, ej. cuota extraordinaria): cada uno genera su
    propio cargo mensual mientras esté activo, en vez de sumarse a mantenimiento."""
    n = Departamento.objects.filter(activo=True).count() or 1
    montos = {}
    for g in GastoFijo.objects.filter(activo=True, concepto__es_recurrente=False).select_related('concepto'):
        aporte = aporte_por_depto(g.monto, g.compartido, n)
        montos[g.concepto.codigo] = montos.get(g.concepto.codigo, CERO) + aporte
    return montos


def desglose_gastos_mes(periodo):
    """Desglose itemizado de gastos del mes (fijos + del mes) para el balance.

    Por cada gasto devuelve su total (columna 'TORRE D' del Excel) y su aporte
    por departamento (columna 'TOTAL'), agrupado por categoría.
    """
    n = Departamento.objects.filter(activo=True).count() or 1

    def item(g, del_mes, objetivos=0):
        divisor = objetivos if objetivos else n
        return {'descripcion': g.descripcion, 'total': g.monto,
                'por_depto': aporte_por_depto(g.monto, g.compartido, divisor), 'compartido': g.compartido,
                'del_mes': del_mes, 'objetivos': objetivos}
    
    general, agua_comun, adicionales = [], [], []
    destino = {Categoria.AGUA_COMUN: agua_comun, Categoria.ADICIONAL: adicionales}
    for g in GastoFijo.objects.filter(activo=True):
        print("#######  "+ g.descripcion +" #######")
        destino.get(g.categoria, general).append(item(g, False))
    
    for g in GastoEdificio.objects.filter(periodo=periodo).prefetch_related('departamentos'):
        print("#######  "+ g.descripcion +" #######")
        destino.get(g.categoria, general).append(item(g, True, g.departamentos.count()))

    return {'n': n, 'general': general, 'agua_comun': agua_comun, 'adicionales': adicionales}
