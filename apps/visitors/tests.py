from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.archive.registry import ARCHIVE_REGISTRY
from apps.visitors.models import Visitor
from apps.visitors.views import blacklist_archive, visitor_archive


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
