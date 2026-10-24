from datetime import date
from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Concepto, Departamento, GastoEdificio, GastoFijo, Proveedor, TarifaAgua
from .servicios import cargos_pendientes

MESES_ES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
            'Agosto', 'Setiembre', 'Octubre', 'Noviembre', 'Diciembre']
MESES = list(enumerate(MESES_ES))[1:]

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Un gasto fijo solo es mantenimiento o agua; 'adicionales' es exclusivo
        # de los gastos del mes.
        cat = GastoEdificio.Categoria
        self.fields['categoria'].choices = [
            (cat.GENERAL.value, cat.GENERAL.label),
            (cat.AGUA_COMUN.value, cat.AGUA_COMUN.label),
        ]


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


class BuscadorWidget(forms.Select):
    """Select con buscador (input + datalist): filtra por el texto de cada opción, así
    un ModelChoiceField se busca por código o nombre. Reutilizable en cualquier form."""
    template_name = 'departamentos/widgets/buscador.html'


class RegistroPagoForm(forms.Form):
    """Registro de un pago. Motivo y periodo vacíos = 'General' / 'Automático':
    el pago salda lo más antiguo; si se eligen, solo se aplica a ese motivo y/o mes
    (la regla vive en servicios.registrar_pago). La fecha es hoy y no se edita."""
    departamento = forms.ModelChoiceField(
        label='Departamento o propietario',
        queryset=Departamento.objects.filter(activo=True),
        widget=BuscadorWidget(attrs={**_control, 'data-ancho': 'full',
                                     'placeholder': 'Buscar por N° de depto o nombre del propietario'}))
    concepto = forms.ModelChoiceField(
        label='Motivo', queryset=Concepto.objects.filter(activo=True), required=False,
        empty_label='General', widget=forms.Select(attrs=_select))
    # Las opciones (meses con deuda) las arma el JS según el depto elegido.
    periodo = forms.DateField(
        label='Periodo de pago', required=False,
        widget=forms.Select(attrs=_select, choices=[('', 'Automático')]))
    monto = forms.DecimalField(
        label='Importe (S/)', max_digits=10, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={**_control, 'step': '0.01', 'placeholder': '0.00'}))
    fecha_pago = forms.DateField(
        label='Fecha de pago', required=False, disabled=True,
        widget=forms.DateInput(attrs=_control, format='%d/%m/%Y'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['departamento'].label_from_instance = (
            lambda d: f'{d.numero_domicilio} — {d.propietario}')
        self.fields['fecha_pago'].initial = timezone.localdate()
        # Al volver con errores, el JS reconstruye el select y reelige este mes.
        if self.is_bound:
            self.fields['periodo'].widget.attrs['data-inicial'] = self.data.get('periodo', '')

    def clean(self):
        datos = super().clean()
        dep, concepto, periodo = (datos.get('departamento'), datos.get('concepto'),
                                  datos.get('periodo'))
        if dep and (concepto or periodo) and not cargos_pendientes(dep, concepto, periodo):
            raise forms.ValidationError('No hay deuda pendiente para ese motivo y periodo.')
        return datos


class CargoForm(MesAnioForm):
    """Alta de un cargo manual (puertas, cámara, cuota, multa, etc.)."""
    concepto = forms.ModelChoiceField(
        queryset=Concepto.objects.filter(activo=True),
        widget=forms.Select(attrs=_select))
    monto = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01,
                               widget=forms.NumberInput(attrs={**_control, 'step': '0.01'}))
    descripcion = forms.CharField(max_length=150, required=False,
                                  widget=forms.TextInput(attrs={**_control, 'placeholder': 'Detalle (opcional)'}))
