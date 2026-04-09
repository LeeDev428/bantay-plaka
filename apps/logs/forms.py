from django import forms
from apps.logs.models import VehicleLog


class ManualLogForm(forms.ModelForm):
    class Meta:
        model = VehicleLog
        fields = ['plate_number', 'entry_type', 'status', 'visitor_name']
        widgets = {
            'plate_number': forms.TextInput(attrs={
                'class': 'input input-bordered w-full uppercase',
                'placeholder': 'e.g. ABC 1234',
                'maxlength': 20,
            }),
            'entry_type': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'status': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'visitor_name': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'Visitor name (if visitor)',
            }),
        }

    def clean_plate_number(self):
        return self.cleaned_data['plate_number'].upper().strip()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry_type'].choices = [
            (VehicleLog.TYPE_RESIDENT, 'Resident'),
            (VehicleLog.TYPE_VISITOR, 'Visitor'),
        ]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('entry_type') == VehicleLog.TYPE_RESIDENT:
            cleaned['visitor_name'] = ''
        return cleaned


class LogEditForm(forms.ModelForm):
    class Meta:
        model = VehicleLog
        fields = ['plate_number', 'entry_type', 'status', 'resident_name', 'visitor_name']
        widgets = {
            'plate_number': forms.TextInput(attrs={
                'class': 'input input-bordered w-full uppercase',
                'maxlength': 20,
            }),
            'entry_type': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'status': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'resident_name': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'Resident full name',
            }),
            'visitor_name': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'Visitor name',
            }),
        }

    def clean_plate_number(self):
        return self.cleaned_data['plate_number'].upper().strip()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry_type'].choices = [
            (VehicleLog.TYPE_RESIDENT, 'Resident'),
            (VehicleLog.TYPE_VISITOR, 'Visitor'),
        ]

    def clean(self):
        cleaned = super().clean()
        entry_type = cleaned.get('entry_type')
        if entry_type == VehicleLog.TYPE_RESIDENT:
            cleaned['visitor_name'] = ''
            if not cleaned.get('resident_name'):
                self.add_error('resident_name', 'Resident name is required for resident logs.')
        elif entry_type == VehicleLog.TYPE_VISITOR:
            cleaned['resident_name'] = ''
            if not cleaned.get('visitor_name'):
                self.add_error('visitor_name', 'Visitor name is required for visitor logs.')
        return cleaned
