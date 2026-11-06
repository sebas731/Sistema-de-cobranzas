from django.urls import path

from ..views import caja as v

urlpatterns = [
    path('caja/', v.estado_caja_vista, name='estado_caja'),
]
