"""Cuenta corriente: catálogo de conceptos, generación de cargos, pagos y deudas."""
from decimal import Decimal

from django.db import transaction

from ..models import Cargo, Concepto, Departamento, GastoEdificio, Imputacion, LecturaMedidor, Pago
from .agua import costo_agua
from .comun import CERO, fecha_vencimiento, redondear
from .gastos import prorrateo_mes

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

    Un cargo por concepto (mantenimiento, agua, área común, cochera, adicionales).
    Idempotente: no duplica; no toca cargos que ya recibieron pagos.
    """
    sembrar_conceptos()
    datos = prorrateo_mes(periodo)
    vencimiento = fecha_vencimiento(periodo, dia_vencimiento)
    conceptos = {c.codigo: c for c in Concepto.objects.filter(codigo__in=_RECURRENTES)}
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
            elif cargo.total_imputado > 0:
                resumen['omitidos_pagados'] += 1
            else:
                cargo.monto = monto
                cargo.fecha_vencimiento = vencimiento
                cargo.save(update_fields=['monto', 'fecha_vencimiento'])
                resumen['actualizados'] += 1

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
def registrar_pago(departamento, monto, concepto=None, metodo='', nota='', periodo=None):
    """Registra un pago y lo imputa a los cargos del depto (más antiguos primero).

    Si `concepto` está definido (ej. Mantenimiento), solo cancela cargos de ese
    concepto. Si `periodo` está definido, solo los de ese mes. Lo que sobra queda
    como saldo a favor del pago.
    """
    monto = redondear(monto)
    pago = Pago.objects.create(departamento=departamento, monto=monto, metodo=metodo,
                               concepto=concepto, nota=nota)

    restante = monto
    for cargo in cargos_pendientes(departamento, concepto, periodo):
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


def cargos_mes_por_departamento(periodo):
    """Una fila por depto (como el Excel) con sus cargos del mes por concepto
    (MANTENIMIENTO/AGUA/AREA_COMUN/COCHERA; el resto suma en 'extras'), total,
    pagado y saldo. Ordenado por número de depto."""
    cargos = (Cargo.objects.filter(periodo=periodo)
              .select_related('departamento', 'concepto').prefetch_related('imputaciones'))
    por_dep = {}
    for c in cargos:
        fila = por_dep.setdefault(c.departamento_id, {
            'departamento': c.departamento,
            **{k: CERO for k in COLUMNAS_MES + ('extras', 'total', 'pagado', 'saldo')}})
        fila[c.concepto.codigo if c.concepto.codigo in COLUMNAS_MES else 'extras'] += c.monto
        fila['total'] += c.monto
        fila['pagado'] += c.total_imputado
        fila['saldo'] += c.saldo
    return sorted(por_dep.values(), key=lambda f: f['departamento'].numero_domicilio)


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
