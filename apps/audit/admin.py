from django.contrib import admin

from apps.audit.models import UserActivity


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor_username', 'actor_role', 'action', 'summary')
    list_filter = ('action', 'actor_role', 'created_at')
    search_fields = ('actor_username', 'summary', 'target_type', 'target_id')
    readonly_fields = (
        'actor',
        'actor_username',
        'actor_role',
        'action',
        'summary',
        'target_type',
        'target_id',
        'details',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
