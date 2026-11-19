from types import SimpleNamespace

from django.core.paginator import Paginator
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.residents.forms import ResidentForm, VehicleForm
from apps.residents.models import Resident


class ResidentVehicleModalTests(SimpleTestCase):
    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_resident_vehicle_modal_shows_vehicle_details_and_admin_actions(self):
        vehicle = SimpleNamespace(
            pk=23,
            plate_number='ABC1234',
            get_vehicle_type_display=lambda: 'Car',
            make='Toyota',
            model='Vios',
            color='White',
            is_approved=True,
            approval_notes='',
        )
        resident = SimpleNamespace(
            pk=7,
            full_name='Jamie Resident',
            first_name='Jamie',
            middle_name='',
            last_name='Resident',
            suffix='',
            street_number='',
            street_name='',
            address='Paragon Village',
            contact_number='09123456789',
            get_valid_id_type_display=lambda: "Driver's License",
            has_valid_id_image=False,
            is_approved=True,
            user=None,
            approved_by=None,
            approval_reason='',
            vehicles=SimpleNamespace(all=lambda: [vehicle]),
        )
        user = SimpleNamespace(
            is_authenticated=True,
            is_admin=True,
            is_guard=False,
            is_resident=False,
            username='admin',
            first_name='Admin',
            role='ADMIN',
            get_full_name=lambda: 'Test Admin',
            get_role_display=lambda: 'Administrator',
        )
        request = RequestFactory().get('/residents/')
        request.resolver_match = SimpleNamespace(url_name='resident_list')

        html = render_to_string('residents/resident_list.html', {
            'user': user,
            'request': request,
            'residents': Paginator([resident], 10).get_page(1),
            'q': '',
            'status_q': '',
            'vehicles_q': '',
            'is_read_only_view': False,
            'resident_form': ResidentForm(prefix='resident'),
            'edit_resident_form': ResidentForm(prefix='edit'),
            'vehicle_form': VehicleForm(prefix='vehicle'),
        })

        self.assertIn('See Vehicles', html)
        self.assertIn('resident-vehicles-modal', html)
        self.assertIn('data-resident-name="Jamie Resident"', html)
        self.assertIn('ABC1234', html)
        self.assertIn('Toyota', html)
        self.assertIn('Approved', html)
        self.assertIn('Add Vehicle', html)
        self.assertIn('/residents/vehicles/23/delete/', html)
        self.assertIn('resident-info-modal', html)
        self.assertIn('View resident information', html)
        self.assertIn('Approve &amp; Activate', html)
        self.assertIn('resident-info-archive-form', html)
        self.assertIn('resident-edit-modal', html)
        self.assertIn('data-resident-edit-modal', html)
        self.assertNotIn('data-reject-resident', html)
        self.assertIn('resident-id-document-modal', html)
        self.assertNotIn('id="resident-id-loading"', html)
        self.assertIn('id="resident-id-document-loading" class="hidden', html)
        self.assertIn('residentIdModal.querySelector(\'#resident-id-document-loading\')', html)


class VehicleApprovalDocumentModalTests(SimpleTestCase):
    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_vehicle_approval_document_links_open_modal_preview(self):
        vehicle = SimpleNamespace(
            pk=23,
            plate_number='ABC1234',
            resident=SimpleNamespace(full_name='Jamie Resident'),
            get_vehicle_type_display=lambda: 'Car',
            make='Toyota',
            model='Vios',
            color='White',
            or_document=True,
            cr_document=True,
            created_at=None,
        )
        user = SimpleNamespace(
            is_authenticated=True,
            is_admin=True,
            is_guard=False,
            is_resident=False,
            username='admin',
            first_name='Admin',
            role='ADMIN',
            get_full_name=lambda: 'Test Admin',
            get_role_display=lambda: 'Administrator',
        )
        request = RequestFactory().get('/residents/vehicles/approvals/')
        request.resolver_match = SimpleNamespace(url_name='vehicle_approval_list')

        html = render_to_string('residents/vehicle_approvals.html', {
            'user': user,
            'request': request,
            'vehicles': Paginator([vehicle], 10).get_page(1),
            'q': '',
            'type_q': '',
            'residents': [],
            'vehicle_form': VehicleForm(prefix='vehicle'),
        })

        self.assertIn('vehicle-document-modal', html)
        self.assertIn('data-document-modal', html)
        self.assertIn('View OR', html)
        self.assertIn('View CR', html)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ResidentListEditTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='resident_editor',
            password='SecurePass123!',
            role=User.ROLE_ADMIN,
        )
        self.resident = Resident.objects.create(
            first_name='Jamie',
            last_name='Resident',
            address='Paragon Village',
            contact_number='09123456789',
            registered_by=self.admin,
        )
        self.client.force_login(self.admin)

    def test_edit_modal_post_updates_resident(self):
        response = self.client.post(reverse('resident_list'), {
            'action': 'edit_resident',
            'resident_pk': self.resident.pk,
            'edit-first_name': 'Jamie Updated',
            'edit-middle_name': '',
            'edit-last_name': 'Resident',
            'edit-suffix': '',
            'edit-sex': '',
            'edit-birth_date': '',
            'edit-age': '',
            'edit-contact_number': '09111111111',
            'edit-telephone_number': '',
            'edit-street_number': '',
            'edit-street_name': '',
            'edit-address': 'Updated Address',
            'edit-valid_id_type': 'PHILSYS_ID',
        })

        self.assertRedirects(response, reverse('resident_list'))
        self.resident.refresh_from_db()
        self.assertEqual(self.resident.first_name, 'Jamie Updated')
        self.assertEqual(self.resident.address, 'Updated Address')
        self.assertEqual(self.resident.contact_number, '09111111111')

    def test_invalid_edit_keeps_modal_open_with_field_errors(self):
        response = self.client.post(reverse('resident_list'), {
            'action': 'edit_resident',
            'resident_pk': self.resident.pk,
            'edit-first_name': 'Jamie',
            'edit-middle_name': '',
            'edit-last_name': 'Resident',
            'edit-suffix': '',
            'edit-sex': '',
            'edit-birth_date': '',
            'edit-age': '',
            'edit-contact_number': '123',
            'edit-telephone_number': '',
            'edit-street_number': '',
            'edit-street_name': '',
            'edit-address': 'Paragon Village',
            'edit-valid_id_type': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Failed to update resident')
        self.assertContains(response, 'Contact number must be 11 digits')
        self.assertContains(response, 'id="resident-edit-modal" class="fixed inset-0 z-[58] flex')
