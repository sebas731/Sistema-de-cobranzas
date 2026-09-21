"""Genera los recibos mensuales de todos los departamentos activos.

Uso:
    python manage.py generar_recibos 2026-07
    python manage.py generar_recibos 2026-07 --vencimiento 5
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from departamentos.servicios import generar_cargos_mes


class Command(BaseCommand):
    help = 'Genera los recibos del periodo (YYYY-MM) para los departamentos activos.'

    def add_arguments(self, parser):
        parser.add_argument('periodo', help='Periodo a facturar, formato YYYY-MM.')
        parser.add_argument('--vencimiento', type=int, default=5,
                            help='Día del mes siguiente en que vencen (default 5).')

    def handle(self, *args, **opts):
        try:
            anio, mes = map(int, opts['periodo'].split('-'))
            periodo = date(anio, mes, 1)
        except (ValueError, TypeError):
            raise CommandError('Periodo inválido. Usa YYYY-MM, ej: 2026-07.')

        resumen = generar_cargos_mes(periodo, dia_vencimiento=opts['vencimiento'])
        self.stdout.write(self.style.SUCCESS(
            f"Periodo {periodo:%Y-%m} ({resumen['n']} deptos): "
            f"{resumen['creados']} creados, {resumen['actualizados']} actualizados, "
            f"{resumen['omitidos_pagados']} ya pagados."))
