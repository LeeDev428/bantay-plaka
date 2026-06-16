from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Q
from django.core.paginator import Paginator
import datetime
from datetime import timedelta

from apps.accounts.forms import LoginForm, UserCreateForm, UserEditForm, ResidentSignupForm
from apps.accounts.models import User
from apps.logs.models import VehicleLog, CameraFeedSnapshot
from apps.logs.forms import ManualLogForm
from apps.logs.services import attach_blacklist_metadata, broadcast_log
from apps.residents.models import Resident, Vehicle
from apps.visitors.models import BlacklistEntry, Visitor


def _camera_feed_context() -> dict:
    latest_entry = (
        VehicleLog.objects
        .filter(
            source=VehicleLog.SOURCE_CAMERA,
            camera_role=VehicleLog.CAMERA_ROLE_ENTRY,
        )
        .order_by('-timestamp')
        .first()
    )
    latest_exit = (
        VehicleLog.objects
        .filter(
            source=VehicleLog.SOURCE_CAMERA,
            camera_role=VehicleLog.CAMERA_ROLE_EXIT,
        )
        .order_by('-timestamp')
        .first()
    )
    last_camera_log = (
        VehicleLog.objects
        .filter(source=VehicleLog.SOURCE_CAMERA)
        .order_by('-timestamp')
        .first()
    )
    entry_live_snapshot = (
        CameraFeedSnapshot.objects
        .filter(camera_role=VehicleLog.CAMERA_ROLE_ENTRY)
        .first()
    )
    exit_live_snapshot = (
        CameraFeedSnapshot.objects
        .filter(camera_role=VehicleLog.CAMERA_ROLE_EXIT)
        .first()
    )

    now = timezone.now()
    last_camera_local = timezone.localtime(last_camera_log.timestamp) if last_camera_log else None
    camera_age_seconds = int((now - last_camera_log.timestamp).total_seconds()) if last_camera_log else None
    camera_stale = camera_age_seconds is None or camera_age_seconds > 120
    preview_enabled = bool(getattr(settings, 'CAMERA_PREVIEW_ENABLED', False))

    return {
        'entry_feed': latest_entry,
        'exit_feed': latest_exit,
        'entry_live_snapshot': entry_live_snapshot,
        'exit_live_snapshot': exit_live_snapshot,
        'has_entry_camera_stream': preview_enabled and bool(getattr(settings, 'ENTRY_CAMERA_RTSP', '').strip()),
        'has_exit_camera_stream': preview_enabled and bool(getattr(settings, 'EXIT_CAMERA_RTSP', '').strip()),
        'last_camera_log': last_camera_log,
        'last_camera_log_local': last_camera_local,
        'camera_age_seconds': camera_age_seconds,
        'camera_stale': camera_stale,
    }


class BantayPlakaLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

    def get_success_url(self):
        return '/dashboard/'


class BantayPlakaLogoutView(LogoutView):
    next_page = '/login/'


def resident_register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = ResidentSignupForm()
    if request.method == 'POST':
        form = ResidentSignupForm(request.POST, request.FILES)
        if form.is_valid():
            _, resident = form.save()
            messages.success(
                request,
                (
                    f'Registration submitted for {resident.full_name}. '
                    'Please wait for admin approval before login and present your OR/CR requirements to HOA admin.'
                )
            )
            return redirect('login')

    return render(request, 'residents/register.html', {'form': form})


@login_required
def dashboard_redirect(request):
    if request.user.is_admin():
        return redirect('admin_dashboard')
    if request.user.is_resident():
        return redirect('resident_dashboard')
    return redirect('guard_dashboard')


# ── Admin views ──────────────────────────────────────────────────────────────

def admin_required(view_func):
    """Decorator: user must be logged in AND have ADMIN role."""
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_admin():
            messages.error(request, 'Access denied. Admin only.')
            return redirect('guard_dashboard')
        return view_func(request, *args, **kwargs)
    return wrapped


@admin_required
def admin_dashboard(request):
    today = timezone.localdate()
    day_start = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min))
    day_end = day_start + timedelta(days=1)

    recent_logs = attach_blacklist_metadata(
        VehicleLog.objects.select_related('logged_by').all()[:10]
    )

    # 7-day chart data
    from datetime import timedelta as _td
    import datetime as _dt
    def _day_range(d):
        tz = timezone.get_current_timezone()
        s = timezone.make_aware(_dt.datetime.combine(d, _dt.time.min), tz)
        return s, s + _td(days=1)

    daily_data = []
    for i in range(6, -1, -1):
        d = today - _td(days=i)
        d_start, d_end = _day_range(d)
        day_qs = VehicleLog.objects.filter(timestamp__gte=d_start, timestamp__lt=d_end)
        daily_data.append({
            'label': d.strftime('%a'),
            'time_in': day_qs.filter(status=VehicleLog.STATUS_IN).count(),
            'time_out': day_qs.filter(status=VehicleLog.STATUS_OUT).count(),
        })

    context = {
        'total_residents': Resident.objects.count(),
        'total_vehicles': Vehicle.objects.count(),
        'total_guards': User.objects.filter(role=User.ROLE_GUARD, is_active=True).count(),
        'pending_residents': Resident.objects.filter(is_approved=False).count(),
        'today_in': VehicleLog.objects.filter(timestamp__gte=day_start, timestamp__lt=day_end, status=VehicleLog.STATUS_IN).count(),
        'today_out': VehicleLog.objects.filter(timestamp__gte=day_start, timestamp__lt=day_end, status=VehicleLog.STATUS_OUT).count(),
        'recent_logs': recent_logs,
        'daily_data': daily_data,
    }
    return render(request, 'dashboard/admin/index.html', context)


@admin_required
def user_management(request):
    q = request.GET.get('q', '').strip()
    role = request.GET.get('role', '').strip().upper()
    status = request.GET.get('status', '').strip()
    users_qs = User.objects.exclude(pk=request.user.pk).exclude(role=User.ROLE_RESIDENT).order_by('role', 'last_name')
    if role in {User.ROLE_ADMIN, User.ROLE_GUARD}:
        users_qs = users_qs.filter(role=role)
    if status == 'active':
        users_qs = users_qs.filter(is_active=True)
    elif status == 'inactive':
        users_qs = users_qs.filter(is_active=False)
    if q:
        users_qs = users_qs.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(contact_number__icontains=q)
        )
    paginator = Paginator(users_qs, 10)
    users = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'dashboard/admin/user_management.html', {
        'users': users,
        'q': q,
        'role': role,
        'status': status,
        'create_form': UserCreateForm(),
        'edit_form_template': UserEditForm(),
    })


@admin_required
def user_create(request):
    if request.method == 'POST':
        form = UserCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'User created successfully.')
            return redirect('user_management')
    else:
        form = UserCreateForm()
    return render(request, 'dashboard/admin/user_form.html', {'form': form, 'action': 'Create'})


@admin_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, 'User updated successfully.')
            return redirect('user_management')
    else:
        form = UserEditForm(instance=user)
    return render(request, 'dashboard/admin/user_form.html', {'form': form, 'action': 'Edit', 'target_user': user})


@admin_required
def user_toggle_active(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        user.is_active = not user.is_active
        user.save()
        state = 'activated' if user.is_active else 'deactivated'
        messages.success(request, f'User {user.get_full_name()} has been {state}.')
    return redirect('user_management')


@admin_required
def user_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        name = user.get_full_name() or user.username
        user.is_active = False
        user.save(update_fields=['is_active'])
        messages.success(request, f'User "{name}" has been deactivated.')
    return redirect('user_management')


# ── Guard views ───────────────────────────────────────────────────────────────

@login_required
def guard_dashboard(request):
    if request.user.is_resident():
        return redirect('resident_dashboard')

    manual_form = ManualLogForm(prefix='manual')
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'create_manual_log':
            manual_form = ManualLogForm(request.POST, prefix='manual')
            if manual_form.is_valid():
                log = manual_form.save(commit=False)
                blacklist_entry = BlacklistEntry.objects.filter(
                    plate_number__iexact=log.plate_number,
                    is_active=True,
                ).first()

                log.source = VehicleLog.SOURCE_MANUAL
                log.logged_by = request.user

                resident_vehicle = Vehicle.objects.select_related('resident').filter(
                    plate_number__iexact=log.plate_number,
                    is_approved=True,
                ).first()
                if resident_vehicle:
                    log.entry_type = VehicleLog.TYPE_RESIDENT
                    log.resident_name = log.resident_name or resident_vehicle.resident.full_name
                    log.visitor_name = ''
                elif log.entry_type == VehicleLog.TYPE_RESIDENT:
                    known_visitor = (
                        Visitor.objects
                        .filter(plate_number__iexact=log.plate_number)
                        .order_by('-created_at')
                        .first()
                    )
                    log.entry_type = VehicleLog.TYPE_VISITOR
                    log.resident_name = ''
                    log.visitor_name = known_visitor.full_name if known_visitor else ''

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
                return redirect('guard_dashboard')
            messages.error(request, 'Failed to save manual log entry. Please complete all required fields.')

    recent_logs = attach_blacklist_metadata(
        VehicleLog.objects.select_related('logged_by').all()[:10]
    )
    context = {
        'recent_logs': recent_logs,
        'manual_form': manual_form,
    }
    context.update(_camera_feed_context())
    return render(request, 'dashboard/guard/index.html', context)


@login_required
def resident_dashboard(request):
    if not request.user.is_resident():
        return redirect('dashboard')

    resident = (
        Resident.objects
        .prefetch_related('vehicles')
        .filter(user=request.user)
        .first()
    )
    if not resident:
        messages.error(request, 'Resident profile not found. Please contact admin.')
        return redirect('logout')

    vehicles = list(resident.vehicles.all())
    plates = [v.plate_number for v in vehicles if v.plate_number]
    active_blacklist = list(
        BlacklistEntry.objects.filter(plate_number__in=plates, is_active=True).order_by('-updated_at')
    )
    bl_map = {entry.plate_number.upper(): entry for entry in active_blacklist}
    for vehicle in vehicles:
        vehicle.blacklist_entry = bl_map.get((vehicle.plate_number or '').upper())

    logs_qs = VehicleLog.objects.filter(plate_number__in=plates).order_by('-timestamp')
    logs = logs_qs[:10]

    if active_blacklist:
        resident_status = 'BLACKLISTED'
        resident_status_reason = active_blacklist[0].remarks or active_blacklist[0].reason or 'Resident has a blacklisted vehicle.'
    elif resident.is_approved:
        resident_status = 'APPROVED'
        resident_status_reason = 'Approved by admin.'
    elif resident.approved_by_id:
        resident_status = 'NOT_APPROVED'
        resident_status_reason = resident.approval_reason or 'Registration rejected by admin.'
    else:
        resident_status = 'PENDING'
        resident_status_reason = resident.approval_reason or 'Waiting for admin approval.'

    context = {
        'resident': resident,
        'vehicles': vehicles,
        'logs': logs,
        'active_blacklist': active_blacklist,
        'resident_status': resident_status,
        'resident_status_reason': resident_status_reason,
        'now_local': timezone.localtime(),
    }
    return render(request, 'dashboard/resident/index.html', context)


@login_required
def resident_vehicles(request):
    if not request.user.is_resident():
        return redirect('dashboard')

    resident = get_object_or_404(Resident.objects.prefetch_related('vehicles'), user=request.user)
    vehicles = list(resident.vehicles.all().order_by('plate_number'))
    plates = [v.plate_number for v in vehicles if v.plate_number]
    active_blacklist = list(
        BlacklistEntry.objects.filter(plate_number__in=plates, is_active=True).order_by('-updated_at')
    )
    bl_map = {entry.plate_number.upper(): entry for entry in active_blacklist}
    for vehicle in vehicles:
        vehicle.blacklist_entry = bl_map.get((vehicle.plate_number or '').upper())

    context = {
        'resident': resident,
        'vehicles': vehicles,
        'now_local': timezone.localtime(),
    }
    return render(request, 'dashboard/resident/vehicles.html', context)


@login_required
def resident_logs(request):
    if not request.user.is_resident():
        return redirect('dashboard')

    resident = get_object_or_404(Resident.objects.prefetch_related('vehicles'), user=request.user)
    plates = list(resident.vehicles.values_list('plate_number', flat=True))
    logs_qs = VehicleLog.objects.filter(plate_number__in=plates).order_by('-timestamp')
    paginator = Paginator(logs_qs, 10)
    logs = paginator.get_page(request.GET.get('page', 1))
    context = {
        'resident': resident,
        'logs': logs,
        'now_local': timezone.localtime(),
    }
    return render(request, 'dashboard/resident/logs.html', context)


@login_required
def resident_profile(request):
    if not request.user.is_resident():
        return redirect('dashboard')

    resident = get_object_or_404(Resident, user=request.user)
    plates = list(resident.vehicles.values_list('plate_number', flat=True))
    active_blacklist = BlacklistEntry.objects.filter(plate_number__in=plates, is_active=True).order_by('-updated_at')
    if active_blacklist.exists():
        resident_status = 'BLACKLISTED'
        resident_status_reason = active_blacklist.first().remarks or active_blacklist.first().reason or 'Resident has a blacklisted vehicle.'
    elif resident.is_approved:
        resident_status = 'APPROVED'
        resident_status_reason = 'Approved by admin.'
    elif resident.approved_by_id:
        resident_status = 'NOT_APPROVED'
        resident_status_reason = resident.approval_reason or 'Registration rejected by admin.'
    else:
        resident_status = 'PENDING'
        resident_status_reason = resident.approval_reason or 'Waiting for admin approval.'

    context = {
        'resident': resident,
        'resident_status': resident_status,
        'resident_status_reason': resident_status_reason,
        'now_local': timezone.localtime(),
    }
    return render(request, 'dashboard/resident/profile.html', context)
