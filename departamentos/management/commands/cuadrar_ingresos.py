# -*- coding: utf-8 -*-
"""Cuadra los ingresos de la BD contra el Excel 'AYUDA PARA CLAUDE.xlsx'
(lista limpia DEPARTAMENTO|PAGO cuyo total propio es S/ 43,224.27, incluida la
caja inicial de Juan Cabrera 2,762.71).

Se compara el TOTAL POR DEPARTAMENTO (no pago por pago), porque en la BD varios
pagos están partidos. Operaciones (todas idempotentes):

  1) Borra ajustes previos de reconciliación (notas conocidas) para partir limpio.
  2) AGREGA el faltante neto por depto (filas que el Excel añadió y la BD no tiene).
  3) MUEVE el pago de S/122.20 "VECINA LILI" del 311 al 701 (el Excel lo asigna
     al 701): deja 311=526.20 y 701=552.00, ambos exactos.
  4) BORRA el duplicado real del 302 (S/108.56 repetido).
  5) REDUCE el pago del 509 de 126.74 a 106.12 (el Excel registra 106.12).
  6) REGISTRA los 2 pagos del Excel que no tienen depto (129.74 y 110.00) como
     movimientos POR IDENTIFICAR, para que el total cuadre.

Las diferencias de céntimos en pagos partidos se DEJAN (instrucción del usuario),
por eso el gran total puede quedar en 43,224.30 (+0.03). Se informa al final.

    python manage.py cuadrar_ingresos            # ensayo (no graba)
    python manage.py cuadrar_ingresos --aplicar   # graba
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from departamentos.models import Departamento, MovimientoCaja, Pago
from departamentos.servicios import registrar_pago

NOTA = 'Ajuste cuadre AYUDA'
NOTAS_PREVIAS = [NOTA, 'Ajuste reconciliación Excel', 'Reconciliación Excel']
DESC_SIN = 'Pago sin depto identificado (Excel AYUDA)'

# Faltante NETO por depto (Excel_total − BD_total). Cada monto es la fila que el
# Excel añadió y la BD no tenía. 701 NO va aquí: se arregla con el MOVE del 311.
FALTAN = {
    '207': '25.00', '208': '72.48', '211': '25.00', '304': '133.59',
    '305': '75.24', '510': '90.01', '702': '95.55', '704': '64.07',
    '710': '80.00', '713': '87.61',
}
FECHA_AJUSTE = date(2026, 11, 20)

MOVE = {'de': '311', 'a': '701', 'monto': Decimal('122.20'), 'pista': 'LILI'}
DUP = {'depto': '302', 'monto': Decimal('108.56')}
REDUCE = {'depto': '509', 'de': Decimal('126.74'), 'a': Decimal('106.12')}
SIN_DEPTO = [Decimal('129.74'), Decimal('110.00')]

OBJETIVO = Decimal('43224.27')


class Command(BaseCommand):
    help = "Cuadra ingresos contra el Excel AYUDA (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true')

    def _dep(self, num):
        return Departamento.objects.filter(numero_domicilio=num).first()

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        w = self.stdout.write
        with transaction.atomic():
            t0 = Pago.objects.aggregate(s=Sum('monto'))['s'] or Decimal('0')

            # 1) Limpia ajustes previos (pagos y movimientos de reconciliación).
            n = Pago.objects.filter(nota__in=NOTAS_PREVIAS).count()
            Pago.objects.filter(nota__in=NOTAS_PREVIAS).delete()
            MovimientoCaja.objects.filter(descripcion__startswith=DESC_SIN).delete()
            w(f'1) Ajustes previos borrados: {n} pagos.')

            # 2) Agrega faltante por depto.
            w('\n2) AGREGAR faltante por depto:')
            for num, mo in sorted(FALTAN.items()):
                dep = self._dep(num)
                if not dep:
                    w(f'   {num}: NO existe, se salta.'); continue
                pg = registrar_pago(dep, Decimal(mo), metodo='Excel AYUDA', nota=NOTA)
                Pago.objects.filter(pk=pg.pk).update(fecha_pago=FECHA_AJUSTE)
                w(f'   +{num}  S/ {mo}')

            # 3) Mueve el pago LILI 122.20 del 311 al 701 (borra y recrea, para
            #    que las imputaciones queden contra los cargos del 701).
            dep701 = self._dep(MOVE['a'])
            mov = Pago.objects.filter(departamento__numero_domicilio=MOVE['de'],
                                      monto=MOVE['monto'],
                                      nota__icontains=MOVE['pista']).first()
            if mov and dep701:
                f = mov.fecha_pago
                mov.delete()  # cascada: libera los cargos del 311
                pg = registrar_pago(dep701, MOVE['monto'], metodo='Excel AYUDA',
                                    nota=f"{NOTA} | VECINA LILI (movido de {MOVE['de']})")
                Pago.objects.filter(pk=pg.pk).update(fecha_pago=f)
                w(f"\n3) MOVER S/{MOVE['monto']} LILI: {MOVE['de']} -> {MOVE['a']} (ok).")
            else:
                w('\n3) MOVER LILI: nada que mover (ya estaba movido).')

            # 4) Borra duplicado 302 (cascada libera cargos).
            dups = list(Pago.objects.filter(departamento__numero_domicilio=DUP['depto'],
                                            monto=DUP['monto']).order_by('id'))
            borr = 0
            for p in dups[1:]:
                p.delete(); borr += 1
            w(f"4) DUPLICADO {DUP['depto']} S/{DUP['monto']}: borrados {borr} (queda {min(len(dups),1)}).")

            # 5) Reduce pago del 509 (borra y recrea al monto correcto).
            red = Pago.objects.filter(departamento__numero_domicilio=REDUCE['depto'],
                                      monto=REDUCE['de']).first()
            if red:
                dep509 = red.departamento
                f = red.fecha_pago
                red.delete()
                pg = registrar_pago(dep509, REDUCE['a'], metodo='Excel AYUDA',
                                    nota=f"{NOTA} | corrige {REDUCE['de']}->{REDUCE['a']}")
                Pago.objects.filter(pk=pg.pk).update(fecha_pago=f)
                w(f"5) REDUCIR {REDUCE['depto']}: {REDUCE['de']} -> {REDUCE['a']} (ok).")
            else:
                w(f"5) REDUCIR {REDUCE['depto']}: ya estaba en {REDUCE['a']} (sin cambio).")

            # 6) Pagos sin depto como POR IDENTIFICAR.
            w('\n6) Pagos sin depto (POR IDENTIFICAR):')
            for mo in SIN_DEPTO:
                MovimientoCaja.objects.create(
                    fecha=FECHA_AJUSTE, tipo=MovimientoCaja.Tipo.POR_IDENTIFICAR,
                    descripcion=f'{DESC_SIN} S/ {mo}', monto=mo)
                w(f'   +sin depto  S/ {mo}')

            # --- Totales ---
            pag = Pago.objects.aggregate(s=Sum('monto'))['s'] or Decimal('0')
            caja = (MovimientoCaja.objects.filter(tipo=MovimientoCaja.Tipo.SALDO_INICIAL)
                    .aggregate(s=Sum('monto'))['s'] or Decimal('0'))
            ident = (MovimientoCaja.objects.filter(tipo=MovimientoCaja.Tipo.POR_IDENTIFICAR)
                     .aggregate(s=Sum('monto'))['s'] or Decimal('0'))
            otros = (MovimientoCaja.objects.filter(tipo=MovimientoCaja.Tipo.INGRESO_OTRO)
                     .aggregate(s=Sum('monto'))['s'] or Decimal('0'))
            total = pag + caja + ident + otros
            w(f'\n--- TOTAL INGRESOS ---')
            w(f'  Pagos de deptos : S/ {pag:,.2f}')
            w(f'  Caja inicial    : S/ {caja:,.2f}')
            w(f'  Por identificar : S/ {ident:,.2f}')
            w(f'  Otros ingresos  : S/ {otros:,.2f}')
            w(f'  TOTAL           : S/ {total:,.2f}')
            w(f'  OBJETIVO        : S/ {OBJETIVO:,.2f}')
            w(f'  DIFERENCIA      : S/ {total - OBJETIVO:,.2f}')
            w(f'\n  (Pagos antes: S/ {t0:,.2f})')

            if not aplicar:
                transaction.set_rollback(True)
                w(self.style.WARNING('\nENSAYO: nada se grabó. Usa --aplicar para grabar.'))
            else:
                w(self.style.SUCCESS('\nCuadre aplicado.'))
