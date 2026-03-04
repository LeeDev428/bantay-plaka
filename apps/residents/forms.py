from django import forms
from apps.residents.models import Resident, Vehicle


class ResidentForm(forms.ModelForm):
    class Meta:
        model = Resident
        fields = ['first_name', 'last_name', 'address', 'contact_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'last_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'address': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'contact_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
        }


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
