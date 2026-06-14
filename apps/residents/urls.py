from django.urls import path
from apps.residents import views
from apps.residents import export_views

urlpatterns = [
    path('', views.resident_list, name='resident_list'),
    path('create/', views.resident_create, name='resident_create'),
    path('<int:pk>/edit/', views.resident_edit, name='resident_edit'),
    path('<int:pk>/delete/', views.resident_delete, name='resident_delete'),
    path('<int:pk>/approve/', views.resident_approve, name='resident_approve'),
    path('<int:pk>/reject/', views.resident_reject, name='resident_reject'),
    path('<int:resident_pk>/vehicles/add/', views.vehicle_create, name='vehicle_create'),
    path('self/vehicles/add/', views.resident_vehicle_create_self, name='resident_vehicle_create_self'),
    # Vehicles
    path('vehicles/approvals/', views.vehicle_approval_list, name='vehicle_approval_list'),
    path('vehicles/<int:pk>/delete/', views.vehicle_delete, name='vehicle_delete'),
    path('vehicles/<int:pk>/documents/', views.vehicle_document_list, name='vehicle_document_list'),
    path('vehicles/<int:pk>/documents/upload/', views.vehicle_document_upload, name='vehicle_document_upload'),
    path('vehicles/documents/', views.vehicle_document_gallery, name='vehicle_document_gallery'),
    path('vehicles/documents/<int:pk>/status/', views.vehicle_document_update_status, name='vehicle_document_update_status'),
    path('self/vehicles/<int:pk>/documents/upload/', views.resident_vehicle_document_upload, name='resident_vehicle_document_upload'),
    path('vehicles/<int:pk>/approve/', views.vehicle_approve, name='vehicle_approve'),
    path('vehicles/<int:pk>/reject/', views.vehicle_reject, name='vehicle_reject'),
    # Exports — residents
    path('export/excel/', export_views.export_residents_excel, name='residents_export_excel'),
    path('export/pdf/',   export_views.export_residents_pdf,   name='residents_export_pdf'),
    # Exports — all vehicles
    path('vehicles/export/excel/', export_views.export_vehicles_excel, name='vehicles_export_excel'),
    path('vehicles/export/pdf/',   export_views.export_vehicles_pdf,   name='vehicles_export_pdf'),
    # Exports — pending approvals
    path('vehicles/approvals/export/excel/', export_views.export_approvals_excel, name='approvals_export_excel'),
    path('vehicles/approvals/export/pdf/',   export_views.export_approvals_pdf,   name='approvals_export_pdf'),
]