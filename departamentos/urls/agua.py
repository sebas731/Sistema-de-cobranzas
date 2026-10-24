from django.urls import path

from ..views import agua as v

urlpatterns = [
    path('lecturas/', v.lecturas_mes, name='lecturas_mes'),
    path('facturas-agua/', v.facturas_agua, name='facturas_agua'),
    path('tarifas/', v.tarifas, name='tarifas'),
    path('tarifas/nueva/', v.TarifaCreateView.as_view(), name='tarifa_nueva'),
    path('tarifas/<int:pk>/', v.TarifaUpdateView.as_view(), name='tarifa_editar'),
]
