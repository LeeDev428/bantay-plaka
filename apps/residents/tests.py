from types import SimpleNamespace

from django.core.paginator import Paginator
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.template.loader import render_to_string

from apps.residents.forms import ResidentForm, VehicleForm


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
