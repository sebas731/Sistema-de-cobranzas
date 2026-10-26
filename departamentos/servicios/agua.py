"""Agua: lecturas de medidor y costo por tramos (tarifa plana y SEDAPAL)."""
from decimal import Decimal

from django.db import transaction

from ..models import Departamento, LecturaMedidor, TarifaAgua
from .comun import CERO, TRUNCAR_DECIMAS, aplicar_redondeo, escalonar, ordenar_tramos, redondear


def costo_agua(m3):
    """Costo del agua con tarifa plana por rango (como el Excel del condominio):
    TODOS los m³ se cobran a la tarifa del rango en que cae el consumo total.
    Ej.: 18 m³ → 18 × 3.931 (rango 0–20); 25 m³ → 25 × 5.524 (rango 20–50).
    El tope de cada rango es inclusivo (20 m³ va en 0–20)."""
    m3 = Decimal(m3)
    if m3 <= 0:
        return CERO
    for t in ordenar_tramos(TarifaAgua.objects.filter(activo=True), 'consumo_limite'):
        if t.consumo_limite is None or m3 <= t.consumo_limite:
            return redondear(m3 * t.tarifa)
    return CERO


def calcular_agua_sedapal(m3, version, estrategia=TRUNCAR_DECIMAS):
    """Cobro de agua estilo SEDAPAL para `m3` con una `TarifaAguaVersion`.

    Agua potable y saneamiento escalonados por tramos (cada m³ paga la tarifa
    del tramo en que cae) + cargo fijo; IGV sobre la suma. Devuelve el desglose
    y el total ya redondeado con la estrategia.
    """
    m3 = Decimal(m3)
    agua = san = CERO
    for t, cantidad in escalonar(m3, version.tramos.all(), 'consumo_hasta'):
        agua += cantidad * t.precio_agua
        san += cantidad * t.precio_saneamiento

    agua = redondear(agua)
    san = redondear(san)
    cargo_fijo = redondear(version.cargo_fijo or 0)
    subtotal = agua + san + cargo_fijo
    igv = redondear(subtotal * (version.igv or 0))
    total_sin_redondeo = subtotal + igv
    return {
        'm3': m3, 'agua_potable': agua, 'saneamiento': san, 'cargo_fijo': cargo_fijo,
        'subtotal': subtotal, 'igv': igv, 'total_sin_redondeo': redondear(total_sin_redondeo),
        'total': aplicar_redondeo(total_sin_redondeo, estrategia),
    }


def agua_comun_por_departamento(periodo_fac, criterio='iguales', estrategia=TRUNCAR_DECIMAS,
                                departamento=None):
    """Agua común del depto = (A − B) del edificio prorrateado.

    Criterio configurable:
    - 'iguales' (el que usa el seed): (A−B) / N° de dptos habitados (partes iguales).
    - 'consumo': proporcional al consumo (m³) del depto sobre el total del mes.
    - 'm2': proporcional al área; como aún no hay campo de m², cae a partes iguales.
    """
    diferencia = periodo_fac.diferencia_ab
    if diferencia <= 0:
        return CERO

    if criterio in ('iguales', 'm2'):
        n = periodo_fac.dptos_habitados or Departamento.objects.filter(activo=True).count() or 1
        base = diferencia / Decimal(n)
    elif criterio == 'consumo':
        lecturas = LecturaMedidor.objects.filter(periodo=periodo_fac.periodo)
        total_m3 = sum((l.m3_consumidos for l in lecturas), Decimal('0'))
        if departamento is None or total_m3 <= 0:
            return CERO
        mia = lecturas.filter(departamento=departamento).first()
        mi_m3 = mia.m3_consumidos if mia else Decimal('0')
        base = diferencia * (mi_m3 / total_m3)
    else:
        raise ValueError(f'Criterio de prorrateo desconocido: {criterio}')
    return aplicar_redondeo(base, estrategia)


def ultima_lectura(departamento, antes_de=None):
    qs = LecturaMedidor.objects.filter(departamento=departamento)
    if antes_de is not None:
        qs = qs.filter(periodo__lt=antes_de)
    return qs.order_by('-periodo').first()


def siguiente_lectura(departamento, despues_de):
    return (LecturaMedidor.objects.filter(departamento=departamento, periodo__gt=despues_de)
            .order_by('periodo').first())


def lectura_del_mes(departamento, periodo):
    """(lectura del mes o None, lectura anterior o None). La anterior se arrastra
    de la lectura actual del mes previo; None = es la primera lectura del depto
    (no hay con qué comparar, así que no hay consumo)."""
    actual = departamento.lecturas.filter(periodo=periodo).first()
    previa = ultima_lectura(departamento, antes_de=periodo)
    if previa is None:
        return actual, None
    # Siempre la actual del mes previo (fuente de verdad), aunque la guardada esté desfasada.
    return actual, previa.lectura_actual


def _n(d):  # 1889.00 → '1889'
    return f'{Decimal(d).normalize():f}'


def validar_lectura(departamento, periodo, lectura_actual):
    """Mensaje de error si la lectura rompe el orden del medidor (no puede bajar
    respecto al mes previo ni superar la del mes siguiente); None si es válida."""
    previa = ultima_lectura(departamento, antes_de=periodo)
    if previa and lectura_actual < previa.lectura_actual:
        return (f'{_n(lectura_actual)} es menor que la lectura de {previa.periodo:%m/%Y} '
                f'({_n(previa.lectura_actual)})')
    siguiente = siguiente_lectura(departamento, periodo)
    if siguiente and lectura_actual > siguiente.lectura_actual:
        return (f'{_n(lectura_actual)} es mayor que la lectura de {siguiente.periodo:%m/%Y} '
                f'({_n(siguiente.lectura_actual)})')
    return None


@transaction.atomic
def registrar_lectura(departamento, periodo, lectura_actual, lectura_anterior=None):
    """Registra la lectura del mes. Arrastra la lectura anterior si no se pasa.
    La primera lectura del depto es solo la base: anterior = actual (0 m³).
    Mantiene la cadena: la lectura anterior del mes siguiente pasa a ser esta."""
    if lectura_anterior is None:
        previa = ultima_lectura(departamento, antes_de=periodo)
        lectura_anterior = previa.lectura_actual if previa else lectura_actual
    lectura, _ = LecturaMedidor.objects.update_or_create(
        departamento=departamento, periodo=periodo,
        defaults={'lectura_anterior': lectura_anterior, 'lectura_actual': lectura_actual},
    )
    siguiente = siguiente_lectura(departamento, periodo)
    if siguiente and siguiente.lectura_anterior != lectura.lectura_actual:
        siguiente.lectura_anterior = lectura.lectura_actual
        siguiente.save(update_fields=['lectura_anterior'])
    return lectura
