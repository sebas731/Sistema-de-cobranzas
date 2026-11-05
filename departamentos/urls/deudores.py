from django.urls import path

from ..views import deudores as v

urlpatterns = [
    path('deudores/', v.vista_deudores, name='deudores'),
    path('deudas-actuales/', v.deudas_actuales, name='deudas_actuales'),
]
