"""Importación desde Excel y plantilla descargable."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from ..forms import ImportarExcelForm
from ..servicios import importar_lecturas_excel


@login_required
def importar_excel(request):
    resumen = None
    form = ImportarExcelForm(request.POST or None, request.FILES or None)
    if request.method == 'POST':
        if form.is_valid():
            try:
                resumen = importar_lecturas_excel(form.cleaned_data['archivo'], form.periodo)
                messages.success(
                    request,
                    f"Importado para {form.periodo:%m/%Y}: {resumen['departamentos']} "
                    f"departamentos y {resumen['lecturas']} lecturas.")
            except Exception as e:
                messages.error(request, f'No se pudo leer el Excel: {e}')
        else:
            messages.error(request, 'Revisa el mes, el año y el archivo.')
    return render(request, 'departamentos/importar/importar.html', {'form': form, 'resumen': resumen})


@login_required
def plantilla_excel(request):
    """Descarga una plantilla .xlsx con el formato que espera el sistema."""
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Datos'
    ws.append(['DEPARTAMENTO', 'LECTURA_ANTERIOR', 'LECTURA_ACTUAL', 'COCHERA'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.append(['101', 1330.54, 1334.34, 0])
    ws.append(['104', 625.27, 627.50, 5])
    ws.append(['201', 800.57, 809.94, 0])
    for col, ancho in zip('ABCD', (16, 18, 18, 10)):
        ws.column_dimensions[col].width = ancho

    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="plantilla_cobranzas.xlsx"'
    wb.save(resp)
    return resp
