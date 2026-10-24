from django.urls import path

from ..views import recibos as v

urlpatterns = [
    path('departamentos/<int:pk>/boleta/', v.recibo_boleta, name='recibo_boleta'),
    path('recibos/<int:pk>/', v.recibo_ver, name='recibo_ver'),
    path('recibos/generar/', v.generar_recibos, name='generar_recibos'),
]
