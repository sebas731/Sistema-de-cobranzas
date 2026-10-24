from django.urls import path

from ..views import proveedores as v

urlpatterns = [
    path('proveedores/', v.proveedores, name='proveedores'),
    path('proveedores/<int:pk>/editar/', v.ProveedorUpdateView.as_view(), name='proveedor_editar'),
]
