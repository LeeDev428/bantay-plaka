from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.decorators import method_decorator

from apps.accounts.forms import LoginForm, UserCreateForm, UserEditForm
from apps.accounts.models import User
from apps.logs.models import VehicleLog


class BantayPlakaLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

    def get_success_url(self):
        return '/dashboard/'


class BantayPlakaLogoutView(LogoutView):
    next_page = '/login/'


@login_required
def dashboard_redirect(request):
    if request.user.is_admin():
        return redirect('admin_dashboard')
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
    from apps.residents.models import Resident, Vehicle
    context = {
        'total_residents': Resident.objects.count(),
        'total_vehicles': Vehicle.objects.count(),
        'total_guards': User.objects.filter(role=User.ROLE_GUARD, is_active=True).count(),
        'recent_logs': VehicleLog.objects.select_related('logged_by').all()[:10],
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
    return render(request, 'dashboard/guard/index.html', {'recent_logs': recent_logs})
