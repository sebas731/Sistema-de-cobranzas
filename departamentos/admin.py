from django.contrib import admin

from .models import (
    Departamento,
    GastoEdificio,
    GastoFijo,
    LecturaMedidor,
    Pago,
    Recibo,
    TarifaAgua,
)


@admin.register(GastoFijo)
class GastoFijoAdmin(admin.ModelAdmin):
    list_display = ('descripcion', 'monto', 'categoria', 'compartido', 'activo')
    list_editable = ('monto', 'compartido', 'activo')
    list_filter = ('categoria', 'compartido', 'activo')
    search_fields = ('descripcion',)


@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    list_display = ('numero_domicilio', 'tipo_domicilio', 'nombres', 'apellidos',
                    'cochera_monto', 'activo')
    list_editable = ('cochera_monto', 'activo')
    list_filter = ('tipo_domicilio', 'activo')
    search_fields = ('numero_domicilio', 'nombres', 'apellidos', 'dni')


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


class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0


@admin.register(Recibo)
class ReciboAdmin(admin.ModelAdmin):
    list_display = ('departamento', 'periodo', 'monto_agua', 'monto_area_comun',
                    'monto_mantenimiento', 'monto_cochera', 'total', 'estado')
    list_filter = ('estado', 'periodo')
    search_fields = ('departamento__numero_domicilio',)
    inlines = [PagoInline]


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('recibo', 'monto', 'fecha_pago', 'metodo')
    list_filter = ('fecha_pago',)
