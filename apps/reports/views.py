import csv
import datetime
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Subquery, OuterRef
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.contrib import messages
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone

from apps.audit.services import audit_export
from apps.export_helpers import build_export_chart_data
from apps.logs.analytics import (
    build_activity_chart_data,
    build_traffic_mix,
    format_activity_range,
    resolve_activity_period,
)
from apps.logs.models import VehicleLog
from apps.logs.services import get_active_blacklist_map


def _day_range(date):
    """Return (start_utc, end_utc) for a local date, avoiding MySQL CONVERT_TZ dependency."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime.combine(date, datetime.time.min), tz)
    end = start + timedelta(days=1)
    return start, end


@login_required
def report_dashboard(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    today = timezone.localdate()
    today_start, today_end = _day_range(today)
    today_logs = VehicleLog.objects.filter(timestamp__gte=today_start, timestamp__lt=today_end)
    today_in = today_logs.filter(status=VehicleLog.STATUS_IN).count()
    today_out = today_logs.filter(status=VehicleLog.STATUS_OUT).count()
    today_unique = today_logs.values('plate_number').distinct().count()

    # Visitors currently inside: latest log is TIME_IN and latest type is VISITOR.
    latest_status = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp', '-pk')
        .values('status')[:1]
    )
    latest_type = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp', '-pk')
        .values('entry_type')[:1]
    )
    latest_timestamp = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp', '-pk')
        .values('timestamp')[:1]
    )
    latest_visitor_name = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp', '-pk')
        .values('visitor_name')[:1]
    )
    currently_inside_items = list(
        VehicleLog.objects
        .values('plate_number')
        .distinct()
        .annotate(last_status=Subquery(latest_status))
        .annotate(last_type=Subquery(latest_type))
        .annotate(last_timestamp=Subquery(latest_timestamp))
        .annotate(last_visitor_name=Subquery(latest_visitor_name))
        .filter(last_status=VehicleLog.STATUS_IN, last_type=VehicleLog.TYPE_VISITOR)
        .order_by('last_timestamp', 'plate_number')
    )
    now = timezone.now()
    overstay_threshold = timedelta(hours=12)
    overstay_count = 0
    for item in currently_inside_items:
        elapsed = max(now - item['last_timestamp'], timedelta())
        total_minutes = int(elapsed.total_seconds() // 60)
        days, remaining_minutes = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(remaining_minutes, 60)
        item['visitor_name'] = item['last_visitor_name'] or '—'
        item['duration_label'] = (
            f'{days}d {hours}h' if days else
            f'{hours}h {minutes}m' if hours else
            f'{minutes}m'
        )
        item['is_overstay'] = elapsed >= overstay_threshold
        overstay_count += item['is_overstay']
    currently_inside = len(currently_inside_items)

    try:
        activity_period = resolve_activity_period(
            request.GET.get('chart_period', '7D'),
            request.GET.get('activity_from', ''),
            request.GET.get('activity_to', ''),
        )
    except ValueError as error:
        messages.error(request, str(error))
        activity_period = resolve_activity_period('7D')

    activity_start = activity_period['activity_start']
    activity_end = activity_period['activity_end']
    daily_data = build_activity_chart_data(
        activity_start,
        activity_end,
        activity_period['activity_bucket'],
        activity_period['activity_label_format'],
    )
    compare_previous_period = request.GET.get('compare_period') == '1'
    comparison_data = []
    comparison_period_label = ''
    if compare_previous_period:
        if activity_period['chart_period_q'] == '1D':
            previous_start = activity_start - timedelta(days=1)
            previous_end = previous_start + (activity_end - activity_start)
        else:
            previous_end = activity_start
            previous_start = previous_end - activity_period['activity_comparison_duration']
        comparison_data = build_activity_chart_data(
            previous_start,
            previous_end,
            activity_period['activity_bucket'],
            activity_period['activity_label_format'],
        )
        comparison_period_label = format_activity_range(previous_start, previous_end)

    traffic_mix = build_traffic_mix(activity_start, activity_end)

    # Top vehicles with filters and pagination
    top_window_q = request.GET.get('top_window', 'WEEK').upper()
    top_entry_type_q = request.GET.get('top_entry_type', 'ALL').upper()
    if top_window_q == 'MONTH':
        period_start = today - timedelta(days=30)
    else:
        top_window_q = 'WEEK'
        period_start = today - timedelta(days=today.weekday())
    period_start_dt, _ = _day_range(period_start)

    top_vehicles_qs = (
        VehicleLog.objects
        .filter(timestamp__gte=period_start_dt)
        .values('plate_number', 'entry_type')
        .annotate(visits=Count('id'))
        .order_by('-visits', 'plate_number')
    )
    if top_entry_type_q in {VehicleLog.TYPE_RESIDENT, VehicleLog.TYPE_VISITOR}:
        top_vehicles_qs = top_vehicles_qs.filter(entry_type=top_entry_type_q)
    else:
        top_entry_type_q = 'ALL'

    top_paginator = Paginator(top_vehicles_qs, 8)
    top_page_number = request.GET.get('top_page', 1)
    top_vehicles = top_paginator.get_page(top_page_number)
    top_rank_offset = (top_vehicles.number - 1) * top_paginator.per_page

    context = {
        'today': today,
        'today_iso': today.isoformat(),
        'today_in': today_in,
        'today_out': today_out,
        'today_unique': today_unique,
        'currently_inside': currently_inside,
        'currently_inside_items': currently_inside_items,
        'overstay_count': overstay_count,
        'overstay_threshold_hours': int(overstay_threshold.total_seconds() // 3600),
        'daily_data': daily_data,
        'comparison_data': comparison_data,
        'compare_previous_period': compare_previous_period,
        'comparison_period_label': comparison_period_label,
        'show_comparison_toggle': True,
        'top_vehicles': top_vehicles,
        'top_rank_offset': top_rank_offset,
        'top_window_q': top_window_q,
        'top_entry_type_q': top_entry_type_q,
    }
    context.update(activity_period)
    context['traffic_period_label'] = activity_period['activity_period_label']
    context.update(traffic_mix)
    return render(request, 'reports/dashboard.html', context)


@login_required
@audit_export('vehicle log report (CSV)')
def export_csv(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    logs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')

    tz = timezone.get_current_timezone()
    if date_from:
        try:
            dt_from = datetime.date.fromisoformat(date_from)
            start, _ = _day_range(dt_from)
            logs = logs.filter(timestamp__gte=start)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.date.fromisoformat(date_to)
            _, end = _day_range(dt_to)
            logs = logs.filter(timestamp__lt=end)
        except ValueError:
            pass

    response = HttpResponse(content_type='text/csv')
    filename = f'bantayplaka_logs_{timezone.localdate()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        'Plate Number', 'Camera Role', 'Entry Type', 'Status', 'Source',
        'Resident/Visitor Name', 'Blacklist Tag', 'Blacklist Remarks', 'Logged By', 'Timestamp (Asia/Manila)',
    ])

    bl_map = get_active_blacklist_map([log.plate_number for log in logs])

    for log in logs:
        local_ts = timezone.localtime(log.timestamp)
        bl_info = bl_map.get((log.plate_number or '').upper(), {})
        writer.writerow([
            log.plate_number,
            log.camera_role,
            log.entry_type,
            log.status,
            log.source,
            log.resident_name or log.visitor_name or '',
            bl_info.get('tag', ''),
            bl_info.get('remarks', ''),
            log.logged_by.get_full_name() if log.logged_by else 'System',
            local_ts.strftime('%Y-%m-%d %H:%M:%S'),
        ])

    return response


@login_required
@audit_export('vehicle log report (PDF)')
def export_pdf(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    from apps.export_helpers import build_pdf_response

    date_from = request.GET.get('from', '')
    date_to   = request.GET.get('to',   '')
    logs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')
    if date_from:
        try:
            start, _ = _day_range(datetime.date.fromisoformat(date_from))
            logs = logs.filter(timestamp__gte=start)
        except ValueError: date_from = ''
    if date_to:
        try:
            _, end = _day_range(datetime.date.fromisoformat(date_to))
            logs = logs.filter(timestamp__lt=end)
        except ValueError: date_to = ''

    bl_map = get_active_blacklist_map([l.plate_number for l in logs])
    rows = []
    for log in logs:
        local_ts = timezone.localtime(log.timestamp)
        rows.append([
            log.plate_number or '',
            log.get_entry_type_display() if hasattr(log, 'get_entry_type_display') else log.entry_type,
            'Time In' if log.status == VehicleLog.STATUS_IN else 'Time Out',
            log.get_source_display() if hasattr(log, 'get_source_display') else log.source,
            log.resident_name or log.visitor_name or '',
            log.logged_by.get_full_name() if log.logged_by else 'System',
            local_ts.strftime('%Y-%m-%d %H:%M:%S'),
        ])

    return build_pdf_response(
        'bantayplaka_logs',
        'Bantay Plaka — Vehicle Log Report',
        headers=['Plate', 'Type', 'Status', 'Source', 'Name', 'Logged By', 'Timestamp'],
        col_widths_mm=[30, 20, 20, 18, 50, 34, 38],
        rows=rows,
        highlight_fn=lambda r: bool(bl_map.get((r[0] or '').upper())),
        mono_col_idx=0,
        date_from=date_from, date_to=date_to,
        chart_data=build_export_chart_data(rows, 2, 6, 'Vehicle Logs by Status'),
    )


@login_required
@audit_export('vehicle log report (Excel)')
def export_excel(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    from apps.export_helpers import build_excel_response

    date_from = request.GET.get('from', '')
    date_to   = request.GET.get('to',   '')
    logs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')
    if date_from:
        try:
            start, _ = _day_range(datetime.date.fromisoformat(date_from))
            logs = logs.filter(timestamp__gte=start)
        except ValueError: date_from = ''
    if date_to:
        try:
            _, end = _day_range(datetime.date.fromisoformat(date_to))
            logs = logs.filter(timestamp__lt=end)
        except ValueError: date_to = ''

    bl_map = get_active_blacklist_map([l.plate_number for l in logs])
    rows = []
    for i, log in enumerate(logs, 1):
        bl = bl_map.get((log.plate_number or '').upper(), {})
        local_ts = timezone.localtime(log.timestamp)
        rows.append([
            i,
            log.plate_number or '',
            'Time In' if log.status == VehicleLog.STATUS_IN else 'Time Out',
            log.get_entry_type_display() if hasattr(log, 'get_entry_type_display') else log.entry_type,
            log.get_source_display()     if hasattr(log, 'get_source_display')     else log.source,
            log.get_camera_role_display()if hasattr(log, 'get_camera_role_display')else (log.camera_role or ''),
            log.resident_name or log.visitor_name or '',
            bl.get('tag_display', bl.get('tag', '')),
            bl.get('remarks', ''),
            log.logged_by.get_full_name() if log.logged_by else 'System',
            local_ts.strftime('%Y-%m-%d %H:%M:%S'),
        ])

    return build_excel_response(
        'bantayplaka_logs',
        'Vehicle Logs',
        headers=['#', 'Plate', 'Status', 'Type', 'Source', 'Camera', 'Name', 'BL Tag', 'BL Remarks', 'Logged By', 'Timestamp'],
        col_widths=[5, 14, 11, 12, 10, 14, 26, 14, 28, 20, 20],
        rows=rows,
        highlight_fn=lambda r: bool(r[7]),
        mono_cols={2}, center_cols={1, 3, 4, 5, 6, 8, 11},
        summary_rows=[
            ('Total Records',    len(rows)),
            ('Time In',          sum(1 for r in rows if r[2] == 'Time In')),
            ('Time Out',         sum(1 for r in rows if r[2] == 'Time Out')),
            ('Resident Entries', sum(1 for r in rows if r[3] == 'Resident')),
            ('Visitor Entries',  sum(1 for r in rows if r[3] == 'Visitor')),
            ('Blacklisted Hits', sum(1 for r in rows if r[7])),
        ],
        date_from=date_from, date_to=date_to,
        chart_data=build_export_chart_data(rows, 2, 10, 'Vehicle Logs by Status'),
    )


@login_required
@audit_export('visitors inside report (CSV)')
def export_visitors_inside(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    latest_status = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp')
        .values('status')[:1]
    )
    latest_type = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp')
        .values('entry_type')[:1]
    )
    latest_ts = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp')
        .values('timestamp')[:1]
    )
    latest_name = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp')
        .values('visitor_name')[:1]
    )

    inside_plates = (
        VehicleLog.objects
        .values('plate_number')
        .distinct()
        .annotate(last_status=Subquery(latest_status))
        .annotate(last_type=Subquery(latest_type))
        .annotate(last_ts=Subquery(latest_ts))
        .annotate(last_name=Subquery(latest_name))
        .filter(last_status=VehicleLog.STATUS_IN, last_type=VehicleLog.TYPE_VISITOR)
        .order_by('plate_number')
    )

    tz = timezone.get_current_timezone()
    response = HttpResponse(content_type='text/csv')
    filename = f'bantayplaka_visitors_inside_{timezone.localdate()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(['Plate Number', 'Visitor Name', 'Entry Time (Asia/Manila)'])
    for item in inside_plates:
        local_ts = timezone.localtime(item['last_ts'], tz).strftime('%Y-%m-%d %H:%M:%S') if item['last_ts'] else ''
        writer.writerow([item['plate_number'], item['last_name'] or '', local_ts])

    return response