from django.db import models
from apps.accounts.models import User


class Resident(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    contact_number = models.CharField(max_length=20, blank=True)
    registered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='registered_residents'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'residents'
        ordering = ['last_name', 'first_name']

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'

    def __str__(self):
        return self.full_name


class Vehicle(models.Model):
    TYPE_CAR = 'CAR'
    TYPE_MOTORCYCLE = 'MOTORCYCLE'
    TYPE_TRUCK = 'TRUCK'
    TYPE_VAN = 'VAN'
    TYPE_OTHER = 'OTHER'
    VEHICLE_TYPE_CHOICES = [
        (TYPE_CAR, 'Car'),
        (TYPE_MOTORCYCLE, 'Motorcycle'),
        (TYPE_TRUCK, 'Truck'),
        (TYPE_VAN, 'Van'),
        (TYPE_OTHER, 'Other'),
    ]

    resident = models.ForeignKey(Resident, on_delete=models.CASCADE, related_name='vehicles')
    plate_number = models.CharField(max_length=20, unique=True, db_index=True)
    vehicle_type = models.CharField(max_length=15, choices=VEHICLE_TYPE_CHOICES, default=TYPE_CAR)
    make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    color = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vehicles'
        ordering = ['plate_number']

    def __str__(self):
        return f'{self.plate_number} — {self.resident.full_name}'
