from django.urls import path
from apps.visitors import views

urlpatterns = [
    path('', views.visitor_list, name='visitor_list'),
    path('log/', views.visitor_log_entry, name='visitor_log_entry'),
    path('export/excel/', views.visitor_export_excel, name='visitor_export_excel'),
    path('export/pdf/', views.visitor_export_pdf, name='visitor_export_pdf'),
    path('<int:pk>/edit/', views.visitor_edit, name='visitor_edit'),
    path('<int:pk>/delete/', views.visitor_delete, name='visitor_delete'),
    path('blacklist/', views.blacklist_list, name='blacklist_list'),
    path('blacklist/export/excel/', views.blacklist_export_excel, name='blacklist_export_excel'),
    path('blacklist/export/pdf/', views.blacklist_export_pdf, name='blacklist_export_pdf'),
    path('blacklist/<int:pk>/toggle/', views.blacklist_toggle, name='blacklist_toggle'),
    path('blacklist/<int:pk>/edit/', views.blacklist_edit, name='blacklist_edit'),
    path('blacklist/<int:pk>/cancel/', views.blacklist_cancel, name='blacklist_cancel'),
]
