"""Helpers para la barra de filtros reutilizable (components/filter_bar.html).

Cada tabla define una lista de campos y se la pasa al componente. Tipos:
- busqueda: caja de texto con lupa (filtra por varios atributos).
- select:   desplegable de opciones (valor exacto).
- rango:    dos cajas numéricas mín–máx (precios, montos, saldos, m³...).

Uso típico en una vista:

    from . import filtros as F
    campos = F.con_valores(request, [
        F.busqueda('q', 'Buscar depto o propietario...'),
        F.select('estado', 'Estado', [('', 'Todos'), ('deuda', 'Con deuda')]),
        F.rango('saldo', 'Saldo'),
    ])
    # ... y para una LISTA en Python:
    filas = [f for f in filas if F.en_rango(f['saldo'], request, 'saldo')]

Para un QuerySet se aplican con .filter()/Q normalmente.
"""
from decimal import Decimal, InvalidOperation


def busqueda(name='q', placeholder='Buscar...'):
    return {'tipo': 'busqueda', 'name': name, 'placeholder': placeholder}


def select(name, label, opciones):
    return {'tipo': 'select', 'name': name, 'label': label, 'opciones': opciones}


def rango(name, label, paso='0.01', sufijo=''):
    return {'tipo': 'rango', 'name': name, 'label': label, 'paso': paso, 'sufijo': sufijo}


def con_valores(request, campos):
    """Devuelve los campos con su valor actual leído de request.GET (para repintar)."""
    res = []
    for c in campos:
        c = dict(c)
        if c['tipo'] == 'rango':
            c['min'] = request.GET.get(c['name'] + '_min', '')
            c['max'] = request.GET.get(c['name'] + '_max', '')
        else:
            c['value'] = request.GET.get(c['name'], '')
        res.append(c)
    return res


def hay_filtros_activos(request, campos):
    for c in campos:
        if c['tipo'] == 'rango':
            if request.GET.get(c['name'] + '_min') or request.GET.get(c['name'] + '_max'):
                return True
        elif request.GET.get(c['name']):
            return True
    return False


# ---- Aplicación sobre LISTAS de Python (ej. filas calculadas del Panel) ----

def _dec(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor).replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None


def coincide_texto(request, name, *valores):
    """True si el texto buscado aparece en alguno de los `valores` (o si no hay búsqueda)."""
    q = (request.GET.get(name) or '').strip().lower()
    if not q:
        return True
    return any(q in str(v).lower() for v in valores)


def en_rango(valor, request, name):
    """True si `valor` está dentro de [min, max] del campo (vacíos = sin tope)."""
    vmin = _dec(request.GET.get(name + '_min'))
    vmax = _dec(request.GET.get(name + '_max'))
    valor = Decimal(valor)
    if vmin is not None and valor < vmin:
        return False
    if vmax is not None and valor > vmax:
        return False
    return True
