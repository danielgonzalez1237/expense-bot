// Bloqueo de Family Office GG: 30 minutos sin tocar la página → se cierra la sesión y se pide el PIN otra vez.
// Un minuto antes avisa con "Sigo aquí". Al volver a abrir después de la media hora, pide el PIN antes de mostrar nada.
// window.foggBloquear() lo bloquea a mano (botón del Mac, ⌘L, "Bloquear ahora"). Los widgets no se tocan: usan su propia llave.
// La misma copia vive en las dos plataformas (patrimonio y P&G).
(function () {
  'use strict';
  if (window.foggBloquear) return;
  var CFG = window.FOGG_CANDADO || {};
  var LIM = 30 * 60 * 1000, AVISO = 60 * 1000, K = 'fogg-ultimo-uso';
  function leer() { try { return Number(localStorage.getItem(K)) || 0; } catch (e) { return 0; } }
  function guardar(t) { try { localStorage.setItem(K, String(t)); } catch (e) { /* sin almacenamiento: cuenta solo esta página */ } }
  var ultimo = leer(), bloqueando = false, aviso = null, texto = null, marcado = 0;
  function quieto() { return Date.now() - Math.max(ultimo, leer()); }
  function irAlPin() {
    if (CFG.entrar) location.replace(CFG.entrar + '?volver=' + encodeURIComponent(location.pathname + location.search + location.hash));
    else location.reload();
  }
  function bloquear(motivo) {
    if (bloqueando) return;
    bloqueando = true;
    try { document.documentElement.style.visibility = 'hidden'; } catch (e) { /* nada */ }
    try { sessionStorage.setItem('fogg-motivo', motivo || 'inactividad'); } catch (e) { /* nada */ }
    var hecho = false;
    var seguir = function () { if (!hecho) { hecho = true; irAlPin(); } };
    try { fetch(CFG.salir, { method: 'POST', credentials: 'same-origin', keepalive: true }).then(seguir, seguir); } catch (e) { seguir(); }
    setTimeout(seguir, 2500);
  }
  window.foggBloquear = function (motivo) { bloquear(motivo || 'manual'); };
  if (ultimo && quieto() >= LIM) { bloquear('inactividad'); return; }   // abrió después de la media hora
  ultimo = Date.now(); guardar(ultimo);

  function uso() {
    var t = Date.now();
    if (!aviso && t - marcado < 5000) return;
    marcado = t; ultimo = t; guardar(t); quitarAviso();
  }
  ['pointerdown', 'keydown', 'wheel', 'touchstart', 'input', 'scroll', 'mousemove'].forEach(function (ev) {
    window.addEventListener(ev, uso, { capture: true, passive: true });
  });
  function revisar() {
    if (bloqueando) return;
    var q = quieto();
    if (q >= LIM) bloquear('inactividad');
    else if (q >= LIM - AVISO) mostrarAviso(LIM - q);
    else quitarAviso();
  }
  setInterval(revisar, 1000);
  document.addEventListener('visibilitychange', revisar);
  window.addEventListener('focus', revisar);
  window.addEventListener('pageshow', revisar);

  function mostrarAviso(ms) {
    var s = Math.max(0, Math.ceil(ms / 1000));
    if (!aviso) {
      aviso = document.createElement('div');
      aviso.setAttribute('role', 'status');
      var abajo = window.innerWidth < 768 ? 84 : 24;
      aviso.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:calc(' + abajo + 'px + env(safe-area-inset-bottom));'
        + 'z-index:2147483000;display:flex;align-items:center;gap:10px;width:max-content;max-width:calc(100vw - 32px);box-sizing:border-box;'
        + 'padding:8px 8px 8px 14px;border-radius:14px;background:#14171c;color:#f1f3f5;border:1px solid #2a2f38;'
        + 'font:500 14px/1.3 Inter,-apple-system,system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.25)';
      var ic = document.createElement('span');
      ic.setAttribute('aria-hidden', 'true');
      ic.style.cssText = 'display:inline-flex;color:#eda100;flex-shrink:0';
      ic.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
      texto = document.createElement('span');
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = 'Sigo aquí';
      b.style.cssText = 'border:0;border-radius:10px;padding:8px 12px;background:#eda100;color:#14171c;font:600 13px Inter,-apple-system,system-ui,sans-serif;cursor:pointer;white-space:nowrap;flex-shrink:0';
      b.addEventListener('click', function () { marcado = 0; uso(); });
      aviso.appendChild(ic); aviso.appendChild(texto); aviso.appendChild(b);
      (document.body || document.documentElement).appendChild(aviso);
    }
    texto.textContent = 'Por seguridad, se bloquea en\u00a00:' + (s < 10 ? '0' : '') + s;
  }
  function quitarAviso() { if (aviso) { aviso.remove(); aviso = null; texto = null; } }
})();
