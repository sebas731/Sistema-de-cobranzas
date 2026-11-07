"""Genera recibo.png y recibo.pdf del recibo REAL de Django para revisar el diseño.

Renderiza la vista recibo_boleta (con la BD local de pruebas, depto 101-DEMO) sin
levantar runserver: Django arma el HTML y Playwright lo pinta, sirviendo /static/
desde las carpetas del proyecto.

Uso:  C:\\Cobranzas\\venv\\Scripts\\python.exe recibo\\verificar.py [mes] [anio]
"""
import os
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
PROYECTO = CARPETA.parent
sys.path.insert(0, str(PROYECTO))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Cobranzas.settings')

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.staticfiles import finders  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from departamentos.models import Departamento  # noqa: E402

mes, anio, numero = (sys.argv[1:4] + ['10', '2026', '101-DEMO'][len(sys.argv[1:4]):])[:3]
ORIGEN = 'http://recibo.local'

# 1. Django arma el HTML del recibo, como si un usuario logueado abriera la página.
depto = Departamento.objects.get(numero_domicilio=numero)
cliente = Client(HTTP_HOST='127.0.0.1')
cliente.force_login(get_user_model().objects.filter(is_superuser=True).first())
respuesta = cliente.get(reverse('recibo_boleta', args=[depto.pk]), {'mes': mes, 'anio': anio})
assert respuesta.status_code == 200, respuesta.status_code
html = respuesta.content.decode()


def servir(route):
    """Entrega la página y los archivos de /static/ (CSS, fuentes, logo) desde el disco."""
    ruta = route.request.url[len(ORIGEN):].split('?')[0]
    if ruta == '/':
        return route.fulfill(body=html, content_type='text/html; charset=utf-8')
    archivo = ruta.startswith('/static/') and finders.find(ruta[len('/static/'):])
    return route.fulfill(path=archivo) if archivo else route.fulfill(status=404)


# 2. Playwright lo pinta y guarda PNG (solo la hoja) y PDF (página 1456 × 1082).
with sync_playwright() as p:
    navegador = p.chromium.launch()
    pagina = navegador.new_page(viewport={'width': 2000, 'height': 1300}, device_scale_factor=2)
    pagina.route(f'{ORIGEN}/**', servir)
    pagina.goto(f'{ORIGEN}/')
    # En la página el recibo va dentro de un contenedor con scroll horizontal; para la
    # captura se quita ese recorte y la hoja se ve entera.
    pagina.add_style_tag(content='.recibo-mantenimiento { overflow: visible !important; }')
    pagina.wait_for_timeout(800)

    if not pagina.evaluate("document.fonts.check('700 16px \"Roboto Condensed\"')"):
        print('AVISO: Roboto Condensed no cargó; revisa static/fonts/')

    # Revisión de la skill: ningún texto cortado ("…") y ninguna zona desbordada.
    problemas = pagina.evaluate('''() => {
      const out = [];
      document.querySelectorAll('.rm-hoja td, .rm-hoja th, .rm-hoja b, .rm-hoja span').forEach(el => {
        if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).textOverflow === 'ellipsis')
          out.push('Texto cortado: ' + el.textContent.trim().slice(0, 60));
      });
      document.querySelectorAll('.rm-zona').forEach(z => {
        if (z.scrollHeight > z.clientHeight + 1)
          out.push(`Zona ${z.className} desborda: ${z.scrollHeight}px de contenido en ${z.clientHeight}px`);
      });
      return out;
    }''')
    for problema in problemas:
        print('AVISO:', problema)

    pagina.locator('.rm-hoja').screenshot(path=str(CARPETA / 'recibo.png'))
    pagina.pdf(path=str(CARPETA / 'recibo.pdf'), width='1456px', height='1082px', print_background=True)
    navegador.close()

print('Listo:', CARPETA / 'recibo.png', 'y', CARPETA / 'recibo.pdf')
