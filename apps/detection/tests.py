import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.detection import views


@override_settings(
    DEBUG=True,
    ANPR_API_KEY='test-camera-key',
    ANPR_ALLOW_WEBCAM_HEARTBEATS=False,
    ENTRY_CAMERA_RTSP='rtsp://entry-camera.example:554/Streaming/Channels/101',
    EXIT_CAMERA_RTSP='rtsp://exit-camera.example:554/Streaming/Channels/101',
)
class CameraHeartbeatSourceTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.snapshot_b64 = base64.b64encode(b'test-frame').decode('ascii')

    def _post_heartbeat(self, camera_source):
        payload = {
            'camera_role': 'ENTRY_CAM',
            'camera_source': camera_source,
            'snapshot_b64': self.snapshot_b64,
        }
        request = self.factory.post(
            '/detection/ingest-frame/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='test-camera-key',
        )
        return views.ingest_camera_frame(request)

    def test_rejects_webcam_heartbeat_for_configured_ip_camera(self):
        with patch.dict(views._FRAME_CACHE, {'ENTRY_CAM': (b'camera-frame', time.time())}):
            response = self._post_heartbeat('0')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(views._FRAME_CACHE['ENTRY_CAM'][0], b'camera-frame')

    def test_rejects_heartbeat_from_other_camera_host(self):
        with patch.object(views, '_update_live_camera_snapshot'), patch(
            'apps.logs.services.broadcast_camera_frame'
        ):
            response = self._post_heartbeat('rtsp://other-camera.example:554/Streaming/Channels/102')

        self.assertEqual(response.status_code, 409)

    def test_accepts_configured_camera_substream(self):
        with patch.dict(views._FRAME_CACHE, {}):
            with (
                patch.object(views, '_update_live_camera_snapshot'),
                patch('apps.logs.services.broadcast_camera_frame'),
                patch.object(
                    views.CameraFeedSnapshot.objects,
                    'filter',
                    return_value=SimpleNamespace(first=lambda: None),
                ),
            ):
                response = self._post_heartbeat('rtsp://entry-camera.example:554/Streaming/Channels/102')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(views._FRAME_CACHE['ENTRY_CAM'][0], b'test-frame')


@override_settings(ANPR_API_KEY='test-camera-key', DEBUG=True)
class RecordingIngestTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _post_recording(self, **payload):
        data = {
            'recording': SimpleUploadedFile('vehicle_entry.mp4', b'test-video', content_type='video/mp4'),
            'camera_role': 'ENTRY_CAM',
            'metadata': json.dumps({
                'started_at': '2026-10-07T03:00:00',
                'duration_seconds': 4.2,
                'plates': [{'plate': 'ABC1234'}],
            }),
        }
        data.update(payload)
        request = self.factory.post(
            '/detection/ingest-recording/',
            data=data,
            HTTP_X_API_KEY='test-camera-key',
        )
        return views.ingest_recording(request)

    def test_persists_recording_metadata_and_file(self):
        saved = SimpleNamespace(pk=12)
        with patch.object(
            views.VideoRecording.objects,
            'filter',
            return_value=SimpleNamespace(first=lambda: None),
        ), patch.object(views.VideoRecording.objects, 'create', return_value=saved) as create:
            response = self._post_recording()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['recording_id'], 12)
        self.assertEqual(create.call_args.kwargs['source_filename'], 'vehicle_entry.mp4')
        self.assertEqual(create.call_args.kwargs['camera_role'], 'ENTRY_CAM')
        self.assertEqual(create.call_args.kwargs['plate_search'], 'ABC1234')

    def test_requires_mp4(self):
        response = self._post_recording(
            recording=SimpleUploadedFile('vehicle_entry.txt', b'not-video', content_type='text/plain')
        )

        self.assertEqual(response.status_code, 400)

    def test_creates_alert_and_broadcasts_when_plate_is_unreadable(self):
        saved = SimpleNamespace(pk=13)
        alert = SimpleNamespace(pk=4)
        payload = {
            'recording': SimpleUploadedFile('vehicle_entry.mp4', b'test-video', content_type='video/mp4'),
            'alert_snapshot': SimpleUploadedFile('vehicle_entry.jpg', b'test-image', content_type='image/jpeg'),
            'camera_role': 'ENTRY_CAM',
            'metadata': json.dumps({
                'started_at': '2026-10-07T03:00:00',
                'duration_seconds': 4.2,
                'plates': [],
                'unrecognized_plate_alert': True,
            }),
        }
        request = self.factory.post(
            '/detection/ingest-recording/',
            data=payload,
            HTTP_X_API_KEY='test-camera-key',
        )
        with (
            patch.object(
                views.VideoRecording.objects,
                'filter',
                return_value=SimpleNamespace(first=lambda: None),
            ),
            patch.object(views.VideoRecording.objects, 'create', return_value=saved),
            patch.object(views.UnrecognizedPlateAlert.objects, 'create', return_value=alert) as create_alert,
            patch.object(views, 'broadcast_unrecognized_plate_alert') as broadcast,
        ):
            response = views.ingest_recording(request)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(json.loads(response.content)['alert_created'])
        self.assertEqual(create_alert.call_args.kwargs['recording'], saved)
        self.assertEqual(create_alert.call_args.kwargs['camera_role'], 'ENTRY_CAM')
        broadcast.assert_called_once_with(alert)

    @override_settings(
        DEBUG=False,
        USE_CLOUDINARY=True,
        CLOUDINARY_CLOUD_NAME='',
        CLOUDINARY_API_KEY='',
        CLOUDINARY_API_SECRET='',
    )
    def test_production_upload_requires_durable_media_storage(self):
        response = self._post_recording()

        self.assertEqual(response.status_code, 503)
