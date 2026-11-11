"""Importación de departamentos y lecturas desde Excel."""
import unicodedata
from datetime import date, datetime

from django.db import transaction

from ..models import Cargo, Concepto, Departamento
from .agua import registrar_lectura
from .comun import a_decimal
from .cuenta import crear_cargo

# Encabezados aceptados (normalizados) -> campo interno.
_ENCABEZADOS = {
    'DEPARTAMENTO': 'numero', 'DPTO': 'numero', 'DEPTO': 'numero',
    'NUMERO': 'numero', 'N_DEPARTAMENTO': 'numero', 'N_DPTO': 'numero',
    'LECTURA_ANTERIOR': 'anterior', 'ANTERIOR': 'anterior',
    'LECTURA_ACTUAL': 'actual', 'ACTUAL': 'actual',
    'COCHERA': 'cochera', 'MONTO_COCHERA': 'cochera', 'N_COCHERA': 'cochera',
}


def _norm(valor):
    """Normaliza un encabezado: mayúsculas, sin acentos, con guion bajo."""
    texto = str(valor or '').strip().upper()
    texto = ''.join(c for c in unicodedata.normalize('NFD', texto)
                    if unicodedata.category(c) != 'Mn')
    return texto.replace(' ', '_')


def _buscar_encabezados(ws):
    """(fila, {campo: columna}) de la fila de encabezados (busca en las primeras filas)."""
    for r in range(1, min(ws.max_row, 15) + 1):
        fila = {}
        for c in range(1, min(ws.max_column, 30) + 1):
            campo = _ENCABEZADOS.get(_norm(ws.cell(row=r, column=c).value))
            if campo:
                fila[campo] = c
        if 'numero' in fila:
            return r, fila
    raise ValueError('No se encontró la columna "DEPARTAMENTO" en la primera hoja.')


@transaction.atomic
def importar_lecturas_excel(fileobj, periodo):
    """Lee un Excel con el formato del sistema y carga departamentos + lecturas.

    Formato esperado (primera hoja): una fila de encabezados con al menos la
    columna DEPARTAMENTO, y opcionalmente LECTURA_ANTERIOR, LECTURA_ACTUAL,
    COCHERA. Una fila por departamento.
    """
    import openpyxl

    ws = openpyxl.load_workbook(fileobj, data_only=True).active
    header_row, colmap = _buscar_encabezados(ws)

    def celda(r, campo):
        return a_decimal(ws.cell(row=r, column=colmap[campo]).value) if campo in colmap else None

    resumen = {'departamentos': 0, 'lecturas': 0, 'errores': []}
    for r in range(header_row + 1, ws.max_row + 1):
        crudo = ws.cell(row=r, column=colmap['numero']).value
        if crudo is None or str(crudo).strip() == '':
            continue
        # Normaliza el número (101.0 -> "101"; "T6-301" -> "T6-301").
        num_dec = a_decimal(crudo)
        numero = str(int(num_dec)) if (num_dec is not None and num_dec == num_dec.to_integral_value()) \
            else str(crudo).strip()

        defaults = {'activo': True}
        cochera = celda(r, 'cochera')
        if cochera is not None:
            defaults['cochera_monto'] = cochera
        dep, _ = Departamento.objects.update_or_create(numero_domicilio=numero, defaults=defaults)
        resumen['departamentos'] += 1

        actual = celda(r, 'actual')
        if actual is not None:
            try:
                registrar_lectura(dep, periodo, lectura_actual=actual, lectura_anterior=celda(r, 'anterior'))
                resumen['lecturas'] += 1
            except Exception as e:  # pragma: no cover - fila con datos inconsistentes
                resumen['errores'].append(f'Depto {numero}: {e}')

    return resumen


# ---------------------------------------------- Importar deudas (cargos)

_ENC_DEUDAS = {
    'DEPARTAMENTO': 'dpto', 'DPTO': 'dpto', 'DEPTO': 'dpto', 'NUMERO': 'dpto',
    'CONCEPTO': 'concepto', 'RUBRO': 'concepto', 'MOTIVO': 'concepto',
    'PERIODO': 'periodo', 'MES': 'periodo', 'FECHA': 'periodo',
    'MONTO': 'monto', 'IMPORTE': 'monto', 'DEUDA': 'monto',
    'DESCRIPCION': 'descripcion', 'DETALLE': 'descripcion', 'NOTA': 'descripcion',
}


def _periodo_de(valor):
    """Convierte la celda de periodo a date(año, mes, 1). Acepta datetime,
    '2026-10', '2026-10-01', '10/2026', '10-2026'."""
    if isinstance(valor, (datetime, date)):
        return date(valor.year, valor.month, 1)
    txt = str(valor or '').strip()
    if not txt:
        raise ValueError('periodo vacío')
    sep = '-' if '-' in txt else '/' if '/' in txt else None
    if sep:
        a, b = (txt.split(sep) + ['', ''])[:2]
        a, b = a.strip(), b.strip()
        anio, mes = (a, b) if len(a) == 4 else (b, a)   # año de 4 dígitos
        return date(int(anio), int(mes), 1)
    raise ValueError(f'periodo inválido: {valor!r} (usa 2026-10 o 10/2026)')


def importar_deudas_excel(fileobj, aplicar=False):
    """Carga deudas (cargos) desde un Excel con columnas DEPARTAMENTO, CONCEPTO,
    PERIODO, MONTO y, opcional, DESCRIPCION. Idempotente por depto+concepto+
    periodo (vuelve a correr = reemplaza el monto, no duplica).

    Con `aplicar=False` (por defecto) SOLO simula: hace todo en una transacción y
    la revierte, devolviendo el resumen de lo que pasaría."""
    import openpyxl

    ws = openpyxl.load_workbook(fileobj, data_only=True).active
    header_row, colmap = None, {}
    for r in range(1, min(ws.max_row, 15) + 1):
        fila = {}
        for c in range(1, min(ws.max_column, 30) + 1):
            campo = _ENC_DEUDAS.get(_norm(ws.cell(row=r, column=c).value))
            if campo:
                fila[campo] = c
        if {'dpto', 'concepto', 'periodo', 'monto'} <= set(fila):
            header_row, colmap = r, fila
            break
    if header_row is None:
        raise ValueError('Faltan columnas: se necesitan DEPARTAMENTO, CONCEPTO, PERIODO y MONTO.')

    deps = {str(d.numero_domicilio).strip(): d for d in Departamento.objects.all()}
    conceptos = {}
    for co in Concepto.objects.all():
        conceptos[_norm(co.codigo)] = co
        conceptos[_norm(co.nombre)] = co

    resumen = {'creados': 0, 'actualizados': 0, 'total': 0, 'errores': [], 'aplicado': aplicar}

    class _Simular(Exception):
        pass

    def val(r, campo):
        return ws.cell(row=r, column=colmap[campo]).value if campo in colmap else None

    try:
        with transaction.atomic():
            for r in range(header_row + 1, ws.max_row + 1):
                crudo = val(r, 'dpto')
                if crudo is None or str(crudo).strip() == '':
                    continue
                num_dec = a_decimal(crudo)
                numero = str(int(num_dec)) if (num_dec is not None and num_dec == num_dec.to_integral_value()) else str(crudo).strip()
                try:
                    dep = deps.get(numero)
                    if dep is None:
                        raise ValueError(f'departamento {numero} no existe')
                    concepto = conceptos.get(_norm(val(r, 'concepto')))
                    if concepto is None:
                        raise ValueError(f'concepto «{val(r, "concepto")}» desconocido')
                    per = _periodo_de(val(r, 'periodo'))
                    monto = a_decimal(val(r, 'monto'))
                    if monto is None:
                        raise ValueError('monto inválido')
                    existe = Cargo.objects.filter(departamento=dep, concepto=concepto, periodo=per).exists()
                    crear_cargo(dep, concepto, per, monto, descripcion=str(val(r, 'descripcion') or '')[:150])
                    resumen['actualizados' if existe else 'creados'] += 1
                    resumen['total'] += 1
                except Exception as e:
                    resumen['errores'].append(f'Fila {r} (depto {numero}): {e}')
            if not aplicar:
                raise _Simular
    except _Simular:
        pass

    return resumen
