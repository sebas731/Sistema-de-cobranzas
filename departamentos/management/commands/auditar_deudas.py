# -*- coding: utf-8 -*-
"""Audita los cargos (deudas) buscando señales de DOBLE CARGA: por ejemplo haber
subido una base con el total de deuda (un 'lump') y además los detalles por
concepto del mismo mes. No modifica nada: solo lee y reporta.

    python manage.py auditar_deudas            # resumen por mes + alertas
    python manage.py auditar_deudas --depto 202  # detalle de un depto
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from departamentos.models import Cargo, Departamento, Imputacion

# Conceptos que serían un 'lump' (total amontonado), no una línea de recibo.
LUMP = {'DEUDA_ANT', 'MANT_ANT', 'SALDO_ANTERIOR', 'DEUDA'}


class Command(BaseCommand):
    help = 'Audita cargos buscando doble carga (lump + detalle en el mismo mes).'

    def add_arguments(self, parser):
        parser.add_argument('--depto', help='Ver el detalle de un depto puntual.')

    def handle(self, *args, **opts):
        if opts['depto']:
            return self._detalle_depto(opts['depto'])

        self.stdout.write('=== RESUMEN POR MES (emitido = suma de cargos) ===')
        por_mes = (Cargo.objects.values('periodo')
                   .annotate(n=Count('id'), s=Sum('monto')).order_by('periodo'))
        total = 0
        for r in por_mes:
            total += float(r['s'] or 0)
            self.stdout.write(f"  {r['periodo']}  {r['n']:4} cargos   S/ {r['s']:>11,.2f}")
        self.stdout.write(f"  {'TOTAL EMITIDO (histórico)':24}  S/ {total:>11,.2f}")

        pend = 0
        for c in Cargo.objects.all():
            imp = Imputacion.objects.filter(cargo=c).aggregate(s=Sum('monto'))['s'] or 0
            pend += float(c.monto) - float(imp)
        self.stdout.write(f"  {'DEUDA PENDIENTE total':24}  S/ {pend:>11,.2f}")

        self.stdout.write('\n=== ALERTAS DE POSIBLE DOBLE CARGA ===')
        alertas = 0
        # 1) ¿Algún cargo usa un concepto 'lump' (total amontonado)?
        lump_qs = Cargo.objects.filter(concepto__codigo__in=LUMP)
        if lump_qs.exists():
            alertas += 1
            self.stdout.write(self.style.WARNING(
                f'  [!] Hay {lump_qs.count()} cargos con concepto tipo "deuda total" '
                f'(lump): S/ {lump_qs.aggregate(s=Sum("monto"))["s"]:,.2f}. '
                'Si también cargaste el detalle por concepto, está DUPLICADO.'))
            for r in lump_qs.values('periodo').annotate(n=Count('id'), s=Sum('monto')).order_by('periodo'):
                self.stdout.write(f'      {r["periodo"]}  {r["n"]} cargos  S/ {r["s"]:,.2f}')

        # 2) Un mes donde un depto tiene un lump Y además detalles -> doble.
        detalle_meses = defaultdict(set)   # (depto, periodo) -> set de codigos
        for c in Cargo.objects.select_related('departamento', 'concepto'):
            detalle_meses[(c.departamento_id, c.periodo)].add(c.concepto.codigo)
        for (dep, per), cods in detalle_meses.items():
            if cods & LUMP and len(cods) > 1:
                alertas += 1
                d = Departamento.objects.get(pk=dep)
                self.stdout.write(self.style.WARNING(
                    f'  [!] Depto {d.numero_domicilio} en {per}: tiene lump + detalle '
                    f'({", ".join(sorted(cods))}) -> revisar.'))

        if not alertas:
            self.stdout.write(self.style.SUCCESS(
                '  [OK] Sin señales de doble carga: no hay conceptos "lump" ni meses con '
                'total + detalle encimados.'))

    def _detalle_depto(self, numero):
        d = Departamento.objects.filter(numero_domicilio=numero).first()
        if not d:
            self.stdout.write(self.style.ERROR(f'No existe el depto {numero}.'))
            return
        self.stdout.write(f'=== DEPTO {numero}: todos sus cargos ===')
        tot = pend = 0
        for c in Cargo.objects.filter(departamento=d).select_related('concepto').order_by('periodo', 'concepto__codigo'):
            imp = Imputacion.objects.filter(cargo=c).aggregate(s=Sum('monto'))['s'] or 0
            debe = float(c.monto) - float(imp)
            tot += float(c.monto); pend += debe
            self.stdout.write(f'   {c.periodo}  {c.concepto.codigo:14} '
                              f'emitido {c.monto:>9.2f}  debe {debe:>9.2f}')
        self.stdout.write(f'   {"TOTAL":16}  emitido {tot:>9.2f}  debe {pend:>9.2f}')
