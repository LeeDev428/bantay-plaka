import datetime

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.utils import timezone

from apps.logs.models import VehicleLog
from apps.logs.forms import ManualLogForm
from apps.logs.services import broadcast_log
from apps.residents.models import Vehicle


def resolve_plate(plate_number: str) -> dict:
    """Check if the plate belongs to a registered resident vehicle."""
    try:
        vehicle = Vehicle.objects.select_related('resident').get(
            plate_number__iexact=plate_number
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
    form = ManualLogForm()
    if request.method == 'POST':
        form = ManualLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.source = VehicleLog.SOURCE_MANUAL
            log.logged_by = request.user

            # auto-resolve resident if plate is registered
            resolved = resolve_plate(log.plate_number)
            if resolved['entry_type'] == VehicleLog.TYPE_RESIDENT:
                log.entry_type = VehicleLog.TYPE_RESIDENT
                log.resident_name = resolved['resident_name']

            log.save()
            broadcast_log(log)
            messages.success(request, f'Log entry saved for {log.plate_number}.')
            return redirect('manual_entry')

    recent_logs = VehicleLog.objects.filter(source=VehicleLog.SOURCE_MANUAL).order_by('-timestamp')[:20]
    return render(request, 'logs/manual_entry.html', {
        'form': form,
        'recent_logs': recent_logs,
    })


@login_required
def log_list(request):
    logs_qs = VehicleLog.objects.select_related('logged_by').order_by('-timestamp')

    # filters
    plate_q = request.GET.get('plate', '').strip()
    entry_type_q = request.GET.get('entry_type', '').strip()
    date_q = request.GET.get('date', '').strip()

    if plate_q:
        logs_qs = logs_qs.filter(plate_number__icontains=plate_q)
    if entry_type_q:
        logs_qs = logs_qs.filter(entry_type=entry_type_q)
    if date_q:
        try:
            tz = timezone.get_current_timezone()
            d = datetime.date.fromisoformat(date_q)
            day_start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
            day_end = day_start + datetime.timedelta(days=1)
            logs_qs = logs_qs.filter(timestamp__gte=day_start, timestamp__lt=day_end)
        except ValueError:
            pass

    paginator = Paginator(logs_qs, 25)
    page = request.GET.get('page', 1)
    logs = paginator.get_page(page)

    return render(request, 'logs/log_list.html', {
        'logs': logs,
        'plate_q': plate_q,
        'entry_type_q': entry_type_q,
        'date_q': date_q,
    })
