import json
import tempfile
import datetime
from pathlib import Path
from types import SimpleNamespace

from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from apps.logs.views import alert_gallery, recording_file, video_gallery
from apps.logs.models import VideoRecording


class VideoGalleryTests(SimpleTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.recordings_dir = Path(self.temp_dir.name)
        self.override = override_settings(
            ANPR_RECORDINGS_DIR=self.recordings_dir,
            STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.factory = RequestFactory()
        self.video_records = patch('apps.logs.views.VideoRecording.objects.all', return_value=[])
        self.video_records.start()
        self.addCleanup(lambda: self.video_records.stop())
        self.user = SimpleNamespace(
            is_authenticated=True,
            is_resident=lambda: False,
            is_admin=lambda: False,
            is_guard=lambda: True,
            username='test-guard',
            first_name='Test',
            role='GUARD',
            get_full_name=lambda: 'Test Guard',
            get_role_display=lambda: 'Security Guard',
        )

    def _request(self, path):
        request = self.factory.get(path)
        request.user = self.user
        request.resolver_match = SimpleNamespace(url_name='video_gallery', namespace='')
        return request

    def test_video_gallery_renders_local_recording_metadata(self):
        recording = self.recordings_dir / 'vehicle_entry.mp4'
        recording.write_bytes(b'local-video')
        recording.with_suffix('.json').write_text(json.dumps({
            'camera_role': 'ENTRY_CAM',
            'started_at': '2026-10-07T03:00:00',
            'duration_seconds': 4.2,
            'plates': [{'plate': 'ABC1234'}],
        }), encoding='utf-8')

        response = video_gallery(self._request(reverse('video_gallery')))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'vehicle_entry.mp4')
        self.assertContains(response, 'ABC1234')
        self.assertContains(response, reverse('recording_file', args=['vehicle_entry.mp4']))

    def test_video_gallery_renders_cloud_recordings(self):
        cloud_recording = SimpleNamespace(
            source_filename='hosted_entry.mp4',
            camera_role='ENTRY_CAM',
            get_camera_role_display=lambda: 'Entry Camera',
            plates=[{'plate': 'ABC1234'}],
            started_at=None,
            duration_seconds=4.2,
            size_bytes=1024,
            uploaded_at=datetime.datetime(2026, 10, 7, 3, 0, tzinfo=datetime.timezone.utc),
            video=SimpleNamespace(url='https://media.example/hosted_entry.mp4'),
        )
        self.video_records.stop()
        self.video_records = patch(
            'apps.logs.views.VideoRecording.objects.all',
            return_value=[cloud_recording],
        )
        self.video_records.start()

        response = video_gallery(self._request(reverse('video_gallery')))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hosted_entry.mp4')
        self.assertContains(response, 'ABC1234')
        self.assertContains(response, 'https://media.example/hosted_entry.mp4')

    def test_recording_file_serves_only_mp4_in_recordings_directory(self):
        recording = self.recordings_dir / 'clip.mp4'
        recording.write_bytes(b'local-video')

        response = recording_file(self._request('/logs/videos/clip.mp4/'), 'clip.mp4')

        self.assertEqual(response['Content-Type'], 'video/mp4')
        self.assertEqual(b''.join(response.streaming_content), b'local-video')

    def test_recording_file_rejects_path_traversal(self):
        with self.assertRaises(Http404):
            recording_file(self._request('/logs/videos/../outside.mp4/'), '../outside.mp4')

    def test_alert_gallery_renders_snapshot_and_clip_links(self):
        alert = SimpleNamespace(
            snapshot=SimpleNamespace(url='https://media.example/alert.jpg'),
            recording=SimpleNamespace(video=SimpleNamespace(url='https://media.example/clip.mp4')),
            camera_role='ENTRY_CAM',
            get_camera_role_display=lambda: 'Entry Camera',
            reason='Vehicle detected but plate could not be recognized',
            created_at=datetime.datetime(2026, 10, 7, 3, 0, tzinfo=datetime.timezone.utc),
        )
        alerts = patch(
            'apps.logs.views.UnrecognizedPlateAlert.objects.select_related',
            return_value=SimpleNamespace(all=lambda: [alert]),
        )
        alerts.start()
        self.addCleanup(alerts.stop)

        response = alert_gallery(self._request(reverse('alert_gallery')))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Plate not recognized')
        self.assertContains(response, 'https://media.example/alert.jpg')
        self.assertContains(response, 'https://media.example/clip.mp4')

    @override_settings(
        USE_CLOUDINARY=True,
        CLOUDINARY_CLOUD_NAME='test-cloud',
        CLOUDINARY_API_KEY='test-key',
        CLOUDINARY_API_SECRET='test-secret',
    )
    def test_video_field_uses_cloudinary_video_resource_type(self):
        storage = VideoRecording._meta.get_field('video').storage

        self.assertEqual(storage.backend.RESOURCE_TYPE, 'video')
