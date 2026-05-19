"""
Export views for Vehicle Logs (Excel + PDF).
Wire into apps/logs/urls.py:
    path('export/excel/', views.export_logs_excel, name='logs_export_excel'),
    path('export/pdf/',   views.export_logs_pdf,   name='logs_export_pdf'),
"""
import datetime

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.db.models import Q

from apps.logs.models import VehicleLog
from apps.logs.services import get_active_blacklist_map


def _parse_date_range(request):
    """Return (date_from_str, date_to_str, filtered_qs)."""
    date_from = request.GET.get('from', '').strip()
    date_to   = request.GET.get('to',   '').strip()

    tz = timezone.get_current_timezone()
    logs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')

    if date_from:
        try:
            d = datetime.date.fromisoformat(date_from)
            start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
            logs = logs.filter(timestamp__gte=start)
        except ValueError:
            date_from = ''
    if date_to:
        try:
            d = datetime.date.fromisoformat(date_to)
            end = timezone.make_aware(datetime.datetime.combine(d, datetime.time.max), tz)
            logs = logs.filter(timestamp__lte=end)
        except ValueError:
            date_to = ''

    return date_from, date_to, logs


def _build_rows(logs, bl_map):
    rows = []
    for i, log in enumerate(logs, 1):
        local_ts = timezone.localtime(log.timestamp)
        bl = bl_map.get((log.plate_number or '').upper(), {})
        rows.append([
            i,
            log.plate_number or '',
            'Time In' if log.status == VehicleLog.STATUS_IN else 'Time Out',
            log.get_entry_type_display() if hasattr(log, 'get_entry_type_display') else log.entry_type,
            log.get_source_display() if hasattr(log, 'get_source_display') else log.source,
            log.get_camera_role_display() if hasattr(log, 'get_camera_role_display') else log.camera_role,
            log.resident_name or log.visitor_name or '',
            bl.get('tag', ''),
            bl.get('remarks', ''),
            log.logged_by.get_full_name() if log.logged_by else 'System',
            local_ts.strftime('%Y-%m-%d %H:%M:%S'),
        ])
    return rows


@login_required
def export_logs_excel(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    from apps.core.export_utils import ExcelExporter

    date_from, date_to, logs = _parse_date_range(request)
    logs_list = list(logs)
    bl_map = get_active_blacklist_map([l.plate_number for l in logs_list])
    rows = _build_rows(logs_list, bl_map)

    ex = ExcelExporter('bantayplaka_vehicle_logs', date_from, date_to, total_records=len(rows))

    headers = ['#', 'Plate', 'Status', 'Entry Type', 'Source', 'Camera', 'Name', 'BL Tag', 'BL Remarks', 'Logged By', 'Timestamp']
    col_widths = [5, 14, 11, 12, 10, 14, 26, 12, 28, 20, 20]
    ws = ex.add_sheet('Vehicle Logs', headers, col_widths)
    ex.write_rows(ws, rows,
                  highlight_fn=lambda r: bool(r[7]),   # BL Tag col
                  mono_cols={2},
                  center_cols={1, 3, 4, 5, 6, 8, 11})

    ex.add_summary_sheet('Summary', [
        ('Total Records',    len(rows)),
        ('Time In',          sum(1 for r in rows if r[2] == 'Time In')),
        ('Time Out',         sum(1 for r in rows if r[2] == 'Time Out')),
        ('Residents',        sum(1 for r in rows if r[3] == 'Resident')),
        ('Visitors',         sum(1 for r in rows if r[3] == 'Visitor')),
        ('Camera Source',    sum(1 for r in rows if r[4] == 'Camera')),
        ('Manual Source',    sum(1 for r in rows if r[4] == 'Manual')),
        ('Blacklisted Hits', sum(1 for r in rows if r[7])),
    ])

    return ex.response()


@login_required
def export_logs_pdf(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    from apps.core.export_utils import PdfExporter

    date_from, date_to, logs = _parse_date_range(request)
    logs_list = list(logs)
    bl_map = get_active_blacklist_map([l.plate_number for l in logs_list])
    rows = _build_rows(logs_list, bl_map)

    pdf = PdfExporter('bantayplaka_vehicle_logs',
                      title='Bantay Plaka — Vehicle Log Report',
                      date_from=date_from, date_to=date_to)

    headers = ['Plate', 'Status', 'Type', 'Source', 'Name', 'Logged By', 'Timestamp']
    pdf_rows = [[r[1], r[2], r[3], r[4], r[6], r[9], r[10]] for r in rows]
    pdf.build(headers, pdf_rows,
              col_widths=[32, 22, 22, 20, 54, 36, 38],
              highlight_fn=lambda r: bool(bl_map.get((r[0] or '').upper())),
              mono_col_idx=0)

    return pdf.response()