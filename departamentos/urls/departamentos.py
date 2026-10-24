from django.urls import path

from ..views import departamentos as v

urlpatterns = [
    path('departamentos/', v.DepartamentoListView.as_view(), name='departamento_lista'),
    path('departamentos/nuevo/', v.DepartamentoCreateView.as_view(), name='departamento_nuevo'),
    path('departamentos/<int:pk>/', v.departamento_detalle, name='departamento_detalle'),
    path('departamentos/<int:pk>/editar/', v.DepartamentoUpdateView.as_view(), name='departamento_editar'),
]
