from django import forms
from django.utils import timezone

from apps.residents.models import Resident, Vehicle


class ResidentForm(forms.ModelForm):
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
            'contact_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'valid_id_type': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'valid_id_image': forms.ClearableFileInput(attrs={'class': 'file-input file-input-bordered w-full'}),
        }

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

    def clean_plate_number(self):
        return self.cleaned_data['plate_number'].upper().strip()
