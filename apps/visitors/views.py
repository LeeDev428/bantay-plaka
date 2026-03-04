from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect

from apps.visitors.models import Visitor
from apps.visitors.forms import VisitorForm
from apps.logs.models import VehicleLog
from apps.logs.services import broadcast_log


@login_required
def visitor_log_entry(request):
    """Guard logs a visitor coming in (TIME IN)."""
    if request.method == 'POST':
        form = VisitorForm(request.POST)
        if form.is_valid():
            visitor = form.save(commit=False)
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
    visitors = Visitor.objects.select_related('logged_by').order_by('-created_at')[:50]
    return render(request, 'visitors/visitor_list.html', {'visitors': visitors})
