from django.db import models


class ArchivedItem(models.Model):
    ENTITY_USER = 'USER'
    ENTITY_RESIDENT = 'RESIDENT'
    ENTITY_VEHICLE = 'VEHICLE'
    ENTITY_VISITOR = 'VISITOR'
    ENTITY_LOG = 'LOG'

    ENTITY_CHOICES = [
        (ENTITY_USER, 'User'),
        (ENTITY_RESIDENT, 'Resident'),
        (ENTITY_VEHICLE, 'Vehicle'),
        (ENTITY_VISITOR, 'Visitor'),
        (ENTITY_LOG, 'Vehicle Log'),
    ]

    entity_type = models.CharField(max_length=20, choices=ENTITY_CHOICES, db_index=True)
    title = models.CharField(max_length=255, db_index=True)
    source_app = models.CharField(max_length=50, blank=True, db_index=True)
    source_pk = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    archived_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='archived_items',
    )
    archived_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'archived_items'
        ordering = ['-archived_at']
        indexes = [
            models.Index(fields=['source_app', 'source_pk']),
        ]

    def __str__(self):
        return f'{self.get_entity_type_display()} - {self.title}'
