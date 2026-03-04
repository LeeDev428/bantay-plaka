from django.contrib import admin
from apps.residents.models import Resident, Vehicle


@admin.register(Resident)
class ResidentAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'address', 'contact_number', 'created_at']
    search_fields = ['first_name', 'last_name', 'address']


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ['plate_number', 'resident', 'vehicle_type', 'make', 'model', 'color']
    search_fields = ['plate_number', 'resident__first_name', 'resident__last_name']
    list_filter = ['vehicle_type']
