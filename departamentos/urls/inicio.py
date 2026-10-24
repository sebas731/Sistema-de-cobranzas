from django.urls import path

from ..views import inicio as v

urlpatterns = [
    path('', v.inicio, name='inicio'),
    path('estilos/', v.estilos, name='estilos'),
    path('buscar/', v.buscar_departamento, name='buscar_departamento'),
]
