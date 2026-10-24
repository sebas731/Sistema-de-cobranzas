"""Gastos del edificio: fijos (recurrentes) y variables del mes."""
from django.db import models

from .departamento import Departamento


class GastoEdificio(models.Model):
    """Gasto VARIABLE del mes (dinámico, se registra por periodo). Según su
    categoría se combina con los gastos fijos: Mantenimiento y Agua se suman a
    la categoría equivalente; 'Gastos adicionales' es un cobro aparte."""

    class Categoria(models.TextChoices):
        GENERAL = 'GENERAL', 'Mantenimiento (servicios generales)'
        AGUA_COMUN = 'AGUA_COMUN', 'Agua de área común'
        ADICIONAL = 'ADICIONAL', 'Gastos adicionales (otros cobros)'

    periodo = models.DateField('Mes (día 1)')
    descripcion = models.CharField(max_length=120)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    categoria = models.CharField(max_length=12, choices=Categoria.choices,
                                default=Categoria.GENERAL)
    compartido = models.BooleanField(
        'Compartido (dividir entre todos)', default=True,
        help_text='Marcado: el monto se divide entre los deptos. '
                  'Desmarcado: cada depto paga el monto completo.')
    departamentos = models.ManyToManyField(
        Departamento, blank=True, related_name='gastos_dirigidos',
        help_text='Personalizado: aplica solo a estos deptos. Vacío = a todos.')

    class Meta:
        verbose_name = 'Gasto variable del mes'
        verbose_name_plural = 'Gastos variables del mes'
        ordering = ['-periodo', 'categoria', 'descripcion']

    @property
    def es_personalizado(self):
        return self.departamentos.exists()

    def __str__(self):
        return f'{self.periodo:%Y-%m} {self.descripcion}: S/ {self.monto}'


class GastoFijo(models.Model):
    """Gasto fijo recurrente: se registra una vez y aplica todos los meses.

    Por defecto cada departamento paga el monto completo. Si se marca como
    `compartido`, el monto se divide entre todos los departamentos activos.
    """

    descripcion = models.CharField(max_length=120)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    categoria = models.CharField(max_length=12, choices=GastoEdificio.Categoria.choices,
                                default=GastoEdificio.Categoria.GENERAL)
    concepto = models.ForeignKey('Concepto', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='gastos_fijos',
                                 help_text='Concepto del recibo al que aporta (ej: Mantenimiento, '
                                           'Luz común). Vacío = se deduce de la categoría.')
    compartido = models.BooleanField(
        'Compartido (dividir entre todos)', default=False,
        help_text='Desmarcado: cada depto paga el monto completo. '
                  'Marcado: el monto se divide entre los deptos.')
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Gasto fijo'
        verbose_name_plural = 'Gastos fijos'
        ordering = ['categoria', 'descripcion']

    def __str__(self):
        modo = 'compartido' if self.compartido else 'por depto'
        return f'{self.descripcion}: S/ {self.monto} ({modo})'
