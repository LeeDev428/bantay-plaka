from django.urls import path
from apps.logs import views
from apps.logs import export_views

urlpatterns = [
    path('manual/', views.manual_entry, name='manual_entry'),
    path('snapshots/', views.snapshot_gallery, name='snapshot_gallery'),
    path('', views.log_list, name='log_list'),
    path('<int:pk>/edit/', views.log_edit, name='log_edit'),
    path('<int:pk>/delete/', views.log_delete, name='log_delete'),
    # Exports
    path('export/excel/', export_views.export_logs_excel, name='logs_export_excel'),
    path('export/pdf/',   export_views.export_logs_pdf,   name='logs_export_pdf'),
]