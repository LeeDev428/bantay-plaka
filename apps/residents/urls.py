from django.urls import path
from apps.residents import views

urlpatterns = [
    path('', views.resident_list, name='resident_list'),
    path('vehicles/approvals/', views.vehicle_approval_list, name='vehicle_approval_list'),
    path('create/', views.resident_create, name='resident_create'),
    path('<int:pk>/edit/', views.resident_edit, name='resident_edit'),
    path('<int:pk>/delete/', views.resident_delete, name='resident_delete'),
    path('<int:pk>/approve/', views.resident_approve, name='resident_approve'),
    path('<int:pk>/reject/', views.resident_reject, name='resident_reject'),
    path('<int:resident_pk>/vehicles/add/', views.vehicle_create, name='vehicle_create'),
    path('vehicles/<int:pk>/delete/', views.vehicle_delete, name='vehicle_delete'),
    path('vehicles/<int:pk>/approve/', views.vehicle_approve, name='vehicle_approve'),
    path('vehicles/<int:pk>/reject/', views.vehicle_reject, name='vehicle_reject'),
    path('self/vehicles/add/', views.resident_vehicle_create_self, name='resident_vehicle_create_self'),
]
