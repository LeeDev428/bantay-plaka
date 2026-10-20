from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.logs.models import VehicleLog
from apps.residents.models import Resident, Vehicle


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ResidentAccountPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='jamie_resident',
            email='jamie@example.com',
            password='SecurePass123!',
            role=User.ROLE_RESIDENT,
        )
        cls.resident = Resident.objects.create(
            user=cls.user,
            first_name='Jamie',
            last_name='Resident',
            address='Paragon Village',
            contact_number='09123456789',
            is_approved=True,
        )
        cls.vehicle = Vehicle.objects.create(
            resident=cls.resident,
            plate_number='ABC1234',
            vehicle_type=Vehicle.TYPE_CAR,
            make='Toyota',
            model='Vios',
            color='White',
            is_approved=False,
        )
        VehicleLog.objects.create(
            plate_number=cls.vehicle.plate_number,
            entry_type=VehicleLog.TYPE_RESIDENT,
            status=VehicleLog.STATUS_IN,
            source=VehicleLog.SOURCE_CAMERA,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_dashboard_shows_resident_vehicle_and_gate_activity_summary(self):
        response = self.client.get(reverse('resident_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pending_vehicle_count'], 1)
        self.assertEqual(response.context['today_log_count'], 1)
        self.assertContains(response, 'Gate movements today')
        self.assertContains(response, 'Recent gate activity')
        self.assertContains(response, 'ABC1234')

    def test_vehicle_page_uses_add_vehicle_modal_without_year_field(self):
        response = self.client.get(reverse('resident_vehicles'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'open-add-vehicle')
        self.assertContains(response, 'add-vehicle-modal')
        self.assertContains(response, 'Submit for Approval')
        self.assertNotContains(response, 'name="year"')
        self.assertEqual(response.context['pending_vehicle_count'], 1)

    def test_invalid_vehicle_submission_reopens_registration_modal(self):
        response = self.client.post(reverse('resident_vehicle_create_self'), {})

        self.assertRedirects(response, f"{reverse('resident_vehicles')}?add_vehicle=1")

    def test_profile_includes_login_and_contact_details(self):
        response = self.client.get(reverse('resident_profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'jamie_resident')
        self.assertContains(response, 'jamie@example.com')
        self.assertContains(response, 'Account created')
        self.assertContains(response, 'Mobile number')
        self.assertContains(response, 'Privacy notice accepted')
        self.assertEqual(response.context['vehicle_count'], 1)
