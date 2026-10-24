from django.urls import path

from ..views import deudores as v

urlpatterns = [
    path('deudores/', v.vista_deudores, name='deudores'),
]
