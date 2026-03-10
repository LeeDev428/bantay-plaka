import csv
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Subquery, OuterRef
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.logs.models import VehicleLog


@login_required
def report_dashboard(request):
    today = timezone.localdate()
    today_logs = VehicleLog.objects.filter(timestamp__date=today)
    today_in = today_logs.filter(status=VehicleLog.STATUS_IN).count()
    today_out = today_logs.filter(status=VehicleLog.STATUS_OUT).count()
    today_unique = today_logs.values('plate_number').distinct().count()

    # Currently inside: plates whose most recent log is TIME_IN
    latest_status = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp')
        .values('status')[:1]
    )
    currently_inside = (
        VehicleLog.objects
        .values('plate_number')
        .distinct()
        .annotate(last_status=Subquery(latest_status))
        .filter(last_status=VehicleLog.STATUS_IN)
        .count()
    )

    # 7-day daily breakdown
    daily_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_qs = VehicleLog.objects.filter(timestamp__date=d)
        daily_data.append({
            'date': d.strftime('%Y-%m-%d'),
            'label': d.strftime('%a'),
            'time_in': day_qs.filter(status=VehicleLog.STATUS_IN).count(),
            'time_out': day_qs.filter(status=VehicleLog.STATUS_OUT).count(),
        })

    # Top vehicles this week
    week_start = today - timedelta(days=today.weekday())
    top_vehicles = (
        VehicleLog.objects
        .filter(timestamp__date__gte=week_start)
        .values('plate_number', 'entry_type')
        .annotate(visits=Count('id'))
        .order_by('-visits')[:10]
    )

    context = {
        'today': today,
        'today_in': today_in,
        'today_out': today_out,
        'today_unique': today_unique,
        'currently_inside': currently_inside,
        'daily_data': daily_data,
        'top_vehicles': top_vehicles,
    }
    return render(request, 'reports/dashboard.html', context)


@login_required
def export_csv(request):
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    logs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')

    if date_from:
        logs = logs.filter(timestamp__date__gte=date_from)
    if date_to:
        logs = logs.filter(timestamp__date__lte=date_to)

    response = HttpResponse(content_type='text/csv')
    filename = f'bantayplaka_logs_{timezone.localdate()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        'Plate Number', 'Entry Type', 'Status', 'Source',
        'Resident/Visitor Name', 'Logged By', 'Timestamp',
    ])

    for log in logs:
        writer.writerow([
            log.plate_number,
            log.entry_type,
            log.status,
            log.source,
            log.resident_name or log.visitor_name or '',
            log.logged_by.get_full_name() if log.logged_by else 'System',
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        ])

    return response
