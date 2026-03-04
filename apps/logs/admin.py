from django.contrib import admin
from apps.logs.models import VehicleLog


@admin.register(VehicleLog)
class VehicleLogAdmin(admin.ModelAdmin):
    list_display = ['plate_number', 'entry_type', 'status', 'source', 'timestamp', 'logged_by']
    list_filter = ['entry_type', 'status', 'source']
    search_fields = ['plate_number', 'resident_name', 'visitor_name']
    readonly_fields = ['timestamp']
