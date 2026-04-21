from django.urls import path
from apps.residents import views

urlpatterns = [
    path('', views.resident_list, name='resident_list'),
    path('create/', views.resident_create, name='resident_create'),
    path('<int:pk>/edit/', views.resident_edit, name='resident_edit'),
    path('<int:pk>/delete/', views.resident_delete, name='resident_delete'),
    path('<int:pk>/approve/', views.resident_approve, name='resident_approve'),
    path('<int:pk>/reject/', views.resident_reject, name='resident_reject'),
    path('<int:resident_pk>/vehicles/add/', views.vehicle_create, name='vehicle_create'),
    path('vehicles/<int:pk>/delete/', views.vehicle_delete, name='vehicle_delete'),
    path('self/vehicles/add/', views.resident_vehicle_create_self, name='resident_vehicle_create_self'),
]
