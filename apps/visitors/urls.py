from django.urls import path
from apps.visitors import views

urlpatterns = [
    path('', views.visitor_list, name='visitor_list'),
    path('log/', views.visitor_log_entry, name='visitor_log_entry'),
    path('blacklist/', views.blacklist_list, name='blacklist_list'),
    path('blacklist/<int:pk>/toggle/', views.blacklist_toggle, name='blacklist_toggle'),
]
