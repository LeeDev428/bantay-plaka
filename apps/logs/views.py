import datetime

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.utils import timezone

from apps.logs.models import VehicleLog
from apps.logs.forms import ManualLogForm, LogEditForm
from apps.logs.services import broadcast_log, attach_blacklist_metadata
from apps.residents.models import Vehicle
from apps.visitors.models import BlacklistEntry


def resolve_plate(plate_number: str) -> dict:
    """Check if the plate belongs to a registered resident vehicle."""
    try:
        vehicle = Vehicle.objects.select_related('resident').get(
            plate_number__iexact=plate_number,
            is_approved=True,
        )
        return {
            'entry_type': VehicleLog.TYPE_RESIDENT,
            'resident_name': vehicle.resident.full_name,
        }
    except Vehicle.DoesNotExist:
        return {
            'entry_type': VehicleLog.TYPE_VISITOR,
            'resident_name': '',
        }


@login_required
def manual_entry(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    form = ManualLogForm()
    if request.method == 'POST':
        form = ManualLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            blacklist_entry = BlacklistEntry.objects.filter(
                plate_number__iexact=log.plate_number,
                is_active=True,
            ).first()

            log.source = VehicleLog.SOURCE_MANUAL
            log.logged_by = request.user

            # auto-resolve resident if plate is registered
            resolved = resolve_plate(log.plate_number)
            if resolved['entry_type'] == VehicleLog.TYPE_RESIDENT:
                log.entry_type = VehicleLog.TYPE_RESIDENT
                log.resident_name = log.resident_name or resolved['resident_name']
                log.visitor_name = ''
            elif log.entry_type == VehicleLog.TYPE_RESIDENT:
                log.entry_type = VehicleLog.TYPE_VISITOR
                log.resident_name = ''

            log.save()
            broadcast_log(log)
            if blacklist_entry:
                blacklist_note = blacklist_entry.remarks or blacklist_entry.reason or 'Blacklisted plate.'
                messages.warning(
                    request,
                    f'Log entry saved for {log.plate_number}. Plate is blacklisted: {blacklist_note}'
                )
            else:
                messages.success(request, f'Log entry saved for {log.plate_number}.')
            return redirect('manual_entry')

    recent_logs = VehicleLog.objects.filter(source=VehicleLog.SOURCE_MANUAL).order_by('-timestamp')[:20]
    recent_logs = attach_blacklist_metadata(recent_logs)
    return render(request, 'logs/manual_entry.html', {
        'form': form,
        'recent_logs': recent_logs,
    })


@login_required
def log_list(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    logs_qs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')

    # filters
    q = request.GET.get('q', '').strip()
    plate_q = request.GET.get('plate', '').strip()
    entry_type_q = request.GET.get('entry_type', '').strip()
    status_q = request.GET.get('status', '').strip()
    date_q = request.GET.get('date', '').strip()

    if q:
        logs_qs = logs_qs.filter(
            Q(plate_number__icontains=q)
            | Q(resident_name__icontains=q)
            | Q(visitor_name__icontains=q)
        )
    if plate_q:
        logs_qs = logs_qs.filter(plate_number__icontains=plate_q)
    if entry_type_q:
        logs_qs = logs_qs.filter(entry_type=entry_type_q)
    if status_q:
        logs_qs = logs_qs.filter(status=status_q)
    if date_q:
        try:
            tz = timezone.get_current_timezone()
            d = datetime.date.fromisoformat(date_q)
            day_start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
            day_end = day_start + datetime.timedelta(days=1)
            logs_qs = logs_qs.filter(timestamp__gte=day_start, timestamp__lt=day_end)
        except ValueError:
            pass

    paginator = Paginator(logs_qs, 10)
    page = request.GET.get('page', 1)
    logs = paginator.get_page(page)
    attach_blacklist_metadata(logs.object_list)

    return render(request, 'logs/log_list.html', {
        'logs': logs,
        'q': q,
        'plate_q': plate_q,
        'entry_type_q': entry_type_q,
        'status_q': status_q,
        'date_q': date_q,
    })


@login_required
def log_edit(request, pk):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    log = get_object_or_404(VehicleLog, pk=pk)
    if request.method == 'POST':
        form = LogEditForm(request.POST, instance=log)
        if form.is_valid():
            updated_log = form.save(commit=False)
            if updated_log.entry_type == VehicleLog.TYPE_RESIDENT:
                updated_log.visitor_name = ''
            else:
                updated_log.resident_name = ''
            updated_log.save()
            messages.success(request, f'Log #{pk} updated successfully.')
        else:
            messages.error(request, 'Failed to update log. Please check the fields.')
    return redirect(request.POST.get('next', 'log_list'))


@login_required
def log_delete(request, pk):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    log = get_object_or_404(VehicleLog, pk=pk)
    if request.method == 'POST':
        log.delete()
        messages.success(request, f'Log #{pk} deleted.')
    return redirect(request.POST.get('next', 'log_list'))
