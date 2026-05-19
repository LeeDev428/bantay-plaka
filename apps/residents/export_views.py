"""
Export views for Residents, Vehicles, and Vehicle Approvals (Excel + PDF).
Wire into apps/residents/urls.py:
    path('export/excel/',           views.export_residents_excel,          name='residents_export_excel'),
    path('export/pdf/',             views.export_residents_pdf,            name='residents_export_pdf'),
    path('vehicles/export/excel/',  views.export_vehicles_excel,           name='vehicles_export_excel'),
    path('vehicles/export/pdf/',    views.export_vehicles_pdf,             name='vehicles_export_pdf'),
    path('vehicles/approvals/export/excel/', views.export_approvals_excel, name='approvals_export_excel'),
    path('vehicles/approvals/export/pdf/',   views.export_approvals_pdf,   name='approvals_export_pdf'),
"""
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

from apps.accounts.views import admin_required
from apps.residents.models import Resident, Vehicle


# ── Residents ────────────────────────────────────────────────────────────────

def _resident_rows(qs):
    rows = []
    for i, r in enumerate(qs, 1):
        plates = ', '.join(v.plate_number for v in r.vehicles.all()) or '—'
        rows.append([
            i,
            r.full_name,
            r.get_sex_display() if r.sex else '—',
            str(r.birth_date) if r.birth_date else '—',
            str(r.age) if r.age else '—',
            f'{r.street_number} {r.street_name}'.strip() or '—',
            r.address or '—',
            r.contact_number or '—',
            r.get_valid_id_type_display() if r.valid_id_type else '—',
            'Approved' if r.is_approved else ('Not Approved' if r.approved_by else 'Pending'),
            plates,
            str(r.created_at.date()) if r.created_at else '—',
        ])
    return rows


@admin_required
def export_residents_excel(request):
    from apps.core.export_utils import ExcelExporter

    qs = Resident.objects.prefetch_related('vehicles').order_by('last_name', 'first_name')
    rows = _resident_rows(qs)

    ex = ExcelExporter('bantayplaka_residents', total_records=len(rows))
    headers = ['#', 'Full Name', 'Sex', 'Birth Date', 'Age', 'Street', 'Address', 'Contact', 'Valid ID', 'Status', 'Vehicles', 'Registered']
    col_widths = [5, 28, 10, 13, 7, 22, 28, 16, 22, 12, 24, 13]
    ws = ex.add_sheet('Residents', headers, col_widths)
    ex.write_rows(ws, rows,
                  highlight_fn=lambda r: r[9] == 'Not Approved',
                  center_cols={1, 3, 4, 5, 9, 10, 12})

    ex.add_summary_sheet('Summary', [
        ('Total Residents', len(rows)),
        ('Approved',        sum(1 for r in rows if r[9] == 'Approved')),
        ('Pending',         sum(1 for r in rows if r[9] == 'Pending')),
        ('Not Approved',    sum(1 for r in rows if r[9] == 'Not Approved')),
        ('With Vehicles',   sum(1 for r in rows if r[10] != '—')),
        ('No Vehicles',     sum(1 for r in rows if r[10] == '—')),
    ])
    return ex.response()


@admin_required
def export_residents_pdf(request):
    from apps.core.export_utils import PdfExporter

    qs = Resident.objects.prefetch_related('vehicles').order_by('last_name', 'first_name')
    rows = _resident_rows(qs)

    pdf = PdfExporter('bantayplaka_residents', title='Bantay Plaka — Residents Directory')
    headers = ['Full Name', 'Sex', 'Birth Date', 'Address', 'Contact', 'Valid ID', 'Status', 'Vehicles']
    pdf_rows = [[r[1], r[2], r[3], r[6], r[7], r[8], r[9], r[10]] for r in rows]
    pdf.build(headers, pdf_rows,
              col_widths=[46, 14, 20, 46, 26, 28, 18, 32])
    return pdf.response()


# ── All Vehicles ──────────────────────────────────────────────────────────────

def _vehicle_rows(qs):
    rows = []
    for i, v in enumerate(qs, 1):
        rows.append([
            i,
            v.plate_number,
            v.resident.full_name,
            v.get_vehicle_type_display() if hasattr(v, 'get_vehicle_type_display') else v.vehicle_type,
            v.make or '—',
            v.model or '—',
            v.color or '—',
            'Approved' if v.is_approved else 'Pending',
            v.approved_by.get_full_name() if v.approved_by else '—',
            str(v.created_at.date()) if v.created_at else '—',
        ])
    return rows


@admin_required
def export_vehicles_excel(request):
    from apps.core.export_utils import ExcelExporter

    qs = Vehicle.objects.select_related('resident', 'approved_by').order_by('plate_number')
    rows = _vehicle_rows(qs)

    ex = ExcelExporter('bantayplaka_vehicles', total_records=len(rows))
    headers = ['#', 'Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Status', 'Approved By', 'Date']
    col_widths = [5, 14, 28, 13, 16, 16, 14, 11, 22, 13]
    ws = ex.add_sheet('Vehicles', headers, col_widths)
    ex.write_rows(ws, rows,
                  highlight_fn=lambda r: r[7] == 'Pending',
                  mono_cols={2},
                  center_cols={1, 4, 7, 8, 10})

    ex.add_summary_sheet('Summary', [
        ('Total Vehicles', len(rows)),
        ('Approved',       sum(1 for r in rows if r[7] == 'Approved')),
        ('Pending',        sum(1 for r in rows if r[7] == 'Pending')),
    ])
    return ex.response()


@admin_required
def export_vehicles_pdf(request):
    from apps.core.export_utils import PdfExporter

    qs = Vehicle.objects.select_related('resident', 'approved_by').order_by('plate_number')
    rows = _vehicle_rows(qs)

    pdf = PdfExporter('bantayplaka_vehicles', title='Bantay Plaka — Registered Vehicles')
    headers = ['Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Status', 'Date']
    pdf_rows = [[r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[9]] for r in rows]
    pdf.build(headers, pdf_rows,
              col_widths=[28, 46, 20, 22, 22, 18, 18, 22],
              mono_col_idx=0)
    return pdf.response()


# ── Pending Vehicle Approvals ─────────────────────────────────────────────────

def _approval_rows(qs):
    rows = []
    for i, v in enumerate(qs, 1):
        rows.append([
            i,
            v.plate_number,
            v.resident.full_name,
            v.get_vehicle_type_display() if hasattr(v, 'get_vehicle_type_display') else v.vehicle_type,
            v.make or '—',
            v.model or '—',
            v.color or '—',
            str(timezone.localtime(v.created_at).strftime('%Y-%m-%d %H:%M')) if v.created_at else '—',
        ])
    return rows


@admin_required
def export_approvals_excel(request):
    from apps.core.export_utils import ExcelExporter

    qs = Vehicle.objects.select_related('resident').filter(is_approved=False).order_by('-created_at')
    rows = _approval_rows(qs)

    ex = ExcelExporter('bantayplaka_pending_vehicles', total_records=len(rows))
    headers = ['#', 'Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Submitted']
    col_widths = [5, 14, 28, 13, 16, 16, 14, 18]
    ws = ex.add_sheet('Pending Approvals', headers, col_widths)
    ex.write_rows(ws, rows, mono_cols={2}, center_cols={1, 4, 7, 8})
    return ex.response()


@admin_required
def export_approvals_pdf(request):
    from apps.core.export_utils import PdfExporter

    qs = Vehicle.objects.select_related('resident').filter(is_approved=False).order_by('-created_at')
    rows = _approval_rows(qs)

    pdf = PdfExporter('bantayplaka_pending_vehicles', title='Bantay Plaka — Pending Vehicle Approvals')
    headers = ['Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Submitted']
    pdf_rows = [[r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in rows]
    pdf.build(headers, pdf_rows,
              col_widths=[28, 52, 22, 24, 24, 20, 30],
              mono_col_idx=0)
    return pdf.response()