from django.urls import path

from ..views import carga_deudas as v

urlpatterns = [
    path('carga-inicial-deudas/', v.carga_deudas, name='carga_deudas'),
]
