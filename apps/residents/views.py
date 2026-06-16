from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Q

from apps.accounts.views import admin_required
from apps.accounts.models import User
from apps.residents.models import Resident, Vehicle
from apps.residents.forms import ResidentForm, VehicleForm


@login_required
def resident_list(request):
    if request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('resident_dashboard')

    q = request.GET.get('q', '').strip()
    status_q = request.GET.get('status', '').strip().lower()
    vehicles_q = request.GET.get('vehicles', '').strip().lower()
    resident_form = ResidentForm(prefix='resident')
    vehicle_form = VehicleForm(prefix='vehicle')

    if request.method == 'POST' and request.user.is_admin():
        action = request.POST.get('action', '').strip()
        if action == 'create_resident':
            resident_form = ResidentForm(request.POST, request.FILES, prefix='resident')
            if resident_form.is_valid():
                resident = resident_form.save(commit=False)
                resident.registered_by = request.user
                resident.is_approved = True
                resident.approved_by = request.user
                resident.approved_at = timezone.now()
                resident.approval_reason = ''
                resident.save()
                messages.success(request, f'{resident.full_name} registered successfully.')
                return redirect('resident_list')
            messages.error(request, 'Failed to register resident. Please check the required fields.')

        if action == 'create_vehicle':
            vehicle_form = VehicleForm(request.POST, prefix='vehicle')
            resident_pk = request.POST.get('resident_pk')
            resident = get_object_or_404(Resident, pk=resident_pk) if resident_pk else None
            if resident and vehicle_form.is_valid():
                vehicle = vehicle_form.save(commit=False)
                vehicle.resident = resident
                vehicle.save()
                messages.success(request, f'Vehicle {vehicle.plate_number} registered.')
                return redirect('resident_list')
            messages.error(request, 'Failed to register vehicle. Please complete all required fields.')

    residents_qs = Resident.objects.select_related('user').prefetch_related('vehicles').filter(
        Q(user__isnull=True) | Q(user__role=User.ROLE_RESIDENT)
    ).order_by('is_approved', 'last_name', 'first_name')
    is_read_only_view = not request.user.is_admin()
    if is_read_only_view:
        residents_qs = residents_qs.filter(is_approved=True)

    if status_q == 'approved':
        residents_qs = residents_qs.filter(is_approved=True)
    elif status_q == 'pending':
        residents_qs = residents_qs.filter(is_approved=False, approved_by__isnull=True)
    elif status_q == 'rejected':
        residents_qs = residents_qs.filter(is_approved=False, approved_by__isnull=False)
    else:
        status_q = ''

    if vehicles_q == 'with':
        residents_qs = residents_qs.filter(vehicles__isnull=False).distinct()
    elif vehicles_q == 'without':
        residents_qs = residents_qs.filter(vehicles__isnull=True)
    else:
        vehicles_q = ''

    if q:
        residents_qs = residents_qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(address__icontains=q)
            | Q(vehicles__plate_number__icontains=q)
        ).distinct()

    paginator = Paginator(residents_qs, 10)
    residents = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'residents/resident_list.html', {
        'residents': residents,
        'q': q,
        'resident_form': resident_form,
        'vehicle_form': vehicle_form,
        'is_read_only_view': is_read_only_view,
        'status_q': status_q,
        'vehicles_q': vehicles_q,
    })


@login_required
def resident_vehicle_create_self(request):
    if not request.user.is_resident():
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    resident = get_object_or_404(Resident, user=request.user)
    if request.method != 'POST':
        return redirect('resident_vehicles')

    form = VehicleForm(request.POST)
    if form.is_valid():
        vehicle = form.save(commit=False)
        vehicle.resident = resident
        vehicle.is_approved = False
        vehicle.approved_by = None
        vehicle.approved_at = None
        vehicle.approval_notes = ''
        vehicle.save()
        messages.success(request, f'Vehicle {vehicle.plate_number} submitted and pending admin approval.')
    else:
        for field_errors in form.errors.values():
            for err in field_errors:
                messages.error(request, err)

    return redirect('resident_vehicles')


@admin_required
def resident_create(request):
    if request.method == 'POST':
        form = ResidentForm(request.POST, request.FILES)
        if form.is_valid():
            resident = form.save(commit=False)
            resident.registered_by = request.user
            resident.is_approved = True
            resident.approved_by = request.user
            resident.approved_at = timezone.now()
            resident.approval_reason = ''
            resident.save()
            messages.success(request, f'{resident.full_name} registered successfully.')
            return redirect('resident_list')
    else:
        form = ResidentForm()
    return render(request, 'residents/resident_form.html', {'form': form, 'action': 'Register'})


@admin_required
def resident_edit(request, pk):
    resident = get_object_or_404(Resident, pk=pk)
    if request.method == 'POST':
        form = ResidentForm(request.POST, request.FILES, instance=resident)
        if form.is_valid():
            form.save()
            messages.success(request, 'Resident updated.')
            return redirect('resident_list')
    else:
        form = ResidentForm(instance=resident)
    return render(request, 'residents/resident_form.html', {'form': form, 'action': 'Edit', 'resident': resident})


@admin_required
def resident_delete(request, pk):
    resident = get_object_or_404(Resident, pk=pk)
    if request.method == 'POST':
        resident.is_approved = False
        resident.approval_reason = 'Archived by admin. Record retained.'
        resident.approved_by = request.user
        resident.approved_at = timezone.now()
        resident.save(update_fields=['is_approved', 'approval_reason', 'approved_by', 'approved_at', 'updated_at'])
        if resident.user:
            resident.user.is_active = False
            resident.user.save(update_fields=['is_active'])
        messages.success(request, 'Resident record archived (deactivated).')
    return redirect('resident_list')


@admin_required
def resident_approve(request, pk):
    resident = get_object_or_404(Resident.objects.select_related('user'), pk=pk)
    if request.method == 'POST':
        resident.is_approved = True
        resident.approved_by = request.user
        resident.approved_at = timezone.now()
        resident.approval_reason = ''
        resident.save(update_fields=['is_approved', 'approved_by', 'approved_at', 'approval_reason', 'updated_at'])
        if resident.user:
            resident.user.is_active = True
            resident.user.save(update_fields=['is_active'])
        messages.success(request, f'{resident.full_name} has been approved.')
    return redirect('resident_list')


@admin_required
def resident_reject(request, pk):
    resident = get_object_or_404(Resident.objects.select_related('user'), pk=pk)
    if request.method == 'POST':
        resident.is_approved = False
        resident.approved_by = request.user
        resident.approved_at = timezone.now()
        resident.approval_reason = (request.POST.get('reason') or '').strip() or 'Registration requirements were not satisfied.'
        resident.save(update_fields=['is_approved', 'approved_by', 'approved_at', 'approval_reason', 'updated_at'])
        if resident.user:
            resident.user.is_active = False
            resident.user.save(update_fields=['is_active'])
        messages.success(request, f'{resident.full_name} has been marked as pending/inactive.')
    return redirect('resident_list')


# ── Vehicle management ────────────────────────────────────────────────────────

@admin_required
def vehicle_create(request, resident_pk):
    resident = get_object_or_404(Resident, pk=resident_pk)
    if request.method == 'POST':
        form = VehicleForm(request.POST)
        if form.is_valid():
            vehicle = form.save(commit=False)
            vehicle.resident = resident
            vehicle.is_approved = True
            vehicle.approved_by = request.user
            vehicle.approved_at = timezone.now()
            vehicle.approval_notes = ''
            vehicle.save()
            messages.success(request, f'Vehicle {vehicle.plate_number} registered.')
            return redirect('resident_list')
    else:
        form = VehicleForm()
    return render(request, 'residents/vehicle_form.html', {'form': form, 'resident': resident})


@admin_required
def vehicle_delete(request, pk):
    _ = get_object_or_404(Vehicle, pk=pk)
    if request.method == 'POST':
        messages.warning(request, 'Delete is disabled. Vehicle record was retained.')
    return redirect('resident_list')


@admin_required
def vehicle_approval_list(request):
    vehicle_form = VehicleForm(prefix='vehicle')
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'create_vehicle':
            vehicle_form = VehicleForm(request.POST, prefix='vehicle')
            resident_pk = request.POST.get('resident_pk')
            resident = get_object_or_404(Resident, pk=resident_pk) if resident_pk else None
            if resident and vehicle_form.is_valid():
                vehicle = vehicle_form.save(commit=False)
                vehicle.resident = resident
                vehicle.is_approved = True
                vehicle.approved_by = request.user
                vehicle.approved_at = timezone.now()
                vehicle.approval_notes = ''
                vehicle.save()
                messages.success(request, f'Vehicle {vehicle.plate_number} registered for {resident.full_name}.')
                return redirect('vehicle_approval_list')
            messages.error(request, 'Failed to register vehicle. Please complete all required fields.')

    q = request.GET.get('q', '').strip()
    type_q = request.GET.get('type', '').strip().upper()
    vehicles_qs = Vehicle.objects.select_related('resident').filter(is_approved=False, approval_notes='').order_by('-created_at')
    if q:
        vehicles_qs = vehicles_qs.filter(
            Q(plate_number__icontains=q)
            | Q(resident__first_name__icontains=q)
            | Q(resident__last_name__icontains=q)
        )
    if type_q in {Vehicle.TYPE_CAR, Vehicle.TYPE_MOTORCYCLE, Vehicle.TYPE_TRUCK, Vehicle.TYPE_VAN, Vehicle.TYPE_OTHER}:
        vehicles_qs = vehicles_qs.filter(vehicle_type=type_q)
    else:
        type_q = ''

    paginator = Paginator(vehicles_qs, 10)
    vehicles = paginator.get_page(request.GET.get('page', 1))

    residents = Resident.objects.filter(is_approved=True).filter(
        Q(user__isnull=True) | Q(user__role=User.ROLE_RESIDENT)
    ).order_by('last_name', 'first_name')

    return render(request, 'residents/vehicle_approvals.html', {
        'vehicles': vehicles,
        'q': q,
        'type_q': type_q,
        'residents': residents,
        'vehicle_form': vehicle_form,
    })


@admin_required
def vehicle_approve(request, pk):
    vehicle = get_object_or_404(Vehicle.objects.select_related('resident'), pk=pk)
    if request.method == 'POST':
        vehicle.is_approved = True
        vehicle.approved_by = request.user
        vehicle.approved_at = timezone.now()
        vehicle.approval_notes = ''
        vehicle.save(update_fields=['is_approved', 'approved_by', 'approved_at', 'approval_notes'])
        messages.success(request, f'Vehicle {vehicle.plate_number} approved.')
    return redirect(request.POST.get('next', 'vehicle_approval_list'))


@admin_required
def vehicle_reject(request, pk):
    vehicle = get_object_or_404(Vehicle.objects.select_related('resident'), pk=pk)
    if request.method == 'POST':
        reason = (request.POST.get('reason') or '').strip()
        vehicle.approval_notes = reason or 'Vehicle registration requirements were not satisfied.'
        vehicle.save(update_fields=['approval_notes'])
        messages.success(request, f'Pending vehicle {vehicle.plate_number} was rejected.')
    return redirect(request.POST.get('next', 'vehicle_approval_list'))
