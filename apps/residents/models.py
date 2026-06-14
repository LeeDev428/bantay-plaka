from tokenize import Comment

from django.db import models
from django.core.files.storage import default_storage
from apps.accounts.models import User


class Resident(models.Model):
    SEX_MALE = 'MALE'
    SEX_FEMALE = 'FEMALE'
    SEX_CHOICES = [
        (SEX_MALE, 'Male'),
        (SEX_FEMALE, 'Female'),
    ]
    VALID_ID_TYPE_CHOICES = [
        ('', "-- Select --"),
        ('DRIVERS_LICENSE', "Driver's License"),
    ]

    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    suffix = models.CharField(max_length=20, blank=True)
    sex = models.CharField(max_length=10, choices=SEX_CHOICES, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    address = models.CharField(max_length=255)
    street_number = models.CharField(max_length=50, blank=True)
    street_name = models.CharField(max_length=150, blank=True)
    contact_number = models.CharField(max_length=20, blank=True)
    valid_id_type = models.CharField(max_length=80, choices=VALID_ID_TYPE_CHOICES, blank=True)
    valid_id_image = models.ImageField(upload_to='resident_ids/', null=True, blank=True)
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resident_profile',
    )
    is_approved = models.BooleanField(default=True, db_index=True)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_residents',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_reason = models.CharField(max_length=255, blank=True)
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
        middle = f' {self.middle_name}' if self.middle_name else ''
        suffix = f' {self.suffix}' if self.suffix else ''
        return f'{self.first_name}{middle} {self.last_name}{suffix}'.strip()

    def __str__(self):
        return self.full_name

    @property
    def has_valid_id_image(self):
        if not self.valid_id_image:
            return False
        name = (self.valid_id_image.name or '').strip()
        if not name:
            return False
        try:
            return default_storage.exists(name)
        except Exception:
            return False


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
    is_approved = models.BooleanField(default=True, db_index=True)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_vehicles',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.CharField(max_length=255, blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='archived_vehicles',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vehicles'
        ordering = ['plate_number']

    def __str__(self):
        return f'{self.plate_number} — {self.resident.full_name}'


class VehicleDocument(models.Model):
    STATUS_OK = 'OK'
    STATUS_NEEDS_UPDATE = 'NEEDS_UPDATE'
    STATUS_CHOICES = [
        (STATUS_OK, 'OK'),
        (STATUS_NEEDS_UPDATE, 'Needs Update'),
    ]

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='documents')
    file = models.ImageField(upload_to='vehicle_documents/orcr/')
    registration_year = models.PositiveSmallIntegerField(db_index=True)
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OK, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_vehicle_documents',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicle_documents'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.vehicle.plate_number} OR/CR {self.registration_year}'
