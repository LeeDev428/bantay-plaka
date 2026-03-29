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

    def handle(self, *args, **options):
        entry_rtsp = (getattr(settings, 'ENTRY_CAMERA_RTSP', '') or '').strip()
        exit_rtsp = (getattr(settings, 'EXIT_CAMERA_RTSP', '') or '').strip()
        if not entry_rtsp or not exit_rtsp:
            raise CommandError(
                'ENTRY_CAMERA_RTSP and EXIT_CAMERA_RTSP must be set in .env before running runstack.'
            )

        host = options['host']
        port = str(options['port'])
        server_mode = options['server']

        base_dir = Path(settings.BASE_DIR)
        py = sys.executable
        create_console = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)

        if server_mode == 'daphne':
            django_cmd = [py, '-m', 'daphne', '-b', host, '-p', port, 'config.asgi:application']
            django_title = 'BantayPlaka - Daphne'
        else:
            django_cmd = [py, 'manage.py', 'runserver', f'{host}:{port}']
            django_title = 'BantayPlaka - Django'

        entry_cmd = [
            py,
            'anpr_engine/anpr_engine.py',
            '--rtsp', _anpr_rtsp(entry_rtsp),
            '--camera-role', 'ENTRY_CAM',
            '--frame-skip', '1',
            '--no-preview',
        ]
        exit_cmd = [
            py,
            'anpr_engine/anpr_engine.py',
            '--rtsp', _anpr_rtsp(exit_rtsp),
            '--camera-role', 'EXIT_CAM',
            '--frame-skip', '1',
            '--no-preview',
        ]

        launches = [
            (django_title, django_cmd),
            ('BantayPlaka - ENTRY CAM', entry_cmd),
            ('BantayPlaka - EXIT CAM', exit_cmd),
        ]

        for title, command in launches:
            subprocess.Popen(
                command,
                cwd=str(base_dir),
                creationflags=create_console,
            )
            self.stdout.write(self.style.SUCCESS(f'Started {title}: {" ".join(command)}'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Open http://{host}:{port}/'))
        self.stdout.write('Keep the 3 spawned windows open (server + ENTRY + EXIT).')
        self.stdout.write('Do not run an extra manual runserver in another terminal.')