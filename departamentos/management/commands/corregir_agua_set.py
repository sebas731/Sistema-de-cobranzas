# -*- coding: utf-8 -*-
"""Corrige el cobro de agua de SETIEMBRE 2026 mal calculado en 4 deptos (se aplicó
tarifa del tramo 20-50 en consumos menores a 20 m³). Baja el cargo de agua al
monto correcto y RE-IMPUTA el pago ya hecho: el sobrepago fluye a la deuda más
antigua que siga pendiente (p. ej. octubre) y, si no debe nada, queda como saldo
a favor del depto. Idempotente (si ya está corregido, lo salta).

    python manage.py corregir_agua_set            # ENSAYO (no graba)
    python manage.py corregir_agua_set --aplicar   # graba
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from departamentos.models import Cargo, Imputacion
from departamentos.servicios.cuenta import cargos_pendientes

SET = date(2026, 9, 1)
CERO = Decimal('0.00')
CORRECTO = {
    '208': Decimal('78.21'),
    '304': Decimal('77.50'),
    '406': Decimal('78.05'),
    '709': Decimal('78.36'),
}


class Command(BaseCommand):
    help = 'Corrige el agua de setiembre mal calculada de los deptos 208, 304, 406, 709.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Graba los cambios (sin esto es ensayo).')

    def _reimputar(self, dep, pago, monto, excluir_pk):
        """Aplica `monto` del `pago` a las deudas más antiguas del depto (sin tocar
        el cargo `excluir_pk`). Lo que no alcanza a imputarse queda como saldo a
        favor del pago. Devuelve lo re-aplicado."""
        aplicado = CERO
        for c in cargos_pendientes(dep):
            if monto <= 0:
                break
            if c.pk == excluir_pk:
                continue
            ap = min(monto, c.saldo)
            if ap <= 0:
                continue
            Imputacion.objects.create(pago=pago, cargo=c, monto=ap)
            monto -= ap
            aplicado += ap
        return aplicado

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        with transaction.atomic():
            for num, correcto in CORRECTO.items():
                cargo = (Cargo.objects.filter(departamento__numero_domicilio=num,
                                              periodo=SET, concepto__codigo='AGUA').first())
                if not cargo:
                    self.stdout.write(f'  {num}: SIN cargo de agua de setiembre (nada que hacer).')
                    continue
                if cargo.monto == correcto:
                    self.stdout.write(f'  {num}: ya estaba corregido ({correcto}).')
                    continue

                dep = cargo.departamento
                imp_total = Imputacion.objects.filter(cargo=cargo).aggregate(s=Sum('monto'))['s'] or CERO
                viejo = cargo.monto
                cargo.monto = correcto
                cargo.save(update_fields=['monto'])

                reubicado = favor = CERO
                sobra = imp_total - correcto   # exceso que se había imputado de más
                if sobra > 0:
                    # Libera el exceso de las imputaciones de este cargo (de la más nueva
                    # a la más vieja) y lo re-aplica a la deuda pendiente del depto.
                    por_liberar = sobra
                    for i in Imputacion.objects.filter(cargo=cargo).order_by('-id'):
                        if por_liberar <= 0:
                            break
                        quita = min(i.monto, por_liberar)
                        pago = i.pago
                        if i.monto - quita <= 0:
                            i.delete()
                        else:
                            i.monto -= quita
                            i.save(update_fields=['monto'])
                        por_liberar -= quita
                        ap = self._reimputar(dep, pago, quita, cargo.pk)
                        reubicado += ap
                        favor += (quita - ap)   # lo que no tenía dónde ir = saldo a favor

                self.stdout.write(
                    f'  {num}: agua {viejo} -> {correcto}  | sobrepago {sobra:.2f}  '
                    f'-> a deuda {reubicado:.2f}  | saldo a favor {favor:.2f}')

            if not aplicar:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('\nENSAYO: nada se grabó. Corre con --aplicar para grabar.'))
            else:
                self.stdout.write(self.style.SUCCESS('\nCorrección aplicada.'))
