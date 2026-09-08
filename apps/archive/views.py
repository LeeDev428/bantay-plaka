from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.views import admin_required
from .registry import ARCHIVE_REGISTRY

# Slugs whose model enforces a conditional "unique while active" constraint
# on plate_number. Restoring one of these must not silently create a second
# active row with the same plate.
PLATE_UNIQUE_SLUGS = {"vehicles", "blacklist"}


@admin_required
def archive_list(request):
    sections = []
    for slug, config in ARCHIVE_REGISTRY.items():
        qs = config["model"].archived_objects.order_by("-archived_at")
        sections.append({"slug": slug, "label": config["label"], "items": qs})
    return render(request, "archive/archive_list.html", {"sections": sections})


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
