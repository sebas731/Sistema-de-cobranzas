"""Importación desde Excel y plantilla descargable."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from ..forms import ImportarDeudasForm, ImportarExcelForm
from ..models import Concepto
from ..servicios import importar_deudas_excel, importar_lecturas_excel


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
def importar_deudas(request):
    """Importa deudas (cargos) desde Excel. Por defecto SIMULA; marca 'Aplicar'
    para guardar. Idempotente: volver a importar reemplaza, no duplica."""
    resumen = None
    form = ImportarDeudasForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            resumen = importar_deudas_excel(form.cleaned_data['archivo'], aplicar=form.cleaned_data['aplicar'])
            modo = 'Aplicado' if resumen['aplicado'] else 'Simulación (no se guardó)'
            msg = (f"{modo}: {resumen['creados']} creadas, {resumen['actualizados']} actualizadas"
                   + (f", {len(resumen['errores'])} con error." if resumen['errores'] else '.'))
            (messages.success if not resumen['errores'] else messages.warning)(request, msg)
        except Exception as e:
            messages.error(request, f'No se pudo leer el Excel: {e}')
    elif request.method == 'POST':
        messages.error(request, 'Adjunta un archivo .xlsx válido.')
    return render(request, 'departamentos/importar/deudas.html', {'form': form, 'resumen': resumen})


@login_required
def plantilla_deudas(request):
    """Plantilla .xlsx bien formateada para importar deudas, con hoja de ayuda."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Deudas'
    cols = ['DEPARTAMENTO', 'CONCEPTO', 'PERIODO', 'MONTO', 'DESCRIPCION']
    ws.append(cols)
    verde = PatternFill('solid', fgColor='2E7D32')
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF', size=11)
        cell.fill = verde
        cell.alignment = Alignment(horizontal='center', vertical='center')
    ejemplos = [
        ['101', 'MANTENIMIENTO', '2026-10', 57.72, 'Mantenimiento octubre'],
        ['101', 'CUOTA', '2026-10', 25.00, 'Cuota extraordinaria'],
        ['203', 'MORA', '2026-10', 15.00, 'Atraso pago setiembre'],
    ]
    for fila in ejemplos:
        ws.append(fila)
    ws.freeze_panes = 'A2'
    for col, ancho in zip('ABCDE', (16, 18, 12, 12, 34)):
        ws.column_dimensions[col].width = ancho
    ws['D1'].number_format = '0.00'

    # Hoja de ayuda con los conceptos válidos.
    ayuda = wb.create_sheet('Instrucciones')
    ayuda.append(['Cómo llenar esta plantilla'])
    ayuda['A1'].font = Font(bold=True, size=13, color='2E7D32')
    for linea in [
        '', 'DEPARTAMENTO: el número tal cual (ej. 101).',
        'CONCEPTO: el código o nombre exacto (ver lista abajo).',
        'PERIODO: el mes en formato 2026-10 (o 10/2026).',
        'MONTO: el importe con 2 decimales (ej. 57.72).',
        'DESCRIPCION: opcional.', '',
        'Reimportar el mismo depto+concepto+periodo REEMPLAZA el monto (no duplica).',
        'Primero se SIMULA; marca «Aplicar» para guardar.', '',
        'CONCEPTOS VÁLIDOS (código — nombre):',
    ]:
        ayuda.append([linea])
    for co in Concepto.objects.order_by('orden'):
        ayuda.append([f'{co.codigo} — {co.nombre}'])
    ayuda.column_dimensions['A'].width = 70

    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="plantilla_deudas.xlsx"'
    wb.save(resp)
    return resp


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
