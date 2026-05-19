from django.urls import path
from apps.reports import views

urlpatterns = [
    path('', views.report_dashboard, name='report_dashboard'),
    path('export/', views.export_csv, name='report_export'),
    path('export/pdf/', views.export_pdf, name='report_export_pdf'),
    path('export/excel/', views.export_excel, name='report_export_excel'),
    path('export/visitors-inside/', views.export_visitors_inside, name='report_export_visitors_inside'),
]