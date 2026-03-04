from django.urls import path
from apps.detection import views

urlpatterns = [
    path('ingest/', views.ingest_plate, name='ingest_plate'),
]
