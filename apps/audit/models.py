from django.conf import settings
from django.db import models


class UserActivity(models.Model):
    ACTION_LOGIN = 'LOGIN'
    ACTION_LOGOUT = 'LOGOUT'
    ACTION_CREATE = 'CREATE'
    ACTION_UPDATE = 'UPDATE'
    ACTION_ARCHIVE = 'ARCHIVE'
    ACTION_APPROVE = 'APPROVE'
    ACTION_REJECT = 'REJECT'
    ACTION_ACTIVATE = 'ACTIVATE'
    ACTION_DEACTIVATE = 'DEACTIVATE'
    ACTION_EXPORT = 'EXPORT'
    ACTION_CHOICES = [
        (ACTION_LOGIN, 'Login'),
        (ACTION_LOGOUT, 'Logout'),
        (ACTION_CREATE, 'Create'),
        (ACTION_UPDATE, 'Update'),
        (ACTION_ARCHIVE, 'Archive'),
        (ACTION_APPROVE, 'Approve'),
        (ACTION_REJECT, 'Reject'),
        (ACTION_ACTIVATE, 'Activate'),
        (ACTION_DEACTIVATE, 'Deactivate'),
        (ACTION_EXPORT, 'Export'),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_events',
    )
    actor_username = models.CharField(max_length=150, db_index=True)
    actor_role = models.CharField(max_length=32, blank=True)
    action = models.CharField(max_length=16, choices=ACTION_CHOICES, db_index=True)
    summary = models.CharField(max_length=500)
    target_type = models.CharField(max_length=64, blank=True, db_index=True)
    target_id = models.CharField(max_length=64, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'user_activity'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', 'created_at']),
        ]

    def __str__(self):
        return f'{self.actor_username}: {self.get_action_display()} — {self.summary}'
