"""Importa los gastos del edificio (hoja DATOS) como egresos del mes.

La hoja DATOS del Excel de mantenimiento lista los gastos reales del edificio
(SEDAPAL, luz, limpieza, vigilancia, ascensores, admin...). Se cargan como
`GastoEdificio` del periodo para que el Balance del mes muestre los EGRESOS
detallados. El mantenimiento por depto (70.74) ya salió del cuadre; estos
gastos son el lado del gasto, para el balance ingresos vs egresos.

Idempotente: borra los GastoEdificio del periodo y los vuelve a crear.

⚠️ NO correr "Generar recibos"/generar_cargos_mes sobre este periodo: usaría
estos gastos para recalcular el mantenimiento y chocaría con el cuadre.

Uso:
    python manage.py importar_gastos "MANTENIMIENTO SEPTIEMBRE 2026.xlsx"
    python manage.py importar_gastos "<xlsx>" --periodo 2026-09
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from departamentos.models import GastoEdificio

HOJA = 'DATOS'


class Command(BaseCommand):
    help = 'Carga los gastos del edificio (hoja DATOS) como egresos del mes.'

    def add_arguments(self, parser):
        parser.add_argument('ruta', help='Ruta al Excel de mantenimiento del mes.')
        parser.add_argument('--periodo', default='2026-09', help='Periodo AAAA-MM. Def: 2026-09.')
        parser.add_argument('--hoja', default=HOJA, help=f"Hoja de gastos. Def: '{HOJA}'.")

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
        objetivo = opts['hoja'].strip().upper()
        nombre = next((s for s in wb.sheetnames if s.strip().upper() == objetivo), None)
        if nombre is None:
            raise CommandError(f"No existe la hoja '{opts['hoja']}'. Hojas: {wb.sheetnames}")
        ws = wb[nombre]

        try:
            y, m = map(int, opts['periodo'].split('-'))
            per = date(y, m, 1)
        except ValueError:
            raise CommandError('--periodo debe ser AAAA-MM (ej. 2026-09).')

        # Lee col A (descripción) + col B (monto) mientras haya pares válidos.
        filas = []
        for r in range(1, ws.max_row + 1):
            a = ws.cell(row=r, column=1).value
            b = ws.cell(row=r, column=2).value
            if a is None or not isinstance(b, (int, float)):
                continue
            desc = str(a).strip()
            if not desc:
                continue
            try:
                monto = Decimal(str(b))
            except (InvalidOperation, ValueError):
                continue
            if monto > 0:
                filas.append((desc[:120], monto))
        if not filas:
            raise CommandError(f"No encontré gastos (col A=descripción, B=monto) en la hoja '{nombre}'.")

        borrados = GastoEdificio.objects.filter(periodo=per).delete()[0]
        total = Decimal('0.00')
        for desc, monto in filas:
            GastoEdificio.objects.create(periodo=per, descripcion=desc, monto=monto,
                                         categoria=GastoEdificio.Categoria.GENERAL, compartido=True)
            total += monto

        self.stdout.write(self.style.SUCCESS(
            f'{len(filas)} gastos del edificio cargados para {per:%m/%Y} por S/ {total:.2f}.'))
        for desc, monto in filas:
            self.stdout.write(f'  {desc:<50} S/ {monto:>9.2f}')
        if borrados:
            self.stdout.write(self.style.WARNING(f'Se reemplazaron {borrados} gasto(s) del mismo mes.'))
