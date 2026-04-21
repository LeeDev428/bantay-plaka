from django import forms

from apps.logs.models import VehicleLog
from apps.visitors.models import Visitor, BlacklistEntry


class VisitorForm(forms.ModelForm):
    status = forms.ChoiceField(
        choices=VehicleLog.STATUS_CHOICES,
        initial=VehicleLog.STATUS_IN,
        widget=forms.Select(attrs={'class': 'select select-bordered w-full'}),
    )

    class Meta:
        model = Visitor
        fields = [
            'first_name',
            'last_name',
            'contact_number',
            'purpose',
            'host_name',
            'plate_number',
            'vehicle_type',
            'vehicle_type_other',
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'last_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'contact_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'purpose': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'e.g. Visit, Delivery'}),
            'host_name': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Resident being visited'}),
            'plate_number': forms.TextInput(attrs={
                'class': 'input input-bordered w-full uppercase',
                'placeholder': 'e.g. ABC 1234',
            }),
            'vehicle_type': forms.Select(
                choices=[('', '-- Select --'), ('CAR', 'Car'), ('MOTORCYCLE', 'Motorcycle'), ('TRUCK', 'Truck'), ('VAN', 'Van'), ('OTHER', 'Other')],
                attrs={'class': 'select select-bordered w-full'},
            ),
            'vehicle_type_other': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'Specify vehicle type (if Other)',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [
            (VehicleLog.STATUS_IN, 'In'),
            (VehicleLog.STATUS_OUT, 'Out'),
        ]
        self.fields['status'].label = 'Status'
        for field_name in ['first_name', 'last_name', 'contact_number', 'purpose', 'host_name', 'plate_number', 'vehicle_type', 'status']:
            self.fields[field_name].required = True

    def clean_plate_number(self):
        return self.cleaned_data['plate_number'].upper().strip()

    def clean(self):
        cleaned = super().clean()
        vehicle_type = cleaned.get('vehicle_type')
        vehicle_type_other = (cleaned.get('vehicle_type_other') or '').strip()
        if vehicle_type == 'OTHER' and not vehicle_type_other:
            self.add_error('vehicle_type_other', 'Please specify the custom vehicle type.')
        if vehicle_type != 'OTHER':
            cleaned['vehicle_type_other'] = ''
        return cleaned


class BlacklistEntryForm(forms.ModelForm):
    class Meta:
        model = BlacklistEntry
        fields = ['plate_number', 'tag', 'reason', 'remarks']
        widgets = {
            'plate_number': forms.TextInput(attrs={
                'class': 'input input-bordered w-full uppercase',
                'placeholder': 'e.g. ABC 1234',
                'maxlength': 20,
            }),
            'tag': forms.Select(attrs={
                'class': 'select select-bordered w-full',
            }),
            'reason': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'Reason',
                'maxlength': 255,
            }),
            'remarks': forms.Textarea(attrs={
                'class': 'textarea textarea-bordered w-full',
                'placeholder': 'Detailed notes',
                'rows': 3,
            }),
        }
        labels = {
            'remarks': 'Notes',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['reason'].required = True
        self.fields['remarks'].required = True

    def clean_reason(self):
        value = (self.cleaned_data.get('reason') or '').strip()
        if not value:
            raise forms.ValidationError('Reason is required.')
        return value

    def clean_remarks(self):
        value = (self.cleaned_data.get('remarks') or '').strip()
        if not value:
            raise forms.ValidationError('Notes are required.')
        return value

    def clean_plate_number(self):
        return self.cleaned_data['plate_number'].upper().strip()
