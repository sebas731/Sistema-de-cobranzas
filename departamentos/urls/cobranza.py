from django.urls import path

from ..views import cobranza as v

urlpatterns = [
    path('cobranza/', v.cobranza, name='cobranza'),
    path('cobranza/cobrar/', v.cobrar, name='cobrar'),
    path('pagos/registrar/', v.registrar_pagos, name='registrar_pagos'),
    path('pagos/', v.pagos_departamento, name='pagos_departamento'),
    path('cargos/asignar/', v.registrar_cargos, name='registrar_cargos'),
]
