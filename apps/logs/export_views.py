"""apps/logs/export_views.py — add to apps/logs/urls.py:
    from apps.logs import export_views
    path('export/excel/', export_views.export_logs_excel, name='logs_export_excel'),
    path('export/pdf/',   export_views.export_logs_pdf,   name='logs_export_pdf'),
"""
import datetime
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

from apps.logs.models import VehicleLog
from apps.logs.services import get_active_blacklist_map
from apps.export_helpers import build_excel_response, build_pdf_response


def _parse_range(request):
    date_from = request.GET.get('from', '').strip()
    date_to   = request.GET.get('to',   '').strip()
    tz = timezone.get_current_timezone()
    qs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')
    if date_from:
        try:
            d = datetime.date.fromisoformat(date_from)
            qs = qs.filter(timestamp__gte=timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), tz))
        except ValueError: date_from = ''
    if date_to:
        try:
            d = datetime.date.fromisoformat(date_to)
            qs = qs.filter(timestamp__lte=timezone.make_aware(datetime.datetime.combine(d, datetime.time.max), tz))
        except ValueError: date_to = ''
    return date_from, date_to, qs


def _rows(logs, bl_map):
    out = []
    for i, log in enumerate(logs, 1):
        bl = bl_map.get((log.plate_number or '').upper(), {})
        local_ts = timezone.localtime(log.timestamp)
        out.append([
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
    return out


@login_required
def export_logs_excel(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')
    date_from, date_to, qs = _parse_range(request)
    logs = list(qs)
    bl_map = get_active_blacklist_map([l.plate_number for l in logs])
    rows = _rows(logs, bl_map)
    return build_excel_response(
        filename_base='bantayplaka_vehicle_logs',
        sheet_title='Vehicle Logs',
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
    )


@login_required
def export_logs_pdf(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')
    date_from, date_to, qs = _parse_range(request)
    logs = list(qs)
    bl_map = get_active_blacklist_map([l.plate_number for l in logs])
    rows = _rows(logs, bl_map)
    return build_pdf_response(
        filename_base='bantayplaka_vehicle_logs',
        report_title='Bantay Plaka — Vehicle Log Report',
        headers=['Plate', 'Status', 'Type', 'Source', 'Name', 'Logged By', 'Timestamp'],
        col_widths_mm=[30, 20, 20, 18, 52, 34, 38],
        rows=[[r[1], r[2], r[3], r[4], r[6], r[9], r[10]] for r in rows],
        highlight_fn=lambda r: bool(bl_map.get((r[0] or '').upper())),
        mono_col_idx=0,
        date_from=date_from, date_to=date_to,
    )