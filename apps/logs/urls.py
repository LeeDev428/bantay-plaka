from django.urls import path
from apps.logs import views

urlpatterns = [
    path('manual/', views.manual_entry, name='manual_entry'),
    path('', views.log_list, name='log_list'),
]
