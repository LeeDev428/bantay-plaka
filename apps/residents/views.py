from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404

from apps.accounts.views import admin_required
from apps.residents.models import Resident, Vehicle
from apps.residents.forms import ResidentForm, VehicleForm


@login_required
def resident_list(request):
    residents = Resident.objects.prefetch_related('vehicles').order_by('last_name', 'first_name')
    return render(request, 'residents/resident_list.html', {'residents': residents})


@admin_required
def resident_create(request):
    if request.method == 'POST':
        form = ResidentForm(request.POST)
        if form.is_valid():
            resident = form.save(commit=False)
            resident.registered_by = request.user
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
        form = ResidentForm(request.POST, instance=resident)
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
        resident.delete()
        messages.success(request, 'Resident removed.')
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
            vehicle.save()
            messages.success(request, f'Vehicle {vehicle.plate_number} registered.')
            return redirect('resident_list')
    else:
        form = VehicleForm()
    return render(request, 'residents/vehicle_form.html', {'form': form, 'resident': resident})


@admin_required
def vehicle_delete(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    resident_pk = vehicle.resident_id
    if request.method == 'POST':
        vehicle.delete()
        messages.success(request, 'Vehicle removed.')
    return redirect('resident_list')
