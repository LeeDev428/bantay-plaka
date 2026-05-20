"""apps/accounts/export_views.py"""
from apps.accounts.models import User
from apps.accounts.views import admin_required
from apps.export_helpers import build_excel_response, build_pdf_response


def _user_rows(qs):
    rows = []
    for i, u in enumerate(qs, 1):
        rows.append([i, u.get_full_name() or '—', u.username, u.email or '—',
                     'Admin' if u.role == User.ROLE_ADMIN else 'Guard',
                     u.contact_number or '—',
                     'Active' if u.is_active else 'Inactive',
                     str(u.date_joined.date()) if u.date_joined else '—'])
    return rows


@admin_required
def export_users_excel(request):
    qs = User.objects.exclude(role=User.ROLE_RESIDENT).order_by('role', 'last_name')
    rows = _user_rows(qs)
    return build_excel_response(
        'bantayplaka_users', 'Users',
        headers=['#', 'Full Name', 'Username', 'Email', 'Role', 'Contact', 'Status', 'Date Joined'],
        col_widths=[5, 28, 18, 30, 12, 16, 11, 14],
        rows=rows,
        highlight_fn=lambda r: r[6] == 'Inactive',
        center_cols={1, 5, 7, 8},
        summary_rows=[
            ('Total Users', len(rows)),
            ('Admins',  sum(1 for r in rows if r[4] == 'Admin')),
            ('Guards',  sum(1 for r in rows if r[4] == 'Guard')),
            ('Active',  sum(1 for r in rows if r[6] == 'Active')),
            ('Inactive',sum(1 for r in rows if r[6] == 'Inactive')),
        ],
    )


@admin_required
def export_users_pdf(request):
    qs = User.objects.exclude(role=User.ROLE_RESIDENT).order_by('role', 'last_name')
    rows = _user_rows(qs)
    return build_pdf_response(
        'bantayplaka_users', 'Bantay Plaka — User Management',
        headers=['Full Name', 'Username', 'Email', 'Role', 'Contact', 'Status', 'Date Joined'],
        col_widths_mm=[46, 26, 48, 18, 26, 16, 20],
        rows=[[r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in rows],
    )