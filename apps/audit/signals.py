from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from apps.audit.models import UserActivity
from apps.audit.services import record_user_activity


@receiver(user_logged_in, dispatch_uid='audit_user_logged_in')
def record_login(sender, request, user, **kwargs):
    record_user_activity(
        actor=user,
        action=UserActivity.ACTION_LOGIN,
        summary='User logged in',
        target_type='account',
        target_id=user.pk,
    )


@receiver(user_logged_out, dispatch_uid='audit_user_logged_out')
def record_logout(sender, request, user, **kwargs):
    if user is None:
        return
    record_user_activity(
        actor=user,
        action=UserActivity.ACTION_LOGOUT,
        summary='User logged out',
        target_type='account',
        target_id=user.pk,
    )
