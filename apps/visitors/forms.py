from django import forms
from apps.visitors.models import Visitor


class VisitorForm(forms.ModelForm):
    class Meta:
        model = Visitor
        fields = ['first_name', 'last_name', 'contact_number', 'purpose', 'host_name', 'plate_number', 'vehicle_type']
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
        }

    def clean_plate_number(self):
        return self.cleaned_data['plate_number'].upper().strip()
