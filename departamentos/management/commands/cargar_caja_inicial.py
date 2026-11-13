"""Registra la CAJA INICIAL (saldo de apertura con el que arrancó la
administración) como MovimientoCaja tipo SALDO_INICIAL. Idempotente.

Por defecto carga la caja de Fiorella (Juan Cabrera, 30/07/2026 = 2762.71).
Uso:
  python manage.py cargar_caja_inicial                 # valores por defecto
  python manage.py cargar_caja_inicial --monto 2762.71 --fecha 2026-07-30
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from departamentos.models import MovimientoCaja


class Command(BaseCommand):
    help = 'Carga la caja inicial (SALDO_INICIAL). Idempotente.'

    def add_arguments(self, p):
        p.add_argument('--monto', default='2762.71', help='Monto de la caja inicial (def. 2762.71)')
        p.add_argument('--fecha', default='2026-07-30', help='Fecha ISO (def. 2026-07-30)')
        p.add_argument('--desc', default='Caja administración anterior (Juan Cabrera)',
                       help='Descripción')

    def handle(self, *a, **o):
        fecha = date.fromisoformat(o['fecha'])
        monto = Decimal(str(o['monto'])).quantize(Decimal('0.01'))
        mov, creado = MovimientoCaja.objects.get_or_create(
            fecha=fecha, tipo=MovimientoCaja.Tipo.SALDO_INICIAL,
            descripcion=o['desc'], monto=monto,
            defaults={'estado': MovimientoCaja.Estado.VIGENTE})
        if creado:
            self.stdout.write(self.style.SUCCESS(f'Caja inicial creada: S/ {monto} al {fecha}.'))
        else:
            self.stdout.write(f'Ya existía (no se duplicó): S/ {monto} al {fecha}.')
