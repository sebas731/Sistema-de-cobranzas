# -*- coding: utf-8 -*-
"""Reconcilia los pagos de vecinos con el Excel Hoja 1: agrega los pagos que
faltan (posteriores al último cargue) e imputa cada uno a la deuda más antigua;
y borra el único duplicado real (depto 302, S/108.56 dos veces el 18-nov).
Idempotente. Por defecto ENSAYO; usa --aplicar para grabar.

    python manage.py reconciliar_pagos            # ensayo
    python manage.py reconciliar_pagos --aplicar   # graba
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from departamentos.models import Departamento, Pago
from departamentos.servicios import registrar_pago

# Pagos que faltan en la BD (del Excel Hoja 1): (depto, fecha ISO, monto).
FALTAN = [
    ('711', '2026-11-08', '167.70'), ('211', '2026-11-10', '124.21'),
    ('207', '2026-11-10', '50.00'),  ('304', '2026-11-12', '133.59'),
    ('611', '2026-11-14', '108.60'), ('509', '2026-11-15', '106.12'),
    ('208', '2026-11-19', '72.48'),  ('305', '2026-11-19', '75.24'),
    ('713', '2026-11-19', '87.61'),  ('704', '2026-11-20', '64.07'),
    ('702', '2026-11-20', '95.55'),  ('710', '2026-11-20', '80.00'),
    ('510', '2026-11-20', '90.01'),
]
# Duplicado real a borrar: depto 302, S/108.56, 18-nov (hay 2, se deja 1).
DUP = {'depto': '302', 'monto': Decimal('108.56'), 'fecha': date(2026, 11, 18)}


class Command(BaseCommand):
    help = 'Agrega los pagos faltantes del Excel y borra el duplicado del depto 302.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true')

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        with transaction.atomic():
            total_antes = Pago.objects.aggregate(s=Sum('monto'))['s'] or Decimal('0')

            self.stdout.write('=== AGREGAR pagos faltantes ===')
            agregados = 0
            for num, f, mo in FALTAN:
                dep = Departamento.objects.filter(numero_domicilio=num).first()
                if not dep:
                    self.stdout.write(f'   {num}: depto no existe, se salta.'); continue
                fecha = date.fromisoformat(f)
                monto = Decimal(mo)
                if Pago.objects.filter(departamento=dep, fecha_pago=fecha, monto=monto).exists():
                    self.stdout.write(f'   {num}  {f}  S/ {mo}  (ya existe, se salta)'); continue
                pago = registrar_pago(dep, monto, metodo='Excel Hoja1', nota='Reconciliación Excel')
                Pago.objects.filter(pk=pago.pk).update(fecha_pago=fecha)
                agregados += 1
                self.stdout.write(f'   {num}  {f}  S/ {mo}  [AGREGADO]')

            self.stdout.write('\n=== BORRAR duplicado ===')
            dups = list(Pago.objects.filter(departamento__numero_domicilio=DUP['depto'],
                                            monto=DUP['monto'], fecha_pago=DUP['fecha']))
            borrados = 0
            if len(dups) > 1:
                for p in dups[1:]:
                    p.delete()   # cascada: borra sus imputaciones
                    borrados += 1
                self.stdout.write(f'   302 S/108.56 18-nov: había {len(dups)}, se borró {borrados} (queda 1).')
            else:
                self.stdout.write(f'   302 S/108.56 18-nov: hay {len(dups)}, nada que borrar.')

            total_desp = Pago.objects.aggregate(s=Sum('monto'))['s'] or Decimal('0')
            self.stdout.write(f'\nTotal pagos ANTES:   S/ {total_antes:,.2f}')
            self.stdout.write(f'Total pagos DESPUÉS: S/ {total_desp:,.2f}   (agregados {agregados}, borrados {borrados})')
            if not aplicar:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('\nENSAYO: nada se grabó. Usa --aplicar para grabar.'))
            else:
                self.stdout.write(self.style.SUCCESS('\nReconciliación aplicada.'))
