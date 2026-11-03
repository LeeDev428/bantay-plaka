import datetime

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.views import admin_required
from apps.audit.models import UserActivity


@admin_required
def activity_list(request):
    events = UserActivity.objects.select_related('actor')
    search_q = request.GET.get('q', '').strip()
    action_q = request.GET.get('action', '').strip()
    date_from_q = request.GET.get('date_from', '').strip()
    date_to_q = request.GET.get('date_to', '').strip()

    if search_q:
        events = events.filter(
            Q(actor_username__icontains=search_q)
            | Q(summary__icontains=search_q)
            | Q(target_type__icontains=search_q)
            | Q(target_id__icontains=search_q)
        )
    valid_actions = {value for value, _label in UserActivity.ACTION_CHOICES}
    if action_q in valid_actions:
        events = events.filter(action=action_q)
    else:
        action_q = ''

    tz = timezone.get_current_timezone()
    if date_from_q:
        try:
            day = datetime.date.fromisoformat(date_from_q)
            start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min), tz)
            events = events.filter(created_at__gte=start)
        except ValueError:
            date_from_q = ''
    if date_to_q:
        try:
            day = datetime.date.fromisoformat(date_to_q)
            end = timezone.make_aware(datetime.datetime.combine(
                day + datetime.timedelta(days=1),
                datetime.time.min,
            ), tz)
            events = events.filter(created_at__lt=end)
        except ValueError:
            date_to_q = ''

    page = Paginator(events, 25).get_page(request.GET.get('page', 1))
    return render(request, 'audit/activity_list.html', {
        'events': page,
        'search_q': search_q,
        'action_q': action_q,
        'date_from_q': date_from_q,
        'date_to_q': date_to_q,
        'action_choices': UserActivity.ACTION_CHOICES,
    })
