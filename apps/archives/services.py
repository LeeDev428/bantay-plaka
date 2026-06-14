import datetime
import decimal

from django.forms.models import model_to_dict

from apps.archives.models import ArchivedItem


def _serialize_value(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if hasattr(value, 'name') and hasattr(value, 'url'):
        # Handles File/Image fields safely.
        return getattr(value, 'name', '') or ''
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


def serialize_instance(instance):
    payload = model_to_dict(instance)
    serialized = {k: _serialize_value(v) for k, v in payload.items()}
    serialized['pk'] = instance.pk
    return serialized


def archive_instance(instance, entity_type: str, archived_by=None, notes: str = '', extra_payload: dict | None = None):
    payload = serialize_instance(instance)
    if extra_payload:
        payload.update({k: _serialize_value(v) for k, v in extra_payload.items()})

    return ArchivedItem.objects.create(
        entity_type=entity_type,
        title=str(instance),
        source_app=instance._meta.app_label,
        source_pk=instance.pk,
        payload=payload,
        notes=notes,
        archived_by=archived_by,
    )
