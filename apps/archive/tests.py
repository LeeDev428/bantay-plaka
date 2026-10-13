from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase

from apps.archive.views import archive_list


class ArchiveListTests(SimpleTestCase):
    def test_context_includes_section_counts_and_recent_slices(self):
        class ArchivedQuerySet:
            def __init__(self, count):
                self.item_count = count

            def order_by(self, *_args):
                return self

            def filter(self, *_args, **_kwargs):
                return self

            def exclude(self, **_kwargs):
                return self

            def count(self):
                return self.item_count

            def __getitem__(self, key):
                return ('recent-items', key)

        registry = {
            'visitors': {
                'model': SimpleNamespace(archived_objects=ArchivedQuerySet(7)),
                'label': 'Visitors',
            },
            'videos': {
                'model': SimpleNamespace(archived_objects=ArchivedQuerySet(2)),
                'label': 'Recorded Videos',
            },
        }
        request = RequestFactory().get('/archive/')
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_admin=Mock(return_value=True),
        )

        with (
            patch('apps.archive.views.ARCHIVE_REGISTRY', registry),
            patch('apps.archive.views.render', return_value='rendered') as render,
        ):
            response = archive_list(request)

        context = render.call_args.args[2]
        self.assertEqual(response, 'rendered')
        self.assertEqual(context['total_count'], 9)
        self.assertEqual([section['count'] for section in context['sections']], [7, 2])
        self.assertEqual(
            [section['recent_items'] for section in context['sections']],
            [('recent-items', slice(None, 5, None))] * 2,
        )
