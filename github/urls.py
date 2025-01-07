from django.urls import path
from . import views

urlpatterns = [
    path('request/', views.request.as_view(), name='request'),
]