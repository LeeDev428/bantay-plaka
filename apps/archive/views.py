import datetime

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.views import admin_required
from .registry import ARCHIVE_REGISTRY

# Slugs whose model enforces a conditional "unique while active" constraint
# on plate_number. Restoring one of these must not silently create a second
# active row with the same plate.
PLATE_UNIQUE_SLUGS = {"vehicles", "blacklist"}

SEARCH_FIELDS = {
    "residents": (
        "first_name", "middle_name", "last_name", "address", "contact_number",
    ),
    "vehicles": (
        "plate_number", "resident__first_name", "resident__last_name",
    ),
    "logs": (
        "plate_number", "resident_name", "visitor_name", "camera_role", "status",
    ),
    "snapshots": (
        "plate_number", "resident_name", "visitor_name", "camera_role", "status",
    ),
    "videos": ("source_filename", "camera_role", "plate_search"),
    "alerts": (
        "camera_role", "reason", "recording__source_filename", "recording__plate_search",
    ),
    "blacklist": ("plate_number", "tag", "reason", "remarks"),
    "visitors": (
        "first_name", "last_name", "contact_number", "purpose", "host_name",
        "plate_number", "visitor_type",
    ),
}


@admin_required
def archive_list(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    valid_categories = set(ARCHIVE_REGISTRY)
    if category not in valid_categories:
        category = ""

    date_from_value = _parse_filter_date(date_from)
    date_to_value = _parse_filter_date(date_to)
    if date_from and date_from_value is None:
        date_from = ""
    if date_to and date_to_value is None:
        date_to = ""

    sections = []
    total_count = 0
    for slug, config in ARCHIVE_REGISTRY.items():
        qs = config["model"].archived_objects.order_by("-archived_at")
        if slug == "logs":
            qs = qs.filter(Q(snapshot__isnull=True) | Q(snapshot=""))
        elif slug == "snapshots":
            qs = qs.filter(snapshot__isnull=False).exclude(snapshot="")
        if query:
            search_query = Q()
            for field in SEARCH_FIELDS[slug]:
                search_query |= Q(**{f"{field}__icontains": query})
            search_query |= (
                Q(archived_by__first_name__icontains=query)
                | Q(archived_by__last_name__icontains=query)
                | Q(archived_by__username__icontains=query)
            )
            qs = qs.filter(search_query)
        if date_from_value:
            qs = qs.filter(archived_at__date__gte=date_from_value)
        if date_to_value:
            qs = qs.filter(archived_at__date__lte=date_to_value)
        count = qs.count()
        total_count += count
        sections.append({
            "slug": slug,
            "label": config["label"],
            "items": qs,
            "recent_items": qs[:5],
            "count": count,
        })
    return render(request, "archive/archive_list.html", {
        "sections": sections,
        "total_count": total_count,
        "query": query,
        "category": category,
        "date_from": date_from,
        "date_to": date_to,
    })


def _parse_filter_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


@admin_required
def restore_item(request, slug, pk):
    config = ARCHIVE_REGISTRY.get(slug)
    if not config:
        messages.error(request, "Unknown archive category.")
        return redirect("archive:list")
    obj = get_object_or_404(config["model"].all_objects, pk=pk, is_archived=True)
    if request.method == "POST":
        if slug in PLATE_UNIQUE_SLUGS and getattr(obj, "plate_number", None):
            conflict = config["model"].objects.filter(plate_number=obj.plate_number).exists()
            if conflict:
                messages.error(
                    request,
                    f"Cannot restore: plate {obj.plate_number} is already registered to an active record. "
                    f"Archive or change that record first.",
                )
                return redirect("archive:list")
        obj.restore()
        # Special case: restoring a resident should reactivate their linked user account.
        if slug == "residents" and getattr(obj, "user_id", None):
            obj.user.is_active = True
            obj.user.save(update_fields=["is_active"])
        messages.success(request, f"{config['label']} item restored.")
    return redirect("archive:list")


@admin_required
def permanent_delete_item(request, slug, pk):
    config = ARCHIVE_REGISTRY.get(slug)
    if not config:
        messages.error(request, "Unknown archive category.")
        return redirect("archive:list")
    obj = get_object_or_404(config["model"].all_objects, pk=pk, is_archived=True)
    if request.method == "POST":
        obj.delete()
        messages.success(request, f"{config['label']} item permanently deleted.")
    return redirect("archive:list")
