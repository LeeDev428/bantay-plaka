from django.db import models
from apps.accounts.models import User


class VehicleLog(models.Model):
    SOURCE_CAMERA = 'CAMERA'
    SOURCE_MANUAL = 'MANUAL'
    SOURCE_CHOICES = [
        (SOURCE_CAMERA, 'Camera'),
        (SOURCE_MANUAL, 'Manual'),
    ]

    TYPE_RESIDENT = 'RESIDENT'
    TYPE_VISITOR = 'VISITOR'
    TYPE_UNKNOWN = 'UNKNOWN'
    ENTRY_TYPE_CHOICES = [
        (TYPE_RESIDENT, 'Resident'),
        (TYPE_VISITOR, 'Visitor'),
        (TYPE_UNKNOWN, 'Unknown'),
    ]

    STATUS_IN = 'TIME_IN'
    STATUS_OUT = 'TIME_OUT'
    STATUS_CHOICES = [
        (STATUS_IN, 'Time In'),
        (STATUS_OUT, 'Time Out'),
    ]

    plate_number = models.CharField(max_length=20, db_index=True)
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES, default=TYPE_UNKNOWN)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_CAMERA)

    # optional links depending on entry type
    resident_name = models.CharField(max_length=200, blank=True)
    visitor_name = models.CharField(max_length=200, blank=True)

    snapshot = models.ImageField(upload_to='snapshots/', null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    logged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='vehicle_logs'
    )

    class Meta:
        db_table = 'vehicle_logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['plate_number', 'timestamp']),
        ]

    def get_display_name(self):
        if self.entry_type == self.TYPE_RESIDENT:
            return self.resident_name or self.plate_number
        if self.entry_type == self.TYPE_VISITOR:
            return self.visitor_name or 'Visitor'
        return 'Unknown'

    def __str__(self):
        return f'{self.plate_number} | {self.entry_type} | {self.status} | {self.timestamp:%Y-%m-%d %H:%M}'
