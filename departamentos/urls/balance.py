from django.urls import path

from ..views import balance as v

urlpatterns = [
    path('balance/', v.balance, name='balance'),
]
