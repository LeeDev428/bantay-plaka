from django import forms
from django.utils import timezone
import re

from apps.residents.models import Resident, Vehicle


CONTACT_NUMBER_REGEX = re.compile(r'^09\d{9}$')


class ResidentForm(forms.ModelForm):
    valid_id_type = forms.ChoiceField(
        choices=Resident.VALID_ID_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'select select-bordered w-full'}),
    )

    class Meta:
        model = Resident
        fields = [
            'first_name',
            'middle_name',
            'last_name',
            'suffix',
            'sex',
            'birth_date',
            'age',
            'contact_number',
            'street_number',
            'street_name',
            'address',
            'valid_id_type',
            'valid_id_image',
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'middle_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'last_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'suffix': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'sex': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'birth_date': forms.DateInput(attrs={'class': 'input input-bordered w-full', 'type': 'date'}),
            'age': forms.NumberInput(attrs={'class': 'input input-bordered w-full', 'min': 0}),
            'address': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'street_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'street_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'contact_number': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': '09XXXXXXXXX',
                'maxlength': 11,
                'inputmode': 'numeric',
            }),
            'valid_id_image': forms.ClearableFileInput(attrs={'class': 'file-input file-input-bordered w-full'}),
        }

    def clean_contact_number(self):
        value = (self.cleaned_data.get('contact_number') or '').strip()
        if value and not CONTACT_NUMBER_REGEX.match(value):
            raise forms.ValidationError('Contact number must be 11 digits and start with 09 (example: 09XXXXXXXXX).')
        return value

    def clean(self):
        cleaned = super().clean()
        birth_date = cleaned.get('birth_date')
        if birth_date:
            today = timezone.localdate()
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
            if age >= 0:
                cleaned['age'] = age
        return cleaned


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ['plate_number', 'vehicle_type', 'make', 'model', 'color']
        widgets = {
            'plate_number': forms.TextInput(attrs={
                'class': 'input input-bordered w-full uppercase',
                'placeholder': 'e.g. ABC 1234',
            }),
            'vehicle_type': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'make': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'e.g. Toyota'}),
            'model': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'e.g. Vios'}),
            'color': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'e.g. White'}),
        }

        labels = {
            'make': 'Brand',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ['plate_number', 'vehicle_type', 'make', 'model', 'color']:
            self.fields[field_name].required = True

    def clean_plate_number(self):
        return self.cleaned_data['plate_number'].upper().strip()
