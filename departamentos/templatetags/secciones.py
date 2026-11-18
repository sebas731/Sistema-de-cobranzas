"""Menú simplificado: 6 secciones en la barra lateral y, dentro de cada una,
pestañas con las pantallas que antes eran opciones sueltas del menú.

Cada pestaña: (url_name, etiqueta, url_names extra que la marcan activa).
"""
from django import template
from django.urls import reverse

register = template.Library()

SECCIONES = [
    ('mes', 'Preparar el mes', 'i-calendar', [
        ('gastos_mes', '1. Gastos del mes', ()),
        ('facturas_agua', '2. Agua (lecturas)', ('lecturas_mes',)),
        ('generar_recibos', '3. Recibos', ('recibos_mes', 'recibo_ver', 'recibo_boleta')),
    ]),
    ('cobranza', 'Cobranza', 'i-cash', [
        ('cobranza', 'Cobrar', ('deudas_actuales',)),
        ('registrar_pagos', 'Cobro masivo', ()),
        ('registrar_cargos', 'Multas / cargos', ()),
        ('pagos_departamento', 'Historial de pagos', ()),
        ('panel', 'Panel del mes', ()),
    ]),
    ('caja', 'Caja', 'i-wallet', [
        ('estado_caja', 'Estado de caja', ()),
    ]),
    ('balance', 'Balance y reportes', 'i-grid', [
        ('balance', 'Balance general', ()),
        ('balance_mes', 'Balance del mes', ()),
        ('cobrado_vs_pagado', 'Cobrado vs pagado', ()),
        ('morosidad', 'Morosidad', ()),
        ('deudores', 'Deudores (resumen)', ()),
        ('estado_cuenta', 'Estado de cuenta (PDF)', ()),
        ('detalle_cobranza', 'Detalle de cobranza (PDF)', ()),
    ]),
    ('deptos', 'Departamentos', 'i-building', [
        ('departamento_lista', 'Departamentos',
         ('departamento_detalle', 'departamento_editar', 'departamento_nuevo')),
        ('proveedores', 'Proveedores y contactos', ('proveedor_editar',)),
    ]),
    ('config', 'Configuración', 'i-sliders', [
        ('configuracion', 'Condominio', ()),
        ('gastos_fijos', 'Gastos fijos', ('gasto_fijo_editar',)),
        ('tarifas', 'Tarifa de agua', ('tarifa_nueva', 'tarifa_editar')),
        ('importar_excel', 'Importar Excel', ()),
        ('importar_deudas', 'Importar deudas', ()),
        ('carga_deudas', 'Carga inicial de deudas', ()),
    ]),
]


def _activa(cur, nombre, extra):
    return cur == nombre or cur in extra


def _seccion_de(cur):
    for clave, _t, _i, tabs in SECCIONES:
        if any(_activa(cur, n, e) for n, _l, e in tabs):
            return clave
    return None


@register.simple_tag(takes_context=True)
def menu_secciones(context):
    """Lista de secciones para la barra lateral (href = primera pestaña)."""
    req = context.get('request')
    cur = req.resolver_match.url_name if req and req.resolver_match else ''
    activa = _seccion_de(cur)
    return [{'titulo': t, 'icono': i, 'href': reverse(tabs[0][0]), 'activa': c == activa}
            for c, t, i, tabs in SECCIONES]


@register.inclusion_tag('departamentos/components/tabs_seccion.html', takes_context=True)
def tabs_seccion(context):
    """Pestañas de la sección actual (no se muestran si la sección tiene una sola)."""
    req = context.get('request')
    cur = req.resolver_match.url_name if req and req.resolver_match else ''
    clave = _seccion_de(cur)
    for c, titulo, _i, tabs in SECCIONES:
        if c == clave and len(tabs) > 1:
            return {'titulo': titulo, 'tabs': [
                {'label': l, 'href': reverse(n), 'activa': _activa(cur, n, e)} for n, l, e in tabs]}
    return {'tabs': []}
