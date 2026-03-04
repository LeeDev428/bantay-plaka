from django.urls import path
from apps.visitors import views

urlpatterns = [
    path('', views.visitor_list, name='visitor_list'),
    path('log/', views.visitor_log_entry, name='visitor_log_entry'),
]
