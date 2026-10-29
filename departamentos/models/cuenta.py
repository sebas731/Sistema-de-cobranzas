"""Libro de cuenta corriente: conceptos, cargos, pagos e imputaciones."""
from decimal import Decimal

from django.db import models
from django.utils import timezone

from .departamento import Departamento


class Concepto(models.Model):
    """Catálogo de conceptos de cobro para el libro de deudas (cuenta corriente).

    Los recurrentes (mantenimiento, agua, área común, cochera) se generan cada mes;
    los demás (puertas, cámara, ascensores, cuota, multa, mora...) se cargan a mano.
    """

    # Códigos estándar usados por la generación mensual.
    MANTENIMIENTO = 'MANTENIMIENTO'
    AGUA = 'AGUA'
    AGUA_BASICO = 'AGUA_BASICO'
    AREA_COMUN = 'AREA_COMUN'
    LUZ_COMUN = 'LUZ_COMUN'
    COCHERA = 'COCHERA'
    GASTO_ADIC = 'GASTO_ADIC'

    class Aplica(models.TextChoices):
        TODOS = 'TODOS', 'Todos los departamentos'
        CON_COCHERA = 'CON_COCHERA', 'Solo los que tienen cochera'
        SELECCION = 'SELECCION', 'Selección de departamentos'

    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=60)
    descripcion = models.CharField(max_length=200, blank=True)
    orden = models.PositiveIntegerField(default=100)
    es_recurrente = models.BooleanField('Se genera cada mes', default=False)
    permite_subitems = models.BooleanField('Admite desglose en sub-ítems', default=False)
    aplica = models.CharField(max_length=12, choices=Aplica.choices, default=Aplica.TODOS)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Concepto de deuda'
        verbose_name_plural = 'Conceptos de deuda'
        ordering = ['orden', 'nombre']

    def __str__(self):
        return self.nombre


class Cargo(models.Model):
    """Un cargo (deuda) contra un departamento: depto + concepto + mes + monto.

    La deuda NO se guarda como un número editable: cada cargo es una línea del
    libro, y su saldo = monto − pagos imputados. El saldo del depto/mes/concepto
    se deriva sumando cargos y restando imputaciones.

    `origen` distingue lo que generó el sistema de lo cargado en la carga
    inicial de deudas (migración desde Excel), que se edita en su propia vista.
    """

    class Origen(models.TextChoices):
        SISTEMA = 'SISTEMA', 'Sistema'
        MIGRACION = 'MIGRACION', 'Carga inicial (migración)'

    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='cargos')
    concepto = models.ForeignKey(Concepto, on_delete=models.PROTECT, related_name='cargos')
    periodo = models.DateField('Mes (día 1)')
    descripcion = models.CharField(max_length=150, blank=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_emision = models.DateField(auto_now_add=True)
    fecha_vencimiento = models.DateField()
    origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.SISTEMA)

    class Meta:
        verbose_name = 'Cargo'
        verbose_name_plural = 'Cargos'
        ordering = ['periodo', 'concepto']
        unique_together = ('departamento', 'concepto', 'periodo')

    @property
    def total_imputado(self):
        return sum((i.monto for i in self.imputaciones.all()), Decimal('0.00'))

    @property
    def saldo(self):
        return self.monto - self.total_imputado

    @property
    def pagado(self):
        return self.saldo <= 0

    @property
    def estado(self):
        if self.saldo <= 0:
            return 'PAGADO'
        if self.fecha_vencimiento < timezone.localdate():
            return 'VENCIDO'
        if self.total_imputado > 0:
            return 'PARCIAL'
        return 'PENDIENTE'

    def __str__(self):
        return f'{self.departamento.numero_domicilio} {self.concepto.codigo} {self.periodo:%Y-%m}: S/ {self.monto}'


class Pago(models.Model):
    """Un pago de un departamento. Se imputa (aplica) contra sus cargos vía Imputacion.

    Si `concepto` está definido, el pago se aplica solo a ese concepto (ej.
    mantenimiento); si no, se aplica a los cargos más antiguos primero (FIFO).
    """

    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='pagos')
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_pago = models.DateField(default=timezone.localdate)
    metodo = models.CharField(max_length=50, blank=True)
    concepto = models.ForeignKey(Concepto, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='pagos',
                                 help_text='Vacío = pago general (cancela lo más antiguo).')
    nota = models.CharField(max_length=200, blank=True)
    # MIGRACION = deuda antigua ya cobrada, cargada en la carga inicial (cuenta como ingreso).
    origen = models.CharField(max_length=10, choices=Cargo.Origen.choices, default=Cargo.Origen.SISTEMA)

    class Meta:
        verbose_name = 'Pago'
        verbose_name_plural = 'Pagos'
        ordering = ['-fecha_pago', '-id']

    @property
    def total_imputado(self):
        return sum((i.monto for i in self.imputaciones.all()), Decimal('0.00'))

    @property
    def saldo_a_favor(self):
        return self.monto - self.total_imputado

    def __str__(self):
        destino = self.concepto.codigo if self.concepto else 'GENERAL'
        return f'Pago S/ {self.monto} de {self.departamento.numero_domicilio} ({destino})'


class Imputacion(models.Model):
    """Aplicación de parte de un pago sobre un cargo concreto (imputación)."""

    pago = models.ForeignKey(Pago, on_delete=models.CASCADE, related_name='imputaciones')
    cargo = models.ForeignKey(Cargo, on_delete=models.CASCADE, related_name='imputaciones')
    monto = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Imputación de pago'
        verbose_name_plural = 'Imputaciones de pago'

    def __str__(self):
        return f'S/ {self.monto} de pago #{self.pago_id} → cargo #{self.cargo_id}'
