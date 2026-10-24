"""Departamentos del edificio y proveedores (contactos)."""
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
