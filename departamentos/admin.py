from django.contrib import admin

from .models import (
    Cargo,
    Concepto,
    Departamento,
    GastoEdificio,
    GastoFijo,
    Imputacion,
    LecturaMedidor,
    Pago,
    Proveedor,
    TarifaAgua,
)


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'rol', 'telefono', 'activo')
    list_editable = ('telefono', 'activo')
    list_filter = ('rol', 'activo')
    search_fields = ('nombre', 'rol', 'telefono')


@admin.register(GastoFijo)
class GastoFijoAdmin(admin.ModelAdmin):
    list_display = ('descripcion', 'monto', 'categoria', 'compartido', 'activo')
    list_editable = ('monto', 'compartido', 'activo')
    list_filter = ('categoria', 'compartido', 'activo')
    search_fields = ('descripcion',)


@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    list_display = ('numero_domicilio', 'tipo_domicilio', 'nombres', 'apellidos',
                    'celular', 'tiene_inquilino', 'celular_inquilino', 'cochera_monto', 'activo')
    list_editable = ('cochera_monto', 'activo')
    list_filter = ('tipo_domicilio', 'tiene_inquilino', 'activo')
    search_fields = ('numero_domicilio', 'nombres', 'apellidos', 'dni', 'celular', 'celular_inquilino')


@admin.register(GastoEdificio)
class GastoEdificioAdmin(admin.ModelAdmin):
    list_display = ('periodo', 'descripcion', 'monto', 'categoria', 'compartido')
    list_editable = ('monto', 'compartido')
    list_filter = ('periodo', 'categoria', 'compartido')
    search_fields = ('descripcion',)


@admin.register(TarifaAgua)
class TarifaAguaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'consumo_limite', 'tarifa', 'activo')
    list_editable = ('tarifa', 'activo')


@admin.register(LecturaMedidor)
class LecturaMedidorAdmin(admin.ModelAdmin):
    list_display = ('departamento', 'periodo', 'lectura_anterior', 'lectura_actual', 'm3_consumidos')
    list_filter = ('periodo',)
    search_fields = ('departamento__numero_domicilio',)


@admin.register(Concepto)
class ConceptoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'orden', 'es_recurrente', 'activo')
    list_editable = ('orden', 'es_recurrente', 'activo')


class ImputacionInline(admin.TabularInline):
    model = Imputacion
    extra = 0


@admin.register(Cargo)
class CargoAdmin(admin.ModelAdmin):
    list_display = ('departamento', 'concepto', 'periodo', 'monto', 'saldo', 'estado')
    list_filter = ('concepto', 'periodo')
    search_fields = ('departamento__numero_domicilio',)
    inlines = [ImputacionInline]


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('departamento', 'monto', 'concepto', 'fecha_pago', 'metodo')
    list_filter = ('fecha_pago', 'concepto')
    search_fields = ('departamento__numero_domicilio',)
    inlines = [ImputacionInline]
