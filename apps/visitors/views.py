from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404

from apps.visitors.models import Visitor, BlacklistEntry
from apps.visitors.forms import VisitorForm, BlacklistEntryForm
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
            log = VehicleLog.objects.create(
                plate_number=visitor.plate_number or 'N/A',
                entry_type=VehicleLog.TYPE_VISITOR,
                status=VehicleLog.STATUS_IN,
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

    visitors = Visitor.objects.select_related('logged_by').order_by('-created_at')[:50]
    return render(request, 'visitors/visitor_list.html', {'visitors': visitors})


@login_required
def blacklist_list(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    if not (request.user.is_admin() or request.user.is_guard()):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    form = BlacklistEntryForm()
    if request.method == 'POST':
        form = BlacklistEntryForm(request.POST)
        if form.is_valid():
            entry, created = BlacklistEntry.objects.get_or_create(
                plate_number=form.cleaned_data['plate_number'],
                defaults={
                    'tag': form.cleaned_data.get('tag', BlacklistEntry.TAG_WATCHLIST),
                    'reason': form.cleaned_data.get('reason', ''),
                    'remarks': form.cleaned_data.get('remarks', ''),
                    'is_active': True,
                    'created_by': request.user,
                }
            )
            if created:
                messages.success(request, f'Plate {entry.plate_number} added to blacklist.')
            else:
                entry.tag = form.cleaned_data.get('tag', entry.tag)
                entry.reason = form.cleaned_data.get('reason', entry.reason)
                entry.remarks = form.cleaned_data.get('remarks', entry.remarks)
                entry.is_active = True
                entry.save(update_fields=['tag', 'reason', 'remarks', 'is_active', 'updated_at'])
                messages.success(request, f'Plate {entry.plate_number} is now active in blacklist.')
            return redirect('blacklist_list')

    entries = BlacklistEntry.objects.select_related('created_by').all()[:100]
    return render(request, 'visitors/blacklist.html', {
        'form': form,
        'entries': entries,
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
    return redirect('blacklist_list')
