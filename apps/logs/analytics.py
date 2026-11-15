import datetime

from django.db.models import Count
from django.utils import timezone

from apps.logs.models import VehicleLog


def build_traffic_mix(start_date, end_date):
    """Count vehicle log entries by type and source for a local date range."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min), tz)
    end = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.min), tz)
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
