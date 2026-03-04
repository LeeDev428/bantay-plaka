from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_ADMIN = 'ADMIN'
    ROLE_GUARD = 'GUARD'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_GUARD, 'Security Guard'),
    ]

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_GUARD)
    contact_number = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    def is_guard(self):
        return self.role == self.ROLE_GUARD

    def __str__(self):
        return f'{self.get_full_name()} ({self.role})'
