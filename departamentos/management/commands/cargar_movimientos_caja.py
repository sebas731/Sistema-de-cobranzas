"""Carga movimientos de caja desde un CSV (saldo inicial, otros ingresos,
depósitos por identificar y egresos pagados).

Uso:  python manage.py cargar_movimientos_caja <ruta_csv> [--aplicar]

SIN --aplicar es un ENSAYO (no guarda nada, solo muestra el resumen).
Es IDEMPOTENTE: la clave es fecha+tipo+descripcion+monto, así que correrlo dos
veces no duplica. NUNCA toca Cargo, Pago ni Imputacion.

Columnas del CSV (cabecera):
  fecha        ISO (2026-07-30)        [obligatoria]
  tipo         SALDO_INICIAL | INGRESO_OTRO | POR_IDENTIFICAR | EGRESO  [obligatoria]
  monto        número > 0              [obligatoria]
  descripcion  texto                   [obligatoria]
  rubro        código de rubro (solo egresos, opcional)
  nota         texto (opcional)
  comprobante  N° operación / referencia (opcional)
"""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from departamentos.models import MovimientoCaja

TIPOS = {t.value for t in MovimientoCaja.Tipo}
RUBROS = {r.value for r in MovimientoCaja.Rubro}


class Ensayo(Exception):
    pass


class Command(BaseCommand):
    help = 'Carga movimientos de caja desde un CSV (idempotente). Sin --aplicar es ensayo.'

    def add_arguments(self, p):
        p.add_argument('csv', help='Ruta del movimientos_caja.csv')
        p.add_argument('--aplicar', action='store_true',
                       help='Confirma los cambios (sin esto es ensayo, no guarda).')

    def _fila(self, i, r):
        """Valida y normaliza una fila; devuelve dict listo para crear."""
        try:
            fecha = date.fromisoformat((r.get('fecha') or '').strip())
        except ValueError:
            raise CommandError(f'Fila {i}: fecha inválida {r.get("fecha")!r} (usa 2026-07-30).')
        tipo = (r.get('tipo') or '').strip().upper()
        if tipo not in TIPOS:
            raise CommandError(f'Fila {i}: tipo {tipo!r} inválido. Usa: {", ".join(sorted(TIPOS))}.')
        try:
            monto = Decimal(str(r.get('monto', '')).replace(',', '').strip()).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            raise CommandError(f'Fila {i}: monto inválido {r.get("monto")!r}.')
        if monto <= 0:
            raise CommandError(f'Fila {i}: el monto debe ser > 0 (es {monto}).')
        desc = (r.get('descripcion') or '').strip()
        if not desc:
            raise CommandError(f'Fila {i}: falta descripcion.')
        rubro = (r.get('rubro') or '').strip().upper()
        if rubro and rubro not in RUBROS:
            raise CommandError(f'Fila {i}: rubro {rubro!r} inválido. Usa: {", ".join(sorted(RUBROS))}.')
        return {'fecha': fecha, 'tipo': tipo, 'monto': monto, 'descripcion': desc[:200],
                'rubro': rubro, 'nota': (r.get('nota') or '').strip()[:255],
                'comprobante': (r.get('comprobante') or '').strip()[:100]}

    def handle(self, *a, **o):
        with open(o['csv'], encoding='utf-8-sig', newline='') as fh:
            filas = [self._fila(i, r) for i, r in enumerate(csv.DictReader(fh), start=2)]

        creados, existentes = [], 0
        por_tipo = {t: Decimal('0.00') for t in TIPOS}
        try:
            with transaction.atomic():
                for f in filas:
                    # Idempotencia: clave fecha+tipo+descripcion+monto (ignora anulados
                    # para no revivirlos). get_or_create evita duplicar.
                    _, creado = MovimientoCaja.objects.get_or_create(
                        fecha=f['fecha'], tipo=f['tipo'], descripcion=f['descripcion'],
                        monto=f['monto'],
                        defaults={'rubro': f['rubro'], 'nota': f['nota'],
                                  'comprobante': f['comprobante']})
                    if creado:
                        creados.append(f)
                        por_tipo[f['tipo']] += f['monto']
                    else:
                        existentes += 1

                self.stdout.write(f'Filas en CSV: {len(filas)}')
                self.stdout.write(f'Nuevos: {len(creados)}  ·  Ya existían (no duplicados): {existentes}')
                for t in sorted(TIPOS):
                    n = sum(1 for f in creados if f['tipo'] == t)
                    self.stdout.write(f'  {t:16} nuevos={n:3}  S/{por_tipo[t]:>12,.2f}')
                if not o['aplicar']:
                    raise Ensayo()
        except Ensayo:
            self.stdout.write(self.style.WARNING('ENSAYO: nada se guardó. Corre con --aplicar para confirmar.'))
            return
        self.stdout.write(self.style.SUCCESS(f'Carga aplicada: {len(creados)} movimientos nuevos.'))
