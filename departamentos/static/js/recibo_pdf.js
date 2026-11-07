/* PDF real del recibo de mantenimiento, generado en el navegador.
 * Requiere html2canvas y jsPDF (y JSZip para descargar varios en un .zip).
 *   reciboPdf(hoja)                -> Promise<Blob>  (hoja = elemento .rm-hoja)
 *   descargarReciboPdf(hoja, nombre)
 *   descargarRecibosZip(items, nombreZip, onProgreso)  items = [{hoja, nombre}]
 * Botones con data-pdf="#id-contenedor" data-archivo="nombre.pdf" bajan ese recibo.
 */
(function () {
  const ANCHO = 1456, ALTO = 1082;   // lienzo fijo del recibo (recibo_mantenimiento.css)

  async function reciboPdf(hoja) {
    await document.fonts.ready;
    const canvas = await html2canvas(hoja, {
      scale: 1.5, useCORS: true, backgroundColor: '#ffffff',
      width: ANCHO, height: ALTO, windowWidth: ANCHO + 400,
      // En la página del mes hay ~90 recibos: copiar solo este (10 s → 2 s por PDF).
      ignoreElements: (el) => el.classList && el.classList.contains('recibo-depto') && !el.contains(hoja),
    });
    const pdf = new jspdf.jsPDF({ orientation: 'landscape', unit: 'px', format: [ANCHO, ALTO], hotfixes: ['px_scaling'] });
    pdf.addImage(canvas.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, ANCHO, ALTO);
    return pdf.output('blob');
  }

  function guardar(blob, nombre) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = nombre;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  async function descargarReciboPdf(hoja, nombre) { guardar(await reciboPdf(hoja), nombre); }

  async function descargarRecibosZip(items, nombreZip, onProgreso) {
    const zip = new JSZip();
    for (let i = 0; i < items.length; i++) {
      zip.file(items[i].nombre, await reciboPdf(items[i].hoja));
      if (onProgreso) onProgreso(i + 1, items.length);
    }
    guardar(await zip.generateAsync({ type: 'blob' }), nombreZip);
  }

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-pdf]');
    if (!btn) return;
    const hoja = document.querySelector(btn.dataset.pdf + ' .rm-hoja');
    const txt = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'Generando…';
    try { await descargarReciboPdf(hoja, btn.dataset.archivo); }
    catch (err) { console.error(err); alert('No se pudo generar el PDF.'); }
    finally { btn.disabled = false; btn.innerHTML = txt; }
  });

  Object.assign(window, { reciboPdf, descargarReciboPdf, descargarRecibosZip });
})();
