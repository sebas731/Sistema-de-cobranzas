"""Importa los cargos NUEVOS de un mes desde la hoja AGUA del Excel de mantenimiento.

Sube SOLO lo que es nuevo del mes (lo demás ya vive en la base de deudas):

    Agua (MONTO)      -> AGUA
    Área común        -> AREA_COMUN
    Cochera           -> COCHERA
    Mantenimiento     -> MANTENIMIENTO   (valor del Excel, NO se recalcula)
    Cuota extraord.   -> CUOTA           (+25 del mes; agosto ya está en la base)

NO se re-suben (ya están en la base de agosto, se contarían doble):
    - Mora / ascensor / cámara / mantenimiento anterior (columna "MORA" del Excel)
    - Multa de inasistencia (columna "MULTA"): los deptos de septiembre son un
      subconjunto de los ya cargados en la base.

Luego, para la cuota, registra los PAGOS que reconcilian el saldo con lo que el
Excel dice que cada depto aún debe (columna GASTOS ADIC.): paga lo más antiguo
primero (agosto, luego septiembre). Idempotente.

Uso:
    python manage.py importar_mantenimiento "MANTENIMIENTO SEPTIEMBRE 2026.xlsx"
    python manage.py importar_mantenimiento "<xlsx>" --periodo 2026-09 --cuota 25
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from departamentos.models import Concepto, Departamento, Imputacion, Pago
from departamentos.servicios import crear_cargo, sembrar_conceptos

HOJA = 'AGUA'
NOTA_PAGO_CUOTA = 'Ajuste cuota set-2026'
# columnas (1-based) de la hoja AGUA
COL = {'dpto': 4, 'agua': 8, 'area': 9, 'cochera': 11, 'mant': 12, 'cuota_debe': 14}


def _dec(v):
    if v in (None, ''):
        return None
    try:
        return Decimal(str(round(float(v), 2)))
    except (InvalidOperation, ValueError, TypeError):
        return None


class Command(BaseCommand):
    help = 'Importa los cargos nuevos del mes (hoja AGUA) + pagos de cuota.'

    def add_arguments(self, parser):
        parser.add_argument('ruta', help='Ruta al Excel de mantenimiento del mes.')
        parser.add_argument('--periodo', default='2026-09', help='Periodo AAAA-MM. Def: 2026-09.')
        parser.add_argument('--cuota', type=Decimal, default=Decimal('25'),
                            help='Cuota extraordinaria del mes. Def: 25.')
        parser.add_argument('--hoja', default=HOJA, help=f"Hoja. Def: '{HOJA}'.")

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
        cuota_mes = opts['cuota']

        sembrar_conceptos()
        conc = {c.codigo: c for c in Concepto.objects.all()}

        # --- 1) Cargos nuevos del mes ---
        mapeo = [('agua', 'AGUA', 'Agua'), ('area', 'AREA_COMUN', 'Área común'),
                 ('cochera', 'COCHERA', 'Cochera'), ('mant', 'MANTENIMIENTO', 'Mantenimiento')]
        resumen = {k: {'n': 0, 'total': Decimal('0')} for _, k, _ in mapeo}
        resumen['CUOTA'] = {'n': 0, 'total': Decimal('0')}
        deudas_cuota, sin_depto = {}, []

        for r in range(3, ws.max_row + 1):
            raw = ws.cell(row=r, column=COL['dpto']).value
            if not isinstance(raw, (int, float)):
                continue
            numero = str(int(raw))
            dep = Departamento.objects.filter(numero_domicilio=numero).first()
            if not dep:
                sin_depto.append(numero)
                continue
            for key, cod, etiqueta in mapeo:
                monto = _dec(ws.cell(row=r, column=COL[key]).value)
                if monto and monto > 0:
                    crear_cargo(dep, conc[cod], per, monto, f'{etiqueta} {per:%m/%Y}')
                    resumen[cod]['n'] += 1
                    resumen[cod]['total'] += monto
            # Cuota del mes (+25) y lo que el Excel dice que aún debe
            if cuota_mes > 0:
                crear_cargo(dep, conc['CUOTA'], per, cuota_mes, f'Cuota extraordinaria {per:%m/%Y}')
                resumen['CUOTA']['n'] += 1
                resumen['CUOTA']['total'] += cuota_mes
            deudas_cuota[dep.pk] = (dep, _dec(ws.cell(row=r, column=COL['cuota_debe']).value) or Decimal('0'))

        # --- 2) Pagos de cuota: reconciliar saldo de cuota con lo que debe el Excel ---
        Pago.objects.filter(nota=NOTA_PAGO_CUOTA).delete()  # idempotencia
        fecha_pago = date(per.year, per.month, 3)
        pagos_n, pagos_total = 0, Decimal('0')
        for dep, debe in deudas_cuota.values():
            cuotas = sorted((c for c in dep.cargos.all()
                             if c.concepto.codigo == 'CUOTA' and c.periodo <= per and c.saldo > 0),
                            key=lambda c: c.periodo)
            saldo_actual = sum((c.saldo for c in cuotas), Decimal('0'))
            pago = saldo_actual - debe
            if pago <= 0:
                continue
            p = Pago.objects.create(departamento=dep, monto=pago, fecha_pago=fecha_pago,
                                    metodo='Cuota', concepto=conc['CUOTA'], nota=NOTA_PAGO_CUOTA)
            restante = pago
            for c in cuotas:
                if restante <= 0:
                    break
                ap = min(restante, c.saldo)
                Imputacion.objects.create(pago=p, cargo=c, monto=ap)
                restante -= ap
            pagos_n += 1
            pagos_total += pago

        # --- Reporte ---
        self.stdout.write(self.style.SUCCESS(f'Cargos nuevos de {per:%m/%Y}:'))
        etiquetas = {'AGUA': 'Agua', 'AREA_COMUN': 'Área común', 'COCHERA': 'Cochera',
                     'MANTENIMIENTO': 'Mantenimiento', 'CUOTA': 'Cuota extraordinaria'}
        gran_total = Decimal('0')
        for cod in ['AGUA', 'AREA_COMUN', 'COCHERA', 'MANTENIMIENTO', 'CUOTA']:
            d = resumen[cod]
            gran_total += d['total']
            self.stdout.write(f"  {etiquetas[cod]:<22} {d['n']:>3} deptos  S/ {d['total']:>10.2f}")
        self.stdout.write(self.style.SUCCESS(f'  {"TOTAL cargos nuevos":<22}        S/ {gran_total:>10.2f}'))
        self.stdout.write(self.style.SUCCESS(
            f'Pagos de cuota registrados: {pagos_n}  por S/ {pagos_total:.2f} (ingreso de {per:%m/%Y}).'))
        self.stdout.write('  (Multa, mora, ascensor y cámara NO se suben: ya están en la base.)')
        if sin_depto:
            self.stdout.write(self.style.WARNING(
                f'{len(set(sin_depto))} depto(s) del Excel NO existen en el sistema: {sorted(set(sin_depto))}'))
