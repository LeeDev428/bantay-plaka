from django.contrib import admin
from apps.logs.models import VehicleLog, CameraFeedSnapshot


@admin.register(VehicleLog)
class VehicleLogAdmin(admin.ModelAdmin):
    list_display = ['plate_number', 'camera_role', 'entry_type', 'status', 'source', 'timestamp', 'logged_by']
    list_filter = ['camera_role', 'entry_type', 'status', 'source']
    search_fields = ['plate_number', 'resident_name', 'visitor_name']
    readonly_fields = ['timestamp']


@admin.register(CameraFeedSnapshot)
class CameraFeedSnapshotAdmin(admin.ModelAdmin):
    list_display = ['camera_role', 'updated_at']
    list_filter = ['camera_role']
    readonly_fields = ['updated_at']
