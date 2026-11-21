# -*- coding: utf-8 -*-
"""Reconcilia los pagos de vecinos con el Excel Hoja 1, comparando el TOTAL POR
DEPARTAMENTO (no pago por pago, porque la BD guarda algunos pagos partidos).

1) Borra cualquier ajuste previo de esta misma reconciliación (idempotente).
2) Agrega el faltante neto de los 9 deptos cuyo total en la BD es menor al Excel.
3) Borra el único duplicado real (depto 302, S/108.56 dos veces el 18-nov).

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

NOTA = 'Ajuste reconciliación Excel'

# Faltante NETO por depto (Excel_total − BD_total), con fecha de referencia.
FALTAN = [
    ('207', '2026-11-20', '25.00'),  ('208', '2026-11-19', '72.48'),
    ('211', '2026-11-20', '25.00'),  ('304', '2026-11-12', '133.59'),
    ('305', '2026-11-19', '75.24'),  ('702', '2026-11-20', '95.55'),
    ('704', '2026-11-20', '64.07'),  ('710', '2026-11-20', '80.00'),
    ('713', '2026-11-19', '87.61'),
]
DUP = {'depto': '302', 'monto': Decimal('108.56'), 'fecha': date(2026, 11, 18)}


class Command(BaseCommand):
    help = 'Reconcilia pagos con el Excel por total de depto (idempotente).'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true')

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        with transaction.atomic():
            total_antes = Pago.objects.aggregate(s=Sum('monto'))['s'] or Decimal('0')

            # 1) Deshace ajustes previos de esta reconciliación (incluye el intento anterior).
            prev = Pago.objects.filter(nota__in=[NOTA, 'Reconciliación Excel'])
            n_prev = prev.count()
            prev.delete()
            self.stdout.write(f'Ajustes previos borrados: {n_prev}')

            # 2) Agrega el faltante neto por depto.
            self.stdout.write('\n=== AGREGAR faltante por depto ===')
            agregados = 0
            for num, f, mo in FALTAN:
                dep = Departamento.objects.filter(numero_domicilio=num).first()
                if not dep:
                    self.stdout.write(f'   {num}: no existe, se salta.'); continue
                pago = registrar_pago(dep, Decimal(mo), metodo='Excel Hoja1', nota=NOTA)
                Pago.objects.filter(pk=pago.pk).update(fecha_pago=date.fromisoformat(f))
                agregados += 1
                self.stdout.write(f'   {num}  {f}  S/ {mo}')

            # 3) Borra el duplicado del 302.
            dups = list(Pago.objects.filter(departamento__numero_domicilio=DUP['depto'],
                                            monto=DUP['monto'], fecha_pago=DUP['fecha']))
            borrados = 0
            if len(dups) > 1:
                for p in dups[1:]:
                    p.delete(); borrados += 1
            self.stdout.write(f'\nDuplicado 302 S/108.56: borrados {borrados} (quedan {len(dups) - borrados}).')

            total_desp = Pago.objects.aggregate(s=Sum('monto'))['s'] or Decimal('0')
            self.stdout.write(f'\nTotal pagos ANTES:   S/ {total_antes:,.2f}')
            self.stdout.write(f'Total pagos DESPUÉS: S/ {total_desp:,.2f}')
            if not aplicar:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('\nENSAYO: nada se grabó. Usa --aplicar para grabar.'))
            else:
                self.stdout.write(self.style.SUCCESS('\nReconciliación aplicada.'))
