from django.db import models
from apps.accounts.models import User


class Visitor(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=20, blank=True)
    purpose = models.CharField(max_length=255, blank=True)
    host_name = models.CharField(max_length=200, blank=True, help_text='Name of resident being visited')
    plate_number = models.CharField(max_length=20, blank=True, db_index=True)
    vehicle_type = models.CharField(max_length=15, blank=True)
    logged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='logged_visitors'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'visitors'
        ordering = ['-created_at']

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'

    def __str__(self):
        return f'{self.full_name} — {self.plate_number or "No Plate"}'
