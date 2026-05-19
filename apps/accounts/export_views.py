"""
Export views for User Management (Excel + PDF).
Wire into apps/accounts/dashboard_urls.py:
    path('users/export/excel/', views.export_users_excel, name='users_export_excel'),
    path('users/export/pdf/',   views.export_users_pdf,   name='users_export_pdf'),
"""
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.views import admin_required


def _user_rows(qs):
    rows = []
    for i, u in enumerate(qs, 1):
        rows.append([
            i,
            u.get_full_name() or '—',
            u.username,
            u.email or '—',
            'Admin' if u.role == User.ROLE_ADMIN else 'Guard',
            u.contact_number or '—',
            'Active' if u.is_active else 'Inactive',
            str(u.date_joined.date()) if u.date_joined else '—',
        ])
    return rows


@admin_required
def export_users_excel(request):
    from apps.core.export_utils import ExcelExporter

    qs = User.objects.exclude(role=User.ROLE_RESIDENT).order_by('role', 'last_name')
    rows = _user_rows(qs)

    ex = ExcelExporter('bantayplaka_users', total_records=len(rows))
    headers = ['#', 'Full Name', 'Username', 'Email', 'Role', 'Contact', 'Status', 'Date Joined']
    col_widths = [5, 28, 18, 30, 12, 16, 11, 14]
    ws = ex.add_sheet('Users', headers, col_widths)
    ex.write_rows(ws, rows,
                  highlight_fn=lambda r: r[6] == 'Inactive',
                  center_cols={1, 5, 7, 8})

    ex.add_summary_sheet('Summary', [
        ('Total Users',  len(rows)),
        ('Admins',       sum(1 for r in rows if r[4] == 'Admin')),
        ('Guards',       sum(1 for r in rows if r[4] == 'Guard')),
        ('Active',       sum(1 for r in rows if r[6] == 'Active')),
        ('Inactive',     sum(1 for r in rows if r[6] == 'Inactive')),
    ])
    return ex.response()


@admin_required
def export_users_pdf(request):
    from apps.core.export_utils import PdfExporter

    qs = User.objects.exclude(role=User.ROLE_RESIDENT).order_by('role', 'last_name')
    rows = _user_rows(qs)

    pdf = PdfExporter('bantayplaka_users', title='Bantay Plaka — User Management')
    headers = ['Full Name', 'Username', 'Email', 'Role', 'Contact', 'Status', 'Date Joined']
    pdf_rows = [[r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in rows]
    pdf.build(headers, pdf_rows,
              col_widths=[46, 26, 48, 18, 26, 16, 20])
    return pdf.response()