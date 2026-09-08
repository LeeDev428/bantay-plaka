from django.conf import settings
from django.db import models
from django.utils import timezone


class ActiveManager(models.Manager):
    """Default manager — excludes archived rows. This is what Model.objects becomes."""
    def get_queryset(self):
        return super().get_queryset().filter(is_archived=False)


class ArchivedManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_archived=True)


class Archivable(models.Model):
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    objects = ActiveManager()
    all_objects = models.Manager()
    archived_objects = ArchivedManager()

    class Meta:
        abstract = True
        base_manager_name = "all_objects"

    def archive(self, user=None):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.archived_by = user
        self.save(update_fields=["is_archived", "archived_at", "archived_by"])

    def restore(self):
        self.is_archived = False
        self.archived_at = None
        self.archived_by = None
        self.save(update_fields=["is_archived", "archived_at", "archived_by"])
