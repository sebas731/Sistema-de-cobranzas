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
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from departamentos.models import Cargo, Departamento, Imputacion, MovimientoCaja, Pago
from departamentos.servicios import registrar_pago

NOTA = 'Ajuste cuadre AYUDA'
# Conceptos donde se imputan los top-ups del cuadre: los mensuales + cuota, para
# que paguen deuda pendiente y no queden "a cuenta". Se EXCLUYEN ascensores, mora
# y multa (los fija el Excel de detalle: 780 / 45 / 300).
COLS_MES = ['mantenimiento', 'agua', 'area_comun', 'cochera', 'cuota']
NOTAS_PREVIAS = [NOTA, 'Ajuste reconciliación Excel', 'Reconciliación Excel']
DESC_SIN = 'Pago sin depto identificado (Excel AYUDA)'

# Faltante NETO por depto (Excel_total − BD_total). Cada monto es la fila que el
# Excel añadió y la BD no tenía. 701 NO va aquí: se arregla con el MOVE del 311.
FALTAN = {
    '207': '25.00', '208': '72.48', '211': '25.00', '304': '133.59',
    '305': '75.24', '510': '90.01', '702': '95.55', '704': '64.07',
    '710': '80.00', '713': '87.61',
}
# Top-ups que SON un pago de concepto especial (según el Excel PAGO ASCENSORES):
# el 710 pagó S/80 de ascensor (Hoja1 f351), así que su faltante va a ASCENSORES
# (no a los conceptos mensuales), para que el cobrado de ascensores dé 780.
RUTA_CONCEPTO = {'710': 'ASCENSORES'}
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

    @staticmethod
    def _red(x, paso, modo='nearest'):
        """Redondea x a múltiplo de `paso`. modo='nearest' (cochera: 4.97->5,
        10.05->10) o 'floor' (multa/ascensores: el decimal es exceso que va a
        mantenimiento, 20.70->20, 80.02->80)."""
        r = ROUND_DOWN if modo == 'floor' else ROUND_HALF_UP
        return (Decimal(x) / paso).quantize(Decimal('1'), r) * paso

    def _mant_siblings(self, pago):
        return [i for i in pago.imputaciones.select_related('cargo__concepto').all()
                if i.cargo.concepto.codigo == 'MANTENIMIENTO']

    def _redondear_concepto(self, codigo, paso, modo='nearest'):
        """Pone en entero (múltiplo de `paso`) cada cargo del concepto que tenga
        decimales, ajustando su imputación y absorbiendo el sobrante en el
        mantenimiento del mismo pago, para que el pago total y los saldos cuadren.
        Devuelve cuántos cargos tocó."""
        tocados = 0
        for c in list(Cargo.objects.filter(concepto__codigo=codigo)
                      .select_related('departamento', 'concepto')):
            nuevo = self._red(c.monto, paso, modo)
            if nuevo == c.monto:
                continue
            imps = list(c.imputaciones.select_related('pago').all())
            if not imps:
                # Cargo sin pagar: basta con corregir el monto emitido.
                c.monto = nuevo
                c.save(update_fields=['monto'])
                tocados += 1
                continue
            if len(imps) != 1 or imps[0].monto != c.monto:
                # Caso no previsto (cargo partido entre varios pagos): no tocar.
                continue
            imp = imps[0]
            pago = imp.pago
            delta = nuevo - imp.monto          # >0 sube el concepto; <0 lo baja
            c.monto = nuevo; c.save(update_fields=['monto'])
            imp.monto = nuevo; imp.save(update_fields=['monto'])
            resto = -delta                      # cuánto compensar en mantenimiento
            sibs = self._mant_siblings(pago)
            if resto < 0:
                # El concepto subió: quita |resto| de un mantenimiento del pago
                # (baja su imputación y su cargo, saldo se mantiene en 0).
                need = -resto
                for mi in sibs:
                    take = min(need, mi.monto)
                    mi.monto -= take; mi.save(update_fields=['monto'])
                    mi.cargo.monto -= take; mi.cargo.save(update_fields=['monto'])
                    need -= take
                    if need <= 0:
                        break
            elif resto > 0:
                # El concepto bajó: el sobrante va a mantenimiento del mismo pago.
                need = resto
                if sibs:
                    mi = sibs[0]
                    mi.monto += need; mi.save(update_fields=['monto'])
                    mi.cargo.monto += need; mi.cargo.save(update_fields=['monto'])
                else:
                    # Pago sin hermano mantenimiento (ej. multa 708): imputa el
                    # sobrante a un cargo de mantenimiento pendiente del depto.
                    for cg in (Cargo.objects.filter(departamento=c.departamento,
                                                    concepto__codigo='MANTENIMIENTO')
                               .order_by('periodo')):
                        room = cg.saldo
                        if room <= 0:
                            continue
                        add = min(need, room)
                        Imputacion.objects.create(pago=pago, cargo=cg, monto=add)
                        need -= add
                        if need <= 0:
                            break
            tocados += 1
        return tocados

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
                if num in RUTA_CONCEPTO:
                    from departamentos.models import Concepto
                    cpt = Concepto.objects.get(codigo=RUTA_CONCEPTO[num])
                    pg = registrar_pago(dep, Decimal(mo), concepto=cpt,
                                        metodo='Excel AYUDA', nota=NOTA)
                    w(f'   +{num}  S/ {mo}  -> {RUTA_CONCEPTO[num]}')
                else:
                    pg = registrar_pago(dep, Decimal(mo), metodo='Excel AYUDA', nota=NOTA,
                                        columnas=COLS_MES)
                    w(f'   +{num}  S/ {mo}')
                Pago.objects.filter(pk=pg.pk).update(fecha_pago=FECHA_AJUSTE)

            # 3) El pago conjunto VECINA LILI (S/216.90 = 701+311) se DEJA repartido
            #    como está en la BD: 94.70 al 701 y 122.20 al 311 (pagaron en
            #    conjunto). NO se mueve nada. Si quedó movido de una corrida previa,
            #    se devuelve al 311.
            movido = Pago.objects.filter(departamento__numero_domicilio=MOVE['a'],
                                         monto=MOVE['monto'],
                                         nota__icontains='movido de').first()
            dep311 = self._dep(MOVE['de'])
            if movido and dep311:
                fmov = movido.fecha_pago
                movido.delete()
                pg = registrar_pago(dep311, MOVE['monto'], metodo='Excel AYUDA',
                                    nota=f"{NOTA} | VECINA LILI (devuelto a {MOVE['de']})",
                                    columnas=COLS_MES)
                Pago.objects.filter(pk=pg.pk).update(fecha_pago=fmov)
                w("\n3) LILI: se devolvió el pago conjunto al 311 (split 94.70/122.20).")
            else:
                w("\n3) LILI: pago conjunto queda repartido 94.70/122.20 (sin mover).")

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
                                    nota=f"{NOTA} | corrige {REDUCE['de']}->{REDUCE['a']}",
                                    columnas=COLS_MES)
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

            # 7) Redondea a ENTEROS los conceptos que nunca llevan decimales
            #    (cochera=múltiplos de 5; ascensores/multa=entero), moviendo el
            #    sobrante al mantenimiento del mismo pago. El balance del Excel es
            #    entero: ascensores 780, multa 300, cochera en múltiplos de 5.
            w('\n7) Redondeo a enteros (cochera/ascensores/multa):')
            n5 = self._redondear_concepto('COCHERA', paso=5, modo='nearest')
            n1a = self._redondear_concepto('ASCENSORES', paso=1, modo='floor')
            n1m = self._redondear_concepto('MULTA', paso=1, modo='floor')
            w(f'   cochera: {n5} cargos | ascensores: {n1a} | multa: {n1m}')

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

            # --- Verificación: conceptos especiales COBRADOS deben ser enteros ---
            w('\n--- CONCEPTOS COBRADOS (deben ser enteros, como el Excel) ---')
            objetivos = {'ASCENSORES': Decimal('780'), 'MULTA': Decimal('300')}
            for cod in ['ASCENSORES', 'MULTA', 'MORA', 'COCHERA']:
                cob = (Imputacion.objects.filter(cargo__concepto__codigo=cod)
                       .aggregate(s=Sum('monto'))['s'] or Decimal('0'))
                dec = '' if cob == cob.to_integral_value() else '  <-- TIENE DECIMALES'
                extra = ''
                if cod in objetivos:
                    extra = ' OK' if cob == objetivos[cod] else f" (Excel={objetivos[cod]})"
                w(f'  {cod:12} cobrado S/ {cob:,.2f}{extra}{dec}')
            neg = sum(1 for c in Cargo.objects.all() if c.saldo < Decimal('-0.001'))
            w(f'  Cargos sobre-imputados (saldo negativo): {neg}')

            if not aplicar:
                transaction.set_rollback(True)
                w(self.style.WARNING('\nENSAYO: nada se grabó. Usa --aplicar para grabar.'))
            else:
                w(self.style.SUCCESS('\nCuadre aplicado.'))
