"""Carga los datos reales del condominio que usa el recibo (cabecera, aviso y pago).

Actualiza la configuración vigente (o la crea) con nombre, logo y texto del aviso,
y deja como único medio de pago activo la cuenta BCP. Los otros medios de pago no
se borran: se desactivan (se pueden reactivar en el admin). No toca email,
WhatsApp, dirección ni horario: si están vacíos o mal, se corrigen en el admin.

En el texto del aviso, `{vencimiento}` se reemplaza por la fecha de pago de cada
recibo (día-mes del vencimiento, ej. 05-09).

Uso (local y VPS; idempotente):
    python manage.py configurar_condominio
    python manage.py collectstatic --noinput   # para que el logo se sirva en el VPS
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from departamentos.models import ConfiguracionCondominio, MedioPago

NOMBRE = 'Condominio Los Girasoles'
LOGO = 'img/logo_condominio.jpg'   # bajo departamentos/static/
AVISO = ('Recuerden realizar sus pagos hasta el {vencimiento}; posterior a la fecha de pago '
         'se cobrará mora de S/ 15.00. Recuerda enviar el voucher.')
BANCO = {'entidad': 'BCP - Cta. Cte.', 'numero': '1917786570020',
         'titular': 'FIORELLA MENDOZA - DPTO 705'}


class Command(BaseCommand):
    help = 'Carga nombre, logo, aviso y la cuenta BCP del condominio en la configuración vigente.'

    @transaction.atomic
    def handle(self, *args, **opts):
        cfg = ConfiguracionCondominio.vigente() or ConfiguracionCondominio(activo=True)
        cfg.nombre, cfg.logo, cfg.texto_aviso, cfg.activo = NOMBRE, LOGO, AVISO, True
        cfg.save()

        banco, creado = MedioPago.objects.update_or_create(
            configuracion=cfg, tipo=MedioPago.Tipo.BANCO, numero=BANCO['numero'],
            defaults={'entidad': BANCO['entidad'], 'titular': BANCO['titular'], 'cci': '',
                      'orden': 10, 'activo': True})
        otros = cfg.medios_pago.filter(activo=True).exclude(pk=banco.pk)
        desactivados = [str(m) for m in otros]
        otros.update(activo=False)

        self.stdout.write(self.style.SUCCESS(f'Configuración "{cfg.nombre}" (id {cfg.pk}) actualizada.'))
        self.stdout.write(f'  Cuenta {banco.entidad} {banco.numero} {"creada" if creado else "actualizada"}.')
        if desactivados:
            self.stdout.write(f'  Medios de pago desactivados: {", ".join(desactivados)}')
        for campo in ('direccion', 'email', 'whatsapp', 'horario_atencion'):
            valor = getattr(cfg, campo)
            self.stdout.write(f'  Revisa en el admin: {campo} = {valor!r}')
