from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q
import datetime
from django.utils import timezone

from apps.visitors.models import Visitor, BlacklistEntry
from apps.visitors.forms import VisitorForm, BlacklistEntryForm, VisitorEditForm
from apps.archives.models import ArchivedItem
from apps.archives.services import archive_instance
from apps.logs.models import VehicleLog
from apps.logs.services import broadcast_log
from apps.export_helpers import build_excel_response, build_pdf_response


def _is_blacklisted(plate_number: str) -> bool:
    if not plate_number:
        return False
    return BlacklistEntry.objects.filter(plate_number__iexact=plate_number, is_active=True).exists()


@login_required
def visitor_log_entry(request):
    """Guard logs a visitor coming in (TIME IN)."""
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if request.method == 'POST':
        form = VisitorForm(request.POST)
        if form.is_valid():
            visitor = form.save(commit=False)

            if _is_blacklisted(visitor.plate_number):
                messages.error(request, f'Plate {visitor.plate_number} is blacklisted. Entry blocked.')
                return redirect('visitor_log_entry')

            visitor.logged_by = request.user
            visitor.save()

            # create vehicle log time-in
            status = form.cleaned_data.get('status', VehicleLog.STATUS_IN)
            log = VehicleLog.objects.create(
                plate_number=visitor.plate_number or 'N/A',
                entry_type=VehicleLog.TYPE_VISITOR,
                status=status,
                source=VehicleLog.SOURCE_MANUAL,
                visitor_name=visitor.full_name,
                logged_by=request.user,
            )
            broadcast_log(log)
            messages.success(request, f'Visitor {visitor.full_name} logged in.')
            return redirect('visitor_list')
    else:
        form = VisitorForm()
    return render(request, 'visitors/visitor_form.html', {'form': form})


@login_required
def visitor_list(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    q = request.GET.get('q', '').strip()
    visitor_type_q = request.GET.get('visitor_type', '').strip().upper()
    date_q = request.GET.get('date', '').strip()

    visitor_form = VisitorForm(prefix='visitor')
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'create_visitor':
            visitor_form = VisitorForm(request.POST, prefix='visitor')
            if visitor_form.is_valid():
                visitor = visitor_form.save(commit=False)
                if _is_blacklisted(visitor.plate_number):
                    messages.error(request, f'Plate {visitor.plate_number} is blacklisted. Entry blocked.')
                else:
                    visitor.logged_by = request.user
                    visitor.save()

                    status = visitor_form.cleaned_data.get('status', VehicleLog.STATUS_IN)
                    log = VehicleLog.objects.create(
                        plate_number=visitor.plate_number or 'N/A',
                        entry_type=VehicleLog.TYPE_VISITOR,
                        status=status,
                        source=VehicleLog.SOURCE_MANUAL,
                        visitor_name=visitor.full_name,
                        logged_by=request.user,
                    )
                    broadcast_log(log)
                    messages.success(request, f'Visitor {visitor.full_name} logged in.')
                    return redirect('visitor_list')
            else:
                messages.error(request, 'Failed to log visitor. Please complete all required fields.')

    visitors_qs = Visitor.objects.select_related('logged_by').order_by('-created_at')
    if q:
        visitors_qs = visitors_qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(plate_number__icontains=q)
            | Q(host_name__icontains=q)
            | Q(purpose__icontains=q)
        )
    if visitor_type_q in {Visitor.TYPE_VISITOR, Visitor.TYPE_VERIFIED}:
        visitors_qs = visitors_qs.filter(visitor_type=visitor_type_q)
    else:
        visitor_type_q = ''
    if date_q:
        try:
            d = datetime.date.fromisoformat(date_q)
            tz = timezone.get_current_timezone()
            day_start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
            day_end = day_start + datetime.timedelta(days=1)
            visitors_qs = visitors_qs.filter(created_at__gte=day_start, created_at__lt=day_end)
        except ValueError:
            date_q = ''

    paginator = Paginator(visitors_qs, 10)
    visitors = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'visitors/visitor_list.html', {
        'visitors': visitors,
        'q': q,
        'visitor_type_q': visitor_type_q,
        'date_q': date_q,
        'visitor_form': visitor_form,
    })


@login_required
def visitor_edit(request, pk):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    visitor = get_object_or_404(Visitor, pk=pk)
    if request.method == 'POST':
        form = VisitorEditForm(request.POST, instance=visitor)
        if form.is_valid():
            form.save()
            messages.success(request, f'Visitor {visitor.full_name} updated.')
        else:
            messages.error(request, 'Failed to update visitor entry.')

    return redirect(request.POST.get('next', 'visitor_list'))


@login_required
def visitor_delete(request, pk):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    visitor = get_object_or_404(Visitor, pk=pk)
    if request.method == 'POST':
        archive_instance(
            visitor,
            entity_type=ArchivedItem.ENTITY_VISITOR,
            archived_by=request.user,
            notes='Visitor archived from visitors module.',
        )
        visitor.delete()
        messages.success(request, 'Visitor archived and removed from active list.')
    return redirect(request.POST.get('next', 'visitor_list'))


@login_required
def visitor_export_excel(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    q = request.GET.get('q', '').strip()
    visitor_type_q = request.GET.get('visitor_type', '').strip().upper()
    date_q = request.GET.get('date', '').strip()

    qs = Visitor.objects.select_related('logged_by').order_by('-created_at')
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(plate_number__icontains=q)
            | Q(host_name__icontains=q)
            | Q(purpose__icontains=q)
        )
    if visitor_type_q in {Visitor.TYPE_VISITOR, Visitor.TYPE_VERIFIED}:
        qs = qs.filter(visitor_type=visitor_type_q)
    if date_q:
        try:
            d = datetime.date.fromisoformat(date_q)
            tz = timezone.get_current_timezone()
            day_start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
            day_end = day_start + datetime.timedelta(days=1)
            qs = qs.filter(created_at__gte=day_start, created_at__lt=day_end)
        except ValueError:
            date_q = ''

    rows = []
    for i, visitor in enumerate(qs, 1):
        rows.append([
            i,
            visitor.full_name,
            visitor.get_visitor_type_display(),
            visitor.plate_number or '—',
            visitor.purpose or '—',
            visitor.host_name or '—',
            visitor.logged_by.get_full_name() if visitor.logged_by else '—',
            timezone.localtime(visitor.created_at).strftime('%Y-%m-%d %H:%M:%S') if visitor.created_at else '—',
        ])

    return build_excel_response(
        'bantayplaka_visitors', 'Visitors',
        headers=['#', 'Name', 'Type', 'Plate', 'Purpose', 'Host', 'Logged By', 'Timestamp'],
        col_widths=[5, 26, 16, 14, 26, 22, 20, 20],
        rows=rows,
        mono_cols={4}, center_cols={1, 3, 4, 8},
        summary_rows=[
            ('Total Visitors', len(rows)),
            ('Visitor', sum(1 for r in rows if r[2] == 'Visitor')),
            ('Verified Visitor', sum(1 for r in rows if r[2] == 'Verified Visitor')),
        ],
        date_from=date_q,
    )


@login_required
def visitor_export_pdf(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    q = request.GET.get('q', '').strip()
    visitor_type_q = request.GET.get('visitor_type', '').strip().upper()
    date_q = request.GET.get('date', '').strip()

    qs = Visitor.objects.select_related('logged_by').order_by('-created_at')
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(plate_number__icontains=q)
            | Q(host_name__icontains=q)
            | Q(purpose__icontains=q)
        )
    if visitor_type_q in {Visitor.TYPE_VISITOR, Visitor.TYPE_VERIFIED}:
        qs = qs.filter(visitor_type=visitor_type_q)
    if date_q:
        try:
            d = datetime.date.fromisoformat(date_q)
            tz = timezone.get_current_timezone()
            day_start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
            day_end = day_start + datetime.timedelta(days=1)
            qs = qs.filter(created_at__gte=day_start, created_at__lt=day_end)
        except ValueError:
            date_q = ''

    rows = []
    for visitor in qs:
        rows.append([
            visitor.full_name,
            visitor.get_visitor_type_display(),
            visitor.plate_number or '—',
            visitor.purpose or '—',
            visitor.host_name or '—',
            timezone.localtime(visitor.created_at).strftime('%Y-%m-%d %H:%M:%S') if visitor.created_at else '—',
        ])

    return build_pdf_response(
        'bantayplaka_visitors', 'Bantay Plaka — Visitors Report',
        headers=['Name', 'Type', 'Plate', 'Purpose', 'Host', 'Timestamp'],
        col_widths_mm=[48, 22, 24, 52, 40, 38],
        rows=rows,
        mono_col_idx=2,
        date_from=date_q,
    )


@login_required
def blacklist_export_excel(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if not (request.user.is_admin() or request.user.is_guard()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    q = request.GET.get('q', '').strip()
    tag_q = request.GET.get('tag', '').strip()
    status_q = request.GET.get('status', '').strip()

    qs = BlacklistEntry.objects.select_related('created_by').order_by('-updated_at')
    if q:
        qs = qs.filter(
            Q(plate_number__icontains=q)
            | Q(reason__icontains=q)
            | Q(remarks__icontains=q)
        )
    if tag_q:
        qs = qs.filter(tag=tag_q)
    if status_q == 'active':
        qs = qs.filter(is_active=True)
    elif status_q == 'inactive':
        qs = qs.filter(is_active=False)

    rows = []
    for i, entry in enumerate(qs, 1):
        rows.append([
            i,
            entry.plate_number,
            entry.get_tag_display(),
            entry.reason or '—',
            entry.remarks or '—',
            'Active' if entry.is_active else 'Inactive',
            entry.created_by.get_full_name() if entry.created_by else '—',
            timezone.localtime(entry.created_at).strftime('%Y-%m-%d %H:%M:%S') if entry.created_at else '—',
        ])

    return build_excel_response(
        'bantayplaka_blacklist', 'Blacklist',
        headers=['#', 'Plate', 'Tag', 'Reason', 'Remarks', 'Status', 'Created By', 'Created At'],
        col_widths=[5, 14, 16, 28, 36, 12, 20, 20],
        rows=rows,
        mono_cols={2}, center_cols={1, 3, 6, 8},
        highlight_fn=lambda r: r[5] == 'Active' and r[2] == 'High Risk',
        summary_rows=[
            ('Total Entries', len(rows)),
            ('Active', sum(1 for r in rows if r[5] == 'Active')),
            ('Inactive', sum(1 for r in rows if r[5] == 'Inactive')),
            ('High Risk', sum(1 for r in rows if r[2] == 'High Risk')),
            ('Watchlist', sum(1 for r in rows if r[2] == 'Watchlist')),
        ],
    )


@login_required
def blacklist_export_pdf(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if not (request.user.is_admin() or request.user.is_guard()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    q = request.GET.get('q', '').strip()
    tag_q = request.GET.get('tag', '').strip()
    status_q = request.GET.get('status', '').strip()

    qs = BlacklistEntry.objects.select_related('created_by').order_by('-updated_at')
    if q:
        qs = qs.filter(
            Q(plate_number__icontains=q)
            | Q(reason__icontains=q)
            | Q(remarks__icontains=q)
        )
    if tag_q:
        qs = qs.filter(tag=tag_q)
    if status_q == 'active':
        qs = qs.filter(is_active=True)
    elif status_q == 'inactive':
        qs = qs.filter(is_active=False)

    rows = []
    for entry in qs:
        rows.append([
            entry.plate_number,
            entry.get_tag_display(),
            entry.reason or '—',
            entry.remarks or '—',
            'Active' if entry.is_active else 'Inactive',
            entry.created_by.get_full_name() if entry.created_by else '—',
            timezone.localtime(entry.created_at).strftime('%Y-%m-%d %H:%M:%S') if entry.created_at else '—',
        ])

    return build_pdf_response(
        'bantayplaka_blacklist', 'Bantay Plaka — Blacklist Report',
        headers=['Plate', 'Tag', 'Reason', 'Remarks', 'Status', 'Created By', 'Created At'],
        col_widths_mm=[26, 22, 40, 64, 18, 34, 34],
        rows=rows,
        mono_col_idx=0,
        highlight_fn=lambda r: r[1] == 'High Risk' and r[4] == 'Active',
    )


@login_required
def blacklist_list(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if not (request.user.is_admin() or request.user.is_guard()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    q = request.GET.get('q', '').strip()
    form = BlacklistEntryForm()
    if request.method == 'POST':
        form = BlacklistEntryForm(request.POST)
        if form.is_valid():
            entry, created = BlacklistEntry.objects.get_or_create(
                plate_number=form.cleaned_data['plate_number'],
                defaults={
                    'tag': form.cleaned_data.get('tag', BlacklistEntry.TAG_WATCHLIST),
                    'reason': form.cleaned_data['reason'],
                    'remarks': form.cleaned_data['remarks'],
                    'is_active': True,
                    'created_by': request.user,
                }
            )
            if created:
                messages.success(request, f'Plate {entry.plate_number} added to blacklist.')
            else:
                entry.tag = form.cleaned_data.get('tag', entry.tag)
                entry.reason = form.cleaned_data['reason']
                entry.remarks = form.cleaned_data['remarks']
                entry.is_active = True
                entry.save(update_fields=['tag', 'reason', 'remarks', 'is_active', 'updated_at'])
                messages.success(request, f'Plate {entry.plate_number} is now active in blacklist.')
            return redirect('blacklist_list')

    tag_q = request.GET.get('tag', '').strip()
    status_q = request.GET.get('status', '').strip()

    entries_qs = BlacklistEntry.objects.select_related('created_by').order_by('-updated_at')
    if q:
        entries_qs = entries_qs.filter(
            Q(plate_number__icontains=q)
            | Q(reason__icontains=q)
            | Q(remarks__icontains=q)
        )
    if tag_q:
        entries_qs = entries_qs.filter(tag=tag_q)
    if status_q == 'active':
        entries_qs = entries_qs.filter(is_active=True)
    elif status_q == 'inactive':
        entries_qs = entries_qs.filter(is_active=False)

    paginator = Paginator(entries_qs, 10)
    entries = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'visitors/blacklist.html', {
        'form': form,
        'entries': entries,
        'q': q,
        'tag_q': tag_q,
        'status_q': status_q,
    })


@login_required
def blacklist_toggle(request, pk):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if not (request.user.is_admin() or request.user.is_guard()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    entry = get_object_or_404(BlacklistEntry, pk=pk)
    if request.method == 'POST':
        entry.is_active = not entry.is_active
        entry.save(update_fields=['is_active', 'updated_at'])
        state = 'activated' if entry.is_active else 'deactivated'
        messages.success(request, f'Blacklist entry for {entry.plate_number} {state}.')
    return redirect(request.POST.get('next', 'blacklist_list'))


@login_required
def blacklist_edit(request, pk):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if not (request.user.is_admin() or request.user.is_guard()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    entry = get_object_or_404(BlacklistEntry, pk=pk)
    if request.method == 'POST':
        form = BlacklistEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, f'Blacklist entry for {entry.plate_number} updated.')
        else:
            messages.error(request, 'Failed to update blacklist entry.')
    return redirect(request.POST.get('next', 'blacklist_list'))


@login_required
def blacklist_cancel(request, pk):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if not (request.user.is_admin() or request.user.is_guard()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    entry = get_object_or_404(BlacklistEntry, pk=pk)
    if request.method == 'POST':
        entry.is_active = False
        entry.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'Blacklist for {entry.plate_number} cancelled.')
    return redirect(request.POST.get('next', 'blacklist_list'))