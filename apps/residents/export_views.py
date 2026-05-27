"""apps/residents/export_views.py"""
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

from apps.accounts.views import admin_required
from apps.residents.models import Resident, Vehicle
from apps.export_helpers import build_excel_response, build_pdf_response


def _resident_rows(qs):
    rows = []
    for i, r in enumerate(qs, 1):
        plates = ', '.join(v.plate_number for v in r.vehicles.all()) or '—'
        status = 'Approved' if r.is_approved else ('Not Approved' if r.approved_by else 'Pending')
        rows.append([i, r.full_name, r.get_sex_display() if r.sex else '—',
                     str(r.birth_date) if r.birth_date else '—',
                     str(r.age) if r.age else '—',
                     f'{r.street_number} {r.street_name}'.strip() or '—',
                     r.address or '—', r.contact_number or '—',
                     r.get_valid_id_type_display() if r.valid_id_type else '—',
                     status, plates,
                     str(r.created_at.date()) if r.created_at else '—'])
    return rows


@admin_required
def export_residents_excel(request):
    qs = Resident.objects.prefetch_related('vehicles').order_by('last_name', 'first_name')
    rows = _resident_rows(qs)
    return build_excel_response(
        'bantayplaka_residents', 'Residents',
        headers=['#', 'Full Name', 'Sex', 'Birth Date', 'Age', 'Street', 'Address', 'Contact', 'Valid ID', 'Status', 'Vehicles', 'Registered'],
        col_widths=[5, 28, 10, 13, 7, 22, 28, 16, 22, 12, 24, 13],
        rows=rows,
        highlight_fn=lambda r: r[9] == 'Not Approved',
        center_cols={1, 3, 4, 5, 9, 10, 12},
        summary_rows=[
            ('Total Residents', len(rows)),
            ('Approved',  sum(1 for r in rows if r[9] == 'Approved')),
            ('Pending',   sum(1 for r in rows if r[9] == 'Pending')),
            ('Not Approved', sum(1 for r in rows if r[9] == 'Not Approved')),
            ('With Vehicles', sum(1 for r in rows if r[10] != '—')),
        ],
    )


@admin_required
def export_residents_pdf(request):
    qs = Resident.objects.prefetch_related('vehicles').order_by('last_name', 'first_name')
    rows = _resident_rows(qs)
    return build_pdf_response(
        'bantayplaka_residents', 'Bantay Plaka — Residents Directory',
        headers=['Full Name', 'Sex', 'Birth Date', 'Address', 'Contact', 'Valid ID', 'Status', 'Vehicles'],
        col_widths_mm=[46, 14, 20, 46, 26, 28, 18, 32],
        rows=[[r[1], r[2], r[3], r[6], r[7], r[8], r[9], r[10]] for r in rows],
    )


def _vehicle_rows(qs):
    rows = []
    for i, v in enumerate(qs, 1):
        rows.append([i, v.plate_number, v.resident.full_name,
                     v.get_vehicle_type_display() if hasattr(v, 'get_vehicle_type_display') else v.vehicle_type,
                     v.make or '—', v.model or '—', v.color or '—',
                     'Approved' if v.is_approved else 'Pending',
                     v.approved_by.get_full_name() if v.approved_by else '—',
                     str(v.created_at.date()) if v.created_at else '—'])
    return rows


@admin_required
def export_vehicles_excel(request):
    qs = Vehicle.objects.select_related('resident', 'approved_by').order_by('plate_number')
    rows = _vehicle_rows(qs)
    return build_excel_response(
        'bantayplaka_vehicles', 'Vehicles',
        headers=['#', 'Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Status', 'Approved By', 'Date'],
        col_widths=[5, 14, 28, 13, 16, 16, 14, 11, 22, 13],
        rows=rows,
        highlight_fn=lambda r: r[7] == 'Pending',
        mono_cols={2}, center_cols={1, 4, 7, 8, 10},
        summary_rows=[
            ('Total Vehicles', len(rows)),
            ('Approved', sum(1 for r in rows if r[7] == 'Approved')),
            ('Pending',  sum(1 for r in rows if r[7] == 'Pending')),
        ],
    )


@admin_required
def export_vehicles_pdf(request):
    qs = Vehicle.objects.select_related('resident', 'approved_by').order_by('plate_number')
    rows = _vehicle_rows(qs)
    return build_pdf_response(
        'bantayplaka_vehicles', 'Bantay Plaka — Registered Vehicles',
        headers=['Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Status', 'Date'],
        col_widths_mm=[28, 46, 20, 22, 22, 18, 18, 22],
        rows=[[r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[9]] for r in rows],
        mono_col_idx=0,
    )


def _approval_rows(qs):
    rows = []
    for i, v in enumerate(qs, 1):
        rows.append([i, v.plate_number, v.resident.full_name,
                     v.get_vehicle_type_display() if hasattr(v, 'get_vehicle_type_display') else v.vehicle_type,
                     v.make or '—', v.model or '—', v.color or '—',
                     timezone.localtime(v.created_at).strftime('%Y-%m-%d %H:%M') if v.created_at else '—'])
    return rows


@admin_required
def export_approvals_excel(request):
    qs = Vehicle.objects.select_related('resident').filter(is_approved=False, approval_notes='').order_by('-created_at')
    rows = _approval_rows(qs)
    return build_excel_response(
        'bantayplaka_pending_vehicles', 'Pending Approvals',
        headers=['#', 'Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Submitted'],
        col_widths=[5, 14, 28, 13, 16, 16, 14, 18],
        rows=rows, mono_cols={2}, center_cols={1, 4, 7, 8},
    )


@admin_required
def export_approvals_pdf(request):
    qs = Vehicle.objects.select_related('resident').filter(is_approved=False, approval_notes='').order_by('-created_at')
    rows = _approval_rows(qs)
    return build_pdf_response(
        'bantayplaka_pending_vehicles', 'Bantay Plaka — Pending Vehicle Approvals',
        headers=['Plate', 'Resident', 'Type', 'Make', 'Model', 'Color', 'Submitted'],
        col_widths_mm=[28, 52, 22, 24, 24, 20, 30],
        rows=[[r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in rows],
        mono_col_idx=0,
    )