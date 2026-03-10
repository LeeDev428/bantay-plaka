from django.urls import path
from apps.logs import views

urlpatterns = [
    path('manual/', views.manual_entry, name='manual_entry'),
    path('', views.log_list, name='log_list'),
    path('<int:pk>/edit/', views.log_edit, name='log_edit'),
    path('<int:pk>/delete/', views.log_delete, name='log_delete'),
]
