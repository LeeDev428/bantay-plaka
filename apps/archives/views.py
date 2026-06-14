from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render

from apps.archives.models import ArchivedItem


@login_required
def archive_list(request):
    if not request.user.is_admin():
        messages.error(request, 'Access denied. Admin only.')
        return redirect('dashboard')

    q = (request.GET.get('q') or '').strip()
    entity_q = (request.GET.get('entity') or '').strip().upper()

    items_qs = ArchivedItem.objects.select_related('archived_by').all().order_by('-archived_at')
    if entity_q in {c[0] for c in ArchivedItem.ENTITY_CHOICES}:
        items_qs = items_qs.filter(entity_type=entity_q)
    else:
        entity_q = ''

    if q:
        items_qs = items_qs.filter(
            Q(title__icontains=q)
            | Q(notes__icontains=q)
            | Q(source_app__icontains=q)
            | Q(payload__icontains=q)
        )

    paginator = Paginator(items_qs, 15)
    items = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'dashboard/admin/archive_list.html', {
        'items': items,
        'q': q,
        'entity_q': entity_q,
        'entity_choices': ArchivedItem.ENTITY_CHOICES,
    })
