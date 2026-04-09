from django.contrib import admin
from apps.visitors.models import Visitor, BlacklistEntry


@admin.register(Visitor)
class VisitorAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'plate_number', 'purpose', 'host_name', 'created_at']
    search_fields = ['first_name', 'last_name', 'plate_number']


@admin.register(BlacklistEntry)
class BlacklistEntryAdmin(admin.ModelAdmin):
    list_display = ['plate_number', 'tag', 'is_active', 'created_by', 'updated_at']
    list_filter = ['tag', 'is_active']
    search_fields = ['plate_number', 'reason', 'remarks']
