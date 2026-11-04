"""Importa el bloque MAYO/JUNIO del Excel BALANCE como INGRESOS (ya cobrados).

Esa tabla (lado derecho de la hoja 'DEUDAS ANTERIORES', total S/ 3460.84) es
mantenimiento de mayo y junio que YA se cobró: es ingreso, NO deuda. Para que el
sistema lo refleje como ingreso sin ensuciar la deuda vigente (S/ 16 662.26),
cada importe se registra como un PAR cargo+pago que se cancela a cero:

    - Concepto propio **MANT_ANT** ("Mantenimiento cobrado meses ant."), NO
      recurrente, para no chocar con la deuda (que usa MANTENIMIENTO) ni con la
      generación mensual.
    - Cargo con ese importe + Pago imputado 100%% a ese cargo:
        * saldo del cargo = 0  -> NO aparece en Deudores, NO suma a la deuda.
        * saldo a favor del pago = 0 -> NO genera "crédito" que descuente en la
          boleta del depto (recibo.py resta los saldos a favor).
        * el Pago cuenta como ingreso del mes (Balance = pagos por fecha_pago):
          mayo -> mayo 2026, junio -> junio 2026.
    - origen = SISTEMA (no MIGRACION), para que la pantalla "Carga inicial de
      deudas" (que borra los pagos MIGRACION al guardar) no los elimine.

Idempotente: al reejecutar borra primero los cargos/pagos del concepto MANT_ANT
y los vuelve a crear.

Uso:
    python manage.py importar_pagos "C:/ruta/BALANCE FIORELLA.xlsx"
    python manage.py importar_pagos "BALANCE FIORELLA.xlsx" --anio 2026
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from departamentos.models import Cargo, Concepto, Departamento, Imputacion, Pago

HOJA = 'DEUDAS ANTERIORES'
COD_CONCEPTO = 'MANT_ANT'
NOMBRE_CONCEPTO = 'Mantenimiento cobrado meses ant.'
METODO = 'Carga inicial (ingreso)'


def _dec(v):
    if v in (None, ''):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


class Command(BaseCommand):
    help = 'Importa el bloque MAYO/JUNIO (ya cobrado) como ingresos (cargo+pago a cero).'

    def add_arguments(self, parser):
        parser.add_argument('ruta', help='Ruta al Excel BALANCE.')
        parser.add_argument('--anio', type=int, default=2026,
                            help='Año de los pagos mayo/junio. Def: 2026.')
        parser.add_argument('--hoja', default=HOJA,
                            help=f"Hoja donde está el bloque. Def: '{HOJA}'.")

    def _leer_col_g(self, wb):
        """{depto: D. ANTERIORES} de la hoja 'DEUDAS ACTUALES' (col C=depto, G=7)."""
        hoja = next((s for s in wb.sheetnames if s.strip().upper() == 'DEUDAS ACTUALES'), None)
        if hoja is None:
            return {}
        wa = wb[hoja]
        out = {}
        for r in range(5, wa.max_row + 1):
            raw = wa.cell(row=r, column=3).value
            if raw is None:
                continue
            num = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
            if not num or not num[0].isdigit():
                continue
            g = _dec(wa.cell(row=r, column=7).value)
            if g:
                out[num] = g
        return out

    def _deptos_con_detalle(self, ws):
        """Deptos que SÍ tienen desglose en el bloque izquierdo (col A, filas 5..32)."""
        out = set()
        for r in range(5, 33):
            a = ws.cell(row=r, column=1).value
            if isinstance(a, (int, float)):
                out.add(str(int(a)))
            elif a and str(a).strip() and str(a).strip()[0].isdigit():
                out.add(str(a).strip())
        return out

    def _ubicar_bloque(self, ws):
        """Fila/columna del encabezado (DEPT | MAYO | JUNIO | TOTAL)."""
        for row in ws.iter_rows():
            for cell in row:
                val = str(cell.value).strip().upper() if cell.value is not None else ''
                if 'DEPT' in val:
                    c = cell.column
                    der = [str(ws.cell(row=cell.row, column=c + k).value or '').strip().upper()
                           for k in range(1, 4)]
                    if der[:3] == ['MAYO', 'JUNIO', 'TOTAL']:
                        return cell.row, c
        return None, None

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

        # Tolera espacios/mayúsculas en el nombre de la hoja (ej. 'DEUDAS ANTERIORES ').
        objetivo = opts['hoja'].strip().upper()
        nombre_hoja = next((s for s in wb.sheetnames if s.strip().upper() == objetivo), None)
        if nombre_hoja is None:
            raise CommandError(f"No existe la hoja '{opts['hoja']}'. Hojas: {wb.sheetnames}")
        ws = wb[nombre_hoja]

        fila_h, col_d = self._ubicar_bloque(ws)
        if fila_h is None:
            raise CommandError(
                "No encontré el encabezado 'DEPT | MAYO | JUNIO | TOTAL' en la hoja. "
                "Revisa --hoja o el formato del Excel.")

        anio = opts['anio']
        fecha_mayo = date(anio, 5, 31)
        fecha_junio = date(anio, 6, 30)

        # Saldos no pagados de mayo/junio: un depto del bloque que ADEMÁS aparece
        # en col G (D. ANTERIORES) SIN detalle propio en el bloque izquierdo pagó
        # solo en parte; su col G es lo que quedó debiendo (ya cargado como deuda
        # por importar_deudas). Ese saldo se DESCUENTA del ingreso para no inflarlo
        # (ej. 202: debía 467.25, pagó 345, debe 122.25). Si el depto tiene detalle
        # propio (ej. 501), su col G es deuda antigua aparte y NO se descuenta.
        col_g = self._leer_col_g(wb)
        con_detalle = self._deptos_con_detalle(ws)
        saldo_pendiente = {d: g for d, g in col_g.items()
                           if g > 0 and d not in con_detalle}

        concepto, _ = Concepto.objects.get_or_create(
            codigo=COD_CONCEPTO,
            defaults={'nombre': NOMBRE_CONCEPTO, 'orden': 15, 'es_recurrente': False,
                      'descripcion': 'Ingreso por mantenimiento de meses anteriores ya cobrado.'})

        # Idempotencia: borra lo de este concepto (pagos + cargos) antes de recrear.
        n_pagos_prev = Pago.objects.filter(concepto=concepto).count()
        Pago.objects.filter(concepto=concepto).delete()        # cascada: imputaciones
        Cargo.objects.filter(concepto=concepto).delete()

        creados = 0
        total = Decimal('0.00')
        sin_depto = []
        ajustados = []
        for r in range(fila_h + 1, ws.max_row + 1):
            raw = ws.cell(row=r, column=col_d).value
            if raw in (None, ''):
                continue
            numero = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
            if not numero or not numero[0].isdigit():
                continue
            dep = Departamento.objects.filter(numero_domicilio=numero).first()
            if not dep:
                sin_depto.append(numero)
                continue
            mayo = _dec(ws.cell(row=r, column=col_d + 1).value) or Decimal('0.00')
            junio = _dec(ws.cell(row=r, column=col_d + 2).value) or Decimal('0.00')

            # Pago parcial: descuenta el saldo pendiente, lo más reciente primero
            # (se pagó lo antiguo: mayo completo, luego junio). El resto es deuda.
            pendiente = saldo_pendiente.get(numero, Decimal('0.00'))
            if pendiente > 0:
                bruto = mayo + junio
                pagado_total = max(bruto - pendiente, Decimal('0.00'))
                mayo_pagado = min(mayo, pagado_total)
                junio_pagado = pagado_total - mayo_pagado
                ajustados.append((numero, bruto, pagado_total, min(pendiente, bruto)))
                mayo, junio = mayo_pagado, junio_pagado

            for monto, fecha, etiqueta in ((mayo, fecha_mayo, 'Mantenimiento mayo'),
                                           (junio, fecha_junio, 'Mantenimiento junio')):
                if monto <= 0:
                    continue
                cargo = Cargo.objects.create(
                    departamento=dep, concepto=concepto, periodo=fecha.replace(day=1),
                    monto=monto, fecha_vencimiento=fecha,
                    descripcion=f'{etiqueta} {anio} (cobrado)')
                pago = Pago.objects.create(
                    departamento=dep, monto=monto, fecha_pago=fecha, metodo=METODO,
                    concepto=concepto, nota=f'{etiqueta} {anio} (cobrado, carga inicial)')
                Imputacion.objects.create(pago=pago, cargo=cargo, monto=monto)
                creados += 1
                total += monto

        self.stdout.write(self.style.SUCCESS(
            f'{creados} ingresos creados por S/ {total:.2f} '
            f'(mayo {fecha_mayo:%m/%Y} + junio {fecha_junio:%m/%Y}).'))
        for numero, bruto, pagado, pend in ajustados:
            self.stdout.write(self.style.WARNING(
                f'Depto {numero}: pago PARCIAL -> debía {bruto:.2f}, pagó {pagado:.2f} '
                f'(ingreso); quedan {pend:.2f} de deuda (ya en D. anteriores).'))
        if n_pagos_prev:
            self.stdout.write(self.style.WARNING(
                f'Se reemplazaron {n_pagos_prev} ingreso(s) de una carga anterior.'))
        if sin_depto:
            self.stdout.write(self.style.WARNING(
                f'{len(set(sin_depto))} depto(s) del Excel NO existen en el sistema '
                f'(se omitieron): {sorted(set(sin_depto))}'))
