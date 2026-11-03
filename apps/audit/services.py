from apps.audit.models import UserActivity


def record_user_activity(
    *,
    actor,
    action,
    summary,
    target_type='',
    target_id='',
    details=None,
):
    username = actor.get_username() if actor and actor.is_authenticated else 'System'
    role = ''
    if actor and actor.is_authenticated:
        role = actor.get_role_display() if hasattr(actor, 'get_role_display') else ''

    return UserActivity.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        actor_username=username,
        actor_role=role,
        action=action,
        summary=summary,
        target_type=target_type,
        target_id=str(target_id or ''),
        details=details or {},
    )


def audit_export(label):
    def decorator(view_func):
        from functools import wraps

        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            is_resident = getattr(request.user, 'is_resident', lambda: False)()
            if response.status_code < 400 and request.user.is_authenticated and not is_resident:
                record_user_activity(
                    actor=request.user,
                    action=UserActivity.ACTION_EXPORT,
                    summary=f'Exported {label}',
                    target_type='report',
                )
            return response

        return wrapped

    return decorator
