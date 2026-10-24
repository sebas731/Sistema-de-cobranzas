"""Seed de demostración: reproduce el recibo físico de la foto (MAYO 2026).

Crea (de forma idempotente) configuración del condominio, medios de pago, una
versión de tarifa de agua, el periodo de facturación de mayo 2026 con los datos
del edificio (A/B), un departamento de prueba con su lectura, un plan de cuotas
('Pintado condominio' 3/10) y el Recibo con sus líneas y sub-ítems (snapshot),
cuyo total coincide con la foto: S/ 153.10.

Uso:
    python manage.py seed_recibo_demo
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from departamentos.models import (
    ConfiguracionCondominio, Concepto, Departamento, LecturaMedidor, MedioPago,
    PeriodoFacturacion, PlanCuota, Recibo, ReciboLinea, ReciboSubLinea,
    TarifaAguaTramo, TarifaAguaVersion,
)
from departamentos.servicios import (
    agua_comun_por_departamento, calcular_agua_sedapal, sembrar_conceptos,
)

D = lambda v: Decimal(str(v))


class Command(BaseCommand):
    help = 'Crea el recibo de demostración de la foto (mayo 2026).'

    @transaction.atomic
    def handle(self, *args, **opts):
        sembrar_conceptos()
        periodo = date(2026, 5, 1)

        # 1) Configuración del condominio + medios de pago.
        #    (Los datos tapados en la foto van como PLACEHOLDER, no inventados.)
        cfg, _ = ConfiguracionCondominio.objects.update_or_create(
            nombre='Conjunto Residencial Los Parques del Agustino',
            defaults={
                'direccion': 'El Agustino, Lima',
                'email': 'residencial.demo@gmail.com',            # PLACEHOLDER (tapado en la foto)
                'whatsapp': '999999999',                          # PLACEHOLDER
                'horario_atencion': ('Lunes a Viernes 08:00am a 13:00pm y de 15:00 a 18:00pm.\n'
                                     'Sábado 08:00 a 13:00pm.'),
                'texto_aviso': ('Recuerda cancelar a tiempo tu recibo. Paga completo. No te olvides '
                                'de enviar la foto del abono.'),
                'activo': True,
            })
        cfg.medios_pago.all().delete()
        MedioPago.objects.create(configuracion=cfg, tipo=MedioPago.Tipo.BANCO, entidad='BBVA',
                                 numero='0011-0814-XXXXXXXXXX', cci='011-0814-XXXXXXXXXXXX-XX',
                                 titular='Junta de Propietarios', orden=10)     # PLACEHOLDER
        MedioPago.objects.create(configuracion=cfg, tipo=MedioPago.Tipo.PLIN, entidad='Plin',
                                 numero='999999999', orden=20)                   # PLACEHOLDER
        MedioPago.objects.create(configuracion=cfg, tipo=MedioPago.Tipo.YAPE, entidad='Yape',
                                 numero='999999999', orden=30)                   # PLACEHOLDER

        # 2) Tarifa de agua versionada (SEDAPAL Doméstico Grupo 1): agua potable y
        #    saneamiento por rango, + cargo fijo, + IGV 18%.
        version, _ = TarifaAguaVersion.objects.update_or_create(
            nombre='SEDAPAL Doméstico Grupo 1', suministro='5924621',
            defaults={'vigencia_desde': date(2026, 1, 1), 'vigencia_hasta': None,
                      'cargo_fijo': D('0.30'), 'igv': D('0.18'), 'activo': True})
        version.tramos.all().delete()
        # (rango hasta m³, agua potable S/·m³, saneamiento S/·m³)
        for hasta, agua, san in [(10, '2.20', '1.38'), (20, '2.36', '1.48'),
                                 (50, '3.22', '1.98'), (None, '7.32', '3.49')]:
            TarifaAguaTramo.objects.create(version=version, consumo_hasta=hasta,
                                           precio_agua=D(agua), precio_saneamiento=D(san))

        # 3) Periodo de facturación mayo 2026 con datos del edificio (A / B) y
        #    N° de dptos habitados para prorratear el agua común (A−B).
        per, _ = PeriodoFacturacion.objects.update_or_create(
            periodo=periodo,
            defaults={'fecha_emision': date(2026, 5, 14), 'fecha_vencimiento': date(2026, 5, 24),
                      'fecha_restriccion': date(2026, 6, 2), 'tarifa_agua': version,
                      'sedapal_total_a': D('24760.60'), 'consumo_dptos_b': D('21166.70'),
                      'dptos_habitados': 460, 'emitido': True})

        # 4) Departamento de prueba (no toca datos reales) con cochera.
        dep, _ = Departamento.objects.update_or_create(
            numero_domicilio='101-DEMO',
            defaults={'nombres': 'Propietario', 'apellidos': 'Demo', 'cochera_monto': D('5.00'),
                      'activo': True})

        # 5) Lectura del medidor (11 m³) con fechas.
        LecturaMedidor.objects.update_or_create(
            departamento=dep, periodo=periodo,
            defaults={'lectura_anterior': D('2514'), 'lectura_actual': D('2525'),
                      'fecha_anterior': date(2026, 4, 9), 'fecha_actual': date(2026, 5, 8)})

        # 6) Plan de cuotas 'Pintado condominio' (10 cuotas; mayo = cuota 3).
        cuota_concepto = Concepto.objects.get(codigo='CUOTA')
        plan, _ = PlanCuota.objects.update_or_create(
            descripcion='Cuota pintado condominio',
            defaults={'concepto': cuota_concepto, 'monto_cuota': D('30.00'), 'num_cuotas': 10,
                      'periodo_inicio': date(2026, 3, 1), 'activo': True})
        n_cuota = plan.numero_en(periodo)  # -> 3

        # Agua (SEDAPAL) y agua común se CALCULAN con el servicio (deben dar 47.10 y 7.80).
        agua = calcular_agua_sedapal(D('11'), version)
        agua_comun = agua_comun_por_departamento(per)

        # 7) Recibo (snapshot) con sus líneas y sub-ítems. Total = 153.10 (foto).
        recibo, _ = Recibo.objects.update_or_create(
            periodo_fac=per, departamento=dep,
            defaults={
                'lectura_anterior_fecha': date(2026, 4, 9), 'lectura_anterior_valor': D('2514'),
                'lectura_actual_fecha': date(2026, 5, 8), 'lectura_actual_valor': D('2525'),
                'm3_consumidos': D('11'),
                'agua_potable': agua['agua_potable'], 'agua_saneamiento': agua['saneamiento'],
                'agua_cargo_fijo': agua['cargo_fijo'], 'agua_igv': agua['igv'],
                'deuda_anterior': D('0.00'), 'descuento_mes_anterior': D('0.00'),
                'saldo_favor': D('0.00'), 'emitido_en': timezone.now(),
            })
        recibo.lineas.all().delete()

        def linea(concepto_cod, etiqueta, monto, grupo, orden, sub=None):
            c = Concepto.objects.filter(codigo=concepto_cod).first() if concepto_cod else None
            ln = ReciboLinea.objects.create(recibo=recibo, concepto=c, etiqueta=etiqueta,
                                            monto=D(monto), grupo=grupo, orden=orden)
            for i, (et, mo) in enumerate(sub or [], start=1):
                ReciboSubLinea.objects.create(linea=ln, etiqueta=et, monto=D(mo), orden=i * 10)
            return ln

        G = ReciboLinea.Grupo
        linea(Concepto.AGUA, 'Pago consumo Dpto.', agua['total'], G.AGUA, 10)
        linea(Concepto.AREA_COMUN, 'Agua común', agua_comun, G.CONSUMO, 20)
        linea(Concepto.LUZ_COMUN, 'Luz común', '5.20', G.CONSUMO, 30)
        linea(Concepto.AGUA_BASICO, 'Agua básico', '0.00', G.CONSUMO, 40)
        linea(Concepto.MANTENIMIENTO, 'Mantenimiento', '58.00', G.CONSUMO, 50,
              sub=[('Vigilancia', '30.00'), ('Limpieza', '18.00'), ('Administración', '10.00')])
        linea('CUOTA', f'Cuota pintado condominio {n_cuota}/{plan.num_cuotas}', '30.00', G.CONSUMO, 60)
        linea(Concepto.COCHERA, 'Cochera Interior N° 10', '5.00', G.CONSUMO, 70)

        # Totales snapshot.
        total_mes = sum((l.monto for l in recibo.lineas.all()), Decimal('0.00'))
        recibo.total_mes = total_mes
        recibo.total_pagar = total_mes + recibo.deuda_anterior - recibo.descuento_mes_anterior
        recibo.save(update_fields=['total_mes', 'total_pagar'])

        self.stdout.write(self.style.SUCCESS(
            f'Recibo demo {dep.numero_domicilio} {per} creado. '
            f'Líneas: {recibo.lineas.count()} · Total del mes: S/ {total_mes} · '
            f'Total a pagar: S/ {recibo.total_pagar}'))
