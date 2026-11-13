"""Filtro de formato de dinero: separador de miles con coma y 2 decimales
(estilo Perú: 1,234.56), independiente del locale del sistema."""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def soles(valor):
    """Formatea un número como 1,234.56 (coma miles, punto decimal).
    Si está vacío o no es numérico, devuelve lo que llegó (o '')."""
    if valor is None or valor == '':
        return ''
    try:
        return f'{Decimal(str(valor)):,.2f}'
    except (InvalidOperation, ValueError, TypeError):
        return valor
