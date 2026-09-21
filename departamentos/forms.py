from datetime import date

from django import forms

from .models import Concepto, Departamento, GastoEdificio, GastoFijo, Proveedor, TarifaAgua

MESES = [
    (1, 'Enero'), (2, 'Febrero'), (3, 'Marzo'), (4, 'Abril'),
    (5, 'Mayo'), (6, 'Junio'), (7, 'Julio'), (8, 'Agosto'),
    (9, 'Setiembre'), (10, 'Octubre'), (11, 'Noviembre'), (12, 'Diciembre'),
]

_control = {'class': 'form-control'}
_select = {'class': 'form-select'}


class MesAnioForm(forms.Form):
    """Selector de periodo"""
    mes = forms.TypedChoiceField(choices=MESES, coerce=int, widget=forms.Select(attrs=_select))
    anio = forms.IntegerField(label='Año', min_value=2000, max_value=2100,
                              widget=forms.NumberInput(attrs=_control))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hoy = date.today()
        self.fields['mes'].initial = hoy.month
        self.fields['anio'].initial = hoy.year

    @property
    def periodo(self):
        return date(self.cleaned_data['anio'], self.cleaned_data['mes'], 1)


class GenerarRecibosForm(MesAnioForm):
    dia_vencimiento = forms.IntegerField(
        label='Dias despues de del mes', min_value=1, max_value=28, initial=5,
        widget=forms.NumberInput(attrs=_control))


class ImportarExcelForm(MesAnioForm):
    archivo = forms.FileField(
        label='Archivo Excel (.xlsx)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xlsm'}))


class DepartamentoForm(forms.ModelForm):
    class Meta:
        model = Departamento
        fields = ['numero_domicilio', 'tipo_domicilio', 'nombres', 'apellidos',
                  'dni', 'celular', 'correo', 'tiene_inquilino', 'celular_inquilino',
                  'cochera_monto']
        widgets = {
            'numero_domicilio': forms.TextInput(attrs={**_control, 'placeholder': '101'}),
            'tipo_domicilio': forms.Select(attrs=_select),
            'nombres': forms.TextInput(attrs=_control),
            'apellidos': forms.TextInput(attrs=_control),
            'dni': forms.TextInput(attrs={**_control, 'maxlength': 8}),
            'celular': forms.TextInput(attrs={**_control, 'maxlength': 15}),
            'correo': forms.EmailInput(attrs=_control),
            'tiene_inquilino': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'celular_inquilino': forms.TextInput(attrs={**_control, 'maxlength': 15}),
            'cochera_monto': forms.NumberInput(attrs={**_control, 'step': '0.01'}),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('tiene_inquilino'):
            cleaned['celular_inquilino'] = ''
        return cleaned


class ProveedorForm(forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = ['nombre', 'rol', 'telefono', 'nota', 'activo']
        widgets = {
            'nombre': forms.TextInput(attrs={**_control, 'placeholder': 'Navarro'}),
            'rol': forms.TextInput(attrs={**_control, 'placeholder': 'Gasfitero'}),
            'telefono': forms.TextInput(attrs={**_control, 'maxlength': 15}),
            'nota': forms.TextInput(attrs=_control),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class GastoEdificioForm(forms.ModelForm):
    class Meta:
        model = GastoEdificio
        fields = ['descripcion', 'monto', 'categoria', 'compartido']
        widgets = {
            'descripcion': forms.TextInput(attrs={**_control, 'placeholder': 'Reparación puerta'}),
            'monto': forms.NumberInput(attrs={**_control, 'step': '0.01'}),
            'categoria': forms.Select(attrs=_select),
            'compartido': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class GastoFijoForm(forms.ModelForm):
    class Meta:
        model = GastoFijo
        fields = ['descripcion', 'monto', 'categoria', 'compartido', 'activo']
        widgets = {
            'descripcion': forms.TextInput(attrs={**_control, 'placeholder': 'Vigilancia'}),
            'monto': forms.NumberInput(attrs={**_control, 'step': '0.01'}),
            'categoria': forms.Select(attrs=_select),
            'compartido': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class TarifaAguaForm(forms.ModelForm):
    class Meta:
        model = TarifaAgua
        fields = ['nombre', 'consumo_limite', 'tarifa', 'activo']
        widgets = {
            'nombre': forms.TextInput(attrs={**_control, 'placeholder': 'Tramo 0-20 m³'}),
            'consumo_limite': forms.NumberInput(attrs={**_control, 'step': '0.01'}),
            'tarifa': forms.NumberInput(attrs={**_control, 'step': '0.0001'}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class PagoForm(forms.Form):
    """Pago imputado. `concepto` vacío = pago general (cancela lo más antiguo)."""
    monto = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01,
                               widget=forms.NumberInput(attrs={**_control, 'step': '0.01'}))
    concepto = forms.ModelChoiceField(
        queryset=Concepto.objects.filter(activo=True), required=False,
        empty_label='General (lo más antiguo primero)',
        widget=forms.Select(attrs=_select))
    metodo = forms.CharField(max_length=50, required=False,
                             widget=forms.TextInput(attrs={**_control, 'placeholder': 'Efectivo, Yape...'}))


class CargoForm(MesAnioForm):
    """Alta de un cargo manual (puertas, cámara, cuota, multa, etc.)."""
    concepto = forms.ModelChoiceField(
        queryset=Concepto.objects.filter(activo=True),
        widget=forms.Select(attrs=_select))
    monto = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01,
                               widget=forms.NumberInput(attrs={**_control, 'step': '0.01'}))
    descripcion = forms.CharField(max_length=150, required=False,
                                  widget=forms.TextInput(attrs={**_control, 'placeholder': 'Detalle (opcional)'}))
