"""Agua: tarifas (plana y versionada SEDAPAL) y lecturas de medidor."""
from decimal import Decimal

from django.db import models

from .departamento import Departamento


class TarifaAgua(models.Model):
    """Tramo de tarifa de agua (facturación por tramos acumulativos).

    Los tramos se encadenan por su `consumo_limite` (tope superior en m³). El
    último tramo debe dejar `consumo_limite` vacío = sin tope.
    """

    nombre = models.CharField(max_length=100)
    consumo_limite = models.DecimalField('Consumo límite (m³)', max_digits=8, decimal_places=2,
                                         null=True, blank=True,
                                         help_text='Tope superior del tramo. Vacío = sin tope.')
    tarifa = models.DecimalField('Tarifa por m³', max_digits=8, decimal_places=4)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Tarifa de agua'
        verbose_name_plural = 'Tarifas de agua'
        ordering = ['consumo_limite']

    def __str__(self):
        tope = f'hasta {self.consumo_limite} m³' if self.consumo_limite is not None else 'sin tope'
        return f'{self.nombre}: S/ {self.tarifa}/m³ ({tope})'


class LecturaMedidor(models.Model):
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='lecturas')
    periodo = models.DateField('Mes (día 1)')
    lectura_anterior = models.DecimalField(max_digits=10, decimal_places=2)
    lectura_actual = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_anterior = models.DateField('Fecha lectura anterior', null=True, blank=True)
    fecha_actual = models.DateField('Fecha lectura actual', null=True, blank=True)

    class Meta:
        verbose_name = 'Lectura de medidor'
        verbose_name_plural = 'Lecturas de medidor'
        ordering = ['-periodo', 'departamento']
        unique_together = ('departamento', 'periodo')

    @property
    def m3_consumidos(self):
        consumo = self.lectura_actual - self.lectura_anterior
        return consumo if consumo > 0 else Decimal('0.00')

    def __str__(self):
        return f'{self.departamento.numero_domicilio} {self.periodo:%Y-%m}: {self.m3_consumidos} m³'


class TarifaAguaVersion(models.Model):
    """Versión vigente de la tarifa de agua (por rangos). El precio ya incluye
    IGV y alcantarillado (así se manejan las tarifas del condominio)."""

    nombre = models.CharField(max_length=120)
    suministro = models.CharField('Suministro SEDAPAL', max_length=40, blank=True)
    vigencia_desde = models.DateField()
    vigencia_hasta = models.DateField(null=True, blank=True, help_text='Vacío = vigente.')
    cargo_fijo = models.DecimalField('Cargo fijo', max_digits=8, decimal_places=2, default=0)
    igv = models.DecimalField('IGV (fracción, ej 0.18)', max_digits=5, decimal_places=4,
                              default=Decimal('0.18'))
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Tarifa de agua (versión)'
        verbose_name_plural = 'Tarifas de agua (versiones)'
        ordering = ['-vigencia_desde']

    def __str__(self):
        return f'{self.nombre} (desde {self.vigencia_desde:%d/%m/%Y})'


class TarifaAguaTramo(models.Model):
    """Tramo de una versión de tarifa. Precio por m³ dentro del tramo (all-in)."""

    version = models.ForeignKey(TarifaAguaVersion, on_delete=models.CASCADE, related_name='tramos')
    consumo_hasta = models.DecimalField('Consumo límite (m³)', max_digits=8, decimal_places=2,
                                        null=True, blank=True, help_text='Tope superior. Vacío = sin tope.')
    precio_agua = models.DecimalField('Agua potable por m³', max_digits=8, decimal_places=4, default=0)
    precio_saneamiento = models.DecimalField('Saneamiento por m³', max_digits=8, decimal_places=4, default=0)

    class Meta:
        verbose_name = 'Tramo de tarifa'
        verbose_name_plural = 'Tramos de tarifa'
        ordering = ['consumo_hasta']

    def __str__(self):
        tope = f'hasta {self.consumo_hasta} m³' if self.consumo_hasta is not None else 'sin tope'
        return f'{tope}: agua {self.precio_agua} / san. {self.precio_saneamiento}'
