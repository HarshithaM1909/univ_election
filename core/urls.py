# core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.voter_auth_view, name='voter_auth'),
]