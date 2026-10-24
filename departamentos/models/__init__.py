"""Modelos de la app, separados por dominio. Se re-exportan aquí para que
`from departamentos.models import X` siga funcionando igual."""
from .agua import LecturaMedidor, TarifaAgua, TarifaAguaTramo, TarifaAguaVersion
from .cuenta import Cargo, Concepto, Imputacion, Pago
from .departamento import Departamento, Proveedor, solo_digitos
from .gastos import GastoEdificio, GastoFijo
from .recibo import (
    ConfiguracionCondominio, MedioPago, PeriodoFacturacion, PlanCuota, Recibo,
    ReciboLinea, ReciboSubLinea,
)
