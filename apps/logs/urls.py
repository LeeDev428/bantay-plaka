from django.urls import path
from apps.logs import views
from apps.logs import export_views

urlpatterns = [
    path('manual/', views.manual_entry, name='manual_entry'),
    path('snapshots/', views.snapshot_gallery, name='snapshot_gallery'),
    path('snapshots/<int:pk>/archive/', views.snapshot_archive, name='snapshot_archive'),
    path('videos/', views.video_gallery, name='video_gallery'),
    path('videos/recordings/<int:pk>/archive/', views.video_archive, name='video_archive'),
    path('alerts/', views.alert_gallery, name='alert_gallery'),
    path('alerts/<int:pk>/archive/', views.alert_archive, name='alert_archive'),
    path('videos/<str:filename>/', views.recording_file, name='recording_file'),
    path('videos/<str:filename>/download/', views.recording_download, name='recording_download'),
    path('', views.log_list, name='log_list'),
    path('<int:pk>/edit/', views.log_edit, name='log_edit'),
    path('<int:pk>/delete/', views.log_delete, name='log_delete'),
    # Exports
    path('export/excel/', export_views.export_logs_excel, name='logs_export_excel'),
    path('export/pdf/',   export_views.export_logs_pdf,   name='logs_export_pdf'),
]