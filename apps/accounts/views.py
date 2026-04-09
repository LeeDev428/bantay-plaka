from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.decorators import method_decorator
from django.utils import timezone
import datetime
from datetime import timedelta

from apps.accounts.forms import LoginForm, UserCreateForm, UserEditForm, ResidentSignupForm
from apps.accounts.models import User
from apps.logs.models import VehicleLog
from apps.logs.services import attach_blacklist_metadata
from apps.residents.models import Resident, Vehicle
from apps.visitors.models import BlacklistEntry


def _camera_feed_context() -> dict:
    latest_entry = (
        VehicleLog.objects
        .filter(
            source=VehicleLog.SOURCE_CAMERA,
            camera_role=VehicleLog.CAMERA_ROLE_ENTRY,
            snapshot__isnull=False,
        )
        .order_by('-timestamp')
        .first()
    )
    latest_exit = (
        VehicleLog.objects
        .filter(
            source=VehicleLog.SOURCE_CAMERA,
            camera_role=VehicleLog.CAMERA_ROLE_EXIT,
            snapshot__isnull=False,
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

    now = timezone.now()
    last_camera_local = timezone.localtime(last_camera_log.timestamp) if last_camera_log else None
    camera_age_seconds = int((now - last_camera_log.timestamp).total_seconds()) if last_camera_log else None
    camera_stale = camera_age_seconds is None or camera_age_seconds > 120

    return {
        'entry_feed': latest_entry,
        'exit_feed': latest_exit,
        'has_entry_camera_stream': bool(getattr(settings, 'ENTRY_CAMERA_RTSP', '').strip()),
        'has_exit_camera_stream': bool(getattr(settings, 'EXIT_CAMERA_RTSP', '').strip()),
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
                f'Registration submitted for {resident.full_name}. Please wait for admin approval before login.'
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

    recent_logs = VehicleLog.objects.select_related('logged_by').all()[:10]
    attach_blacklist_metadata(recent_logs)

    context = {
        'total_residents': Resident.objects.count(),
        'total_vehicles': Vehicle.objects.count(),
        'total_guards': User.objects.filter(role=User.ROLE_GUARD, is_active=True).count(),
        'pending_residents': Resident.objects.filter(is_approved=False).count(),
        'today_in': VehicleLog.objects.filter(timestamp__gte=day_start, timestamp__lt=day_end, status=VehicleLog.STATUS_IN).count(),
        'today_out': VehicleLog.objects.filter(timestamp__gte=day_start, timestamp__lt=day_end, status=VehicleLog.STATUS_OUT).count(),
        'recent_logs': recent_logs,
    }
    return render(request, 'dashboard/admin/index.html', context)


@admin_required
def user_management(request):
    users = User.objects.exclude(pk=request.user.pk).order_by('role', 'last_name')
    return render(request, 'dashboard/admin/user_management.html', {'users': users})


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


# ── Guard views ───────────────────────────────────────────────────────────────

@login_required
def guard_dashboard(request):
    recent_logs = VehicleLog.objects.select_related('logged_by').all()[:10]
    attach_blacklist_metadata(recent_logs)
    context = {'recent_logs': recent_logs}
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

    plates = [v.plate_number for v in resident.vehicles.all() if v.plate_number]
    active_blacklist = BlacklistEntry.objects.filter(plate_number__in=plates, is_active=True).order_by('-updated_at')
    context = {
        'resident': resident,
        'vehicles': resident.vehicles.all(),
        'active_blacklist': active_blacklist,
    }
    return render(request, 'dashboard/resident/index.html', context)
