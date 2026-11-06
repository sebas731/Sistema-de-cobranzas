from django.urls import path

from ..views import caja as v

urlpatterns = [
    path('caja/', v.estado_caja_vista, name='estado_caja'),
    path('caja/cobrado-pagado/', v.cobrado_vs_pagado_vista, name='cobrado_vs_pagado'),
    path('caja/morosidad/', v.morosidad_vista, name='morosidad'),
]
