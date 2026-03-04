from django.urls import path
from apps.accounts import views

urlpatterns = [
    path('', views.dashboard_redirect, name='dashboard'),
    # Admin
    path('admin/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/users/', views.user_management, name='user_management'),
    path('admin/users/create/', views.user_create, name='user_create'),
    path('admin/users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('admin/users/<int:pk>/toggle/', views.user_toggle_active, name='user_toggle_active'),
    # Guard
    path('guard/', views.guard_dashboard, name='guard_dashboard'),
]
