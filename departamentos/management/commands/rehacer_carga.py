"""
Comando: python manage.py rehacer_carga --dir <carpeta_csv> [--aplicar] [--crear-conceptos]

Borra la carga anterior (Imputacion, Pago, Cargo, ReciboLinea, Recibo) y vuelve a cargar
desde cargos.csv, pagos.csv e imputaciones.csv (cuadre Torre D).

SIN --aplicar es un ENSAYO: hace todo dentro de una transacción y al final la revierte,
mostrando los totales. Con --aplicar confirma los cambios.

Copiar a: <tu_app>/management/commands/rehacer_carga.py
"""
import csv
import os
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

# ======================= CONFIGURA AQUÍ (nombres de tu sistema) =======================
APP = "departamentos"                   # app_label donde viven los modelos

M_DEPARTAMENTO = "Departamento"
F_DEP_NUMERO = "numero_domicilio"       # campo con 101, 102... (int o texto)

M_CONCEPTO = "Concepto"
F_CON_CODIGO = "codigo"                 # campo con 'MANTENIMIENTO', 'AGUA', ...
F_CON_NOMBRE = "nombre"                 # usado solo con --crear-conceptos

M_CARGO = "Cargo"
F_CARGO_DEP, F_CARGO_CON, F_CARGO_PER, F_CARGO_MONTO = "departamento", "concepto", "periodo", "monto"
F_CARGO_DESC = "descripcion"            # pon None si no existe

M_PAGO = "Pago"
F_PAGO_DEP, F_PAGO_MONTO, F_PAGO_FECHA = "departamento", "monto", "fecha_pago"
F_PAGO_ORIGEN = "origen"                # pon None si no existe
VALOR_ORIGEN = "MIGRACION"
F_PAGO_NOTA = "nota"                    # pon None si no existe

M_IMPUTACION = "Imputacion"
F_IMP_PAGO, F_IMP_CARGO, F_IMP_MONTO = "pago", "cargo", "monto"

# Se borran en este orden (hijos antes que padres). No se tocan Departamento, Concepto,
# lecturas, tarifas ni gastos/egresos.
A_BORRAR = ["Imputacion", "ReciboLinea", "Recibo", "Pago", "Cargo"]

# Si tus códigos de concepto se llaman distinto, mapea aquí: código CSV -> código tuyo
MAPA_CONCEPTOS = {
    "CUOTA_EXTRA": "CUOTA",
}
# ======================================================================================


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
        p.add_argument("--conservar-desde", default=None,
                       help="Ej. 2026-10: NO toca cargos de ese periodo en adelante (octubre registrado a mano). "
                            "Solo reemplaza cargos anteriores y los pagos de la carga (origen MIGRACION).")

    # ---------- validación de que los nombres configurados existen ----------
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
        # '2026-09' -> date(2026,9,1) si el campo es DateField; si es texto, se deja igual
        if isinstance(field, models.DateField):
            y, m = texto.split("-")
            return date(int(y), int(m), 1)
        return texto

    def handle(self, *a, **o):
        base = o["dir"]
        cargos_csv = leer(os.path.join(base, "cargos.csv"))
        pagos_csv = leer(os.path.join(base, "pagos.csv"))
        imps_csv = leer(os.path.join(base, "imputaciones.csv"))

        Dep, Con = self.modelo(M_DEPARTAMENTO), self.modelo(M_CONCEPTO)
        Cargo, Pago, Imp = self.modelo(M_CARGO), self.modelo(M_PAGO), self.modelo(M_IMPUTACION)
        for M, fs in [(Dep, [F_DEP_NUMERO]), (Con, [F_CON_CODIGO]),
                      (Cargo, [F_CARGO_DEP, F_CARGO_CON, F_CARGO_PER, F_CARGO_MONTO, F_CARGO_DESC]),
                      (Pago, [F_PAGO_DEP, F_PAGO_MONTO, F_PAGO_FECHA, F_PAGO_ORIGEN, F_PAGO_NOTA]),
                      (Imp, [F_IMP_PAGO, F_IMP_CARGO, F_IMP_MONTO])]:
            for f in fs:
                self.campo(M, f)
        f_per = self.campo(Cargo, F_CARGO_PER)
        f_num = self.campo(Dep, F_DEP_NUMERO)

        # departamentos
        deps = {}
        for d in Dep.objects.all():
            deps[str(getattr(d, F_DEP_NUMERO)).strip()] = d
        faltan = sorted({r["dpto"] for r in cargos_csv + pagos_csv} - set(deps))
        if faltan:
            raise CommandError(f"Departamentos que no existen en el sistema: {faltan}")

        # conceptos
        cod = lambda c: MAPA_CONCEPTOS.get(c, c)
        necesarios = sorted({cod(r["concepto"]) for r in cargos_csv})
        cons = {getattr(c, F_CON_CODIGO): c for c in Con.objects.all()}
        faltan = [c for c in necesarios if c not in cons]
        if faltan and not o["crear_conceptos"]:
            raise CommandError(f"Conceptos que faltan: {faltan}. Mapéalos en MAPA_CONCEPTOS o usa --crear-conceptos.")

        try:
            with transaction.atomic():
                for c in faltan:
                    cons[c] = Con.objects.create(**{F_CON_CODIGO: c, F_CON_NOMBRE: c.replace("_", " ").title()})
                    self.stdout.write(f"  concepto creado: {c}")

                # 1) borrar carga anterior
                if o["conservar_desde"]:
                    lim = self.periodo(f_per, o["conservar_desde"])
                    viejos = Cargo.objects.filter(**{F_CARGO_PER + "__lt": lim})
                    if not F_PAGO_ORIGEN:
                        raise CommandError("--conservar-desde necesita el campo origen del Pago.")
                    mig = Pago.objects.filter(**{F_PAGO_ORIGEN: VALOR_ORIGEN})
                    # seguridad: pagos registrados a mano (no MIGRACION) imputados a cargos viejos
                    manuales = Imp.objects.filter(**{F_IMP_CARGO + "__in": viejos}).exclude(**{F_IMP_PAGO + "__in": mig})
                    if manuales.exists():
                        lst = [(str(getattr(i, F_IMP_PAGO)), str(getattr(i, F_IMP_CARGO)), getattr(i, F_IMP_MONTO)) for i in manuales[:30]]
                        raise CommandError(f"Hay {manuales.count()} imputaciones de pagos registrados a mano sobre cargos "
                                           f"anteriores a {o['conservar_desde']}. Revísalos antes (se volverían saldo a favor): {lst}")
                    for nombre in ["ReciboLinea", "Recibo"]:
                        try:
                            n, _ = apps.get_model(APP, nombre).objects.all().delete()
                            self.stdout.write(f"  borrados {nombre}: {n} (vuelve a generar los recibos de octubre)")
                        except LookupError:
                            pass
                    n, _ = Imp.objects.filter(**{F_IMP_PAGO + "__in": mig}).delete(); self.stdout.write(f"  imputaciones de la carga: {n}")
                    n, _ = Imp.objects.filter(**{F_IMP_CARGO + "__in": viejos}).delete(); self.stdout.write(f"  imputaciones a cargos viejos: {n}")
                    n, _ = mig.delete(); self.stdout.write(f"  pagos de la carga borrados: {n}")
                    n, _ = viejos.delete(); self.stdout.write(f"  cargos anteriores a {o['conservar_desde']} borrados: {n}")
                    self.stdout.write(f"  se conservan {Cargo.objects.count()} cargos desde {o['conservar_desde']}")
                    A_BORRAR_EFECTIVO = []
                else:
                    A_BORRAR_EFECTIVO = A_BORRAR
                for nombre in A_BORRAR_EFECTIVO:
                    try:
                        M = apps.get_model(APP, nombre)
                    except LookupError:
                        self.stdout.write(f"  (no existe {nombre}, se omite)")
                        continue
                    n, _ = M.objects.all().delete()
                    self.stdout.write(f"  borrados {nombre}: {n}")

                # 2) cargos
                idx = {}
                if o["conservar_desde"] and any(r["periodo"] >= o["conservar_desde"] for r in cargos_csv):
                    raise CommandError("cargos.csv trae periodos que se deben conservar; no se carga.")
                for r in cargos_csv:
                    per_cargo = self.periodo(f_per, r["periodo"])
                    kw = {F_CARGO_DEP: deps[r["dpto"]], F_CARGO_CON: cons[cod(r["concepto"])],
                          F_CARGO_PER: per_cargo, F_CARGO_MONTO: D(r["monto"])}
                    if F_CARGO_DESC:
                        kw[F_CARGO_DESC] = r["descripcion"][:250]
                    # fecha_vencimiento es obligatoria en el modelo: día 5 del periodo.
                    if isinstance(per_cargo, date):
                        kw["fecha_vencimiento"] = per_cargo.replace(day=5)
                    idx[(r["dpto"], r["concepto"], r["periodo"])] = Cargo.objects.create(**kw)

                # 3) pagos
                pid = {}
                for r in pagos_csv:
                    kw = {F_PAGO_DEP: deps[r["dpto"]], F_PAGO_MONTO: D(r["monto"]),
                          F_PAGO_FECHA: date.fromisoformat(r["fecha"])}
                    if F_PAGO_ORIGEN:
                        kw[F_PAGO_ORIGEN] = VALOR_ORIGEN
                    if F_PAGO_NOTA:
                        kw[F_PAGO_NOTA] = f"Hoja1 fila {r['fila_hoja1']} | {r['nota']}"[:200]
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
                lim_q = {F_CARGO_PER + "__lt": self.periodo(f_per, o["conservar_desde"])} if o["conservar_desde"] else {}
                db_c = Cargo.objects.filter(**lim_q).aggregate(s=models.Sum(F_CARGO_MONTO))["s"]
                db_i = Imp.objects.filter(**{F_IMP_CARGO + "__in": Cargo.objects.filter(**lim_q)}).aggregate(s=models.Sum(F_IMP_MONTO))["s"]
                self.stdout.write(f"Cargos (hasta setiembre): {Cargo.objects.filter(**lim_q).count()}  S/{db_c}  (CSV {tc})")
                self.stdout.write(f"Pagos:  {Pago.objects.count()}  S/{tp}")
                self.stdout.write(f"Imputado: S/{db_i}  (CSV {ti})")
                self.stdout.write(f"DEUDA TOTAL: S/{db_c - db_i}   (esperado 18335.15)")
                if db_c != tc or db_i != ti or tp != ti:
                    raise CommandError("Los totales no cuadran: se revierte todo.")

                if not o["aplicar"]:
                    raise Ensayo()
        except Ensayo:
            self.stdout.write(self.style.WARNING("ENSAYO: nada se guardó. Corre con --aplicar para confirmar."))
            return
        self.stdout.write(self.style.SUCCESS("Carga aplicada."))
