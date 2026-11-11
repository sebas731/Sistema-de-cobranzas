from django.urls import path

from ..views import importar as v

urlpatterns = [
    path('importar/', v.importar_excel, name='importar_excel'),
    path('importar/plantilla/', v.plantilla_excel, name='plantilla_excel'),
    path('importar/deudas/', v.importar_deudas, name='importar_deudas'),
    path('importar/deudas/plantilla/', v.plantilla_deudas, name='plantilla_deudas'),
]
