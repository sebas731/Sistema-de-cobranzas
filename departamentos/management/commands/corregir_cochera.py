# -*- coding: utf-8 -*-
"""Corrige los cargos de COCHERA: la cochera es un cobro FIJO de S/5 por cochera
(no se prorratea). Deja cada cargo en el múltiplo de 5 más cercano (5 por cochera,
10 si el depto tiene 2), así el total mensual queda en 60 exacto, sin decimales.
Idempotente. Por defecto ENSAYO; usa --aplicar para grabar.

    python manage.py corregir_cochera            # ensayo
    python manage.py corregir_cochera --aplicar   # graba
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from departamentos.models import Cargo

CINCO = Decimal('5')


class Command(BaseCommand):
    help = 'Fija los cargos de cochera en múltiplos de S/5 (sin decimales de prorrateo).'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true')

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        cambios = 0
        with transaction.atomic():
            for c in Cargo.objects.filter(concepto__codigo='COCHERA').select_related('departamento'):
                nuevo = CINCO * (c.monto / CINCO).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                if nuevo <= 0:
                    nuevo = CINCO
                if c.monto != nuevo:
                    self.stdout.write(f'   {c.periodo}  depto {c.departamento.numero_domicilio:4}  '
                                      f'{c.monto} -> {nuevo}')
                    c.monto = nuevo
                    c.save(update_fields=['monto'])
                    cambios += 1

            self.stdout.write('\n=== Total cochera por mes (debe ser 60.00) ===')
            for r in (Cargo.objects.filter(concepto__codigo='COCHERA').values('periodo')
                      .annotate(s=Sum('monto')).order_by('periodo')):
                self.stdout.write(f"   {r['periodo']}  S/ {r['s']:.2f}")

            self.stdout.write(f'\nCargos ajustados: {cambios}')
            if not aplicar:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('ENSAYO: nada se grabó. Usa --aplicar para grabar.'))
            else:
                self.stdout.write(self.style.SUCCESS('Aplicado.'))
