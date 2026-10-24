"""Recibo de mantenimiento (estilo boleta física) con snapshot de montos."""
from decimal import Decimal

from django.db import models

from .agua import TarifaAguaVersion
from .cuenta import Concepto
from .departamento import Departamento, solo_digitos


class ConfiguracionCondominio(models.Model):
    """Datos del condominio para la cabecera/pie del recibo (singleton lógico)."""

    nombre = models.CharField(max_length=150, default='Condominio')
    direccion = models.CharField(max_length=200, blank=True)
    logo = models.CharField('Logo (ruta o URL)', max_length=300, blank=True,
                            help_text='Ruta bajo /static o URL. Sin subida de archivos.')
    email = models.EmailField(blank=True)
    whatsapp = models.CharField(max_length=30, blank=True, validators=[solo_digitos])
    horario_atencion = models.TextField(blank=True)
    texto_aviso = models.TextField('Texto del aviso importante', blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Configuración del condominio'
        verbose_name_plural = 'Configuración del condominio'

    @classmethod
    def vigente(cls):
        return cls.objects.filter(activo=True).first() or cls.objects.first()

    def __str__(self):
        return self.nombre


class MedioPago(models.Model):
    """Cuenta bancaria / billetera para pagar el recibo."""

    class Tipo(models.TextChoices):
        BANCO = 'BANCO', 'Cuenta bancaria'
        PLIN = 'PLIN', 'Plin'
        YAPE = 'YAPE', 'Yape'
        OTRO = 'OTRO', 'Otro'

    configuracion = models.ForeignKey(ConfiguracionCondominio, on_delete=models.CASCADE,
                                      related_name='medios_pago')
    tipo = models.CharField(max_length=8, choices=Tipo.choices, default=Tipo.BANCO)
    entidad = models.CharField('Banco / billetera', max_length=80)
    numero = models.CharField('Número de cuenta / celular', max_length=60, blank=True)
    cci = models.CharField('CCI', max_length=40, blank=True)
    titular = models.CharField(max_length=120, blank=True)
    orden = models.PositiveIntegerField(default=100)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Medio de pago'
        verbose_name_plural = 'Medios de pago'
        ordering = ['orden', 'id']

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.entidad}'


class PeriodoFacturacion(models.Model):
    """Periodo mensual de cobranza con sus fechas y los totales del edificio."""

    periodo = models.DateField('Mes (día 1)', unique=True)
    fecha_emision = models.DateField()
    fecha_vencimiento = models.DateField()
    fecha_restriccion = models.DateField('Restricción del servicio', null=True, blank=True)
    tarifa_agua = models.ForeignKey(TarifaAguaVersion, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='periodos')
    # Datos del edificio para el prorrateo del agua común (A − B).
    sedapal_total_a = models.DecimalField('A) Total recibos SEDAPAL', max_digits=12,
                                          decimal_places=2, default=0)
    consumo_dptos_b = models.DecimalField('B) Consumo dptos. habitados', max_digits=12,
                                          decimal_places=2, default=0)
    dptos_habitados = models.PositiveIntegerField(
        'N° de dptos. habitados', null=True, blank=True,
        help_text='Divisor para prorratear el agua común (A−B) en partes iguales.')
    emitido = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Periodo de facturación'
        verbose_name_plural = 'Periodos de facturación'
        ordering = ['-periodo']

    @property
    def diferencia_ab(self):
        return (self.sedapal_total_a or Decimal('0')) - (self.consumo_dptos_b or Decimal('0'))

    def __str__(self):
        return f'{self.periodo:%m/%Y}'


class PlanCuota(models.Model):
    """Cuota extraordinaria en armadas (ej: 'Pintado condominio' en 10 cuotas)."""

    descripcion = models.CharField(max_length=150)
    concepto = models.ForeignKey(Concepto, on_delete=models.PROTECT, related_name='planes_cuota')
    monto_cuota = models.DecimalField(max_digits=10, decimal_places=2)
    num_cuotas = models.PositiveIntegerField('N° total de cuotas')
    periodo_inicio = models.DateField('Primer periodo (día 1)')
    departamentos = models.ManyToManyField(Departamento, blank=True, related_name='planes_cuota',
                                           help_text='Vacío = todos los departamentos.')
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Plan de cuotas'
        verbose_name_plural = 'Planes de cuotas'
        ordering = ['descripcion']

    def numero_en(self, periodo):
        """Número de cuota (1-based) que corresponde a `periodo`, o None si fuera de rango."""
        meses = (periodo.year - self.periodo_inicio.year) * 12 + (periodo.month - self.periodo_inicio.month)
        n = meses + 1
        return n if 1 <= n <= self.num_cuotas else None

    def __str__(self):
        return f'{self.descripcion} ({self.num_cuotas} cuotas)'


class Recibo(models.Model):
    """Recibo mensual de un departamento. Guarda un SNAPSHOT de sus líneas y
    montos: si luego cambian tarifas o gastos, el recibo emitido no varía."""

    periodo_fac = models.ForeignKey(PeriodoFacturacion, on_delete=models.CASCADE, related_name='recibos')
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='recibos')
    # Snapshot de lecturas de agua.
    lectura_anterior_fecha = models.DateField(null=True, blank=True)
    lectura_anterior_valor = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    lectura_actual_fecha = models.DateField(null=True, blank=True)
    lectura_actual_valor = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    m3_consumidos = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Snapshot del desglose del agua (para el "detalle de facturación").
    agua_potable = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    agua_saneamiento = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    agua_cargo_fijo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    agua_igv = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Snapshot de montos / saldo.
    total_mes = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deuda_anterior = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    descuento_mes_anterior = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    saldo_favor = models.DecimalField('Saldo a favor', max_digits=10, decimal_places=2, default=0)
    total_pagar = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    emitido_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Recibo'
        verbose_name_plural = 'Recibos'
        unique_together = ('periodo_fac', 'departamento')
        ordering = ['departamento']

    def __str__(self):
        return f'Recibo {self.departamento.numero_domicilio} {self.periodo_fac}'


class ReciboLinea(models.Model):
    """Línea del recibo (concepto cobrado), con etiqueta y monto congelados."""

    class Grupo(models.TextChoices):
        CONSUMO = 'CONSUMO', 'Consumo / mantenimiento'
        AGUA = 'AGUA', 'Agua'

    recibo = models.ForeignKey(Recibo, on_delete=models.CASCADE, related_name='lineas')
    concepto = models.ForeignKey(Concepto, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='lineas_recibo')
    etiqueta = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    grupo = models.CharField(max_length=8, choices=Grupo.choices, default=Grupo.CONSUMO)
    orden = models.PositiveIntegerField(default=100)

    class Meta:
        verbose_name = 'Línea de recibo'
        verbose_name_plural = 'Líneas de recibo'
        ordering = ['orden', 'id']

    def __str__(self):
        return f'{self.etiqueta}: S/ {self.monto}'


class ReciboSubLinea(models.Model):
    """Sub-ítem del desglose de una línea (ej: Mantenimiento → limpieza/vigilancia)."""

    linea = models.ForeignKey(ReciboLinea, on_delete=models.CASCADE, related_name='sublineas')
    etiqueta = models.CharField(max_length=120)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    orden = models.PositiveIntegerField(default=100)

    class Meta:
        verbose_name = 'Sub-ítem de recibo'
        verbose_name_plural = 'Sub-ítems de recibo'
        ordering = ['orden', 'id']

    def __str__(self):
        return f'{self.etiqueta}: S/ {self.monto}'
