from decimal import Decimal

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

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
    nombres = models.CharField('Nombres del propietario', max_length=100, blank=True)
    apellidos = models.CharField('Apellidos del propietario', max_length=100, blank=True)
    dni = models.CharField('DNI', max_length=8, blank=True, validators=[solo_digitos])
    celular = models.CharField('Celular del propietario', max_length=15, blank=True, validators=[solo_digitos])
    correo = models.EmailField(blank=True)
    tiene_inquilino = models.BooleanField('¿Tiene inquilino?', default=False)
    celular_inquilino = models.CharField('Celular del inquilino', max_length=15, blank=True,
                                         validators=[solo_digitos])
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
    departamentos = models.ManyToManyField(
        Departamento, blank=True, related_name='gastos_dirigidos',
        help_text='Personalizado: aplica solo a estos deptos. Vacío = a todos.')

    class Meta:
        verbose_name = 'Gasto adicional del mes'
        verbose_name_plural = 'Gastos adicionales del mes'
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


class Concepto(models.Model):
    """Catálogo de conceptos de cobro para el libro de deudas (cuenta corriente).

    Los recurrentes (mantenimiento, agua, área común, cochera) se generan cada mes;
    los demás (puertas, cámara, ascensores, cuota, multa, mora...) se cargan a mano.
    """

    # Códigos estándar usados por la generación mensual.
    MANTENIMIENTO = 'MANTENIMIENTO'
    AGUA = 'AGUA'
    AREA_COMUN = 'AREA_COMUN'
    COCHERA = 'COCHERA'
    GASTO_ADIC = 'GASTO_ADIC'

    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=60)
    orden = models.PositiveIntegerField(default=100)
    es_recurrente = models.BooleanField('Se genera cada mes', default=False)
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
    """

    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='cargos')
    concepto = models.ForeignKey(Concepto, on_delete=models.PROTECT, related_name='cargos')
    periodo = models.DateField('Mes (día 1)')
    descripcion = models.CharField(max_length=150, blank=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_emision = models.DateField(auto_now_add=True)
    fecha_vencimiento = models.DateField()

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


class Proveedor(models.Model):
    """Proveedor / contacto del edificio (gasfitero, seguridad, limpieza, etc.)."""

    nombre = models.CharField(max_length=120)
    rol = models.CharField('Rol / servicio', max_length=80, blank=True,
                           help_text='Ej: Gasfitero, Seguridad, Limpieza, Cámaras, Bombas...')
    telefono = models.CharField(max_length=15, blank=True, validators=[solo_digitos])
    nota = models.CharField(max_length=200, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Proveedor'
        verbose_name_plural = 'Proveedores'
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.rol})' if self.rol else self.nombre
