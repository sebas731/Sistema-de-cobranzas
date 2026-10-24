from django.urls import path

from ..views import panel as v

urlpatterns = [
    path('panel/', v.panel, name='panel'),
    path('balance-mes/', v.balance_mes, name='balance_mes'),
]
