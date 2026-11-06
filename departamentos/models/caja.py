"""Caja real del edificio: movimientos de dinero que NO afectan la deuda."""
from django.conf import settings
from django.db import models


class MovimientoCaja(models.Model):
    """Un movimiento de la caja real: saldo inicial, otro ingreso, depósito por
    identificar o egreso pagado. NUNCA genera cargos ni toca la deuda de un
    departamento. No se borra: se ANULA (queda en el historial y no suma).
    """

    class Tipo(models.TextChoices):
        SALDO_INICIAL = 'SALDO_INICIAL', 'Saldo inicial'
        INGRESO_OTRO = 'INGRESO_OTRO', 'Otro ingreso'
        POR_IDENTIFICAR = 'POR_IDENTIFICAR', 'Depósito por identificar'
        EGRESO = 'EGRESO', 'Egreso (gasto pagado)'

    class Rubro(models.TextChoices):
        VIGILANCIA = 'VIGILANCIA', 'Vigilancia'
        LIMPIEZA_PERSONAL = 'LIMPIEZA_PERSONAL', 'Personal de limpieza'
        LIMPIEZA_MATERIAL = 'LIMPIEZA_MATERIAL', 'Materiales de limpieza'
        LUZ = 'LUZ', 'Luz'
        AGUA = 'AGUA', 'Agua'
        ASCENSORES = 'ASCENSORES', 'Ascensores'
        ADMINISTRATIVOS = 'ADMINISTRATIVOS', 'Gastos administrativos'
        INTERNET = 'INTERNET', 'Internet'
        AREA_COMUN = 'AREA_COMUN', 'Área común condominio'
        MANTENIMIENTO = 'MANTENIMIENTO', 'Mantenimiento y reparaciones'
        BANCARIOS = 'BANCARIOS', 'Gastos bancarios (ITF)'
        DEVOLUCIONES = 'DEVOLUCIONES', 'Devoluciones'
        ACOPIO = 'ACOPIO', 'Acopio'
        OTROS = 'OTROS', 'Otros'

    class Estado(models.TextChoices):
        VIGENTE = 'VIGENTE', 'Vigente'
        ANULADO = 'ANULADO', 'Anulado'

    fecha = models.DateField()
    tipo = models.CharField(max_length=16, choices=Tipo.choices)
    rubro = models.CharField(max_length=20, choices=Rubro.choices, blank=True,
                             help_text='Solo para egresos: el rubro del gasto.')
    descripcion = models.CharField(max_length=200)
    monto = models.DecimalField(max_digits=12, decimal_places=2)  # siempre > 0
    nota = models.CharField(max_length=255, blank=True)
    comprobante = models.CharField('N° de operación / referencia', max_length=100, blank=True)
    estado = models.CharField(max_length=8, choices=Estado.choices, default=Estado.VIGENTE)
    motivo_anulacion = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='movimientos_caja')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimiento de caja'
        verbose_name_plural = 'Movimientos de caja'
        ordering = ['-fecha', '-id']
        constraints = [
            models.CheckConstraint(condition=models.Q(monto__gt=0),
                                   name='movimientocaja_monto_positivo'),
        ]

    @property
    def signo(self):
        """+1 si suma a la caja (saldo inicial / ingresos), -1 si resta (egreso)."""
        return -1 if self.tipo == self.Tipo.EGRESO else 1

    @property
    def vigente(self):
        return self.estado == self.Estado.VIGENTE

    def anular(self, motivo, usuario=None):
        """Marca el movimiento como anulado (no se borra). `usuario` se acepta por
        simetría con la auditoría; el anulador se puede registrar en la nota."""
        self.estado = self.Estado.ANULADO
        self.motivo_anulacion = motivo
        self.save(update_fields=['estado', 'motivo_anulacion'])

    def __str__(self):
        return f'{self.fecha} {self.get_tipo_display()} S/ {self.monto} ({self.descripcion})'
