import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.audit.models import UserActivity
from apps.audit.services import audit_export
from apps.audit.signals import record_login, record_logout
from apps.audit.views import activity_list


class UserActivityViewTests(SimpleTestCase):
    class ActivityQuery(list):
        def select_related(self, *_args):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def count(self):
            return len(self)

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_admin_can_view_filtered_activity(self):
        event = SimpleNamespace(
            actor_username='guard1',
            actor_role='Security Guard',
            action=UserActivity.ACTION_CREATE,
            summary='Added visitor Alex',
            target_type='visitor',
            target_id='7',
            created_at=datetime.datetime(2026, 11, 4, 1, 0, tzinfo=datetime.timezone.utc),
            get_action_display=lambda: 'Create',
        )
        request = RequestFactory().get('/activity/?action=CREATE&q=guard1')
        request.resolver_match = SimpleNamespace(url_name='user_activity', namespace='')
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_admin=lambda: True,
            is_guard=lambda: False,
            is_resident=lambda: False,
            username='admin',
            first_name='Admin',
            role='ADMIN',
            get_full_name=lambda: 'Admin User',
            get_role_display=lambda: 'Admin',
        )
        with patch(
            'apps.audit.views.UserActivity.objects.select_related',
            return_value=self.ActivityQuery([event]),
        ):
            response = activity_list(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'User Activity Logs')
        self.assertContains(response, 'Added visitor Alex')
        self.assertContains(response, 'guard1')
        self.assertContains(response, 'visitor #7')
        self.assertNotRegex(
            response.content.decode(),
            r'href="/dashboard/admin/users/" class="sidebar-link active"',
        )
        self.assertRegex(
            response.content.decode(),
            r'href="/activity/" class="sidebar-link active"',
        )

    def test_non_admin_is_redirected_from_activity_page(self):
        request = RequestFactory().get('/activity/')
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_admin=lambda: False,
            is_resident=lambda: False,
        )

        with patch('apps.accounts.views.messages.error'):
            response = activity_list(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/dashboard/guard/')


class UserActivitySignalTests(SimpleTestCase):
    def test_login_and_logout_signals_record_actor(self):
        user = SimpleNamespace(pk=9)
        request = RequestFactory().get('/')

        with patch('apps.audit.signals.record_user_activity') as record:
            record_login(sender=None, request=request, user=user)
            record_logout(sender=None, request=request, user=user)

        self.assertEqual(record.call_count, 2)
        self.assertEqual(record.call_args_list[0].kwargs['action'], UserActivity.ACTION_LOGIN)
        self.assertEqual(record.call_args_list[1].kwargs['action'], UserActivity.ACTION_LOGOUT)
        self.assertEqual(record.call_args_list[0].kwargs['actor'], user)

    def test_logout_without_user_does_not_create_activity(self):
        request = RequestFactory().get('/')
        with patch('apps.audit.signals.record_user_activity') as record:
            record_logout(sender=None, request=request, user=None)
        record.assert_not_called()


class AuditExportTests(SimpleTestCase):
    def test_successful_export_is_recorded(self):
        user = SimpleNamespace(
            is_authenticated=True,
            is_resident=lambda: False,
        )
        request = SimpleNamespace(user=user)
        view = audit_export('vehicle report (Excel)')(lambda _request: HttpResponse('report'))

        with patch('apps.audit.services.record_user_activity') as record:
            response = view(request)

        self.assertEqual(response.status_code, 200)
        record.assert_called_once_with(
            actor=user,
            action=UserActivity.ACTION_EXPORT,
            summary='Exported vehicle report (Excel)',
            target_type='report',
        )

    def test_failed_export_is_not_recorded(self):
        user = SimpleNamespace(
            is_authenticated=True,
            is_resident=lambda: False,
        )
        request = SimpleNamespace(user=user)
        view = audit_export('vehicle report (Excel)')(lambda _request: HttpResponse(status=500))

        with patch('apps.audit.services.record_user_activity') as record:
            view(request)

        record.assert_not_called()
