"""Tests del servicio de cálculo del recibo. Siempre con Decimal, nunca float."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from departamentos import servicios as s
from departamentos.models import (
    Concepto, Departamento, GastoFijo, LecturaMedidor, PeriodoFacturacion,
    PlanCuota, TarifaAguaTramo, TarifaAguaVersion,
)

D = lambda v: Decimal(str(v))


def _crear_tarifa_sedapal():
    """Tarifa de la foto: SEDAPAL Doméstico Grupo 1 + cargo fijo 0.30 + IGV 18%."""
    v = TarifaAguaVersion.objects.create(
        nombre='SEDAPAL Doméstico Grupo 1', suministro='5924621',
        vigencia_desde=date(2026, 1, 1), cargo_fijo=D('0.30'), igv=D('0.18'))
    for hasta, agua, san in [(10, '2.20', '1.38'), (20, '2.36', '1.48'),
                             (50, '3.22', '1.98'), (None, '7.32', '3.49')]:
        TarifaAguaTramo.objects.create(version=v, consumo_hasta=hasta,
                                       precio_agua=D(agua), precio_saneamiento=D(san))
    return v


class AguaSedapalTests(TestCase):
    """Reglas 1-5: agua potable + saneamiento escalonados + cargo fijo + IGV."""

    def setUp(self):
        self.v = _crear_tarifa_sedapal()

    def test_caso_foto_11m3(self):
        r = s.calcular_agua_sedapal(D('11'), self.v)
        self.assertEqual(r['agua_potable'], D('24.36'))   # 10×2.20 + 1×2.36
        self.assertEqual(r['saneamiento'], D('15.28'))    # 10×1.38 + 1×1.48
        self.assertEqual(r['cargo_fijo'], D('0.30'))
        self.assertEqual(r['igv'], D('7.19'))             # 18% de 39.94
        self.assertEqual(r['total_sin_redondeo'], D('47.13'))
        self.assertEqual(r['total'], D('47.10'))          # truncado a décimas
        self.assertIsInstance(r['total'], Decimal)

    def test_consumo_cero(self):
        r = s.calcular_agua_sedapal(D('0'), self.v)
        self.assertEqual(r['agua_potable'], D('0.00'))
        self.assertEqual(r['saneamiento'], D('0.00'))
        # Solo cargo fijo + IGV: 0.30 + 0.05 = 0.35 → truncado a décimas = 0.30
        self.assertEqual(r['igv'], D('0.05'))
        self.assertEqual(r['total'], D('0.30'))

    def test_tramo_50_mas(self):
        r = s.calcular_agua_sedapal(D('60'), self.v)
        # agua: 10×2.20 + 10×2.36 + 30×3.22 + 10×7.32 = 215.40
        self.assertEqual(r['agua_potable'], D('215.40'))
        # saneamiento: 10×1.38 + 10×1.48 + 30×1.98 + 10×3.49 = 122.90
        self.assertEqual(r['saneamiento'], D('122.90'))
        self.assertEqual(r['total'], D('399.50'))         # 338.60 + 60.95 = 399.55 → 399.50

    def test_estrategias_redondeo(self):
        self.assertEqual(s.aplicar_redondeo(D('47.13'), s.TRUNCAR_DECIMAS), D('47.10'))
        self.assertEqual(s.aplicar_redondeo(D('47.13'), s.REDONDEAR_CENTIMOS), D('47.13'))
        self.assertEqual(s.aplicar_redondeo(D('47.16'), s.REDONDEAR_DECIMAS), D('47.20'))
        self.assertEqual(s.aplicar_redondeo(D('47.19'), s.TRUNCAR_CENTIMOS), D('47.19'))


class ReciboCompletoTests(TestCase):
    """Recibo completo = el caso de la foto debe dar S/ 153.10."""

    def setUp(self):
        s.sembrar_conceptos()
        self.version = _crear_tarifa_sedapal()
        self.periodo = date(2026, 5, 1)
        self.per = PeriodoFacturacion.objects.create(
            periodo=self.periodo, fecha_emision=date(2026, 5, 14),
            fecha_vencimiento=date(2026, 5, 24), fecha_restriccion=date(2026, 6, 2),
            tarifa_agua=self.version, sedapal_total_a=D('24760.60'),
            consumo_dptos_b=D('21166.70'), dptos_habitados=460)
        self.dep = Departamento.objects.create(numero_domicilio='101-T', cochera_monto=D('5.00'))
        LecturaMedidor.objects.create(
            departamento=self.dep, periodo=self.periodo,
            lectura_anterior=D('2514'), lectura_actual=D('2525'),
            fecha_anterior=date(2026, 4, 9), fecha_actual=date(2026, 5, 8))
        # Conceptos del mes (gastos fijos por concepto).
        c_mant = Concepto.objects.get(codigo='MANTENIMIENTO')
        c_luz = Concepto.objects.get(codigo='LUZ_COMUN')
        c_agb = Concepto.objects.get(codigo='AGUA_BASICO')
        for desc, monto in [('Vigilancia', '30.00'), ('Limpieza', '18.00'), ('Administración', '10.00')]:
            GastoFijo.objects.create(descripcion=desc, monto=D(monto), concepto=c_mant, compartido=False)
        GastoFijo.objects.create(descripcion='Luz común', monto=D('5.20'), concepto=c_luz, compartido=False)
        GastoFijo.objects.create(descripcion='Agua básico', monto=D('0.00'), concepto=c_agb, compartido=False)
        # Cuota en armadas: mayo 2026 = 3/10.
        PlanCuota.objects.create(
            descripcion='Cuota pintado condominio', concepto=Concepto.objects.get(codigo='CUOTA'),
            monto_cuota=D('30.00'), num_cuotas=10, periodo_inicio=date(2026, 3, 1))

    def test_total_foto_153_10(self):
        r = s.calcular_recibo(self.dep, self.per)
        montos = {l['etiqueta']: l['monto'] for l in r['lineas']}
        self.assertEqual(montos['Pago consumo Dpto.'], D('47.10'))
        self.assertEqual(montos['Agua común'], D('7.80'))
        self.assertEqual(montos['Mantenimiento'], D('58.00'))
        self.assertEqual(montos['Luz común'], D('5.20'))
        self.assertEqual(montos['Cochera'], D('5.00'))
        self.assertEqual(montos['Cuota pintado condominio 3/10'], D('30.00'))
        self.assertEqual(r['total_mes'], D('153.10'))
        self.assertEqual(r['total_pagar'], D('153.10'))

    def test_mantenimiento_desglosado(self):
        r = s.calcular_recibo(self.dep, self.per)
        mant = next(l for l in r['lineas'] if l['etiqueta'] == 'Mantenimiento')
        sub = dict(mant['sub_items'])
        self.assertEqual(sub['Vigilancia'], D('30.00'))
        self.assertEqual(sub['Limpieza'], D('18.00'))
        self.assertEqual(sub['Administración'], D('10.00'))

    def test_deuda_pendiente_y_pago_parcial(self):
        # Cargo del mes anterior (abril) por 100, con un pago parcial de 40 → saldo 60.
        c_mant = Concepto.objects.get(codigo='MANTENIMIENTO')
        s.crear_cargo(self.dep, c_mant, date(2026, 4, 1), D('100.00'))
        s.registrar_pago(self.dep, D('40.00'), concepto=c_mant)  # imputa 40 → saldo 60
        # Un pago sin cargo abierto de ese concepto → queda como saldo a favor (100).
        s.registrar_pago(self.dep, D('100.00'), concepto=Concepto.objects.get(codigo='COCHERA'))

        r = s.calcular_recibo(self.dep, self.per)
        self.assertEqual(r['deuda_pendiente'], D('60.00'))
        self.assertEqual(r['saldo_favor'], D('100.00'))
        # total a pagar = total del mes (153.10) + deuda (60) − saldo a favor (100)
        self.assertEqual(r['total_pagar'], D('113.10'))

    def test_boleta_en_layout_con_formato_fisico(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('caja'))
        s.crear_cargo(self.dep, Concepto.objects.get(codigo='MANTENIMIENTO'), self.periodo, D('58.00'))
        resp = self.client.get(f'/departamentos/{self.dep.pk}/boleta/?mes=5&anio=2026')
        self.assertContains(resp, 'class="recibo-mantenimiento"')
        self.assertContains(resp, 'class="sidebar')  # dentro del layout del sistema
        self.assertContains(resp, 'TOTAL A PAGAR')
        self.assertContains(resp, 'S/. 58.00')


class RegistroPagoTests(TestCase):
    """Reglas del formulario de pago: General/Automático salda lo más antiguo;
    motivo y/o periodo limitan a qué deuda se aplica; lo que sobra queda a favor."""

    def setUp(self):
        s.sembrar_conceptos()
        self.dep = Departamento.objects.create(numero_domicilio='201-T', nombres='Ana', apellidos='Ruiz')
        self.mant = Concepto.objects.get(codigo='MANTENIMIENTO')
        self.coch = Concepto.objects.get(codigo='COCHERA')
        self.abr, self.may = date(2026, 4, 1), date(2026, 5, 1)
        self.c_mant_abr = s.crear_cargo(self.dep, self.mant, self.abr, D('50.00'))
        self.c_coch_abr = s.crear_cargo(self.dep, self.coch, self.abr, D('5.00'))
        self.c_mant_may = s.crear_cargo(self.dep, self.mant, self.may, D('50.00'))
        self.c_coch_may = s.crear_cargo(self.dep, self.coch, self.may, D('5.00'))

    def saldos(self):
        return [c.saldo for c in (self.c_mant_abr, self.c_coch_abr, self.c_mant_may, self.c_coch_may)]

    def test_general_automatico_salda_lo_mas_antiguo(self):
        s.registrar_pago(self.dep, D('52.00'))
        self.assertEqual(self.saldos(), [D('0.00'), D('3.00'), D('50.00'), D('5.00')])

    def test_periodo_solo_ese_mes(self):
        s.registrar_pago(self.dep, D('55.00'), periodo=self.may)
        self.assertEqual(self.saldos(), [D('50.00'), D('5.00'), D('0.00'), D('0.00')])

    def test_motivo_lo_mas_antiguo_de_ese_motivo(self):
        s.registrar_pago(self.dep, D('7.00'), concepto=self.coch)
        self.assertEqual(self.saldos(), [D('50.00'), D('0.00'), D('50.00'), D('3.00')])

    def test_motivo_y_periodo(self):
        pago = s.registrar_pago(self.dep, D('8.00'), concepto=self.coch, periodo=self.may)
        self.assertEqual(self.saldos(), [D('50.00'), D('5.00'), D('50.00'), D('0.00')])
        self.assertEqual(pago.saldo_a_favor, D('3.00'))

    def test_form_rechaza_motivo_y_periodo_sin_deuda(self):
        from departamentos.forms import RegistroPagoForm
        s.registrar_pago(self.dep, D('5.00'), concepto=self.coch, periodo=self.may)
        form = RegistroPagoForm({'departamento': self.dep.pk, 'concepto': self.coch.pk,
                                 'periodo': '2026-05-01', 'monto': '5'})
        self.assertFalse(form.is_valid())
        form = RegistroPagoForm({'departamento': self.dep.pk, 'concepto': '',
                                 'periodo': '2026-05-01', 'monto': '5', 'fecha_pago': '01/01/2000'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['fecha_pago'], timezone.localdate())  # no se edita

    def test_vista_registra_y_redirige(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('caja'))
        self.assertContains(self.client.get('/pagos/registrar/'), 'Este pago se aplicará a:')
        r = self.client.post('/pagos/registrar/', {'departamento': self.dep.pk, 'concepto': '',
                                                   'periodo': '', 'monto': '60.00'})
        self.assertRedirects(r, '/pagos/registrar/')
        self.assertEqual(self.saldos(), [D('0.00'), D('0.00'), D('45.00'), D('5.00')])


class PrimeraLecturaTests(TestCase):
    """La primera lectura de un depto es solo la base: no genera consumo.
    Desde la segunda, el consumo es actual − anterior."""

    def setUp(self):
        self.dep = Departamento.objects.create(numero_domicilio='301-T')

    def test_primera_lectura_no_consume(self):
        l = s.registrar_lectura(self.dep, date(2026, 5, 1), D('2514'))
        self.assertEqual(l.lectura_anterior, D('2514'))
        self.assertEqual(l.m3_consumidos, D('0'))
        self.assertEqual(s.lectura_del_mes(self.dep, date(2026, 5, 1)), (l, None))

    def test_segunda_lectura_usa_la_anterior(self):
        s.registrar_lectura(self.dep, date(2026, 5, 1), D('2514'))
        l = s.registrar_lectura(self.dep, date(2026, 6, 1), D('2525'))
        self.assertEqual(l.m3_consumidos, D('11'))
        self.assertEqual(s.lectura_del_mes(self.dep, date(2026, 7, 1)), (None, D('2525')))

    def test_anterior_explicita_se_respeta(self):
        l = s.registrar_lectura(self.dep, date(2026, 5, 1), D('2525'), lectura_anterior=D('2514'))
        self.assertEqual(l.m3_consumidos, D('11'))


class CadenaLecturasTests(TestCase):
    """Las lecturas de meses seguidos están encadenadas: la anterior de un mes es
    la actual del mes previo, y una lectura no puede romper el orden del medidor."""

    def setUp(self):
        self.dep = Departamento.objects.create(numero_domicilio='401-T')
        self.ago, self.sep, self.oct = date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)
        for p, v in [(self.ago, '100'), (self.sep, '120'), (self.oct, '150')]:
            s.registrar_lectura(self.dep, p, D(v))

    def test_cambiar_un_mes_actualiza_la_anterior_del_siguiente(self):
        s.registrar_lectura(self.dep, self.sep, D('130'))
        oct_ = LecturaMedidor.objects.get(departamento=self.dep, periodo=self.oct)
        self.assertEqual(oct_.lectura_anterior, D('130'))
        self.assertEqual(oct_.m3_consumidos, D('20'))
        self.assertEqual(s.lectura_del_mes(self.dep, self.oct)[1], D('130'))

    def test_no_puede_superar_al_mes_siguiente(self):
        self.assertIn('mayor que la lectura de 09/2026 (120)', s.validar_lectura(self.dep, self.ago, D('125')))
        self.assertIsNone(s.validar_lectura(self.dep, self.ago, D('110')))

    def test_no_puede_bajar_del_mes_previo(self):
        self.assertIn('menor que la lectura de 08/2026 (100)', s.validar_lectura(self.dep, self.sep, D('90')))

    def test_vista_rechaza_y_no_guarda_nada(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('agua'))
        otro = Departamento.objects.create(numero_domicilio='402-T')
        r = self.client.post('/facturas-agua/?mes=8&anio=2026', content_type='application/json', data={
            'rows': [{'pk': otro.pk, 'actual': 50}, {'pk': self.dep.pk, 'actual': 125}]})
        self.assertFalse(r.json()['ok'])
        self.assertIn('Depto 401-T', r.json()['error'])
        self.assertFalse(LecturaMedidor.objects.filter(departamento=otro).exists())
        self.assertEqual(LecturaMedidor.objects.get(departamento=self.dep, periodo=self.ago).lectura_actual, D('100'))


class CuotaExtraordinariaTests(TestCase):
    """Un gasto fijo 'Se cobra como: Cuota extraordinaria' genera su propio cargo
    cada mes mientras esté activo, sin sumarse a mantenimiento."""

    def setUp(self):
        s.sembrar_conceptos()
        self.dep = Departamento.objects.create(numero_domicilio='401-T')
        GastoFijo.objects.create(descripcion='Vigilancia', monto=D('30.00'))
        self.cuota = GastoFijo.objects.create(
            descripcion='Cuota extraordinaria', monto=D('45.00'),
            concepto=Concepto.objects.get(codigo='CUOTA'))

    def montos(self, periodo):
        return {c.concepto.codigo: c.monto for c in self.dep.cargos.filter(periodo=periodo)}

    def test_genera_cargo_aparte(self):
        s.generar_cargos_mes(date(2026, 11, 1))
        m = self.montos(date(2026, 11, 1))
        self.assertEqual(m['CUOTA'], D('45.00'))
        self.assertEqual(m['MANTENIMIENTO'], D('30.00'))
        fila = s.cargos_mes_por_departamento(date(2026, 11, 1))[0]
        self.assertEqual(fila['CUOTA'], D('45.00'))

    def test_desactivada_no_se_cobra(self):
        self.cuota.activo = False
        self.cuota.save()
        s.generar_cargos_mes(date(2026, 12, 1))
        self.assertNotIn('CUOTA', self.montos(date(2026, 12, 1)))

    def test_formulario_ofrece_cuota(self):
        from departamentos.forms import GastoFijoForm
        codigos = [c.codigo for c in GastoFijoForm().fields['concepto'].queryset]
        self.assertEqual(codigos, ['CUOTA'])
