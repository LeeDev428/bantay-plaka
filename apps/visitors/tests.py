from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.archive.registry import ARCHIVE_REGISTRY
from apps.visitors.forms import VisitorForm
from apps.visitors.models import Visitor
from apps.visitors.views import blacklist_archive, visitor_archive


class VisitorFormTests(SimpleTestCase):
    def setUp(self):
        self.data = {
            'first_name': 'Test',
            'last_name': 'Visitor',
            'contact_number': '09123456789',
            'purpose': 'Visit',
            'host_name': 'Resident',
            'plate_number': 'ABC1234',
            'vehicle_type': 'CAR',
            'status': 'TIME_IN',
            'visitor_type': Visitor.TYPE_VISITOR,
        }

    def test_regular_visitor_does_not_require_documents(self):
        form = VisitorForm(data=self.data)

        self.assertTrue(form.is_valid(), form.errors)

    def test_verified_visitor_requires_id_or_and_cr_documents(self):
        form = VisitorForm(data={**self.data, 'visitor_type': Visitor.TYPE_VERIFIED})

        self.assertFalse(form.is_valid())
        for field_name in ('valid_id_document', 'or_document', 'cr_document'):
            self.assertIn(field_name, form.errors)

    def test_verified_visitor_accepts_valid_id_or_and_cr_documents(self):
        png = b'\x89PNG\r\n\x1a\nvalid'
        files = {
            field_name: SimpleUploadedFile(f'{field_name}.png', png, content_type='image/png')
            for field_name in ('valid_id_document', 'or_document', 'cr_document')
        }
        form = VisitorForm(
            data={**self.data, 'visitor_type': Visitor.TYPE_VERIFIED},
            files=files,
        )

        self.assertTrue(form.is_valid(), form.errors)


class VisitorArchiveTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(
            is_authenticated=True,
            is_resident=Mock(return_value=False),
            is_admin=Mock(return_value=True),
            is_guard=Mock(return_value=False),
        )

    def test_visitors_are_registered_in_the_archive_page(self):
        self.assertIs(ARCHIVE_REGISTRY['visitors']['model'], Visitor)

    def test_blacklist_archive_route_uses_archive_path(self):
        self.assertEqual(reverse('blacklist_archive', args=[12]), '/visitors/blacklist/12/archive/')

    def test_post_archives_visitor_and_redirects_to_current_page(self):
        visitor = SimpleNamespace(
            full_name='Test Visitor',
            archive=Mock(),
        )
        request = self.factory.post(
            '/visitors/12/archive/',
            {'next': '/visitors/?visitor_type=VISITOR'},
        )
        request.user = self.user

        with (
            patch('apps.visitors.views.get_object_or_404', return_value=visitor),
            patch('apps.visitors.views.messages.success') as success_message,
        ):
            response = visitor_archive(request, 12)

        visitor.archive.assert_called_once_with(user=self.user)
        success_message.assert_called_once()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/visitors/?visitor_type=VISITOR')

    def test_get_does_not_archive_visitor(self):
        request = self.factory.get('/visitors/12/archive/')
        request.user = self.user

        with patch('apps.visitors.views.get_object_or_404') as get_visitor:
            response = visitor_archive(request, 12)

        get_visitor.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/visitors/')

    def test_resident_cannot_archive_visitor(self):
        self.user.is_resident.return_value = True
        request = self.factory.post('/visitors/12/archive/')
        request.user = self.user

        with (
            patch('apps.visitors.views.get_object_or_404') as get_visitor,
            patch('apps.visitors.views.messages.error') as error_message,
        ):
            response = visitor_archive(request, 12)

        get_visitor.assert_not_called()
        error_message.assert_called_once_with(request, 'Access denied.')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/dashboard/resident/')

    def test_post_archives_blacklist_entry_and_redirects_to_current_page(self):
        entry = SimpleNamespace(
            plate_number='ABC1234',
            archive=Mock(),
        )
        request = self.factory.post(
            '/visitors/blacklist/12/archive/',
            {'next': '/visitors/blacklist/?status=active'},
        )
        request.user = self.user

        with (
            patch('apps.visitors.views.get_object_or_404', return_value=entry),
            patch('apps.visitors.views.messages.success') as success_message,
        ):
            response = blacklist_archive(request, 12)

        entry.archive.assert_called_once_with(user=self.user)
        success_message.assert_called_once()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/visitors/blacklist/?status=active')
