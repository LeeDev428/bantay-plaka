from django.urls import path
from django.contrib.auth import views as auth_views
from apps.accounts import views

urlpatterns = [
    path('', views.dashboard_redirect, name='home'),
    path('login/', views.BantayPlakaLoginView.as_view(), name='login'),
    path('resident/register/', views.resident_register, name='resident_register'),
    path('password-reset/', auth_views.PasswordResetView.as_view(template_name='registration/password_reset_form.html'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'), name='password_reset_complete'),
    path('logout/', views.BantayPlakaLogoutView.as_view(), name='logout'),
]
