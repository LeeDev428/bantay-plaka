import datetime
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from apps.logs.models import VehicleLog


ACTIVITY_PERIOD_DAYS = {
    '1D': 1,
    '3D': 3,
    '7D': 7,
    '14D': 14,
    '30D': 30,
}
MAX_CUSTOM_ACTIVITY_RANGE = timedelta(days=3660)


def resolve_activity_period(period, activity_from='', activity_to='', now=None):
    """Return validated, timezone-aware bounds and display settings for a chart range."""
    tz = timezone.get_current_timezone()
    period = (period or '7D').strip().upper()
    period = {
        '1': '1D',
        '3': '3D',
        '7': '7D',
        '14': '14D',
        '30': '30D',
    }.get(period, period)

    if period == 'CUSTOM':
        if not activity_from or not activity_to:
            raise ValueError('Choose both a start and end date/time for a custom range.')
        try:
            start = datetime.datetime.fromisoformat(activity_from)
            end_inclusive = datetime.datetime.fromisoformat(activity_to)
        except ValueError as error:
            raise ValueError('Enter valid start and end date/time values.') from error
        if timezone.is_naive(start):
            start = timezone.make_aware(start, tz)
        else:
            start = timezone.localtime(start, tz)
        if timezone.is_naive(end_inclusive):
            end_inclusive = timezone.make_aware(end_inclusive, tz)
        else:
            end_inclusive = timezone.localtime(end_inclusive, tz)
        if start >= end_inclusive:
            raise ValueError('The start date/time must be before the end date/time.')
        if end_inclusive - start > MAX_CUSTOM_ACTIVITY_RANGE:
            raise ValueError('Custom date/time ranges cannot exceed 10 years.')
        try:
            end = end_inclusive + timedelta(minutes=1)
        except OverflowError as error:
            raise ValueError('Enter a valid custom date/time range.') from error
        duration = end - start
        comparison_duration = duration
        if duration <= timedelta(days=2):
            bucket = 'hour'
            label_format = '%I %p' if duration <= timedelta(days=1) else '%b %d %I %p'
        elif duration <= timedelta(days=62):
            bucket = 'day'
            label_format = '%b %d'
        else:
            bucket = 'month'
            label_format = '%b %Y'
        period_label = (
            f'{start:%b %d, %Y %I:%M %p} – '
            f'{end_inclusive:%b %d, %Y %I:%M %p}'
        )
    else:
        days = ACTIVITY_PERIOD_DAYS.get(period)
        if days is None:
            raise ValueError('Choose a valid activity chart period.')
        current_time = timezone.localtime(now or timezone.now(), tz)
        today = current_time.date()
        start_date = today - timedelta(days=days - 1)
        start = timezone.make_aware(
            datetime.datetime.combine(start_date, datetime.time.min),
            tz,
        )
        end = current_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
        duration = end - start
        comparison_duration = timedelta(days=days)
        bucket = 'hour' if days == 1 else 'day'
        label_format = '%I %p' if days == 1 else '%a' if days <= 7 else '%b %d'
        period_label = f'Last {days} day' + ('' if days == 1 else 's')
        end_inclusive = end - timedelta(minutes=1)
        activity_from = start.strftime('%Y-%m-%dT%H:%M')
        activity_to = end_inclusive.strftime('%Y-%m-%dT%H:%M')

    return {
        'chart_period_q': period,
        'activity_start': start,
        'activity_end': end,
        'activity_comparison_duration': comparison_duration,
        'activity_from_q': activity_from,
        'activity_to_q': activity_to,
        'activity_bucket': bucket,
        'activity_label_format': label_format,
        'activity_period_label': period_label,
    }


def format_activity_range(start, end):
    end_inclusive = end - timedelta(minutes=1)
    return f'{start:%b %d, %Y %I:%M %p} – {end_inclusive:%b %d, %Y %I:%M %p}'


def build_activity_chart_data(start, end, bucket, label_format):
    """Aggregate Time In and Time Out counts into local-time chart buckets."""
    tz = timezone.get_current_timezone()
    current = timezone.localtime(start, tz)
    if bucket == 'hour':
        current = current.replace(minute=0, second=0, microsecond=0)
    elif bucket == 'day':
        current = current.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        current = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    data = []
    while current < end:
        try:
            if bucket == 'hour':
                next_bucket = current + timedelta(hours=1)
            elif bucket == 'day':
                next_bucket = current + timedelta(days=1)
            else:
                year = current.year + (current.month == 12)
                month = 1 if current.month == 12 else current.month + 1
                next_bucket = timezone.make_aware(
                    datetime.datetime(year, month, 1),
                    tz,
                )
        except (OverflowError, ValueError):
            next_bucket = end

        bucket_start = max(current, start)
        bucket_end = min(next_bucket, end)
        counts = {
            VehicleLog.STATUS_IN: 0,
            VehicleLog.STATUS_OUT: 0,
        }
        for item in (
            VehicleLog.objects
            .filter(timestamp__gte=bucket_start, timestamp__lt=bucket_end)
            .values('status')
            .annotate(total=Count('pk'))
        ):
            counts[item['status']] = item['total']
        data.append({
            'label': current.strftime(label_format).lstrip('0'),
            'time_in': counts[VehicleLog.STATUS_IN],
            'time_out': counts[VehicleLog.STATUS_OUT],
        })
        current = next_bucket
    return data


def build_traffic_mix(start, end):
    """Count vehicle log entries by type and source for an aware datetime range."""
    logs = VehicleLog.objects.filter(timestamp__gte=start, timestamp__lt=end)

    def breakdown(field, labels, colors):
        counts = dict(
            logs.values(field)
            .annotate(total=Count('pk'))
            .values_list(field, 'total')
        )
        total = sum(counts.values())
        categories = [
            {
                'label': label,
                'count': counts.get(value, 0),
                'percentage': round(counts.get(value, 0) * 100 / total, 1) if total else 0,
                'color': colors[value],
            }
            for value, label in labels
        ]
        return categories, total

    traffic_categories, traffic_total = breakdown(
        'entry_type',
        [
            (VehicleLog.TYPE_RESIDENT, 'Residents'),
            (VehicleLog.TYPE_VISITOR, 'Visitors'),
            (VehicleLog.TYPE_UNKNOWN, 'Unknown'),
        ],
        {
            VehicleLog.TYPE_RESIDENT: '#3b82f6',
            VehicleLog.TYPE_VISITOR: '#8b5cf6',
            VehicleLog.TYPE_UNKNOWN: '#94a3b8',
        },
    )
    traffic_sources, traffic_source_total = breakdown(
        'source',
        [
            (VehicleLog.SOURCE_CAMERA, 'Camera'),
            (VehicleLog.SOURCE_MANUAL, 'Manual'),
        ],
        {
            VehicleLog.SOURCE_CAMERA: '#10b981',
            VehicleLog.SOURCE_MANUAL: '#f59e0b',
        },
    )
    return {
        'traffic_categories': traffic_categories,
        'traffic_total': traffic_total,
        'traffic_sources': traffic_sources,
        'traffic_source_total': traffic_source_total,
    }
