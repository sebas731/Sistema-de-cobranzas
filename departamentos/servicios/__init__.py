"""Lógica de negocio, separada por tema. Se re-exporta aquí para que
`from departamentos.servicios import X` siga funcionando igual.

- comun.py:       redondeo, fechas de vencimiento, parseo y tramos.
- agua.py:        lecturas, costo de agua (tarifa plana y SEDAPAL), agua común.
- gastos.py:      prorrateo de gastos fijos + del mes.
- cuenta.py:      conceptos, cargos, pagos y deudores (cuenta corriente).
- importacion.py: carga desde Excel.
- migracion.py:   carga inicial de deudas desde Excel (parser + cargos de migración).
- boleta.py:      datos de la boleta de cobranza.
- recibo.py:      cálculo y emisión del recibo (snapshot).
"""
from .agua import (
    agua_comun_por_departamento, calcular_agua_sedapal, costo_agua, lectura_del_mes,
    registrar_lectura, siguiente_lectura, ultima_lectura, validar_lectura,
)
from .boleta import EDIFICIO, datos_boleta
from .comun import (
    CENTIMOS, REDONDEAR_CENTIMOS, REDONDEAR_DECIMAS, TRUNCAR_CENTIMOS, TRUNCAR_DECIMAS,
    a_decimal, aplicar_redondeo, fecha_vencimiento, redondear, sumar_meses,
)
from .cuenta import (
    COLUMNAS_COBRO, COLUMNAS_MES, CONCEPTOS_ESTANDAR, MESES_SUELTOS, agrupar_deuda, borrar_generacion_mes,
    cargos_mes_por_departamento, cargos_pendientes, crear_cargo, deuda_anterior, deudores,
    generar_cargos_mes, registrar_pago, sembrar_conceptos,
)
from .gastos import desglose_gastos_mes, prorrateo_mes
from .importacion import importar_lecturas_excel
from .recibo import calcular_recibo, conceptos_mensuales, emitir_recibo
