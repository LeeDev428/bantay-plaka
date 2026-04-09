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

    CAMERA_ROLE_ENTRY = 'ENTRY_CAM'
    CAMERA_ROLE_EXIT = 'EXIT_CAM'
    CAMERA_ROLE_UNKNOWN = 'UNKNOWN'
    CAMERA_ROLE_CHOICES = [
        (CAMERA_ROLE_ENTRY, 'Entry Camera'),
        (CAMERA_ROLE_EXIT, 'Exit Camera'),
        (CAMERA_ROLE_UNKNOWN, 'Unknown'),
    ]

    plate_number = models.CharField(max_length=20, db_index=True)
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES, default=TYPE_VISITOR)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_CAMERA)
    camera_role = models.CharField(
        max_length=20,
        choices=CAMERA_ROLE_CHOICES,
        default=CAMERA_ROLE_UNKNOWN,
        blank=True,
        db_index=True,
    )

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
            return self.visitor_name or self.plate_number
        return self.visitor_name or self.resident_name or self.plate_number

    @property
    def local_time(self):
        """UTC timestamp converted to Asia/Manila local time."""
        from django.utils import timezone
        return timezone.localtime(self.timestamp)

    def __str__(self):
        return f'{self.plate_number} | {self.camera_role} | {self.entry_type} | {self.status} | {self.timestamp:%Y-%m-%d %H:%M}'
