"""
Comando: python manage.py rehacer_carga --dir <carpeta_csv> [--aplicar] [--crear-conceptos]

Borra la carga anterior (Imputacion, Pago, Cargo, ReciboLinea, Recibo) y vuelve a cargar
desde cargos.csv, pagos.csv e imputaciones.csv (cuadre Torre D del otro agente).

SIN --aplicar es un ENSAYO: hace todo dentro de una transacción y al final la revierte,
mostrando los totales. Con --aplicar confirma los cambios.

AJUSTADO a los modelos reales del proyecto (app 'departamentos'):
  - APP = departamentos
  - Departamento.numero_domicilio
  - Pago.nota  (no 'observacion')
  - CUOTA_EXTRA -> CUOTA
  - Cargo.fecha_vencimiento (obligatorio): se setea al día 5 del periodo.
"""
import csv
import os
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

# ======================= CONFIG (modelos reales del proyecto) =======================
APP = "departamentos"                   # app_label donde viven los modelos

M_DEPARTAMENTO = "Departamento"
F_DEP_NUMERO = "numero_domicilio"       # campo con 101, 102...

M_CONCEPTO = "Concepto"
F_CON_CODIGO = "codigo"
F_CON_NOMBRE = "nombre"

M_CARGO = "Cargo"
F_CARGO_DEP, F_CARGO_CON, F_CARGO_PER, F_CARGO_MONTO = "departamento", "concepto", "periodo", "monto"
F_CARGO_DESC = "descripcion"
F_CARGO_VENC = "fecha_vencimiento"      # obligatorio en este modelo; se calcula del periodo

M_PAGO = "Pago"
F_PAGO_DEP, F_PAGO_MONTO, F_PAGO_FECHA = "departamento", "monto", "fecha_pago"
F_PAGO_ORIGEN = "origen"
VALOR_ORIGEN = "MIGRACION"
F_PAGO_NOTA = "nota"

M_IMPUTACION = "Imputacion"
F_IMP_PAGO, F_IMP_CARGO, F_IMP_MONTO = "pago", "cargo", "monto"

A_BORRAR = ["Imputacion", "ReciboSubLinea", "ReciboLinea", "Recibo", "Pago", "Cargo"]

MAPA_CONCEPTOS = {
    "CUOTA_EXTRA": "CUOTA",
}
# ====================================================================================


def D(x):
    return Decimal(str(x)).quantize(Decimal("0.01"))


def leer(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class Ensayo(Exception):
    pass


class Command(BaseCommand):
    help = "Borra la carga anterior y recarga cargos/pagos/imputaciones del cuadre."

    def add_arguments(self, p):
        p.add_argument("--dir", required=True, help="Carpeta con cargos.csv, pagos.csv, imputaciones.csv")
        p.add_argument("--aplicar", action="store_true", help="Confirma los cambios (sin esto es ensayo)")
        p.add_argument("--crear-conceptos", action="store_true", help="Crea conceptos que falten")

    def modelo(self, nombre):
        try:
            return apps.get_model(APP, nombre)
        except LookupError:
            raise CommandError(f"No existe el modelo {APP}.{nombre}. Ajusta la configuración arriba.")

    def campo(self, Model, nombre):
        if nombre is None:
            return None
        try:
            return Model._meta.get_field(nombre)
        except Exception:
            campos = [f.name for f in Model._meta.get_fields()]
            raise CommandError(f"{Model.__name__} no tiene el campo '{nombre}'. Campos: {campos}")

    def periodo(self, field, texto):
        if isinstance(field, models.DateField):
            y, m = texto.split("-")
            return date(int(y), int(m), 1)
        return texto

    def _venc(self, per):
        """Día 5 del mes del periodo (vencimiento). Acepta date o texto 'AAAA-MM'."""
        if isinstance(per, date):
            return date(per.year, per.month, 5)
        y, m = str(per).split("-")[:2]
        return date(int(y), int(m), 5)

    def handle(self, *a, **o):
        base = o["dir"]
        cargos_csv = leer(os.path.join(base, "cargos.csv"))
        pagos_csv = leer(os.path.join(base, "pagos.csv"))
        imps_csv = leer(os.path.join(base, "imputaciones.csv"))

        Dep, Con = self.modelo(M_DEPARTAMENTO), self.modelo(M_CONCEPTO)
        Cargo, Pago, Imp = self.modelo(M_CARGO), self.modelo(M_PAGO), self.modelo(M_IMPUTACION)
        for M, fs in [(Dep, [F_DEP_NUMERO]), (Con, [F_CON_CODIGO]),
                      (Cargo, [F_CARGO_DEP, F_CARGO_CON, F_CARGO_PER, F_CARGO_MONTO, F_CARGO_DESC, F_CARGO_VENC]),
                      (Pago, [F_PAGO_DEP, F_PAGO_MONTO, F_PAGO_FECHA, F_PAGO_ORIGEN, F_PAGO_NOTA]),
                      (Imp, [F_IMP_PAGO, F_IMP_CARGO, F_IMP_MONTO])]:
            for f in fs:
                self.campo(M, f)
        f_per = self.campo(Cargo, F_CARGO_PER)

        deps = {}
        for d in Dep.objects.all():
            deps[str(getattr(d, F_DEP_NUMERO)).strip()] = d
        faltan = sorted({r["dpto"] for r in cargos_csv + pagos_csv} - set(deps))
        if faltan:
            raise CommandError(f"Departamentos que no existen en el sistema: {faltan}")

        cod = lambda c: MAPA_CONCEPTOS.get(c, c)
        necesarios = sorted({cod(r["concepto"]) for r in cargos_csv})
        cons = {getattr(c, F_CON_CODIGO): c for c in Con.objects.all()}
        faltan_con = [c for c in necesarios if c not in cons]
        if faltan_con and not o["crear_conceptos"]:
            raise CommandError(f"Conceptos que faltan: {faltan_con}. Mapéalos en MAPA_CONCEPTOS o usa --crear-conceptos.")

        try:
            with transaction.atomic():
                for c in faltan_con:
                    cons[c] = Con.objects.create(**{F_CON_CODIGO: c, F_CON_NOMBRE: c.replace("_", " ").title()})
                    self.stdout.write(f"  concepto creado: {c}")

                # 1) borrar carga anterior
                for nombre in A_BORRAR:
                    try:
                        M = apps.get_model(APP, nombre)
                    except LookupError:
                        self.stdout.write(f"  (no existe {nombre}, se omite)")
                        continue
                    n, _ = M.objects.all().delete()
                    self.stdout.write(f"  borrados {nombre}: {n}")

                # 2) cargos
                idx = {}
                for r in cargos_csv:
                    per = self.periodo(f_per, r["periodo"])
                    kw = {F_CARGO_DEP: deps[r["dpto"]], F_CARGO_CON: cons[cod(r["concepto"])],
                          F_CARGO_PER: per, F_CARGO_MONTO: D(r["monto"]),
                          F_CARGO_VENC: self._venc(per)}
                    if F_CARGO_DESC:
                        kw[F_CARGO_DESC] = r["descripcion"][:150]
                    idx[(r["dpto"], r["concepto"], r["periodo"])] = Cargo.objects.create(**kw)

                # 3) pagos
                pid = {}
                for r in pagos_csv:
                    kw = {F_PAGO_DEP: deps[r["dpto"]], F_PAGO_MONTO: D(r["monto"]),
                          F_PAGO_FECHA: date.fromisoformat(r["fecha"])}
                    if F_PAGO_ORIGEN:
                        kw[F_PAGO_ORIGEN] = VALOR_ORIGEN
                    if F_PAGO_NOTA:
                        kw[F_PAGO_NOTA] = f"Hoja1 fila {r['fila_hoja1']} | {r['nota']}"[:150]
                    pid[r["pago_id"]] = Pago.objects.create(**kw)

                # 4) imputaciones (ya resueltas: NO usar FIFO)
                for r in imps_csv:
                    Imp.objects.create(**{F_IMP_PAGO: pid[r["pago_id"]],
                                          F_IMP_CARGO: idx[(r["dpto"], r["concepto"], r["periodo"])],
                                          F_IMP_MONTO: D(r["monto"])})

                # 5) verificación
                tc = sum(D(r["monto"]) for r in cargos_csv)
                ti = sum(D(r["monto"]) for r in imps_csv)
                tp = sum(D(r["monto"]) for r in pagos_csv)
                db_c = Cargo.objects.aggregate(s=models.Sum(F_CARGO_MONTO))["s"]
                db_i = Imp.objects.aggregate(s=models.Sum(F_IMP_MONTO))["s"]
                self.stdout.write(f"Cargos: {Cargo.objects.count()}  S/{db_c}  (CSV {tc})")
                self.stdout.write(f"Pagos:  {Pago.objects.count()}  S/{tp}")
                self.stdout.write(f"Imputado: S/{db_i}  (CSV {ti})")
                self.stdout.write(f"DEUDA TOTAL: S/{db_c - db_i}   (esperado 18534.56)")
                if db_c != tc or db_i != ti or tp != ti:
                    raise CommandError("Los totales no cuadran: se revierte todo.")

                if not o["aplicar"]:
                    raise Ensayo()
        except Ensayo:
            self.stdout.write(self.style.WARNING("ENSAYO: nada se guardó. Corre con --aplicar para confirmar."))
            return
        self.stdout.write(self.style.SUCCESS("Carga aplicada."))
