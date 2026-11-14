from django.urls import path

from ..views import configuracion as v

urlpatterns = [
    path('configuracion/', v.configuracion, name='configuracion'),
]
