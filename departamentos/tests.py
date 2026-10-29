"""Tests del servicio de cálculo del recibo. Siempre con Decimal, nunca float."""
import os
from datetime import date
from decimal import Decimal
from unittest import skipUnless

from django.test import TestCase
from django.utils import timezone

from departamentos import servicios as s
from departamentos.models import (
    Cargo, Concepto, Departamento, GastoFijo, LecturaMedidor, Pago, PeriodoFacturacion,
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
        form = RegistroPagoForm({'departamentos': [self.dep.pk], 'concepto': self.coch.pk,
                                 'periodo': '2026-05-01', 'monto': '5'})
        self.assertFalse(form.is_valid())
        form = RegistroPagoForm({'departamentos': [self.dep.pk], 'concepto': '',
                                 'periodo': '2026-05-01', 'monto': '5', 'fecha_pago': '01/01/2000'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['fecha_pago'], timezone.localdate())  # no se edita

    def test_vista_registra_y_redirige(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('caja'))
        self.assertContains(self.client.get('/pagos/registrar/'), 'Este pago se aplicará a:')
        r = self.client.post('/pagos/registrar/', {'departamentos': [self.dep.pk], 'concepto': '',
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

    def test_facturas_muestra_meses_anteriores(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('agua2'))
        r = self.client.get('/facturas-agua/?mes=11&anio=2026&meses=6')
        self.assertEqual([h['nombre'] for h in r.context['historial_json']],
                         ['Lect. Jun 2026', 'Lect. Jul 2026', 'Lect. Ago 2026', 'Lect. Set 2026'])
        fila = next(f for f in r.context['filas_json'] if f['pk'] == self.dep.pk)
        self.assertEqual((fila['h202608'], fila['h202609'], fila['anterior']), (100.0, 120.0, 150.0))

    def test_vista_guarda_las_validas_y_devuelve_las_invalidas(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('agua'))
        otro = Departamento.objects.create(numero_domicilio='402-T')
        r = self.client.post('/facturas-agua/?mes=8&anio=2026', content_type='application/json', data={
            'rows': [{'pk': otro.pk, 'actual': 50}, {'pk': self.dep.pk, 'actual': 125}]})
        d = r.json()
        self.assertEqual((d['ok'], d['guardadas']), (True, 1))
        self.assertEqual([(e['pk'], e['depto']) for e in d['errores']], [(self.dep.pk, '401-T')])
        self.assertTrue(LecturaMedidor.objects.filter(departamento=otro).exists())
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


class CostoAguaPlanaTests(TestCase):
    """Tarifa plana por rango (Excel del condominio): todos los m³ a la tarifa del
    rango en que cae el consumo. Valores reales de la planilla de julio."""

    def setUp(self):
        from departamentos.models import TarifaAgua
        TarifaAgua.objects.create(nombre='0-20', consumo_limite=D('20'), tarifa=D('3.931'))
        TarifaAgua.objects.create(nombre='20-50', consumo_limite=D('50'), tarifa=D('5.524'))
        TarifaAgua.objects.create(nombre='50+', consumo_limite=None, tarifa=D('5.524'))

    def test_coincide_con_excel(self):
        for m3, esperado in [('3.80', '14.94'), ('8.89', '34.95'), ('2.23', '8.77'),
                             ('7.94', '31.21'), ('18.91', '74.34'), ('0', '0.00')]:
            self.assertEqual(s.costo_agua(D(m3)), D(esperado), m3)

    def test_sobre_20_todo_a_la_tarifa_alta(self):
        self.assertEqual(s.costo_agua(D('20')), D('78.62'))    # tope inclusivo: 20 × 3.931
        self.assertEqual(s.costo_agua(D('25')), D('138.10'))   # 25 × 5.524, no por tramos
        self.assertEqual(s.costo_agua(D('60')), D('331.44'))   # rango sin tope


class PanelMoraMultaTests(TestCase):
    """Panel del mes: Multa del mes y Mora pendiente de meses anteriores se suman
    al saldo y el cobro del panel también salda esa mora."""

    def setUp(self):
        s.sembrar_conceptos()
        self.dep = Departamento.objects.create(numero_domicilio='501-T')
        c = lambda cod: Concepto.objects.get(codigo=cod)
        self.mora_ago = s.crear_cargo(self.dep, c('MORA'), date(2026, 8, 1), D('15.00'))
        self.mora_sep = s.crear_cargo(self.dep, c('MORA'), date(2026, 9, 1), D('15.00'))
        self.mant_oct = s.crear_cargo(self.dep, c('MANTENIMIENTO'), date(2026, 10, 1), D('70.00'))
        self.multa_oct = s.crear_cargo(self.dep, c('MULTA'), date(2026, 10, 1), D('20.00'))
        s.registrar_pago(self.dep, D('5.00'), concepto=c('MORA'))  # mora de agosto queda en 10

    def test_fila_suma_mora_anterior_y_multa(self):
        f = s.cargos_mes_por_departamento(date(2026, 10, 1), con_mora_anterior=True)[0]
        self.assertEqual(f['MORA'], D('25.00'))    # 10 (ago) + 15 (sep), pendientes
        self.assertEqual(f['MULTA'], D('20.00'))
        self.assertEqual(f['saldo'], D('115.00'))  # 70 + 20 + 25
        sin = s.cargos_mes_por_departamento(date(2026, 10, 1))[0]
        self.assertEqual(sin['saldo'], D('90.00'))  # el balance no cambia

    def test_depto_solo_con_mora_aparece(self):
        otro = Departamento.objects.create(numero_domicilio='502-T')
        s.crear_cargo(otro, Concepto.objects.get(codigo='MORA'), date(2026, 9, 1), D('15.00'))
        filas = s.cargos_mes_por_departamento(date(2026, 10, 1), con_mora_anterior=True)
        self.assertEqual([f['departamento'].numero_domicilio for f in filas], ['501-T', '502-T'])

    def test_cobro_del_panel_salda_mora_primero(self):
        s.registrar_pago(self.dep, D('115.00'), periodo=date(2026, 10, 1), con_mora_anterior=True)
        for c in (self.mora_ago, self.mora_sep, self.mant_oct, self.multa_oct):
            self.assertEqual(c.saldo, D('0.00'))


class PagoEnLoteTests(TestCase):
    """Registrar pago con varios deptos: el mismo importe y motivo para cada uno
    (ej. 25 de cuota extraordinaria a 56 deptos de una vez)."""

    def setUp(self):
        from django.contrib.auth.models import User
        s.sembrar_conceptos()
        self.cuota = Concepto.objects.get(codigo='CUOTA')
        self.mant = Concepto.objects.get(codigo='MANTENIMIENTO')
        self.deps = [Departamento.objects.create(numero_domicilio=n) for n in ('102', '108', '203')]
        for d in self.deps:
            s.crear_cargo(d, self.cuota, date(2026, 9, 1), D('50.00'))
            s.crear_cargo(d, self.mant, date(2026, 8, 1), D('70.00'))   # más antigua: no se toca
        self.client.force_login(User.objects.create_user('caja'))

    def test_lote_aplica_solo_a_la_cuota(self):
        r = self.client.post('/pagos/registrar/', {
            'departamentos': [d.pk for d in self.deps], 'concepto': self.cuota.pk,
            'periodo': '2026-09-01', 'monto': '25.00'})
        self.assertRedirects(r, '/pagos/registrar/')
        for d in self.deps:
            saldos = {c.concepto.codigo: c.saldo for c in d.cargos.all()}
            self.assertEqual(saldos, {'CUOTA': D('25.00'), 'MANTENIMIENTO': D('70.00')})

    def test_lote_rechaza_si_alguno_no_debe(self):
        from departamentos.forms import RegistroPagoForm
        sin = Departamento.objects.create(numero_domicilio='999')
        form = RegistroPagoForm({'departamentos': [self.deps[0].pk, sin.pk], 'concepto': self.cuota.pk,
                                 'periodo': '', 'monto': '25'})
        self.assertFalse(form.is_valid())
        self.assertIn('999', str(form.non_field_errors()))


class CobroPorColumnasTests(TestCase):
    """Panel: elegir columnas (ej. Agua) para que el cobro vaya solo a esos conceptos."""

    def setUp(self):
        from django.contrib.auth.models import User
        s.sembrar_conceptos()
        self.dep = Departamento.objects.create(numero_domicilio='601-T')
        c = lambda cod: Concepto.objects.get(codigo=cod)
        self.oct = date(2026, 10, 1)
        self.mant = s.crear_cargo(self.dep, c('MANTENIMIENTO'), self.oct, D('70.00'))
        self.agua = s.crear_cargo(self.dep, c('AGUA'), self.oct, D('30.00'))
        self.puertas = s.crear_cargo(self.dep, c('PUERTAS'), self.oct, D('10.00'))
        self.mora = s.crear_cargo(self.dep, c('MORA'), date(2026, 9, 1), D('15.00'))
        self.client.force_login(User.objects.create_user('caja'))

    def test_pendiente_por_columna(self):
        f = s.cargos_mes_por_departamento(self.oct, con_mora_anterior=True)[0]
        self.assertEqual(f['pendiente']['agua'], D('30.00'))
        self.assertEqual(f['pendiente']['mora'], D('15.00'))
        self.assertEqual(f['pendiente']['otros'], D('10.00'))   # puertas no tiene columna propia

    def test_solo_agua(self):
        self.client.post(f'/panel/?mes=10&anio=2026', {
            'accion': 'pago', 'departamento': self.dep.pk, 'monto': '30.00', 'columnas': ['agua']})
        self.assertEqual(self.agua.saldo, D('0.00'))
        self.assertEqual(self.mora.saldo, D('15.00'))            # la mora (más antigua) no se tocó
        self.assertEqual(self.mant.saldo, D('70.00'))

    def test_varias_columnas_pago_parcial(self):
        s.registrar_pago(self.dep, D('20.00'), periodo=self.oct, con_mora_anterior=True,
                         columnas=['agua', 'mora'])
        self.assertEqual(self.mora.saldo, D('0.00'))             # primero lo más antiguo
        self.assertEqual(self.agua.saldo, D('25.00'))
        self.assertEqual(self.mant.saldo, D('70.00'))


class ConfigurarCondominioTests(TestCase):
    """Datos reales del condominio en el recibo: logo, aviso con vencimiento y cuenta BCP."""

    def test_comando_y_recibo(self):
        from io import StringIO
        from django.core.management import call_command
        from departamentos.models import ConfiguracionCondominio, MedioPago
        cfg = ConfiguracionCondominio.objects.create(nombre='Demo')
        MedioPago.objects.create(configuracion=cfg, tipo=MedioPago.Tipo.PLIN, entidad='Plin', numero='999')
        call_command('configurar_condominio', stdout=StringIO())
        call_command('configurar_condominio', stdout=StringIO())  # idempotente
        cfg.refresh_from_db()
        self.assertEqual(cfg.nombre, 'Condominio Los Girasoles')
        self.assertEqual(list(cfg.medios_pago.filter(activo=True).values_list('numero', flat=True)),
                         ['1917786570020'])

        s.sembrar_conceptos()
        dep = Departamento.objects.create(numero_domicilio='705')
        s.crear_cargo(dep, Concepto.objects.get(codigo='MANTENIMIENTO'), date(2026, 8, 1), D('75.00'))
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('recibo'))
        r = self.client.get(f'/departamentos/{dep.pk}/boleta/?mes=8&anio=2026')
        self.assertContains(r, 'img/logo_condominio')
        self.assertContains(r, 'pagos hasta el 05-09;')
        self.assertContains(r, 'BCP - Cta. Cte.')
        self.assertNotContains(r, '999')


EXCEL_REAL = r'C:/Users/usuario/Downloads/BALANCE FIORELLA.xlsx'


def _libro_deudas():
    """Libro con la forma del Excel del condominio (sin datos personales)."""
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = 'NUMEROS'
    act = wb.create_sheet('DEUDAS ACTUALES')
    act.append([None, None, 'DEUDORES'])
    act.append([])
    act.append([None] * 6 + ['MANTENIMIENTO'])
    act.append([None, None, '# DPTO', 'PUERTAS', 'CAMARA', 'ASCENSORES', 'D. ANTERIORES', 'JULIO', 'AGOSTO',
                'CUOTA', 'MULTA', 'MORA', 'TOTAL'])
    act.append([None, None, 101, None, None, None, None, None, 106.46, 25, 20, None, 151.459999999999])
    act.append([None, None, 106, 35.72, 191.01, 80, 1207.96, 111.4, 75.37, 25, 20, None, 1746.46])
    act.append([None, None, 202, None, None, None, 122.25, 226.6, 265.95, 25, None, 15, 654.8])
    act.append([None, None, 307, None, None, 80, 677.15, 76.099999999999994, 74.97, 25, 20, None, 999])
    act.append([None, None, None, 35.72, 191.01, 160, 2007.36, None, None, None, None, None, 2400.48])
    ant = wb.create_sheet('DEUDAS ANTERIORES ')
    ant.append([None, None, None, None, 'DEUDAS POR CONCEPTO'])
    ant.append([])
    ant.append(['DEPART.', 'AÑO', 'ENERO', 'FEB.', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOS.', 'SET.',
                'OCT.', 'NOV.', 'DIC.', None, None, 'PUERTAS', 'TOTAL', None, None, None, '# DEPT', 'MAYO', 'JUNIO',
                'TOTAL'])
    ant.append([106, 2025, None, None, 72.47, 89.94, 74.79, 74.79, 74.48, 60.33, 78.05, 78.77, 74.66, 74.59,
                None, None, 35.72, 788.59, None, None, None, 101, None, 102.36, 102.36])
    ant.append([None, 2026, 75.91, 76.15, 76.15, 76.15, 74.23, 76.5] + [None] * 9 + [455.09]
               + [None] * 3 + [102, 113.41, 120.01, 233.42])
    ant.append([None] * 17 + [1243.68] + [None] * 6 + [335.78])
    ant.append([307, 2025] + [None] * 9 + [79.67, 74.66, 74.12] + [None] * 4 + [228.45])
    ant.append([None, 2026, 76.03, '…....', 73.39, 75.1, 73.95, 76.8] + [None] * 9 + [375.17])
    ant.append([None] * 15 + ['TOTAL GENERAL', None, 9999])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class CargaInicialParserTests(TestCase):
    """Parser del Excel de deudas: forma de DEUDAS ACTUALES + detalle de DEUDAS ANTERIORES."""

    def setUp(self):
        from departamentos.servicios.migracion import leer_excel_deudas
        self.libro = leer_excel_deudas(_libro_deudas(), 2026)
        self.f = {r['dpto']: r for r in self.libro['filas']}

    def test_columnas_meses_y_control(self):
        self.assertEqual(self.libro['meses'], ['2026-07', '2026-08'])
        self.assertEqual(list(self.f), ['101', '106', '202', '307'])
        self.assertEqual(self.libro['total_control'], D('2400.48'))
        self.assertEqual(self.f['101']['total'], D('151.46'))          # ruido de float redondeado
        self.assertEqual(self.f['307']['meses']['2026-07'], D('76.10'))
        self.assertTrue(any('no coincide' in a for a in self.libro['advertencias']))

    def test_conciliacion_de_d_anteriores(self):
        r = self.f['106']
        self.assertEqual(r['d_ant'], D('1207.96'))                     # 1,243.68 − puertas 35.72
        self.assertEqual(len(r['detalle']['meses']), 16)
        self.assertEqual(r['errores'], [])
        self.assertEqual(self.f['202']['detalle'], {'meses': {}, 'sin_periodo': D('122.25')})

    def test_errores_y_advertencias_por_fila(self):
        r = self.f['307']                                              # detalle 603.12 ≠ 677.15
        self.assertTrue(any('≠ detalle' in e for e in r['errores']))
        self.assertTrue(any('texto en 02/2026' in a for a in r['advertencias']))
        self.assertTrue(any('TOTAL del Excel 999.00' in a for a in r['advertencias']))
        self.assertEqual(self.f['101']['errores'], [])

    @skipUnless(os.path.exists(EXCEL_REAL), 'Excel real del condominio no disponible en esta máquina')
    def test_excel_real_del_condominio(self):
        from departamentos.servicios.migracion import leer_excel_deudas
        with open(EXCEL_REAL, 'rb') as fh:
            libro = leer_excel_deudas(fh, 2026)
        f = {r['dpto']: r for r in libro['filas']}
        self.assertEqual((len(libro['filas']), libro['total_calculado']), (45, D('16662.26')))
        self.assertEqual(libro['total_control'], D('16510.80'))        # la fórmula del Excel omite al 101
        self.assertEqual((f['508']['total'], f['508']['d_ant']), (D('4572.59'), D('4105.51')))
        meses = sorted(f['508']['detalle']['meses'])
        self.assertEqual((len(meses), meses[0], meses[-1]), (54, '2022-01', '2026-06'))
        self.assertEqual([r['dpto'] for r in libro['filas'] if r['errores']], [])


class CargaInicialGuardarTests(TestCase):
    """Los montos de la grilla pasan a cargos reales de migración, editables hasta que cuadre."""

    def setUp(self):
        from departamentos.servicios.migracion import leer_excel_deudas
        s.sembrar_conceptos()
        self.corte = date(2026, 8, 1)
        self.dep = Departamento.objects.create(numero_domicilio='106')
        self.fila = {r['dpto']: r for r in leer_excel_deudas(_libro_deudas(), 2026)['filas']}['106']

    def _guardar(self, fila=None):
        from departamentos.servicios.migracion import guardar_deuda_migrada
        return guardar_deuda_migrada(self.dep, fila or self.fila, self.corte)

    def test_crea_cargos_con_su_periodo_y_cuadra(self):
        creados, _, _ = self._guardar()
        mig = Cargo.objects.filter(origen=Cargo.Origen.MIGRACION)
        self.assertEqual(creados, mig.count())
        self.assertEqual(sum(c.monto for c in mig), D('1746.46'))
        self.assertTrue(mig.filter(concepto__codigo='MANTENIMIENTO', periodo=date(2025, 3, 1),
                                   monto=D('72.47')).exists())
        self.assertTrue(mig.filter(concepto__codigo='PUERTAS', periodo=self.corte, monto=D('35.72')).exists())
        # En el recibo de septiembre es deuda pendiente.
        self.assertEqual(s.deuda_anterior(self.dep, date(2026, 9, 1)), D('1746.46'))

    def test_editar_actualiza_y_cero_quita(self):
        self._guardar()
        fila = {**self.fila, 'puertas': D('0'), 'cuota': D('30.00')}
        _, actualizados, borrados = self._guardar(fila)
        self.assertEqual((actualizados, borrados), (1, 1))
        self.assertFalse(Cargo.objects.filter(concepto__codigo='PUERTAS').exists())

    def test_no_pisa_cargos_del_sistema_ni_baja_de_lo_pagado(self):
        s.crear_cargo(self.dep, Concepto.objects.get(codigo='MANTENIMIENTO'), date(2026, 7, 1), D('69.04'))
        with self.assertRaisesRegex(ValueError, 'cargo del sistema'):
            self._guardar()
        self.assertFalse(Cargo.objects.filter(origen=Cargo.Origen.MIGRACION).exists())   # nada a medias
        Cargo.objects.all().delete()
        self._guardar()
        s.registrar_pago(self.dep, D('100.00'))           # salda primero lo más antiguo (2025-03)
        meses = {**self.fila['detalle']['meses'], '2025-03': D('10.00')}
        fila = {**self.fila, 'detalle': {**self.fila['detalle'], 'meses': meses}}
        with self.assertRaisesRegex(ValueError, 'ya tiene pagos'):
            self._guardar(fila)

    def test_vista_guardar_y_quitar_todo(self):
        import json
        from django.contrib.auth.models import User
        from departamentos.views.carga_deudas import _a_json
        self.client.force_login(User.objects.create_user('migra'))
        url = '/carga-inicial-deudas/?corte=2026-08'
        self.assertContains(self.client.get(url), 'Carga inicial de deudas')
        fila = _a_json(self.fila, self.dep)
        r = self.client.post(url, json.dumps({'accion': 'guardar', 'rows': [fila]}),
                             content_type='application/json').json()
        self.assertEqual((r['errores'], r['resumen']['deptos'], r['migrado']['total']), ([], 1, 1746.46))
        r = self.client.post(url, json.dumps({'accion': 'quitar_todo'}), content_type='application/json').json()
        self.assertEqual((r['con_pagos'], r['migrado']['n']), (0, 0))

    def test_excel_con_meses_posteriores_mueve_el_corte(self):
        """Con boletas del sistema desde julio el corte por defecto es junio; el Excel
        trae JULIO y AGOSTO: el corte pasa a agosto en vez de marcar error en cada fila."""
        from django.contrib.auth.models import User
        s.crear_cargo(self.dep, Concepto.objects.get(codigo='MANTENIMIENTO'), date(2026, 7, 1), D('69.04'))
        self.client.force_login(User.objects.create_user('migra2'))
        r = self.client.post('/carga-inicial-deudas/', {'archivo': _libro_deudas(), 'anio': '2026'}).json()
        self.assertEqual((r['corte'], [m['nombre'] for m in r['meses']]), ('2026-08', ['JULIO', 'AGOSTO']))
        self.assertIsNotNone(r['filas_sistema'])
        self.assertFalse(any('posterior al corte' in e for f in r['filas'] for e in f['errores']))
        self.assertIn('pasó a Agosto 2026', r['advertencias'][0])


class BorrarGeneracionTests(TestCase):
    """Borrar las boletas generadas de un mes: solo lo generado y sin pagos, salvo que se pida más."""

    def setUp(self):
        s.sembrar_conceptos()
        self.jul = date(2026, 7, 1)
        self.a = Departamento.objects.create(numero_domicilio='101')
        self.b = Departamento.objects.create(numero_domicilio='102')
        mant = Concepto.objects.get(codigo='MANTENIMIENTO')
        for dep in (self.a, self.b):
            s.crear_cargo(dep, mant, self.jul, D('69.04'))
            s.crear_cargo(dep, Concepto.objects.get(codigo='AREA_COMUN'), self.jul, D('10.11'))
        s.crear_cargo(self.a, Concepto.objects.get(codigo='MULTA'), self.jul, D('20.00'))     # manual
        s.registrar_pago(self.b, D('50.00'), concepto=mant)                                   # 102 pagó parte
        Cargo.objects.create(departamento=self.a, concepto=mant, periodo=date(2026, 6, 1), monto=D('75.00'),
                             fecha_vencimiento=date(2026, 7, 5), origen=Cargo.Origen.MIGRACION)
        Cargo.objects.create(departamento=self.a, concepto=mant, periodo=self.jul.replace(month=8),
                             monto=D('69.04'), fecha_vencimiento=date(2026, 9, 5))             # otro mes

    def test_vista_previa_no_borra_y_respeta_pagos_y_manuales(self):
        r = s.borrar_generacion_mes(self.jul)
        self.assertEqual((r['n'], r['deptos'], r['total'], r['conservados']), (3, 2, D('89.26'), 1))
        self.assertEqual(Cargo.objects.filter(periodo=self.jul).count(), 5)            # solo vista previa

    def test_borra_lo_generado_y_conserva_lo_demas(self):
        s.borrar_generacion_mes(self.jul, aplicar=True)
        quedan = sorted(Cargo.objects.filter(periodo=self.jul).values_list('departamento__numero_domicilio',
                                                                         'concepto__codigo'))
        self.assertEqual(quedan, [('101', 'MULTA'), ('102', 'MANTENIMIENTO')])
        self.assertTrue(Cargo.objects.filter(origen=Cargo.Origen.MIGRACION).exists())   # carga inicial intacta
        self.assertTrue(Cargo.objects.filter(periodo=date(2026, 8, 1)).exists())        # otros meses intactos

    def test_con_pagos_el_pago_queda_a_favor(self):
        r = s.borrar_generacion_mes(self.jul, incluir_manuales=True, incluir_pagados=True, aplicar=True)
        self.assertEqual((r['n'], r['pagos_liberados']), (5, D('50.00')))
        self.assertFalse(Cargo.objects.filter(periodo=self.jul).exists())
        self.assertEqual(self.b.pagos.get().saldo_a_favor, D('50.00'))

    def test_pantalla_vista_previa_y_confirmar(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('boletas'))
        r = self.client.get('/recibos/generar/', {'b-mes': 7, 'b-anio': 2026})
        self.assertContains(r, 'Borrar 3 cargos de 07/2026')
        self.assertContains(r, 'Se conservan 1 cargo')
        r = self.client.post('/recibos/generar/', {'accion': 'borrar', 'b-mes': 7, 'b-anio': 2026})
        self.assertRedirects(r, '/recibos/generar/')
        self.assertEqual(Cargo.objects.filter(periodo=self.jul).count(), 2)


class DeudasCobradasTests(TestCase):
    """Tabla de deudas de mayo/junio ya cobradas: deuda saldada con un pago de la carga
    inicial que suma como ingreso en el balance, en la fecha de cobro."""

    def setUp(self):
        from departamentos.servicios.migracion import fila_vacia
        s.sembrar_conceptos()
        self.corte, self.cobro = date(2026, 8, 1), date(2026, 8, 31)
        self.dep = Departamento.objects.create(numero_domicilio='102')
        self.fila = fila_vacia('102', ['2026-07', '2026-08'])
        self.fila['cobradas'] = {'2026-05': D('113.41'), '2026-06': D('120.01')}

    def _guardar(self, fila=None, fecha=None):
        from departamentos.servicios.migracion import guardar_deuda_migrada
        return guardar_deuda_migrada(self.dep, fila or self.fila, self.corte, fecha or self.cobro)

    def test_parser_lee_la_tabla_de_cobradas(self):
        from departamentos.servicios.migracion import leer_excel_deudas
        libro = leer_excel_deudas(_libro_deudas(), 2026)
        self.assertEqual({d: c['meses'] for d, c in libro['cobradas'].items()},
                         {'101': {'2026-06': D('102.36')}, '102': {'2026-05': D('113.41'), '2026-06': D('120.01')}})
        self.assertEqual((libro['cobradas_total'], libro['cobradas_control']), (D('335.78'), D('335.78')))

    def test_es_ingreso_y_no_deuda(self):
        from departamentos.views.balance import _balance_ingresos
        self._guardar()
        self.assertEqual(s.deuda_anterior(self.dep, date(2026, 9, 1)), D('0.00'))      # no sale en el recibo
        pagos = Pago.objects.filter(origen=Cargo.Origen.MIGRACION)
        self.assertEqual((pagos.count(), sum(p.monto for p in pagos)), (2, D('233.42')))
        total, filas, _ = _balance_ingresos(2026, 8)
        self.assertEqual(total, D('233.42'))
        self.assertEqual([f['nombre'] for f in filas], ['Mantenimiento'])

    def test_mes_con_parte_pendiente_y_parte_cobrada(self):
        from departamentos.servicios.migracion import deuda_migrada
        self.fila['detalle']['meses'] = {'2026-06': D('50.00')}
        self.fila['d_ant'] = D('50.00')
        self._guardar()
        cargo = Cargo.objects.get(periodo=date(2026, 6, 1))
        self.assertEqual((cargo.monto, cargo.saldo), (D('170.01'), D('50.00')))
        r = deuda_migrada(self.corte, ['2026-07', '2026-08'])['102']
        self.assertEqual((r['d_ant'], r['cobradas']['2026-06'], r['total']), (D('50.00'), D('120.01'), D('50.00')))

    def test_cambiar_fecha_y_quitar(self):
        import json
        from django.contrib.auth.models import User
        self._guardar()
        self._guardar(fecha=date(2026, 8, 15))
        self.assertEqual(set(Pago.objects.values_list('fecha_pago', flat=True)), {date(2026, 8, 15)})
        self.client.force_login(User.objects.create_user('cobradas'))
        r = self.client.post('/carga-inicial-deudas/?corte=2026-08', json.dumps({'accion': 'quitar_todo'}),
                             content_type='application/json').json()
        self.assertEqual(r['borrados'], 2)
        self.assertFalse(Pago.objects.exists())


class DeudaAcumuladaTests(TestCase):
    """Deuda por antigüedad (solo vista): viendo septiembre, julio-agosto-septiembre
    sueltos y todo lo anterior (de cualquier concepto) en un solo acumulado."""

    def setUp(self):
        s.sembrar_conceptos()
        self.dep = Departamento.objects.create(numero_domicilio='508', nombres='Ana')
        mant, puertas = Concepto.objects.get(codigo='MANTENIMIENTO'), Concepto.objects.get(codigo='PUERTAS')
        for p, m in [((2025, 3), '72.47'), ((2026, 6), '76.80'), ((2026, 7), '76.10'), ((2026, 8), '74.97'),
                     ((2026, 9), '69.04'), ((2026, 10), '70.00')]:
            s.crear_cargo(self.dep, mant, date(*p, 1), D(m))
        s.crear_cargo(self.dep, puertas, date(2026, 5, 1), D('35.72'))

    def test_agrupa_lo_de_hace_mas_de_dos_meses(self):
        g = s.agrupar_deuda(self.dep.cargos.all(), date(2026, 9, 1))
        self.assertEqual((g['acumulado'], g['hasta']), (D('184.99'), date(2026, 6, 1)))   # 72.47+76.80+35.72
        self.assertEqual(g['meses'], [(date(2026, 7, 1), D('76.10')), (date(2026, 8, 1), D('74.97')),
                                      (date(2026, 9, 1), D('69.04'))])
        self.assertEqual((g['posterior'], g['total']), (D('70.00'), D('475.10')))

    def test_pantalla_deudores(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('acum'))
        r = self.client.get('/deudores/', {'mes': 9, 'anio': 2026})
        self.assertEqual((r.context['col_hasta'], r.context['col_meses']), ('Junio 2026', ['Jul 2026', 'Ago 2026', 'Set 2026']))
        fila = r.context['filas'][0]
        self.assertEqual((fila['acumulado'], [m['monto'] for m in fila['por_mes']], fila['posterior']),
                         (D('184.99'), [D('76.10'), D('74.97'), D('69.04')], D('70.00')))
        self.assertContains(r, 'Acumulado (hasta Junio 2026)')

    def test_registrar_pagos_trae_el_corte_del_acumulado(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('acum2'))
        r = self.client.get('/pagos/registrar/')
        hoy = timezone.localdate().replace(day=1)
        self.assertEqual(r.context['antiguedad']['actual'], hoy.isoformat())
        self.assertEqual(r.context['antiguedad']['inicio'], s.sumar_meses(hoy, -2).isoformat())
        self.assertContains(r, 'antiguedadData')
