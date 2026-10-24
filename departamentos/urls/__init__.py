"""Rutas de la app, una lista por módulo de vistas (mismo nombre de archivo).
Los `name=` no cambian: las plantillas siguen usando {% url 'panel' %}, etc."""
from . import (
    agua, balance, cobranza, departamentos, deudores, gastos, importar, inicio,
    panel, proveedores, recibos,
)

urlpatterns = [
    *inicio.urlpatterns,
    *panel.urlpatterns,
    *balance.urlpatterns,
    *deudores.urlpatterns,
    *cobranza.urlpatterns,
    *departamentos.urlpatterns,
    *proveedores.urlpatterns,
    *recibos.urlpatterns,
    *gastos.urlpatterns,
    *agua.urlpatterns,
    *importar.urlpatterns,
]
