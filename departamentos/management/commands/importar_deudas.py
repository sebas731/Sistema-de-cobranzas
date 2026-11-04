"""Importa la hoja DEUDAS ACTUALES del Excel BALANCE como cargos del sistema.

Fuente: hoja 'DEUDAS ACTUALES' (una fila por depto). Cada columna -> un concepto
y un periodo, de modo que el recibo muestre la deuda del mes y la anterior:

    AGOSTO       -> Mantenimiento  2026-08  (del mes)
    JULIO        -> Mantenimiento  2026-07  (anterior)
    D. ANTERIORES-> Mantenimiento  2026-06  (anterior, arrastre acumulado)
    PUERTAS/CAMARA/ASCENSORES/CUOTA/MULTA/MORA -> su concepto, 2026-08

La tabla MAYO/JUNIO (derecha de DEUDAS ANTERIORES) NO se importa: está pagada
(es ingreso, se maneja aparte).

Uso:
    python manage.py importar_deudas "C:/ruta/BALANCE FIORELLA.xlsx"
    python manage.py importar_deudas "BALANCE FIORELLA.xlsx" --base 2026-08
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from departamentos.models import Concepto, Departamento
from departamentos.servicios import crear_cargo, sembrar_conceptos

HOJA = 'DEUDAS ACTUALES'
# columnas de la hoja (1-based): C=3 ... M=13
COL_DPTO = 3


def _mes_anterior(d, n=1):
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


class Command(BaseCommand):
    help = 'Importa DEUDAS ACTUALES del Excel BALANCE como cargos.'

    def add_arguments(self, parser):
        parser.add_argument('ruta', help='Ruta al Excel BALANCE.')
        parser.add_argument('--base', default='2026-08',
                            help='Periodo base AAAA-MM (mes "del mes"). Def: 2026-08.')

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            import openpyxl
        except ImportError:
            raise CommandError('Falta openpyxl (pip install openpyxl).')
        try:
            wb = openpyxl.load_workbook(opts['ruta'], data_only=True)
        except FileNotFoundError:
            raise CommandError(f"No se encontró el archivo: {opts['ruta']}")
        if HOJA not in wb.sheetnames:
            raise CommandError(f"No existe la hoja '{HOJA}'. Hojas: {wb.sheetnames}")
        ws = wb[HOJA]

        try:
            y, m = map(int, opts['base'].split('-'))
            base = date(y, m, 1)
        except ValueError:
            raise CommandError('--base debe ser AAAA-MM (ej. 2026-08).')
        jul = _mes_anterior(base, 1)   # mes anterior
        ant = _mes_anterior(base, 2)   # dos meses antes (arrastre)

        # (columna, codigo_concepto, periodo, etiqueta)
        mapeo = [
            (4,  'PUERTAS',       base, 'Puertas'),
            (5,  'CAMARA',        base, 'Cámaras'),
            (6,  'ASCENSORES',    base, 'Ascensores'),
            (7,  'MANTENIMIENTO', ant,  'Deuda anterior'),
            (8,  'MANTENIMIENTO', jul,  f'Mantenimiento {jul:%m/%Y}'),
            (9,  'MANTENIMIENTO', base, f'Mantenimiento {base:%m/%Y}'),
            (10, 'CUOTA',         base, 'Cuota'),
            (11, 'MULTA',         base, 'Multa'),
            (12, 'MORA',          base, 'Mora'),
        ]

        sembrar_conceptos()
        conc = {c.codigo: c for c in Concepto.objects.all()}

        def dec(v):
            if v in (None, ''):
                return None
            try:
                return Decimal(str(v))
            except (InvalidOperation, ValueError):
                return None

        creados = 0
        deptos_tocados = set()
        sin_depto = []
        for r in range(5, ws.max_row + 1):
            raw = ws.cell(row=r, column=COL_DPTO).value
            if raw is None:
                continue
            numero = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
            if not numero or not numero[0].isdigit():
                continue
            dep = Departamento.objects.filter(numero_domicilio=numero).first()
            if not dep:
                sin_depto.append(numero)
                continue
            for col, cod, per, etiqueta in mapeo:
                monto = dec(ws.cell(row=r, column=col).value)
                if monto and monto > 0 and cod in conc:
                    crear_cargo(dep, conc[cod], per, monto, etiqueta)
                    creados += 1
                    deptos_tocados.add(numero)

        self.stdout.write(self.style.SUCCESS(
            f'{creados} cargos creados en {len(deptos_tocados)} departamentos '
            f'(base {base:%m/%Y}).'))
        if sin_depto:
            self.stdout.write(self.style.WARNING(
                f'{len(set(sin_depto))} depto(s) del Excel NO existen en el sistema '
                f'(se omitieron): {sorted(set(sin_depto))}'))
