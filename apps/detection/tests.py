import base64
import json
import queue
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.detection import views
from apps.logs.models import VehicleLog


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

    def test_persists_recording_thumbnail(self):
        saved = SimpleNamespace(pk=12)
        thumbnail = SimpleUploadedFile(
            'vehicle_entry.jpg', b'test-thumbnail', content_type='image/jpeg',
        )
        with patch.object(
            views.VideoRecording.objects,
            'filter',
            return_value=SimpleNamespace(first=lambda: None),
        ), patch.object(views.VideoRecording.objects, 'create', return_value=saved) as create:
            response = self._post_recording(thumbnail=thumbnail)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs['thumbnail'].name, 'vehicle_entry.jpg')

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


@override_settings(ANPR_API_KEY='test-camera-key', DEBUG=True)
class CameraPlateIngestTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _ingest(self, plate, camera_role):
        request = self.factory.post(
            '/detection/ingest/',
            data=json.dumps({
                'plate_number': plate,
                'camera_role': camera_role,
            }),
            content_type='application/json',
            HTTP_X_API_KEY='test-camera-key',
        )
        return views.ingest_plate(request)

    def test_exit_is_not_debounced_by_recent_entry(self):
        VehicleLog.objects.create(
            plate_number='ABC 1234',
            entry_type=VehicleLog.TYPE_VISITOR,
            status=VehicleLog.STATUS_IN,
            source=VehicleLog.SOURCE_CAMERA,
            camera_role=VehicleLog.CAMERA_ROLE_ENTRY,
        )

        with patch.object(views, 'resolve_plate', return_value={
            'entry_type': VehicleLog.TYPE_VISITOR,
            'resident_name': '',
            'visitor_name': '',
        }), patch.object(views, 'broadcast_log'):
            response = self._ingest('ABC 1234', 'EXIT_CAM')

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['status'], VehicleLog.STATUS_OUT)
        self.assertEqual(VehicleLog.objects.filter(plate_number='ABC 1234').count(), 2)

    def test_repeat_on_same_camera_role_is_debounced(self):
        with patch.object(views, 'resolve_plate', return_value={
            'entry_type': VehicleLog.TYPE_VISITOR,
            'resident_name': '',
            'visitor_name': '',
        }), patch.object(views, 'broadcast_log'):
            first = self._ingest('XYZ 5678', 'ENTRY_CAM')
            second = self._ingest('XYZ 5678', 'ENTRY_CAM')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(json.loads(second.content)['skipped'])
        self.assertEqual(VehicleLog.objects.filter(plate_number='XYZ 5678').count(), 1)


class ANPRClipEventTests(SimpleTestCase):
    def test_pending_recording_is_recovered_from_disk(self):
        from anpr_engine.anpr_engine import ANPREngine

        engine = ANPREngine.__new__(ANPREngine)
        engine._recording_upload_queue = queue.Queue(maxsize=10)

        class RecorderStub:
            active = False
            _path = None

        with tempfile.TemporaryDirectory() as temp_dir:
            RecorderStub.output_dir = Path(temp_dir)
            engine._recorder = RecorderStub()
            pending = Path(temp_dir) / 'vehicle_pending_entry.mp4'
            pending.write_bytes(b'test-video')

            engine._recover_pending_recordings()

            self.assertEqual(engine._recording_upload_queue.get_nowait(), pending)

    def test_recognized_plate_blocked_by_episode_limit_is_not_an_unreadable_alert(self):
        from anpr_engine.anpr_engine import ANPREngine

        engine = ANPREngine.__new__(ANPREngine)
        engine._state_lock = threading.Lock()
        engine._clip_events = []
        engine._episode_id = 2
        engine.vehicle_detector = object()
        engine._episode_logged = True
        engine._dropped_by_episode = 0

        with patch('anpr_engine.anpr_engine.ONE_PLATE_PER_EPISODE', True):
            result = engine._try_log_plate(None, 'ABC1234', 0.92, None, None, 'detector')

        self.assertEqual(result, 'episode')
        self.assertEqual(len(engine._clip_events), 1)
        self.assertEqual(engine._clip_events[0]['plate'], 'ABC1234')
        self.assertFalse(engine._clip_events[0]['sent_to_django'])
        unrecognized_plate_alert = not bool(engine._clip_events)
        self.assertFalse(unrecognized_plate_alert)

    def test_event_before_recording_start_is_associated_with_its_vehicle_episode(self):
        from anpr_engine.anpr_engine import ANPREngine

        class RecorderStub:
            active = True
            started_at = 100.0

            def stop(self, events, reason):
                self.events = events
                return None, 0.0

        engine = ANPREngine.__new__(ANPREngine)
        engine._state_lock = threading.Lock()
        engine._clip_events = [{
            'time': '2026-10-08T07:00:00',
            'ts': 99.0,
            'plate': 'ABC1234',
            'ocr_confidence': 0.92,
            'source': 'detector',
            'sent_to_django': True,
            'snapshot': None,
            'episode_id': 2,
        }]
        engine._recording_episode_id = 2
        engine._recorder = RecorderStub()
        engine.cooldown_seconds = 0.0
        engine._cooldown_until = 0.0
        engine._recording_upload_enabled = False
        engine._clips_saved = 0

        engine._stop_clip('vehicle left')

        self.assertEqual(len(engine._recorder.events), 1)
        self.assertEqual(engine._recorder.events[0]['plate'], 'ABC1234')
        self.assertNotIn('episode_id', engine._recorder.events[0])
        self.assertIsNone(engine._recording_episode_id)
