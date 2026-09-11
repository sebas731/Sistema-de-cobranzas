from django.urls import path

from . import views

urlpatterns = [
    path('', views.balance, name='balance'),
    path('balance-mes/', views.balance_mes, name='balance_mes'),
    path('buscar/', views.buscar_departamento, name='buscar_departamento'),

    path('departamentos/', views.DepartamentoListView.as_view(), name='departamento_lista'),
    path('departamentos/nuevo/', views.DepartamentoCreateView.as_view(), name='departamento_nuevo'),
    path('departamentos/<int:pk>/', views.departamento_detalle, name='departamento_detalle'),
    path('departamentos/<int:pk>/editar/', views.DepartamentoUpdateView.as_view(), name='departamento_editar'),
    path('recibos/<int:pk>/boleta/', views.recibo_boleta, name='recibo_boleta'),

    path('gastos-fijos/', views.gastos_fijos, name='gastos_fijos'),
    path('gastos-fijos/<int:pk>/editar/', views.GastoFijoUpdateView.as_view(), name='gasto_fijo_editar'),
    path('gastos/', views.gastos_mes, name='gastos_mes'),
    path('lecturas/', views.lecturas_mes, name='lecturas_mes'),
    path('importar/', views.importar_excel, name='importar_excel'),
    path('importar/plantilla/', views.plantilla_excel, name='plantilla_excel'),
    path('recibos/generar/', views.generar_recibos, name='generar_recibos'),

    path('tarifas/', views.tarifas, name='tarifas'),
    path('tarifas/nueva/', views.TarifaCreateView.as_view(), name='tarifa_nueva'),
    path('tarifas/<int:pk>/', views.TarifaUpdateView.as_view(), name='tarifa_editar'),
]
