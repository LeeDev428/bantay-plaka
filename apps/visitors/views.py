from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q

from apps.visitors.models import Visitor, BlacklistEntry
from apps.visitors.forms import VisitorForm, BlacklistEntryForm, VisitorEditForm
from apps.logs.models import VehicleLog
from apps.logs.services import broadcast_log


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
    visitors_qs = Visitor.objects.select_related('logged_by').order_by('-created_at')
    if q:
        visitors_qs = visitors_qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(plate_number__icontains=q)
            | Q(host_name__icontains=q)
            | Q(purpose__icontains=q)
        )

    paginator = Paginator(visitors_qs, 10)
    visitors = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'visitors/visitor_list.html', {'visitors': visitors, 'q': q})


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
        visitor.delete()
        messages.success(request, 'Visitor entry deleted.')
    return redirect(request.POST.get('next', 'visitor_list'))


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