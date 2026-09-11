from decimal import Decimal

from django.core.validators import RegexValidator
from django.db import models

solo_digitos = RegexValidator(r'^\d+$', 'Ingrese solo dígitos.')


class Departamento(models.Model):
    """Cada departamento del edificio. Solo el número es obligatorio."""

    class TipoDomicilio(models.TextChoices):
        DUPLEX = 'DUPLEX', 'Dúplex'
        FLAT = 'FLAT', 'Flat'
        CASA = 'CASA', 'Casa'

    tipo_domicilio = models.CharField(max_length=10, choices=TipoDomicilio.choices,
                                      default=TipoDomicilio.FLAT)
    numero_domicilio = models.CharField('N° Departamento', max_length=20, unique=True)
    nombres = models.CharField(max_length=100, blank=True)
    apellidos = models.CharField(max_length=100, blank=True)
    dni = models.CharField('DNI', max_length=8, blank=True, validators=[solo_digitos])
    celular = models.CharField(max_length=9, blank=True, validators=[solo_digitos])
    correo = models.EmailField(blank=True)
    cochera_monto = models.DecimalField('Cobro de cochera', max_digits=10, decimal_places=2,
                                        default=0, help_text='0 si no tiene cochera.')
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Departamento'
        verbose_name_plural = 'Departamentos'
        ordering = ['numero_domicilio']

    @property
    def propietario(self):
        return f'{self.apellidos}, {self.nombres}'.strip(', ') or 'Sin propietario'

    @property
    def tiene_cochera(self):
        return self.cochera_monto and self.cochera_monto > 0

    def __str__(self):
        return f'{self.numero_domicilio} ({self.propietario})'


class GastoEdificio(models.Model):
    """Gasto ADICIONAL del mes (dinámico, se registra por periodo). Se combina
    con los gastos fijos para calcular el mantenimiento y el área común."""

    class Categoria(models.TextChoices):
        GENERAL = 'GENERAL', 'Mantenimiento (servicios generales)'
        AGUA_COMUN = 'AGUA_COMUN', 'Agua de área común'

    periodo = models.DateField('Mes (día 1)')
    descripcion = models.CharField(max_length=120)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    categoria = models.CharField(max_length=12, choices=Categoria.choices,
                                default=Categoria.GENERAL)
    compartido = models.BooleanField(
        'Compartido (dividir entre todos)', default=True,
        help_text='Marcado: el monto se divide entre los deptos. '
                  'Desmarcado: cada depto paga el monto completo.')

    class Meta:
        verbose_name = 'Gasto adicional del mes'
        verbose_name_plural = 'Gastos adicionales del mes'
        ordering = ['-periodo', 'categoria', 'descripcion']

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


class Recibo(models.Model):
    """Recibo mensual de un departamento (una fila por depto y mes, como el Excel).

    Los montos son snapshot: quedan congelados al generarse, así editar gastos o
    tarifas luego no altera recibos ya emitidos.
    """

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        PAGADO = 'PAGADO', 'Pagado'
        VENCIDO = 'VENCIDO', 'Vencido'

    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='recibos')
    periodo = models.DateField('Mes (día 1)')
    fecha_vencimiento = models.DateField()

    m3_consumidos = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monto_agua = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monto_area_comun = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monto_mantenimiento = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monto_cochera = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gastos_adicionales = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    mora = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)

    class Meta:
        verbose_name = 'Recibo'
        verbose_name_plural = 'Recibos'
        ordering = ['-periodo', 'departamento']
        unique_together = ('departamento', 'periodo')

    @property
    def extras(self):
        """Todo lo que no son los conceptos base: adicionales del mes + mora."""
        return self.gastos_adicionales + self.mora

    @property
    def total(self):
        return (self.monto_agua + self.monto_area_comun + self.monto_mantenimiento
                + self.monto_cochera + self.gastos_adicionales + self.mora)

    @property
    def total_pagado(self):
        return sum((p.monto for p in self.pagos.all()), Decimal('0.00'))

    @property
    def saldo(self):
        return self.total - self.total_pagado

    def __str__(self):
        return f'Recibo {self.departamento.numero_domicilio} {self.periodo:%Y-%m}: S/ {self.total}'


class Pago(models.Model):
    recibo = models.ForeignKey(Recibo, on_delete=models.CASCADE, related_name='pagos')
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_pago = models.DateField(auto_now_add=True)
    metodo = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = 'Pago'
        verbose_name_plural = 'Pagos'
        ordering = ['-fecha_pago']

    def __str__(self):
        return f'Pago S/ {self.monto} a recibo #{self.recibo_id}'
