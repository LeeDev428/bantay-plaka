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
        .order_by('-timestamp')
        .values('status')[:1]
    )
    latest_type = (
        VehicleLog.objects
        .filter(plate_number=OuterRef('plate_number'))
        .order_by('-timestamp')
        .values('entry_type')[:1]
    )
    currently_inside = (
        VehicleLog.objects
        .values('plate_number')
        .distinct()
        .annotate(last_status=Subquery(latest_status))
        .annotate(last_type=Subquery(latest_type))
        .filter(last_status=VehicleLog.STATUS_IN, last_type=VehicleLog.TYPE_VISITOR)
        .count()
    )
    currently_inside_items = list(
        VehicleLog.objects
        .values('plate_number')
        .distinct()
        .annotate(last_status=Subquery(latest_status))
        .annotate(last_type=Subquery(latest_type))
        .filter(last_status=VehicleLog.STATUS_IN, last_type=VehicleLog.TYPE_VISITOR)
        .order_by('plate_number')
    )

    # 7-day daily breakdown — use UTC ranges so no CONVERT_TZ needed
    daily_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        d_start, d_end = _day_range(d)
        day_qs = VehicleLog.objects.filter(timestamp__gte=d_start, timestamp__lt=d_end)
        daily_data.append({
            'date': d.strftime('%Y-%m-%d'),
            'label': d.strftime('%a'),
            'time_in': day_qs.filter(status=VehicleLog.STATUS_IN).count(),
            'time_out': day_qs.filter(status=VehicleLog.STATUS_OUT).count(),
        })

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
        'daily_data': daily_data,
        'top_vehicles': top_vehicles,
        'top_rank_offset': top_rank_offset,
        'top_window_q': top_window_q,
        'top_entry_type_q': top_entry_type_q,
    }
    return render(request, 'reports/dashboard.html', context)


@login_required
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
def export_pdf(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        return HttpResponse('reportlab is not installed. Run: pip install reportlab', status=500)

    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    logs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')

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

    response = HttpResponse(content_type='application/pdf')
    filename = f'bantayplaka_logs_{timezone.localdate()}.pdf'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(response, pagesize=landscape(A4), leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph('Bantay Plaka — Vehicle Log Report', styles['Title']))
    elements.append(Paragraph(f'Generated: {timezone.localdate()}', styles['Normal']))
    elements.append(Spacer(1, 6*mm))

    headers = ['Plate', 'Type', 'Status', 'Source', 'Name', 'Timestamp']
    data = [headers]

    bl_map = get_active_blacklist_map([log.plate_number for log in logs])

    for log in logs:
        local_ts = timezone.localtime(log.timestamp)
        name = log.resident_name or log.visitor_name or ''
        data.append([
            log.plate_number or '',
            log.entry_type,
            log.status,
            log.source,
            name,
            local_ts.strftime('%Y-%m-%d %H:%M'),
        ])

    col_widths = [35*mm, 25*mm, 22*mm, 22*mm, 60*mm, 40*mm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f1f5f9')]),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(table)
    doc.build(elements)
    return response


@login_required
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
