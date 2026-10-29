"""Cuenta corriente: catálogo de conceptos, generación de cargos, pagos y deudas."""
from decimal import Decimal

from django.db import transaction

from ..models import Cargo, Concepto, Departamento, GastoEdificio, Imputacion, LecturaMedidor, Pago
from .agua import costo_agua
from .comun import CERO, fecha_vencimiento, redondear, sumar_meses
from .gastos import fijos_aparte_por_depto, prorrateo_mes

# (código, nombre, orden, es_recurrente)
CONCEPTOS_ESTANDAR = [
    ('MANTENIMIENTO', 'Mantenimiento', 10, True),
    ('AGUA', 'Agua', 20, True),
    ('AGUA_BASICO', 'Agua básico', 25, True),
    ('AREA_COMUN', 'Área común', 30, True),
    ('LUZ_COMUN', 'Luz común', 35, True),
    ('COCHERA', 'Cochera', 40, True),
    ('GASTO_ADIC', 'Gastos adicionales', 50, True),
    ('PUERTAS', 'Puertas', 60, False),
    ('CAMARA', 'Cámaras', 70, False),
    ('ASCENSORES', 'Ascensores', 80, False),
    ('CUOTA', 'Cuota extraordinaria', 90, False),
    ('MULTA', 'Multa', 100, False),
    ('MORA', 'Mora', 110, False),
    ('OTRO', 'Otro', 120, False),
    # Deuda sin mes de la carga inicial ("D. anteriores" sin detalle): se cobra primero.
    ('DEUDA_ANT', 'Deuda anterior', 5, False),
]

# Conceptos recurrentes → de dónde sale su monto por depto.
_RECURRENTES = ['MANTENIMIENTO', 'AGUA', 'AREA_COMUN', 'COCHERA', 'GASTO_ADIC']

# Gastos personalizados del mes: concepto al que aporta cada categoría.
_CATEGORIA_A_CONCEPTO = {
    GastoEdificio.Categoria.GENERAL: 'MANTENIMIENTO',
    GastoEdificio.Categoria.AGUA_COMUN: 'AREA_COMUN',
    GastoEdificio.Categoria.ADICIONAL: 'GASTO_ADIC',
}


def sembrar_conceptos():
    """Crea los conceptos estándar si faltan (idempotente)."""
    for codigo, nombre, orden, recurrente in CONCEPTOS_ESTANDAR:
        Concepto.objects.get_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'orden': orden, 'es_recurrente': recurrente})


# ---------------------------------------------- Generación de cargos

def _extras_dirigidos(periodo):
    """{dep_id: {codigo_concepto: monto}} de los gastos personalizados del mes,
    al concepto que corresponde a su categoría (no todo a "adicionales")."""
    extra_por_dep = {}
    dirigidos = (GastoEdificio.objects.filter(periodo=periodo, departamentos__isnull=False)
                 .distinct().prefetch_related('departamentos'))
    for g in dirigidos:
        objetivos = list(g.departamentos.all())
        if not objetivos:
            continue
        codigo = _CATEGORIA_A_CONCEPTO.get(g.categoria, 'GASTO_ADIC')
        aporte = (g.monto / len(objetivos)) if g.compartido else g.monto
        for d in objetivos:
            porconcepto = extra_por_dep.setdefault(d.id, {})
            porconcepto[codigo] = porconcepto.get(codigo, CERO) + aporte
    return extra_por_dep


@transaction.atomic
def generar_cargos_mes(periodo, dia_vencimiento=5):
    """Genera (o actualiza) los cargos recurrentes del mes por cada depto activo.

    Un cargo por concepto (mantenimiento, agua, área común, cochera, adicionales y
    los gastos fijos que se cobran aparte, como la cuota extraordinaria).
    Idempotente: no duplica; no toca cargos que ya recibieron pagos.
    """
    sembrar_conceptos()
    datos = prorrateo_mes(periodo)
    vencimiento = fecha_vencimiento(periodo, dia_vencimiento)
    aparte = fijos_aparte_por_depto()
    conceptos = {c.codigo: c for c in Concepto.objects.filter(codigo__in=[*_RECURRENTES, *aparte])}
    resumen = {'creados': 0, 'actualizados': 0, 'omitidos_pagados': 0, 'n': datos['n']}
    extra_por_dep = _extras_dirigidos(periodo)

    for dep in Departamento.objects.filter(activo=True):
        lectura = LecturaMedidor.objects.filter(departamento=dep, periodo=periodo).first()
        m3 = lectura.m3_consumidos if lectura else CERO
        extra = extra_por_dep.get(dep.id, {})

        montos = {
            'MANTENIMIENTO': redondear(datos['mantenimiento'] + extra.get('MANTENIMIENTO', CERO)),
            'AGUA': costo_agua(m3),
            'AREA_COMUN': redondear(datos['area_comun'] + extra.get('AREA_COMUN', CERO)),
            'COCHERA': redondear(dep.cochera_monto or 0),
            'GASTO_ADIC': redondear(datos['adicionales'] + extra.get('GASTO_ADIC', CERO)),
            # Gastos fijos que se cobran aparte (ej. cuota extraordinaria activa).
            **{codigo: redondear(monto) for codigo, monto in aparte.items()},
        }
        for codigo, monto in montos.items():
            concepto = conceptos[codigo]
            cargo = Cargo.objects.filter(departamento=dep, concepto=concepto, periodo=periodo).first()

            if monto <= 0:
                # Sin monto: si existe un cargo sin pagos, se elimina; si tiene pagos, se deja.
                if cargo and cargo.total_imputado == 0:
                    cargo.delete()
                continue

            if cargo is None:
                Cargo.objects.create(departamento=dep, concepto=concepto, periodo=periodo,
                                     monto=monto, fecha_vencimiento=vencimiento)
                resumen['creados'] += 1
            elif cargo.total_imputado > cargo.monto:
                resumen['omitidos_pagados'] += 1
            else:
                cargo.monto = monto
                cargo.fecha_vencimiento = vencimiento
                cargo.save(update_fields=['monto', 'fecha_vencimiento'])
                resumen['actualizados'] += 1

    return resumen


def borrar_generacion_mes(periodo, incluir_manuales=False, incluir_pagados=False, aplicar=False):
    """Vista previa (y, con `aplicar`, borrado) de las boletas generadas de un mes.

    Por defecto solo los cargos que crea la generación (conceptos recurrentes) y
    sin pagos. `incluir_manuales` suma las multas/cargos puestos a mano ese mes;
    `incluir_pagados` también quita cargos con pagos: sus imputaciones se borran y
    esos pagos quedan como saldo a favor del depto. Nunca toca la carga inicial.
    Devuelve el resumen de lo que se borra (o se borró) y de lo que se conserva.
    """
    cargos = (Cargo.objects.filter(periodo=periodo, origen=Cargo.Origen.SISTEMA)
              .select_related('departamento', 'concepto').prefetch_related('imputaciones'))
    if not incluir_manuales:
        cargos = cargos.filter(concepto__es_recurrente=True)
    borrar, con_pagos = [], []
    for c in cargos:
        (con_pagos if c.total_imputado > 0 else borrar).append(c)
    if incluir_pagados:
        borrar, conservados = borrar + con_pagos, []
    else:
        conservados = con_pagos

    por_concepto = {}
    for c in borrar:
        fila = por_concepto.setdefault(c.concepto.nombre, {'n': 0, 'total': CERO, 'orden': c.concepto.orden})
        fila['n'] += 1
        fila['total'] += c.monto
    resumen = {
        'n': len(borrar), 'total': sum((c.monto for c in borrar), CERO),
        'deptos': len({c.departamento_id for c in borrar}),
        'por_concepto': [{'nombre': k, **v} for k, v in sorted(por_concepto.items(), key=lambda x: x[1]['orden'])],
        'pagos_liberados': sum((c.total_imputado for c in borrar), CERO),
        'conservados': len(conservados), 'conservados_pagado': sum((c.total_imputado for c in conservados), CERO),
    }
    if aplicar and borrar:
        with transaction.atomic():
            Cargo.objects.filter(pk__in=[c.pk for c in borrar]).delete()
    return resumen


@transaction.atomic
def crear_cargo(departamento, concepto, periodo, monto, descripcion='', dia_vencimiento=5):
    """Crea o actualiza un cargo (puertas, cámara, cuota, multa, mora, etc.).

    Idempotente por (depto, concepto, periodo): si ya existe, reemplaza el monto
    y la descripción (evita duplicados y el error de unicidad).
    """
    cargo, _ = Cargo.objects.update_or_create(
        departamento=departamento, concepto=concepto, periodo=periodo,
        defaults={'monto': redondear(monto), 'descripcion': descripcion,
                  'fecha_vencimiento': fecha_vencimiento(periodo, dia_vencimiento)})
    return cargo


# ---------------------------------------------- Pagos

def cargos_pendientes(departamento, concepto=None, periodo=None):
    """Cargos con saldo > 0 del depto, del más antiguo al más nuevo: es el orden en
    que se imputan los pagos. `concepto` y `periodo` (opcionales) filtran por motivo
    y por mes. Ordena en Python para aprovechar un prefetch de `cargos`."""
    cargos = sorted(departamento.cargos.all(),
                    key=lambda c: (c.periodo, c.concepto.orden, c.concepto_id))
    return [c for c in cargos
            if (concepto is None or c.concepto_id == concepto.pk)
            and (periodo is None or c.periodo == periodo)
            and c.saldo > 0]


@transaction.atomic
def registrar_pago(departamento, monto, concepto=None, metodo='', nota='', periodo=None,
                   con_mora_anterior=False, columnas=None):
    """Registra un pago y lo imputa a los cargos del depto (más antiguos primero).

    Si `concepto` está definido (ej. Mantenimiento), solo cancela cargos de ese
    concepto. Si `periodo` está definido, solo los de ese mes; con
    `con_mora_anterior`, antes salda la mora pendiente de meses anteriores (así
    cobra el panel). `columnas` (columnas del panel, ej. ['agua', 'mora']) limita
    el pago a esos conceptos. Lo que sobra queda como saldo a favor del pago.
    """
    monto = redondear(monto)
    pago = Pago.objects.create(departamento=departamento, monto=monto, metodo=metodo,
                               concepto=concepto, nota=nota)

    cargos = cargos_pendientes(departamento, concepto, periodo)
    if con_mora_anterior and periodo is not None:
        cargos = [c for c in cargos_pendientes(departamento)
                  if c.periodo < periodo and c.concepto.codigo == 'MORA'] + cargos
    if columnas:
        cargos = [c for c in cargos if columna_panel(c.concepto.codigo) in columnas]

    restante = monto
    for cargo in cargos:
        if restante <= 0:
            break
        aplicar = min(restante, cargo.saldo)
        Imputacion.objects.create(pago=pago, cargo=cargo, monto=aplicar)
        restante -= aplicar

    return pago


# ---------------------------------------------- Consultas de deuda

def deuda_anterior(departamento, periodo):
    """Saldo acumulado de meses anteriores al `periodo` (deuda arrastrada)."""
    return sum((c.saldo for c in departamento.cargos.all() if c.periodo < periodo), CERO)


COLUMNAS_MES = ('MANTENIMIENTO', 'AGUA', 'AREA_COMUN', 'COCHERA')


# Partes de 'extras' que el panel muestra en su propia columna.
COLUMNAS_APARTE = ('CUOTA', 'MULTA', 'MORA')

# Columna del panel en la que cae cada concepto (el resto va a 'otros'). Sirve
# para cobrar solo las columnas que se eligen en el panel.
COLUMNA_PANEL = {
    'MANTENIMIENTO': 'mantenimiento', 'AGUA': 'agua', 'AREA_COMUN': 'area_comun',
    'COCHERA': 'cochera', 'CUOTA': 'cuota', 'MULTA': 'multa', 'MORA': 'mora',
}
COLUMNAS_COBRO = (*COLUMNA_PANEL.values(), 'otros')


def columna_panel(codigo):
    return COLUMNA_PANEL.get(codigo, 'otros')


def cargos_mes_por_departamento(periodo, con_mora_anterior=False):
    """Una fila por depto (como el Excel) con sus cargos del mes por concepto
    (MANTENIMIENTO/AGUA/AREA_COMUN/COCHERA; el resto suma en 'extras'), total,
    pagado y saldo. CUOTA/MULTA/MORA son la parte de 'extras' de ese concepto,
    para mostrarlas en su propia columna. Ordenado por número de depto.

    Con `con_mora_anterior`, la mora que se debe de meses anteriores se suma a
    MORA, extras, total y saldo (el panel la cobra junto con el mes).

    'pendiente' = {columna del panel: saldo por cobrar}, para cobrar solo las
    columnas elegidas."""
    por_dep = {}

    def fila_de(dep):
        return por_dep.setdefault(dep.id, {
            'departamento': dep,
            **{k: CERO for k in COLUMNAS_MES + ('extras', *COLUMNAS_APARTE, 'total', 'pagado', 'saldo')},
            'pendiente': {k: CERO for k in COLUMNAS_COBRO}})

    cargos = (Cargo.objects.filter(periodo=periodo)
              .select_related('departamento', 'concepto').prefetch_related('imputaciones'))
    for c in cargos:
        fila = fila_de(c.departamento)
        fila[c.concepto.codigo if c.concepto.codigo in COLUMNAS_MES else 'extras'] += c.monto
        if c.concepto.codigo in COLUMNAS_APARTE:
            fila[c.concepto.codigo] += c.monto
        fila['total'] += c.monto
        fila['pagado'] += c.total_imputado
        fila['saldo'] += c.saldo
        fila['pendiente'][columna_panel(c.concepto.codigo)] += max(c.saldo, CERO)

    if con_mora_anterior:
        moras = (Cargo.objects.filter(periodo__lt=periodo, concepto__codigo='MORA')
                 .select_related('departamento').prefetch_related('imputaciones'))
        for c in moras:
            if c.saldo > 0:
                fila = fila_de(c.departamento)
                for k in ('MORA', 'extras', 'total', 'saldo'):
                    fila[k] += c.saldo
                fila['pendiente']['mora'] += c.saldo

    return sorted(por_dep.values(), key=lambda f: f['departamento'].numero_domicilio)


# Meses previos al de referencia que se muestran sueltos; lo más antiguo va acumulado.
MESES_SUELTOS = 2


def agrupar_deuda(cargos, referencia, meses_sueltos=MESES_SUELTOS):
    """Saldo pendiente agrupado para MOSTRAR (no cambia cómo se aplican los pagos).

    Viendo septiembre: julio, agosto y septiembre van por separado y todo lo
    anterior (de cualquier concepto) en un solo `acumulado`. Lo posterior al mes
    de referencia (boletas ya emitidas de meses siguientes) va en `posterior`.
    Devuelve {'acumulado', 'hasta' (último mes del acumulado), 'meses':
    [(periodo, monto)], 'posterior', 'total'}.
    """
    inicio = sumar_meses(referencia, -meses_sueltos)
    por_mes = {sumar_meses(inicio, k): CERO for k in range(meses_sueltos + 1)}
    acumulado = posterior = CERO
    for c in cargos:
        saldo = c.saldo
        if saldo <= 0:
            continue
        if c.periodo < inicio:
            acumulado += saldo
        elif c.periodo > referencia:
            posterior += saldo
        else:
            por_mes[c.periodo] += saldo
    return {'acumulado': acumulado, 'hasta': sumar_meses(inicio, -1), 'meses': sorted(por_mes.items()),
            'posterior': posterior, 'total': acumulado + posterior + sum(por_mes.values(), CERO)}


def deudores(periodo=None):
    """Lista de departamentos con deuda > 0 y el desglose de su saldo.

    Devuelve dicts con: departamento, total, anterior, mes (saldo del `periodo`),
    por_concepto. Ordenado de mayor a menor deuda.
    """
    filas = []
    deps = (Departamento.objects.filter(activo=True)
            .prefetch_related('cargos__concepto', 'cargos__imputaciones'))
    for dep in deps:
        total = anterior = mes = Decimal('0.00')
        por_concepto = {}
        for c in dep.cargos.all():
            saldo = c.saldo
            total += saldo
            if saldo == 0:
                continue
            por_concepto[c.concepto.codigo] = por_concepto.get(c.concepto.codigo, CERO) + saldo
            if periodo is not None:
                if c.periodo < periodo:
                    anterior += saldo
                elif c.periodo == periodo:
                    mes += saldo
        if total > 0:
            filas.append({'departamento': dep, 'total': total, 'anterior': anterior,
                          'mes': mes, 'por_concepto': por_concepto})
    filas.sort(key=lambda f: f['total'], reverse=True)
    return filas
