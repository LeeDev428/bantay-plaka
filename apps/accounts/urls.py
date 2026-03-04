from django.urls import path
from apps.accounts import views

urlpatterns = [
    path('', views.dashboard_redirect, name='home'),
    path('login/', views.BantayPlakaLoginView.as_view(), name='login'),
    path('logout/', views.BantayPlakaLogoutView.as_view(), name='logout'),
]
