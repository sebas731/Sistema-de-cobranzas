# -*- coding: utf-8 -*-
"""Carga los gastos del mes (egresos reales del condominio) de JULIO y AGOSTO 2026
como GastoEdificio, replicando la estructura con que se cargó septiembre.

Los montos salen de la columna TORRE "D" de los Excel de mantenimiento:
  - SEDAPAL = consumo de agua por depto + consumo de agua de área común.
  - PLUZ    = energía eléctrica ENEL (área común).

Es idempotente (update_or_create por periodo + descripción): se puede correr las
veces que sea sin duplicar. Por defecto hace un ENSAYO; usa --aplicar para grabar.

    python manage.py cargar_egresos_mes            # ensayo
    python manage.py cargar_egresos_mes --aplicar  # graba
"""
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from departamentos.models import GastoEdificio
from departamentos.models.gastos import CategoriaServicio as TS

T, C = TS.TORRE, TS.CONDOMINIO

# (descripción, monto, tipo_servicio). Todos categoría GENERAL, compartido=True,
# aplicados a TODOS los departamentos (sin lista personalizada).
DATOS = {
    date(2026, 7, 1): [
        ('SEDAPAL', '4487.53', C),   # 3855.90 (por depto) + 631.63 (área común)
        ('VIGILANCIA', '3097.93', C),
        ('PLUZ', '1084.36', C),
        ('LIMPIEZA', '1200.00', T),
        ('GASTOS ADMINISTRATIVOS / 89 DPTOS.', '380.00', T),
        ('ASCENSORES', '200.00', T),
        ('MATERIALES DE LIMPIEZA / 89 DPTOS.', '100.00', T),
        ('ACOPIO', '26.25', C),
        ('AGUA VIGILANCIA', '9.40', C),
        ('ENERGÍA ELÉCTRICA (BOMBA CONTRA INCENDIO/89 DPTOS)', '3.28', C),
        ('ENERGÍA ELÉCTRICA (BOMBA SUMERGIBLE / 89 DPTOS)', '42.92', C),
    ],
    date(2026, 8, 1): [
        ('SEDAPAL', '3636.50', C),   # 3120.30 (por depto) + 516.20 (área común)
        ('VIGILANCIA', '3097.93', C),
        ('PLUZ', '1091.64', C),
        ('LIMPIEZA', '1200.00', T),
        ('GASTOS ADMINISTRATIVOS / 89 DPTOS.', '380.00', T),
        ('ASCENSORES', '200.00', T),
        ('MATERIALES DE LIMPIEZA / 89 DPTOS.', '100.00', T),
        ('ACOPIO', '26.25', C),
        ('AGUA VIGILANCIA', '9.60', C),
        ('ENERGÍA ELÉCTRICA (BOMBA CONTRA INCENDIO/89 DPTOS)', '3.28', C),
        ('ENERGÍA ELÉCTRICA (BOMBA SUMERGIBLE / 89 DPTOS)', '47.78', C),
    ],
}


class Command(BaseCommand):
    help = 'Carga los gastos del mes (egresos) de julio y agosto 2026.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Graba en la base (sin esto solo hace un ensayo).')

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        creados = actualizados = 0
        total_general = 0.0

        with transaction.atomic():
            for periodo, lineas in DATOS.items():
                sub = 0.0
                self.stdout.write(f'\n== {periodo:%B %Y} ==')
                for desc, monto, tipo in lineas:
                    obj, creado = GastoEdificio.objects.update_or_create(
                        periodo=periodo, descripcion=desc,
                        defaults={
                            'monto': monto,
                            'categoria': GastoEdificio.Categoria.GENERAL,
                            'tipo_servicio': tipo,
                            'compartido': True,
                        })
                    sub += float(monto)
                    creados += creado
                    actualizados += (not creado)
                    marca = 'NUEVO' if creado else 'actualiza'
                    self.stdout.write(f'   [{marca:9}] {desc[:48]:48} S/ {monto}')
                self.stdout.write(f'   {"TOTAL EGRESO " + periodo.strftime("%B"):48} S/ {sub:,.2f}')
                total_general += sub

            self.stdout.write(f'\nTotal egresos cargados: S/ {total_general:,.2f}')
            self.stdout.write(f'Líneas nuevas: {creados}   actualizadas: {actualizados}')
            if not aplicar:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('\nENSAYO: no se grabó nada. Corre con --aplicar para grabar.'))
            else:
                self.stdout.write(self.style.SUCCESS('\nGrabado correctamente.'))
