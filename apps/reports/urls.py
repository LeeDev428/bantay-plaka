from django.urls import path
from apps.reports import views

urlpatterns = [
    path('', views.report_dashboard, name='report_dashboard'),
    path('export/', views.export_csv, name='report_export'),
]
