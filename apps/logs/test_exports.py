from datetime import datetime, timezone as datetime_timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from openpyxl import load_workbook

from apps.logs.export_views import export_logs_excel, export_logs_pdf


class VehicleLogExportChartTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(
            is_authenticated=True,
            is_resident=lambda: False,
        )
        self.logs = [
            SimpleNamespace(
                status='TIME_IN',
                timestamp=datetime(2026, 10, 10, 3, 0, tzinfo=datetime_timezone.utc),
                plate_number='ABC1234',
                entry_type='VISITOR',
                source='CAMERA',
                camera_role='ENTRY_CAM',
                resident_name='',
                visitor_name='Guest',
                logged_by=None,
                get_entry_type_display=lambda: 'Visitor',
                get_source_display=lambda: 'Camera',
                get_camera_role_display=lambda: 'Entry Camera',
            ),
            SimpleNamespace(
                status='TIME_OUT',
                timestamp=datetime(2026, 10, 10, 4, 0, tzinfo=datetime_timezone.utc),
                plate_number='XYZ9876',
                entry_type='RESIDENT',
                source='CAMERA',
                camera_role='EXIT_CAM',
                resident_name='Resident',
                visitor_name='',
                logged_by=None,
                get_entry_type_display=lambda: 'Resident',
                get_source_display=lambda: 'Camera',
                get_camera_role_display=lambda: 'Exit Camera',
            ),
            SimpleNamespace(
                status='TIME_IN',
                timestamp=datetime(2026, 10, 11, 3, 0, tzinfo=datetime_timezone.utc),
                plate_number='ABC1234',
                entry_type='VISITOR',
                source='CAMERA',
                camera_role='ENTRY_CAM',
                resident_name='',
                visitor_name='Guest',
                logged_by=None,
                get_entry_type_display=lambda: 'Visitor',
                get_source_display=lambda: 'Camera',
                get_camera_role_display=lambda: 'Entry Camera',
            ),
        ]
        self.queryset_patch = patch(
            'apps.logs.export_views.VehicleLog.objects.select_related',
            return_value=SimpleNamespace(order_by=lambda *args: self.logs),
        )
        self.queryset_patch.start()
        self.addCleanup(self.queryset_patch.stop)
        self.blacklist_patch = patch(
            'apps.logs.export_views.get_active_blacklist_map',
            return_value={},
        )
        self.blacklist_patch.start()
        self.addCleanup(self.blacklist_patch.stop)

    def _request(self, path):
        request = self.factory.get(path)
        request.user = self.user
        return request

    def test_excel_export_includes_both_charts(self):
        response = export_logs_excel(self._request('/logs/export/excel/'))
        workbook = load_workbook(BytesIO(response.content))
        summary = workbook['Summary']

        self.assertEqual(len(summary._charts), 2)
        self.assertEqual(summary['A26'].value, 'Time In')
        self.assertEqual(summary['B26'].value, 2)
        self.assertEqual(summary['A27'].value, 'Time Out')
        self.assertEqual(summary['B27'].value, 1)
        self.assertEqual(summary['D26'].value, '2026-10-10')
        self.assertEqual(summary['E26'].value, 2)

    def test_excel_export_shows_na_when_no_records_are_available(self):
        self.logs.clear()

        response = export_logs_excel(self._request('/logs/export/excel/'))
        workbook = load_workbook(BytesIO(response.content))
        summary = workbook['Summary']

        self.assertEqual(summary._charts, [])
        self.assertEqual(summary['D2'].value, 'Time In vs. Time Out: N/A')
        self.assertEqual(summary['L2'].value, 'Records per Day: N/A')

    def test_pdf_export_builds_with_both_charts(self):
        response = export_logs_pdf(self._request('/logs/export/pdf/'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 1000)

    def test_pdf_export_builds_na_visuals_when_no_records_are_available(self):
        self.logs.clear()

        response = export_logs_pdf(self._request('/logs/export/pdf/'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 1000)
