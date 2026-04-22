from django.urls import path
from apps.detection import views

urlpatterns = [
    path('ingest/', views.ingest_plate, name='ingest_plate'),
    path('ingest-frame/', views.ingest_camera_frame, name='ingest_camera_frame'),
    path('preview/<str:camera_role>/', views.camera_preview, name='camera_preview'),
    path('frame/<str:camera_role>/', views.camera_frame, name='camera_frame'),
]
