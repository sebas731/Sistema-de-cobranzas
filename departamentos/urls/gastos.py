from django.urls import path

from ..views import gastos as v

urlpatterns = [
    path('gastos-fijos/', v.gastos_fijos, name='gastos_fijos'),
    path('gastos-fijos/<int:pk>/editar/', v.GastoFijoUpdateView.as_view(), name='gasto_fijo_editar'),
    path('gastos/', v.gastos_mes, name='gastos_mes'),
]
