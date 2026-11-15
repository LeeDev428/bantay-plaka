import datetime
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.logs.models import VehicleLog


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ReportPeriodComparisonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='reports_guard',
            password='SecurePass123!',
            role=User.ROLE_GUARD,
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.current_first_day = self.today - timedelta(days=6)
        self.previous_first_day = self.current_first_day - timedelta(days=7)

        self._create_log(self.current_first_day, VehicleLog.STATUS_OUT, 'CURRENT1')
        self._create_log(self.previous_first_day, VehicleLog.STATUS_IN, 'PREVIOUS1')

    def _create_log(self, day, status, plate, entry_type=VehicleLog.TYPE_VISITOR):
        timestamp = timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour=12)))
        return self._create_log_at(timestamp, status, plate, entry_type)

    def _create_log_at(self, timestamp, status, plate, entry_type=VehicleLog.TYPE_VISITOR, visitor_name=''):
        log = VehicleLog.objects.create(
            plate_number=plate,
            entry_type=entry_type,
            status=status,
            source=VehicleLog.SOURCE_MANUAL,
            visitor_name=visitor_name,
        )
        VehicleLog.objects.filter(pk=log.pk).update(timestamp=timestamp)
        return log

    @patch('apps.reports.views.timezone.localdate')
    def test_comparison_uses_the_equivalent_previous_period(self, localdate):
        localdate.return_value = self.today
        response = self.client.get(
            reverse('report_dashboard'),
            {'chart_period': '7', 'compare_period': '1'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['compare_previous_period'])
        self.assertEqual(len(response.context['daily_data']), 7)
        self.assertEqual(response.context['daily_data'][0]['time_out'], 1)
        self.assertEqual(response.context['comparison_data'][0]['time_in'], 1)
        self.assertIn('Previous Period · Time In', response.content.decode())
        self.assertContains(response, 'Compare previous period')

    def test_comparison_is_opt_in(self):
        response = self.client.get(reverse('report_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['compare_previous_period'])
        self.assertEqual(response.context['comparison_data'], [])

    def test_traffic_categories_count_entries_in_the_selected_period(self):
        self._create_log(
            self.current_first_day,
            VehicleLog.STATUS_IN,
            'RESIDENT1',
            entry_type=VehicleLog.TYPE_RESIDENT,
        )
        self._create_log(
            self.current_first_day,
            VehicleLog.STATUS_IN,
            'UNKNOWN1',
            entry_type=VehicleLog.TYPE_UNKNOWN,
        )

        response = self.client.get(
            reverse('report_dashboard'),
            {'chart_period': '7'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['traffic_total'], 3)
        self.assertEqual(
            [(item['label'], item['count']) for item in response.context['traffic_categories']],
            [('Residents', 1), ('Visitors', 1), ('Unknown', 1)],
        )
        self.assertContains(response, 'Traffic by Category')
        self.assertContains(response, 'trafficCategoryChart')

    def test_current_visitors_show_entry_time_and_twelve_hour_overstay(self):
        now = timezone.now()
        self._create_log_at(
            now - timedelta(hours=13),
            VehicleLog.STATUS_IN,
            'OVERSTAY1',
            visitor_name='Sam Guest',
        )
        self._create_log_at(
            now - timedelta(hours=11),
            VehicleLog.STATUS_IN,
            'RECENT01',
            visitor_name='Pat Guest',
        )

        response = self.client.get(reverse('report_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['currently_inside'], 3)
        self.assertEqual(response.context['overstay_count'], 2)
        visitors = {item['plate_number']: item for item in response.context['currently_inside_items']}
        self.assertTrue(visitors['OVERSTAY1']['is_overstay'])
        self.assertFalse(visitors['RECENT01']['is_overstay'])
        self.assertEqual(visitors['OVERSTAY1']['visitor_name'], 'Sam Guest')
        self.assertContains(response, 'Over 12h')
        self.assertContains(response, 'Export CSV')

    def test_current_visitors_section_shows_an_empty_state(self):
        VehicleLog.objects.all().delete()

        response = self.client.get(reverse('report_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['currently_inside'], 0)
        self.assertContains(response, 'No visitors are currently inside.')
