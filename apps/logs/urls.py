from django.urls import path
from apps.logs import views
from apps.logs import export_views

urlpatterns = [
    path('manual/', views.manual_entry, name='manual_entry'),
    path('snapshots/', views.snapshot_gallery, name='snapshot_gallery'),
    path('videos/', views.video_gallery, name='video_gallery'),
    path('alerts/', views.alert_gallery, name='alert_gallery'),
    path('videos/<str:filename>/', views.recording_file, name='recording_file'),
    path('', views.log_list, name='log_list'),
    path('<int:pk>/edit/', views.log_edit, name='log_edit'),
    path('<int:pk>/delete/', views.log_delete, name='log_delete'),
    # Exports
    path('export/excel/', export_views.export_logs_excel, name='logs_export_excel'),
    path('export/pdf/',   export_views.export_logs_pdf,   name='logs_export_pdf'),
]