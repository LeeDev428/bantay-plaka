from django.contrib import admin
from apps.visitors.models import Visitor


@admin.register(Visitor)
class VisitorAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'plate_number', 'purpose', 'host_name', 'created_at']
    search_fields = ['first_name', 'last_name', 'plate_number']
