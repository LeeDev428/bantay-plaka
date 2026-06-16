from django.urls import path
from apps.accounts import views
from apps.accounts import export_views

urlpatterns = [
    path('', views.dashboard_redirect, name='dashboard'),
    # Admin
    path('admin/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/users/', views.user_management, name='user_management'),
    path('admin/users/create/', views.user_create, name='user_create'),
    path('admin/users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('admin/users/<int:pk>/toggle/', views.user_toggle_active, name='user_toggle_active'),
    path('admin/users/<int:pk>/delete/', views.user_delete, name='user_delete'),
    # Exports — users
    path('admin/users/export/excel/', export_views.export_users_excel, name='users_export_excel'),
    path('admin/users/export/pdf/',   export_views.export_users_pdf,   name='users_export_pdf'),
    # Guard
    path('guard/', views.guard_dashboard, name='guard_dashboard'),
    # Resident
    path('resident/', views.resident_dashboard, name='resident_dashboard'),
    path('resident/vehicles/', views.resident_vehicles, name='resident_vehicles'),
    path('resident/logs/', views.resident_logs, name='resident_logs'),
    path('resident/profile/', views.resident_profile, name='resident_profile'),
]