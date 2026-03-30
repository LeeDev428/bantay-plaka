import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _anpr_rtsp(rtsp_url: str) -> str:
    """Use main stream for ANPR for better plate readability."""
    return (rtsp_url or '').replace('/Streaming/Channels/102', '/Streaming/Channels/101')


class Command(BaseCommand):
    help = 'Launch Django server and 2-camera ANPR workers in separate terminal windows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--server',
            choices=['runserver', 'daphne'],
            default='runserver',
            help='Server backend to launch. Default: runserver',
        )
        parser.add_argument('--host', default='127.0.0.1', help='Server host. Default: 127.0.0.1')
        parser.add_argument('--port', default='8000', help='Server port. Default: 8000')
        parser.add_argument(
            '--webcam',
            action='store_true',
            help='Use local webcam for ENTRY_CAM worker (temporary camera-less testing mode).',
        )
        parser.add_argument(
            '--webcam-index',
            default='0',
            help='Webcam index to use when --webcam is enabled. Default: 0',
        )
        parser.add_argument(
            '--strict-roles',
            action='store_true',
            dest='strict_roles',
            help='Use strict camera-role mapping (default: enabled).',
        )
        parser.add_argument(
            '--no-strict-roles',
            action='store_false',
            dest='strict_roles',
            help='Disable strict camera-role mapping.',
        )
        parser.set_defaults(strict_roles=True)

    def handle(self, *args, **options):
        webcam_mode = bool(options.get('webcam'))
        webcam_index = str(options.get('webcam_index') or '0').strip()
        strict_roles = bool(options.get('strict_roles'))

        entry_rtsp = (getattr(settings, 'ENTRY_CAMERA_RTSP', '') or '').strip()
        exit_rtsp = (getattr(settings, 'EXIT_CAMERA_RTSP', '') or '').strip()
        if not webcam_mode and (not entry_rtsp or not exit_rtsp):
            raise CommandError(
                'ENTRY_CAMERA_RTSP and EXIT_CAMERA_RTSP must be set in .env before running runstack. '
                'Or run with --webcam for temporary local testing.'
            )

        host = options['host']
        port = str(options['port'])
        server_mode = options['server']

        base_dir = Path(settings.BASE_DIR)
        preferred_py = base_dir / 'venv' / 'Scripts' / 'python.exe'
        py = str(preferred_py) if preferred_py.exists() else sys.executable

        if preferred_py.exists() and Path(sys.executable).resolve() != preferred_py.resolve():
            self.stdout.write(
                self.style.WARNING(
                    f'Current interpreter is {sys.executable}. Using canonical interpreter: {py}'
                )
            )

        def _cmdline(parts: list[str]) -> str:
            return subprocess.list2cmdline(parts)

        def _spawn(title: str, parts: list[str]):
            # Keep terminal open after process exits so startup errors are visible.
            command = f'title {title} && {_cmdline(parts)}'
            subprocess.Popen(
                ['cmd.exe', '/k', command],
                cwd=str(base_dir),
                creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0),
            )
            self.stdout.write(self.style.SUCCESS(f'Started {title}: {_cmdline(parts)}'))

        if server_mode == 'daphne':
            django_cmd = [py, '-m', 'daphne', '-b', host, '-p', port, 'config.asgi:application']
            django_title = 'BantayPlaka - Daphne'
        else:
            django_cmd = [py, 'manage.py', 'runserver', f'{host}:{port}', '--noreload']
            django_title = 'BantayPlaka - Django'

        launches = [(django_title, django_cmd)]

        if webcam_mode:
            entry_cmd = [
                py,
                'anpr_engine/anpr_engine.py',
                '--rtsp', webcam_index,
                '--mode', 'yolo',
                '--frame-skip', '1',
            ]
            if strict_roles:
                entry_cmd.extend(['--camera-role', 'ENTRY_CAM'])
            launches.append(('BantayPlaka - ENTRY CAM (Webcam)', entry_cmd))
            self.stdout.write(self.style.WARNING('Webcam mode enabled: only ENTRY_CAM worker will be started.'))
        else:
            entry_cmd = [
                py,
                'anpr_engine/anpr_engine.py',
                '--rtsp', _anpr_rtsp(entry_rtsp),
                '--frame-skip', '1',
                '--rtsp-drain-grabs', '3',
                '--no-preview',
            ]
            exit_cmd = [
                py,
                'anpr_engine/anpr_engine.py',
                '--rtsp', _anpr_rtsp(exit_rtsp),
                '--frame-skip', '1',
                '--rtsp-drain-grabs', '3',
                '--no-preview',
            ]
            if strict_roles:
                entry_cmd.extend(['--camera-role', 'ENTRY_CAM'])
                exit_cmd.extend(['--camera-role', 'EXIT_CAM'])
            launches.append(('BantayPlaka - ENTRY CAM', entry_cmd))
            launches.append(('BantayPlaka - EXIT CAM', exit_cmd))

        for title, command in launches:
            _spawn(title, command)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Open http://{host}:{port}/'))
        if webcam_mode:
            self.stdout.write('Keep the 2 spawned windows open (server + ENTRY webcam worker).')
        else:
            self.stdout.write('Keep the 3 spawned windows open (server + ENTRY + EXIT).')
        self.stdout.write('Do not run an extra manual runserver in another terminal.')