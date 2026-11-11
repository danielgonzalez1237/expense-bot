// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: yellow; icon-glyph: receipt;

// Anotar gasto · P&G mensual (Family Office GG)
// Abre una hoja con el diseño de FO GG: valor, moneda, en qué, con qué, cuándo y una nota. Queda anotado en el bot
// a nombre de quien emparejó este teléfono. Se usa desde un atajo (pantalla bloqueada, Siri, Botón de Acción).
// Seguridad: este archivo no trae ninguna llave. La primera vez pide el código de un solo uso de
// P&G GG → Ajustes → Teléfonos y Macs. La llave queda en el llavero del iPhone y se puede quitar desde ahí.
// Desde un atajo se le puede pasar {monto, comercio, tarjeta} (por ejemplo, al pagar con Apple Pay):
// la hoja abre con eso puesto y todo se puede cambiar antes de tocar Anotar.

const BASE = "https://expense-bot-production-04c4.up.railway.app";
const CLAVE = "pyg.llave";

// ---------- la hoja (HTML) ----------
function hoja(d, prefs, previo, llave) {
  const datos = JSON.stringify({ d, prefs, previo, llave }).replace(/</g, "\\u003c");
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<style>
:root{--tope:60px;--fondo:#f3f4f6;--hoja:#ffffff;--txt:#121418;--apag:#6b7280;--borde:#e3e6ea;--suave:#eceef2;--oro:#eda100;--orot:#8a5f00;--orosuave:#fdf1d3;--alerta:#c63a2d;--sombra:0 1px 2px rgba(16,24,40,.05),0 2px 6px rgba(16,24,40,.06)}
@media (prefers-color-scheme: dark){:root{--fondo:#0b0d11;--hoja:#15181e;--txt:#f2f3f5;--apag:#9aa1ab;--borde:#262b33;--suave:#1d2128;--orot:#f2b730;--orosuave:rgba(237,161,0,.16);--alerta:#f07a6a;--sombra:none}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--fondo);color:var(--txt);font-family:-apple-system,"SF Pro Text",Inter,system-ui,sans-serif;font-variant-numeric:tabular-nums;-webkit-text-size-adjust:100%}
body.quieto{overflow:hidden}
button{font:inherit;color:inherit;cursor:pointer;transition:transform .12s ease,background-color .15s ease,border-color .15s ease}
button:active{transform:scale(.97)}
.oculto{display:none!important}
.hoja{max-width:520px;margin:0 auto;padding:var(--tope) 20px 0}
.cab{display:flex;align-items:center;justify-content:space-between;gap:12px}
.cab h1{margin:0;font:700 28px/1.1 -apple-system,"SF Pro Display",Inter,system-ui,sans-serif;letter-spacing:-.02em}
.sub{margin-top:4px;font-size:13px;color:var(--apag)}
.avatar{width:42px;height:42px;border-radius:21px;flex-shrink:0;background:var(--orosuave);color:var(--orot);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:17px;box-shadow:inset 0 0 0 2px var(--oro)}
.aviso{margin-top:14px;display:flex;gap:10px;align-items:center;border-radius:14px;background:var(--orosuave);color:var(--orot);font-size:13px;line-height:1.35;padding:10px 12px}
.aviso svg{flex-shrink:0}
.tarjeta{background:var(--hoja);border:1px solid var(--borde);border-radius:20px;box-shadow:var(--sombra)}
.valor{margin-top:16px;padding:14px 16px 16px}
.seg{display:flex;gap:2px;padding:3px;border-radius:11px;background:var(--suave);width:max-content;margin:0 auto}
.seg button{border:0;background:transparent;color:var(--apag);font-size:13px;padding:7px 14px;border-radius:8px;font-weight:600}
.seg button.on{background:var(--hoja);color:var(--txt);box-shadow:0 1px 3px rgba(0,0,0,.12)}
.centro{display:flex;justify-content:center;margin-top:12px}
.cifra{position:relative;display:inline-flex;align-items:baseline;gap:.12em;font:700 58px/1.15 -apple-system,"SF Pro Display",Inter,system-ui,sans-serif;letter-spacing:-.03em;max-width:100%}
.simbolo{font-size:.5em;color:var(--apag);font-weight:600;letter-spacing:0}
.monto{border:0;background:transparent;color:var(--txt);font:inherit;letter-spacing:inherit;padding:0;outline:none;min-width:.6em;text-align:left;caret-color:var(--oro)}
.monto::placeholder{color:var(--apag);opacity:.45}
.medida{position:absolute;visibility:hidden;white-space:pre;left:0;top:0}
.presu{margin-top:14px}
.barra{height:8px;border-radius:4px;background:var(--suave);overflow:hidden;display:flex;gap:0}
.barra i{display:block;height:100%;transition:width .25s ease}
.barra .g{background:var(--oro);border-radius:4px 0 0 4px}
.barra .e{background:repeating-linear-gradient(-45deg,var(--oro) 0 3px,transparent 3px 6px);opacity:.75}
.presu.pasado .barra .g{background:var(--alerta)}
.presu.pasado .barra .e{background:repeating-linear-gradient(-45deg,var(--alerta) 0 3px,transparent 3px 6px)}
.presu-t{display:flex;flex-wrap:wrap;justify-content:space-between;gap:2px 10px;margin-top:7px;font-size:12.5px;color:var(--apag)}
.presu-t b{font-weight:600;color:var(--txt)}
.presu.pasado .presu-t b{color:var(--alerta)}
.tit{font-size:15px;font-weight:650;margin:22px 0 10px;letter-spacing:-.01em}
.rejilla{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.cat{border:1px solid var(--borde);background:var(--hoja);border-radius:16px;padding:11px 4px 9px;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;gap:5px;min-height:78px;box-shadow:var(--sombra);position:relative}
.cat .emo{font-size:25px;line-height:1}
.cat .et{font-size:12px;line-height:1.2;text-align:center;font-weight:500;max-width:100%;overflow-wrap:anywhere}
.cat .gr{font-size:10.5px;line-height:1.15;color:var(--apag);text-align:center;max-width:100%;overflow-wrap:break-word}
.cat.on{background:var(--orosuave);border-color:var(--oro);box-shadow:0 0 0 1px var(--oro)}
.cat.on .et{color:var(--orot);font-weight:650}
.cat.mas{background:transparent;border-style:dashed;box-shadow:none;color:var(--apag)}
.cat.mas svg{margin:1px 0 2px}
.tarjetas{display:flex;gap:10px;overflow-x:auto;margin:0 -20px;padding:6px 20px 10px;scroll-padding:0 20px;scroll-snap-type:x proximity;scrollbar-width:none}
.tarjetas::-webkit-scrollbar{display:none}
.tj{flex-shrink:0;scroll-snap-align:start;border:0;border-radius:14px;min-width:116px;min-height:66px;padding:10px 13px;display:flex;flex-direction:column;justify-content:flex-end;align-items:flex-start;text-align:left;white-space:nowrap;position:relative;box-shadow:0 2px 6px rgba(0,0,0,.14)}
.tj-a{font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;opacity:.82}
.tj-b{font-size:14px;font-weight:600;margin-top:2px}
.tj.on{box-shadow:0 0 0 2.5px var(--fondo),0 0 0 5px var(--oro)}
.tj .ok{position:absolute;top:8px;right:8px;width:20px;height:20px;border-radius:10px;background:var(--oro);display:flex;align-items:center;justify-content:center}
.tj.otro{background:transparent!important;color:var(--apag)!important;border:1.5px dashed var(--borde);box-shadow:none;justify-content:center;align-items:center;min-width:92px}
.lista{overflow:hidden}
.fila{display:flex;align-items:center;gap:12px;padding:0 14px;min-height:54px}
.fila+.fila{border-top:1px solid var(--borde)}
.fila>svg{flex-shrink:0;color:var(--apag)}
.dias{display:flex;gap:6px;flex-wrap:wrap}
.dias button,.otro-dia{border:1px solid var(--borde);background:var(--suave);border-radius:17px;padding:7px 13px;font-size:14px;font-weight:500;position:relative;display:inline-flex;align-items:center}
.dias .on{background:var(--orosuave);border-color:var(--oro);color:var(--orot);font-weight:650}
.otro-dia input{position:absolute;inset:0;opacity:0;width:100%;height:100%;font-size:16px;border:0}
.nota{flex:1;min-width:0;border:0;background:transparent;color:var(--txt);font-size:16px;outline:none;padding:14px 0}
.nota::placeholder{color:var(--apag)}
.pie{position:sticky;bottom:0;margin:6px -20px 0;padding:18px 20px calc(14px + env(safe-area-inset-bottom));background:linear-gradient(to bottom,transparent,var(--fondo) 26%)}
.error{color:var(--alerta);font-size:13px;line-height:1.35;text-align:center;margin-bottom:10px;font-weight:500}
.anotar{width:100%;min-height:58px;border:0;border-radius:17px;background:var(--oro);color:#14171c;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;padding:9px 16px;box-shadow:0 6px 16px rgba(237,161,0,.28)}
.anotar b{font-size:17px;font-weight:700}
.anotar small{font-size:12.5px;font-weight:500;opacity:.78;text-align:center;line-height:1.25}
.anotar:disabled{background:var(--suave);color:var(--apag);box-shadow:none;transform:none}
.capa{position:fixed;inset:0;z-index:20;background:var(--fondo);padding:var(--tope) 20px 24px;overflow-y:auto;-webkit-overflow-scrolling:touch;display:none}
.capa.si{display:block;animation:sube .22s ease-out}
.capa-cab{display:flex;align-items:center;justify-content:space-between;gap:12px}
.capa-cab b{font-size:22px;letter-spacing:-.02em}
.capa-cab button{border:0;background:transparent;color:var(--orot);font-size:16px;font-weight:600;padding:6px 0}
.buscar{display:flex;align-items:center;gap:8px;margin:12px 0 4px;background:var(--suave);border-radius:12px;padding:0 12px;color:var(--apag)}
.buscar input{flex:1;min-width:0;border:0;background:transparent;color:var(--txt);font-size:16px;padding:11px 0;outline:none}
.capa-g{font-size:12px;font-weight:650;color:var(--apag);letter-spacing:.04em;text-transform:uppercase;margin:18px 4px 6px}
.capa-f{width:100%;border:0;background:var(--hoja);display:flex;align-items:center;gap:12px;padding:13px 14px;text-align:left;font-size:16px;border-bottom:1px solid var(--borde)}
.capa-g+.capa-f{border-radius:14px 14px 0 0}
.capa-f.fin{border-radius:0 0 14px 14px;border-bottom:0}
.capa-g+.capa-f.fin{border-radius:14px}
.capa-f .emo{font-size:21px;width:26px;text-align:center}
.capa-f .t{flex:1}
.capa-f.on .t{color:var(--orot);font-weight:650}
.capa-vacio{color:var(--apag);text-align:center;padding:30px 10px;font-size:14px}
.listo{position:fixed;inset:0;z-index:30;background:var(--fondo);display:none;padding:var(--tope) 24px 24px;overflow-y:auto}
.listo.si{display:flex;align-items:center;justify-content:center}
.listo-in{width:100%;max-width:380px;display:flex;flex-direction:column;align-items:center;text-align:center}
.listo.si .listo-in{animation:sube .35s ease-out}
.check{width:78px;height:78px}
.check circle{fill:var(--oro);transform-box:fill-box;transform-origin:center;transform:scale(0)}
.check path{fill:none;stroke:#14171c;stroke-width:4.5;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:36;stroke-dashoffset:36}
.listo.si .check circle{animation:pop .4s cubic-bezier(.2,.9,.3,1.3) forwards}
.listo.si .check path{animation:raya .35s .28s ease-out forwards}
.l-ok{margin-top:14px;font-size:15px;color:var(--apag);font-weight:600}
.l-monto{font:700 40px/1.15 -apple-system,"SF Pro Display",Inter,system-ui,sans-serif;letter-spacing:-.03em;margin-top:2px;overflow-wrap:anywhere}
.l-det{margin-top:6px;font-size:15px;line-height:1.35}
.l-fecha{margin-top:3px;font-size:13px;color:var(--apag)}
.listo .tarjeta{width:100%;margin-top:20px;padding:14px 16px;text-align:left}
.otro{margin-top:18px;width:100%;min-height:52px;border-radius:16px;border:1px solid var(--borde);background:var(--hoja);font-size:16px;font-weight:650;box-shadow:var(--sombra)}
.salir{margin-top:12px;font-size:12.5px;color:var(--apag)}
@keyframes pop{to{transform:scale(1)}}
@keyframes raya{to{stroke-dashoffset:0}}
@keyframes sube{from{opacity:0;transform:translateY(10px)}}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}.check circle{transform:none}.check path{stroke-dashoffset:0}}
</style></head><body>
<div class="hoja">
  <header class="cab">
    <div><h1>Anotar gasto</h1><div class="sub" id="quien"></div></div>
    <div class="avatar" id="avatar" aria-hidden="true"></div>
  </header>
  <div class="aviso oculto" id="aviso"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="6" width="18" height="13" rx="3"/><path d="M3 10.5h18M7 15h3"/></svg><span id="avisoT"></span></div>

  <section class="tarjeta valor" id="valor">
    <div class="seg" id="monedas" role="group" aria-label="Moneda"></div>
    <div class="centro"><label class="cifra" id="cifra"><span class="simbolo" id="simbolo"></span><input class="monto" id="monto" inputmode="numeric" autocomplete="off" placeholder="0" aria-label="Valor"><span class="medida" id="medida" aria-hidden="true"></span></label></div>
    <div class="presu" id="pP"><div class="barra"><i class="g" id="pG"></i><i class="e" id="pE"></i></div><div class="presu-t"><span id="pI"></span><b id="pD"></b></div></div>
  </section>

  <div class="tit">¿En qué?</div>
  <div class="rejilla" id="cats"></div>

  <div class="tit">¿Con qué?</div>
  <div class="tarjetas" id="mets"></div>

  <div class="tit">¿Cuándo y dónde?</div>
  <div class="tarjeta lista">
    <div class="fila"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>
      <div class="dias"><button id="dHoy">Hoy</button><button id="dAyer">Ayer</button><label class="otro-dia" id="dOtro"><span id="dOtroT">Otro día</span><input type="date" id="fecha" aria-label="Otra fecha"></label></div></div>
    <div class="fila"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/></svg>
      <input class="nota" id="nota" maxlength="120" placeholder="Comercio o nota (opcional)" autocomplete="off"></div>
  </div>

  <div class="pie">
    <div class="error oculto" id="error"></div>
    <button class="anotar" id="anotar" disabled><b id="aT">Escribe el valor</b><small id="aS" class="oculto"></small></button>
  </div>
</div>

<div class="capa" id="capa" role="dialog" aria-modal="true" aria-labelledby="capaT">
  <div class="capa-cab"><b id="capaT"></b><button id="capaX">Cerrar</button></div>
  <label class="buscar"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg><input id="buscar" placeholder="Buscar" autocomplete="off" enterkeyhint="search"></label>
  <div id="capaL"></div>
</div>

<div class="listo" id="listo" role="status">
  <div class="listo-in">
    <svg class="check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="25"/><path d="M15.5 27l7 7 14-15.5"/></svg>
    <div class="l-ok">Anotado</div>
    <div class="l-monto" id="lMonto"></div>
    <div class="l-det" id="lDet"></div>
    <div class="l-fecha" id="lFecha"></div>
    <div class="tarjeta"><div class="presu" id="lP" style="margin-top:0"><div class="barra"><i class="g" id="lG"></i><i class="e" id="lE"></i></div><div class="presu-t"><span id="lI"></span><b id="lD"></b></div></div></div>
    <button class="otro" id="otro">Anotar otro</button>
    <div class="salir">Para salir, toca Close arriba a la izquierda</div>
  </div>
</div>
<script>
const {d, prefs, previo, llave} = ${datos};
const $ = (id) => document.getElementById(id);
const MESES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"];
const DIAS = ["domingo","lunes","martes","miércoles","jueves","viernes","sábado"];
const ENTERAS = ["COP","CLP","ARS"];
const SIMB = { COP: "$", USD: "US$", AED: "AED", BOB: "Bs", CLP: "CLP", ARS: "ARS" };
const CHECK = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#14171c" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const REJILLA = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" aria-hidden="true"><rect x="4" y="4" width="6.5" height="6.5" rx="2"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="2"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="2"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="2"/></svg>';
const tasas = d.tasas || {};
let resumen = d.resumen || {};
const est = { moneda: (prefs && prefs.moneda && d.monedas.includes(prefs.moneda)) ? prefs.moneda : "COP", cat: null,
              met: d.metodo_ultimo || d.metodos_sugeridos[0] || "Sin especificar", fecha: d.hoy };
const usd = (x) => "US$" + Math.round(x).toLocaleString("en-US");
const cap = (s) => String(s).charAt(0).toUpperCase() + String(s).slice(1);
const sinTildes = (s) => String(s || "").normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
function el(tag, cls, txt) { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
function diaUTC(iso) { return new Date(iso + "T12:00:00Z"); }
function sumarDias(iso, n) { const t = diaUTC(iso); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); }
function fechaLarga(iso) { const t = diaUTC(iso); return DIAS[t.getUTCDay()] + " " + t.getUTCDate() + " de " + MESES[t.getUTCMonth()]; }
function fechaCorta(iso) { const t = diaUTC(iso); return cap(DIAS[t.getUTCDay()].slice(0, 3)) + " " + t.getUTCDate() + " de " + MESES[t.getUTCMonth()]; }
const AYER = sumarDias(d.hoy, -1);
function parse(t, m) {
  t = String(t || "").trim();
  if (ENTERAS.includes(m)) return Number(t.replace(/[^0-9]/g, "")) || 0;
  t = t.replace(/\\s/g, "").replace(/,/g, ".");
  const p = t.split("."); if (p.length > 2) t = p.slice(0, -1).join("") + "." + p[p.length - 1];
  const n = parseFloat(t.replace(/[^0-9.]/g, "")); return isFinite(n) ? n : 0;
}
function fmt(n, m) {
  if (!(n > 0)) return "";
  if (ENTERAS.includes(m)) return (m === "COP" ? "$" : m + " ") + Math.round(n).toLocaleString("de-DE");
  const s = n.toLocaleString("en-US", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  return ({ USD: "US$", AED: "AED ", BOB: "Bs " })[m] + s;
}

// ----- valor y moneda -----
function puntos() {   // en pesos el valor se ve con puntos de miles mientras se escribe
  if (!ENTERAS.includes(est.moneda)) return;
  const n = $("monto").value.replace(/[^0-9]/g, "");
  $("monto").value = n ? Number(n).toLocaleString("de-DE") : "";
}
function ajustarCifra() {   // el valor siempre cabe completo: la letra se achica si hace falta
  $("medida").textContent = $("monto").value || "0";
  const caja = $("valor").clientWidth - 32;
  for (const t of [58, 50, 44, 38, 33, 28, 24]) {
    $("cifra").style.fontSize = t + "px";
    $("monto").style.width = Math.ceil($("medida").getBoundingClientRect().width + 3) + "px";
    if ($("cifra").getBoundingClientRect().width <= caja) break;
  }
}
function pintarMonedas() {
  const prin = ["COP","USD","AED"].filter(m => d.monedas.includes(m));
  if (!prin.includes(est.moneda)) prin.push(est.moneda);
  const c = $("monedas"); c.innerHTML = "";
  prin.forEach(m => {
    const b = el("button", m === est.moneda ? "on" : "", m); b.setAttribute("aria-pressed", m === est.moneda);
    b.onclick = () => {
      if (est.moneda === m) return;
      const n = parse($("monto").value, est.moneda); est.moneda = m;
      $("monto").value = n > 0 ? String(ENTERAS.includes(m) ? Math.round(n) : n) : "";
      pintarMonedas(); puntos(); revisar();
    };
    c.appendChild(b);
  });
  $("simbolo").textContent = SIMB[est.moneda] || est.moneda;
  $("monto").setAttribute("inputmode", ENTERAS.includes(est.moneda) ? "numeric" : "decimal");
}

// ----- en qué -----
let cats = d.frecuentes.slice();
const repite = {}; d.categorias.forEach(c => { (repite[c.label] = repite[c.label] || new Set()).add(c.key); });
const grupoDe = (c) => (repite[c.label] && repite[c.label].size > 1 && c.grupo) ? c.grupo : "";
const catDe = (k) => d.categorias.find(x => x.key === k) || cats.find(x => x.key === k);
function elegirCat(c) {
  est.cat = c.key;
  if (!cats.slice(0, 7).some(x => x.key === c.key)) cats = [c].concat(cats.filter(x => x.key !== c.key));
  $("monto").blur(); pintarCats(); revisar();
}
function pintarCats() {
  const cont = $("cats"); cont.innerHTML = "";
  cats.slice(0, 7).forEach(c => {
    const on = est.cat === c.key, b = el("button", "cat" + (on ? " on" : ""));
    b.setAttribute("aria-pressed", on);
    b.appendChild(el("span", "emo", c.icon)); b.appendChild(el("span", "et", c.label));
    const g = grupoDe(c); if (g) b.appendChild(el("span", "gr", g));
    b.onclick = () => elegirCat(c); cont.appendChild(b);
  });
  const m = el("button", "cat mas"); m.innerHTML = REJILLA; m.appendChild(el("span", "et", "Todas")); m.onclick = abrirCats; cont.appendChild(m);
  cont.querySelectorAll(".et").forEach(e => {
    const ancho = e.parentElement.clientWidth - 8;
    const palabra = e.textContent.split(" ").reduce((x, y) => y.length > x.length ? y : x, "");
    const m = el("span", "", palabra); m.style.cssText = "position:absolute;visibility:hidden;white-space:nowrap;font-weight:650";
    e.parentElement.appendChild(m);
    for (const t of [12, 11.2, 10.5, 10]) { m.style.fontSize = t + "px"; e.style.fontSize = t + "px"; if (m.getBoundingClientRect().width <= ancho) break; }
    m.remove();
  });
}

// ----- con qué -----
let mets = d.metodos_sugeridos.slice();
const COLORES = { bdb: ["#0b3b8c","#2563d6","#fff"], bbva: ["#072146","#1464a5","#fff"], bancolombia: ["#e9b800","#ffd84a","#17191d"],
  falabella: ["#0d6a3a","#22a05a","#fff"], wio: ["#33208a","#6d48e6","#fff"], enbd: ["#0a3f6b","#1c76b8","#fff"],
  usdt: ["#13795b","#26a17b","#fff"], rappicard: ["#d93a26","#ff6a4d","#fff"], efectivo: ["#1d6b45","#2f9a62","#fff"] };
function partes(m) {
  const w = String(m).split(" ");
  if (m === "Sin especificar" || w.length === 1) return { a: "", b: m, banco: w[0].toLowerCase() };
  if (w[0] === "Transferencia") return { a: "Transferencia", b: w.slice(1).join(" "), banco: w[1].toLowerCase() };
  return { a: w[0], b: w.slice(1).join(" "), banco: w[0].toLowerCase() };
}
function pintarMets() {
  if (est.met && !mets.includes(est.met)) mets.unshift(est.met);
  const cont = $("mets"); cont.innerHTML = "";
  mets.forEach(m => {
    const p = partes(m), c = COLORES[p.banco] || ["#394150","#5d6779","#fff"], on = est.met === m;
    const b = el("button", "tj" + (on ? " on" : "")); b.setAttribute("aria-pressed", on);
    b.style.background = "linear-gradient(135deg," + c[0] + "," + c[1] + ")"; b.style.color = c[2];
    if (p.a) b.appendChild(el("span", "tj-a", p.a));
    b.appendChild(el("span", "tj-b", p.b));
    if (on) { const k = el("span", "ok"); k.innerHTML = CHECK; b.appendChild(k); }
    b.onclick = () => { est.met = m; pintarMets(); revisar(); };
    cont.appendChild(b);
  });
  const o = el("button", "tj otro"); o.appendChild(el("span", "tj-b", "Otro…")); o.onclick = abrirMets; cont.appendChild(o);
}

// ----- lista con buscador (todas las categorías, todos los métodos) -----
let capa = { items: [], sel: null, fn: null };
function abrirCapa(titulo, items, sel, fn) {
  capa = { items, sel, fn };
  $("capaT").textContent = titulo; $("buscar").value = ""; pintarCapa();
  $("monto").blur(); $("capa").classList.add("si"); $("capa").scrollTop = 0; document.body.classList.add("quieto");
}
function cerrarCapa() { $("buscar").blur(); $("capa").classList.remove("si"); document.body.classList.remove("quieto"); }
function pintarCapa() {
  const q = sinTildes($("buscar").value.trim()), cont = $("capaL"); cont.innerHTML = "";
  const vis = capa.items.filter(it => !q || sinTildes(it.texto + " " + (it.grupo || "")).includes(q));
  vis.forEach((it, i) => {
    if (!i || vis[i - 1].grupo !== it.grupo) cont.appendChild(el("div", "capa-g", it.grupo));
    const fin = i === vis.length - 1 || vis[i + 1].grupo !== it.grupo;
    const b = el("button", "capa-f" + (it.key === capa.sel ? " on" : "") + (fin ? " fin" : ""));
    if (it.emo) b.appendChild(el("span", "emo", it.emo));
    b.appendChild(el("span", "t", it.texto));
    if (it.key === capa.sel) { const k = el("span", ""); k.innerHTML = CHECK.replace('stroke="#14171c"', 'stroke="currentColor"').replace(/12/g, "18"); k.style.color = "var(--oro)"; b.appendChild(k); }
    b.onclick = () => { cerrarCapa(); capa.fn(it.key); };
    cont.appendChild(b);
  });
  if (!vis.length) cont.appendChild(el("div", "capa-vacio", "Nada con «" + $("buscar").value.trim() + "»"));
}
$("buscar").addEventListener("input", pintarCapa);
$("capaX").onclick = cerrarCapa;
function abrirCats() {
  const g = (c) => c.grupo || "Otras";
  const items = d.categorias.slice().sort((a, b) => (g(a) === "Otras") - (g(b) === "Otras") || g(a).localeCompare(g(b), "es") || a.label.localeCompare(b.label, "es"))
    .map(c => ({ key: c.key, texto: c.label, emo: c.icon, grupo: g(c) }));
  abrirCapa("Todas las categorías", items, est.cat, (k) => { const c = catDe(k); if (c) elegirCat(c); });
}
function abrirMets() {
  const suyos = d.metodos_sugeridos, orden = { "Los que más usas": 0, "Tarjetas": 1, "Transferencias": 2, "Otros": 3 };
  const items = suyos.map(m => ({ key: m, texto: m, grupo: "Los que más usas" }));
  d.metodos.filter(m => !suyos.includes(m)).forEach(m => items.push({ key: m, texto: m,
    grupo: m.startsWith("Transferencia") ? "Transferencias" : (partes(m).a ? "Tarjetas" : "Otros") }));
  items.sort((a, b) => orden[a.grupo] - orden[b.grupo]);
  abrirCapa("¿Con qué pagaste?", items, est.met, (k) => { est.met = k; pintarMets(); revisar(); });
}

// ----- cuándo -----
function pintarDias() {
  $("dHoy").className = est.fecha === d.hoy ? "on" : ""; $("dAyer").className = est.fecha === AYER ? "on" : "";
  const otro = est.fecha !== d.hoy && est.fecha !== AYER;
  $("dOtro").className = "otro-dia" + (otro ? " on" : ""); $("dOtroT").textContent = otro ? fechaCorta(est.fecha) : "Otro día";
}
$("dHoy").onclick = () => { est.fecha = d.hoy; pintarDias(); revisar(); };
$("dAyer").onclick = () => { est.fecha = AYER; pintarDias(); revisar(); };
$("fecha").max = d.hoy; $("fecha").min = sumarDias(d.hoy, -90); $("fecha").value = d.hoy;
$("fecha").addEventListener("change", () => { const v = $("fecha").value; if (v && v <= d.hoy && v >= $("fecha").min) est.fecha = v; pintarDias(); revisar(); });

// ----- presupuesto del mes: lo que queda, y lo que quedaría con este gasto -----
function pintarPresu(pre, rsm, extra, aparte) {
  const caja = $(pre + "P"), tope = Number(rsm && rsm.tope_usd), gast = Number((rsm && rsm.gastado_usd) || 0);
  if (!(tope > 0)) { caja.classList.add("oculto"); return; }
  caja.classList.remove("oculto");
  const queda = rsm.queda_usd != null ? Number(rsm.queda_usd) : tope - gast, despues = queda - extra;
  const g = Math.max(0, Math.min(gast / tope, 1)), e = Math.max(0, Math.min((gast + extra) / tope, 1) - g);
  $(pre + "G").style.width = (g * 100) + "%"; $(pre + "E").style.width = (e * 100) + "%";
  caja.classList.toggle("pasado", despues < 0);
  $(pre + "I").textContent = cap(MESES[Number(String(rsm.mes).slice(5, 7)) - 1]) + " · " + usd(gast) + " de " + usd(tope);
  $(pre + "D").textContent = aparte ? aparte
    : extra > 0 ? (despues >= 0 ? "Quedarían " + usd(despues) : "Te pasarías por " + usd(-despues))
    : (queda >= 0 ? "Quedan " + usd(queda) : "Vas " + usd(-queda) + " por encima");
}
function revisar() {
  const n = parse($("monto").value, est.moneda), c = est.cat ? catDe(est.cat) : null, listoYa = n > 0 && !!c;
  $("anotar").disabled = !listoYa;
  $("aT").textContent = !(n > 0) ? "Escribe el valor" : !c ? "Elige en qué fue" : "Anotar " + fmt(n, est.moneda);
  $("aS").textContent = listoYa ? c.icon + " " + c.label + " · " + (est.met || "Sin especificar") + (est.fecha !== d.hoy ? " · " + (est.fecha === AYER ? "ayer" : fechaCorta(est.fecha)) : "") : "";
  $("aS").classList.toggle("oculto", !listoYa);
  const mismo = est.fecha.slice(0, 7) === String(resumen.mes || ""), tasa = tasas[est.moneda];
  pintarPresu("p", resumen, mismo && n > 0 && tasa ? n / tasa : 0, mismo ? "" : "Este gasto va a " + MESES[Number(est.fecha.slice(5, 7)) - 1]);
  ajustarCifra();
}

// ----- lo que llega de Apple Pay -----
if (previo && previo.monto) {
  $("aviso").classList.remove("oculto"); $("avisoT").textContent = "Llegó de Apple Pay · revisa y toca Anotar";
  if (previo.moneda && d.monedas.includes(previo.moneda)) est.moneda = previo.moneda;
  $("monto").value = String(ENTERAS.includes(est.moneda) ? Math.round(previo.monto) : previo.monto);
  if (previo.comercio) $("nota").value = previo.comercio;
  if (previo.tarjeta) {
    // primero los métodos de quien anota, luego los que llevan su nombre, luego todos
    const t = String(previo.tarjeta).toLowerCase(), yo = String(d.persona || "").toLowerCase();
    const orden = [...new Set(d.metodos_sugeridos.concat(d.metodos.filter(x => yo && x.toLowerCase().includes(yo)), d.metodos))];
    const m = orden.find(x => x.toLowerCase().includes(t) || t.includes(x.toLowerCase().replace(/^\\S+\\s/, "")));
    if (m) est.met = m;
  }
  if (previo.categoria) { const c = d.categorias.find(x => x.key === previo.categoria); if (c) elegirCat(c); }
  else if (previo.comercio) {   // la categoría que esta persona suele usar en ese comercio
    fetch("/api/anotar/sugerir", { method: "POST", headers: { "Content-Type": "application/json", "X-Equipo": llave }, body: JSON.stringify({ comercio: String(previo.comercio) }) })
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        if (!j || !j.categoria || est.cat) return;
        const c = catDe(j.categoria); if (!c) return;
        elegirCat(c);
        $("avisoT").textContent = "Llegó de Apple Pay · " + (j.veces > 1 ? "aquí sueles anotar " : "la última vez aquí fue ") + c.icon + " " + c.label;
      }).catch(() => {});
  }
}

// ----- arranque -----
$("avatar").textContent = String(d.persona || "?").charAt(0).toUpperCase();
$("quien").textContent = d.persona + " · " + fechaLarga(d.hoy) + " · " + d.hora;
pintarMonedas(); puntos(); pintarCats(); pintarMets(); pintarDias(); revisar();
$("monto").addEventListener("input", () => { puntos(); revisar(); });
window.addEventListener("resize", ajustarCifra);
setTimeout(() => { if (!previo || !previo.monto) $("monto").focus(); }, 300);

// ----- anotar -----
let enviando = false;
$("anotar").onclick = () => {
  if (enviando || $("anotar").disabled) return;
  enviando = true; $("anotar").disabled = true; $("aT").textContent = "Anotando…"; $("error").classList.add("oculto");
  const c = catDe(est.cat);
  const datos = { monto: parse($("monto").value, est.moneda), moneda: est.moneda, categoria: est.cat,
                  metodo_pago: est.met || "Sin especificar", nota: $("nota").value.trim(), fecha: est.fecha };
  fetch("/api/anotar/gasto", { method: "POST", headers: { "Content-Type": "application/json", "X-Equipo": llave }, body: JSON.stringify(datos) })
    .then(async (r) => {
      let j = {}; try { j = await r.json(); } catch (e) { j = {}; }
      if (r.ok && j.ok) { listo(j, datos, c); location.href = "anotar-hecho://" + encodeURIComponent(JSON.stringify({ moneda: datos.moneda })); return; }
      fallo(r.status === 401 ? "Este teléfono ya no está activo: pide un código nuevo en Gastos del mes → Ajustes → Teléfonos y Macs." :
        (j.detail ? String(j.detail) : "No se pudo anotar. Revisa el internet y vuelve a intentar."));
    })
    .catch(() => fallo("No se pudo anotar. Revisa el internet y vuelve a intentar."));
};
function listo(r, datos, c) {
  $("lMonto").textContent = fmt(datos.monto, datos.moneda);
  $("lDet").textContent = (c ? c.icon + " " + c.label : (r.icon || "") + " " + (r.categoria_label || "")) + " · " + datos.metodo_pago;
  $("lFecha").textContent = (datos.fecha === d.hoy ? "Hoy" : cap(fechaLarga(datos.fecha))) + " · a nombre de " + d.persona;
  if (r.resumen) { pintarPresu("l", r.resumen, 0, ""); if (r.resumen.mes === resumen.mes) resumen = r.resumen; }
  else $("lP").classList.add("oculto");
  $("monto").blur(); $("nota").blur();
  $("listo").classList.remove("si"); void $("listo").offsetWidth; $("listo").classList.add("si");
}
function fallo(msg) { enviando = false; revisar(); $("error").textContent = msg; $("error").classList.remove("oculto"); }
$("otro").onclick = () => {
  $("listo").classList.remove("si"); enviando = false;
  est.cat = null; est.fecha = d.hoy; $("monto").value = ""; $("nota").value = ""; $("aviso").classList.add("oculto");
  pintarCats(); pintarDias(); revisar(); window.scrollTo(0, 0);
  setTimeout(() => $("monto").focus(), 150);
};
</script></body></html>`;
}

// ---------- llave del teléfono ----------
async function emparejar() {
  const a = new Alert();
  a.title = "Activar Anotar gasto";
  a.message = "Este código sale de GASTOS DEL MES (no de Patrimonio): Ajustes → Teléfonos y Macs → «Código para un iPhone». Vale 10 minutos y una sola vez.";
  a.addTextField("ABCD-EFGH");
  a.addAction("Activar");
  a.addCancelAction("Cancelar");
  if ((await a.present()) === -1) return false;
  const r = new Request(BASE + "/api/equipos/emparejar");
  r.method = "POST";
  r.headers = { "Content-Type": "application/json" };
  r.body = JSON.stringify({ codigo: a.textFieldValue(0), nombre: Device.name() || "iPhone" });
  let j = {};
  try { j = await r.loadJSON(); } catch (e) { j = {}; }
  if (r.response && r.response.statusCode === 200 && typeof j.llave === "string" && j.llave.startsWith("pg_")) {
    Keychain.set(CLAVE, j.llave);
    return true;
  }
  await avisar("No se pudo activar", (j.detail || "Revisa el código y vuelve a intentar.") + "\n\nOjo: Anotar gasto usa el código de Gastos del mes → Ajustes → Teléfonos y Macs, no el de Patrimonio.");
  return false;
}

async function avisar(titulo, texto) {
  const n = new Alert();
  n.title = titulo;
  n.message = texto;
  n.addAction("OK");
  await n.present();
}

async function api(ruta, cuerpo) {
  const r = new Request(BASE + ruta);
  r.headers = { "X-Equipo": Keychain.get(CLAVE), "Content-Type": "application/json" };
  r.timeoutInterval = 20;
  if (cuerpo) { r.method = "POST"; r.body = JSON.stringify(cuerpo); }
  let j = null;
  try { j = await r.loadJSON(); } catch (e) { j = null; }
  return { code: r.response ? r.response.statusCode : 0, j };
}

// ---------- lo que viene de un atajo (Apple Pay) ----------
// El importe de Wallet llega como texto con el formato del iPhone: "$58.000", "COP 58.000,00", "AED 49.00", "US$12.50".
function leerImporte(v) {
  if (typeof v === "number") return { monto: v, moneda: null };
  let t = String(v || "");
  const moneda = /AED|د\.إ/i.test(t) ? "AED" : /US\$|USD/i.test(t) ? "USD" : /COP|\$/.test(t) ? "COP" : null;
  t = t.replace(/[^0-9.,]/g, "");
  const ip = t.lastIndexOf("."), ic = t.lastIndexOf(",");
  let n;
  if (ip >= 0 && ic >= 0) {                       // los dos: el último es el decimal
    const dec = ip > ic ? "." : ",", mil = dec === "." ? "," : ".";
    n = Number(t.split(mil).join("").replace(dec, "."));
  } else if (ip >= 0 || ic >= 0) {                // uno solo: con 3 cifras después (o varios) es de miles
    const partes = t.split(ip >= 0 ? "." : ",");
    n = (partes.length > 2 || partes[partes.length - 1].length === 3) ? Number(partes.join("")) : Number(partes.join("."));
  } else n = Number(t);
  return { monto: isFinite(n) ? n : 0, moneda };
}

function entrada() {
  let p = typeof args !== "undefined" ? args.shortcutParameter : null;
  if (typeof p === "string") { try { p = JSON.parse(p); } catch (e) { p = null; } }
  if (!p || typeof p !== "object") return null;
  const imp = leerImporte(p.monto ?? p.amount);
  const moneda = String(p.moneda || imp.moneda || "").toUpperCase() || null;
  return imp.monto > 0 ? { monto: imp.monto, moneda, comercio: p.comercio || p.merchant || "", tarjeta: p.tarjeta || p.card || "", categoria: p.categoria || "" } : null;
}

// ---------- correr ----------
async function main() {
  const fm = FileManager.local();
  const PREF = fm.joinPath(fm.documentsDirectory(), "anotar-gasto.json");   // solo la última moneda, sin secretos
  let prefs = {};
  try { if (fm.fileExists(PREF)) prefs = JSON.parse(fm.readString(PREF)); } catch (e) { prefs = {}; }

  if (!Keychain.contains(CLAVE) && !(await emparejar())) return;
  let { code, j: datos } = await api("/api/anotar/datos");
  if (code === 401) {
    Keychain.remove(CLAVE);
    await avisar("Este teléfono ya no está activo", "Se quitó en Ajustes → Teléfonos y Macs. Pide un código nuevo para activarlo otra vez.");
    if (!(await emparejar())) return;
    ({ code, j: datos } = await api("/api/anotar/datos"));
  }
  if (code !== 200 || !datos) {
    await avisar("Sin conexión", "No pude hablar con P&G GG. Revisa el internet y vuelve a intentar.");
    return;
  }

  const wv = new WebView();
  // cuando la hoja anota bien, avisa con un enlace propio: se guarda la última moneda y no se navega a ningún lado
  wv.shouldAllowRequest = (req) => {
    const u = (req && req.url) || "";
    if (u.startsWith("anotar-hecho://")) {
      try { fm.writeString(PREF, JSON.stringify({ moneda: JSON.parse(decodeURIComponent(u.slice(15))).moneda })); } catch (e) { /* no importa */ }
      return false;
    }
    return true;
  };
  // la hoja se carga "como si fuera" P&G GG, así puede anotar directo con la llave de este teléfono
  await wv.loadHTML(hoja(datos, prefs, entrada(), Keychain.get(CLAVE)), BASE);
  await wv.present(false);
}

if (typeof Script !== "undefined") {
  await main();
  Script.complete();
}
