from django.urls import path

from ..views import cobranza as v

urlpatterns = [
    path('pagos/registrar/', v.registrar_pagos, name='registrar_pagos'),
    path('cargos/asignar/', v.registrar_cargos, name='registrar_cargos'),
]
