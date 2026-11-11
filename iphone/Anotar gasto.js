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
function hoja(d, prefs, previo, llave, guardado) {
  const datos = JSON.stringify({ d, prefs, previo, llave, guardado: !!guardado }).replace(/</g, "\\u003c");
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<style>
:root{--tope:62px;--fondo:#f5f6f8;--hoja:#ffffff;--txt:#111318;--apag:#64748b;--borde:#e5e8ec;--suave:#f2f4f7;--gris:#e3e6eb;--grist:#9aa3b2;--oro:#eda100;--orot:#8a5f00;--orosuave:#fdf3dc;--alerta:#c63a2d;--sombra:0 1px 2px rgba(16,24,40,.06)}
@media (prefers-color-scheme: dark){:root{--fondo:#0b0d11;--hoja:#13161c;--txt:#f1f3f5;--apag:#94a3b8;--borde:#1f232b;--suave:#1a1e25;--gris:#1f232b;--grist:#5b6575;--orot:#f2b730;--orosuave:rgba(237,161,0,.13);--alerta:#f07a6a;--sombra:0 0 0 1px #1f232b}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{touch-action:pan-y;overflow-x:hidden}
html,body{margin:0;background:var(--fondo);color:var(--txt);font-family:Inter,-apple-system,system-ui,sans-serif;font-size:15px;font-feature-settings:"tnum" 1,"cv11" 1;-webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%}
body{overflow:hidden;width:100%;height:100%;position:fixed;inset:0}
.marco{position:fixed;top:var(--tope);left:0;right:0;bottom:0;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior:none}
button{font:inherit;color:inherit;cursor:pointer;transition:transform .12s ease,background-color .15s ease,border-color .15s ease,color .15s ease}
button:active{transform:scale(.97)}
.oculto{display:none!important}
.mascara{position:fixed;top:0;left:0;right:0;height:var(--tope);background:var(--fondo);z-index:15}
.hoja{max-width:520px;margin:0 auto;padding:0 18px}
.marca{display:flex;align-items:center;gap:9px}
.marca svg{flex-shrink:0}
.marca-t{flex:1;min-width:0;line-height:1.2}
.marca-t b{display:block;font-size:16px;font-weight:600;letter-spacing:-.01em}
.marca-t>span{display:block;font-size:11.5px;color:var(--apag);margin-top:1px}
.origen{color:var(--orot);font-weight:500}
.avatar{width:30px;height:30px;border-radius:15px;flex-shrink:0;background:var(--hoja);display:flex;align-items:center;justify-content:center;font-weight:600;font-size:12.5px;box-shadow:var(--sombra),inset 0 0 0 1px var(--borde)}
.tarjeta{background:var(--hoja);border:1px solid var(--borde);border-radius:16px;box-shadow:var(--sombra)}
.valor{margin-top:10px;padding:10px 12px 11px}
.arriba{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:6px}
.seg{display:flex;gap:2px;padding:2px;border-radius:9px;background:var(--suave);width:max-content}
.seg button,.seg .otro-dia{border:0;background:transparent;color:var(--apag);font-size:12px;padding:5px 9px;border-radius:7px;font-weight:500;position:relative;display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.seg .on{background:var(--hoja);color:var(--txt);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.1)}
.otro-dia input{position:absolute;inset:0;opacity:0;width:100%;height:100%;font-size:16px;border:0;padding:0}
.centro{display:flex;justify-content:center;margin-top:8px}
.cifra{position:relative;display:inline-flex;align-items:baseline;gap:.14em;font-weight:600;font-size:40px;line-height:1.15;letter-spacing:-.03em;font-feature-settings:"tnum" 1,"lnum" 1;max-width:100%}
.simbolo{font-size:.5em;color:var(--apag);font-weight:500;letter-spacing:0}
.monto{border:0;background:transparent;color:var(--txt);font:inherit;letter-spacing:inherit;padding:0;outline:none;min-width:.6em;text-align:left;caret-color:var(--oro)}
.monto::placeholder{color:var(--apag);opacity:.45}
.medida{position:absolute;visibility:hidden;white-space:pre;left:0;top:0}
.presu{margin-top:9px}
.barra{height:6px;border-radius:3px;background:var(--suave);overflow:hidden;display:flex}
.barra i{display:block;height:100%;transition:width .25s ease}
.barra .g{background:var(--oro)}
.barra .e{background:repeating-linear-gradient(-45deg,var(--oro) 0 3px,transparent 3px 6px);opacity:.75}
.presu.pasado .barra .g{background:var(--alerta)}
.presu.pasado .barra .e{background:repeating-linear-gradient(-45deg,var(--alerta) 0 3px,transparent 3px 6px)}
.presu-t{display:flex;flex-wrap:wrap;justify-content:space-between;gap:2px 10px;margin-top:6px;font-size:11.5px;color:var(--apag)}
.presu-t b{font-weight:600;color:var(--txt)}
.presu.pasado .presu-t b{color:var(--alerta)}
.tit{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:13px;font-weight:600;margin:13px 0 6px}
.ver{border:0;background:transparent;color:var(--orot);font-size:12.5px;font-weight:500;display:inline-flex;align-items:center;gap:2px;padding:3px 0}
.todas .buscar{margin:0 0 8px}
.todas{padding-bottom:4px}
.rejilla{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px}
.cat{border:1px solid var(--borde);background:var(--hoja);border-radius:13px;padding:8px 2px 7px;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;gap:4px;min-height:58px;box-shadow:var(--sombra)}
.cat .emo{font-size:20px;line-height:1}
.cat .et{font-size:var(--et,11.5px);line-height:1.2;text-align:center;font-weight:500;max-width:100%}
.cat .gr{font-size:10px;line-height:1.1;color:var(--apag);text-align:center}
.cat.on{background:var(--orosuave);border-color:var(--oro);box-shadow:0 0 0 1px var(--oro)}
.cat.on .et{color:var(--orot);font-weight:600}
.cat.mas{background:transparent;border-style:dashed;box-shadow:none;color:var(--apag)}
.campo{display:flex;align-items:center;gap:9px;padding:0 12px;min-height:42px;border-radius:13px}
.campo>svg{flex-shrink:0;color:var(--apag)}
.campo:focus-within{border-color:var(--oro);box-shadow:0 0 0 1px var(--oro)}
.campo.ok>svg{color:var(--oro)}
.nota{flex:1;min-width:0;border:0;background:transparent;color:var(--txt);font:inherit;font-size:16px;outline:none;padding:9px 0}
.nota::placeholder{color:var(--apag)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.metodos{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.metodos .chip{white-space:normal;text-align:left;line-height:1.25;min-height:36px;font-size:12.5px}
.chip{border:1px solid var(--borde);background:var(--hoja);border-radius:10px;padding:7px 10px;font-size:13px;font-weight:500;display:inline-flex;align-items:center;gap:7px;box-shadow:var(--sombra);white-space:nowrap}
.chip.on{background:var(--orosuave);border-color:var(--oro);color:var(--orot);font-weight:600;box-shadow:0 0 0 1px var(--oro)}
.chip.otro{background:transparent;border-style:dashed;color:var(--apag);box-shadow:none}
.punto{width:7px;height:7px;border-radius:4px;flex-shrink:0;display:inline-block}
.pie{position:sticky;bottom:0;margin:6px -18px 0;padding:10px 18px calc(10px + env(safe-area-inset-bottom));background:linear-gradient(to bottom,transparent,var(--fondo) 30%)}
.error{color:var(--alerta);font-size:12.5px;line-height:1.35;text-align:center;margin-bottom:8px;font-weight:500}
.anotar{width:100%;min-height:48px;border:0;border-radius:14px;background:var(--oro);color:#14171c;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;padding:7px 14px}
.anotar b{font-size:15px;font-weight:600}
.anotar small{font-size:11.5px;font-weight:500;opacity:.78;text-align:center;line-height:1.25}
.anotar:disabled{background:var(--gris);color:var(--grist);transform:none}
.capa{position:fixed;top:var(--tope);left:0;right:0;bottom:0;z-index:20;background:var(--fondo);display:none;flex-direction:column}
.buscar{display:flex;align-items:center;gap:7px;margin-top:8px;background:var(--hoja);border:1px solid var(--borde);border-radius:11px;padding:0 11px;color:var(--apag)}
.buscar input{flex:1;min-width:0;border:0;background:transparent;color:var(--txt);font:inherit;font-size:16px;padding:8px 0;outline:none}
.lista-g{background:var(--hoja);border:1px solid var(--borde);border-radius:14px;overflow:hidden;box-shadow:var(--sombra)}
.fila{width:100%;border:0;background:transparent;display:flex;align-items:center;gap:10px;padding:10px 12px;font-size:14.5px;text-align:left}
.fila+.fila,.subs+.fila{border-top:1px solid var(--borde)}
.fila .emo{font-size:18px;width:22px;text-align:center;flex-shrink:0}
.fila .t{flex:1;min-width:0}
.fila .cuenta{font-size:12px;color:var(--apag)}
.fila .flecha{display:inline-flex;color:var(--apag);transition:transform .15s ease}
.fila.abierto .flecha{transform:rotate(90deg)}
.fila.abierto{background:var(--suave)}
.fila.on .t{color:var(--orot);font-weight:600}
.fila:active{transform:none;background:var(--suave)}
.subs{display:flex;flex-wrap:wrap;gap:6px;padding:4px 12px 12px 44px;background:var(--suave)}
.sub{border:1px solid var(--borde);background:var(--hoja);border-radius:9px;padding:6px 9px;font-size:13px;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.sub.on{background:var(--orosuave);border-color:var(--oro);color:var(--orot);font-weight:600}
.capa-g{font-size:11px;font-weight:600;color:var(--apag);letter-spacing:.05em;text-transform:uppercase;margin:14px 2px 6px}
.capa-g:first-child{margin-top:4px}
.pares{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.pares .sub{justify-content:flex-start;white-space:normal;text-align:left;padding:8px 10px}
.capa-vacio{color:var(--apag);text-align:center;padding:28px 10px;font-size:13.5px}
.listo{position:fixed;top:var(--tope);left:0;right:0;bottom:0;z-index:30;background:var(--fondo);display:none;padding:12px 22px 22px;overflow-y:auto}
.listo.si{display:flex;align-items:center;justify-content:center}
.listo-in{width:100%;max-width:360px;display:flex;flex-direction:column;align-items:center;text-align:center}
.listo.si .listo-in{animation:sube .35s ease-out}
.check{width:58px;height:58px}
.check circle{fill:var(--oro);transform-box:fill-box;transform-origin:center}
.check path{fill:none;stroke:#14171c;stroke-width:4.5;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:36}
.listo.si .check circle{animation:pop .4s cubic-bezier(.2,.9,.3,1.3) backwards}
.listo.si .check path{animation:raya .35s .28s ease-out backwards}
.l-ok{margin-top:12px;font-size:13px;color:var(--apag);font-weight:500}
.l-monto{font-weight:600;font-size:32px;line-height:1.15;letter-spacing:-.03em;margin-top:2px;overflow-wrap:anywhere}
.l-det{margin-top:6px;font-size:14px;line-height:1.35}
.l-fecha{margin-top:3px;font-size:12.5px;color:var(--apag);line-height:1.35}
.listo .tarjeta{width:100%;margin-top:16px;padding:12px 14px;text-align:left}
.denuevo{margin-top:14px;width:100%;min-height:46px;border-radius:14px;border:1px solid var(--borde);background:var(--hoja);font-size:15px;font-weight:600;box-shadow:var(--sombra)}
.salir{margin-top:10px;font-size:12px;color:var(--apag)}
@keyframes pop{from{transform:scale(0)}}
@keyframes raya{from{stroke-dashoffset:36}}
@keyframes sube{from{opacity:0;transform:translateY(8px)}}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<div class="mascara" aria-hidden="true"></div>
<div class="marco" id="marco"><div class="hoja">
  <div class="marca">
    <svg width="22" height="22" viewBox="0 0 64 64" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="4.6"><path d="M45.38 19.96 A18 18 0 1 0 50 32 L44.24 32"/><path d="M39.06 25.64 A9.5 9.5 0 1 0 41.5 32 L36.27 32"/></g></svg>
    <div class="marca-t"><b>Anotar gasto</b><span><span id="quien"></span><span class="origen oculto" id="origen"> · desde Apple Pay</span></span></div>
    <div class="avatar" id="avatar" aria-hidden="true"></div>
  </div>

  <section class="tarjeta valor" id="valor">
    <div class="arriba">
      <div class="seg" id="monedas" role="group" aria-label="Moneda"></div>
      <div class="seg" role="group" aria-label="Día"><button id="dHoy">Hoy</button><button id="dAyer">Ayer</button><label class="otro-dia" id="dOtro"><svg id="dOtroI" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg><span id="dOtroT"></span><input type="date" id="fecha" aria-label="Otro día"></label></div>
    </div>
    <div class="centro"><label class="cifra" id="cifra"><span class="simbolo" id="simbolo"></span><input class="monto" id="monto" inputmode="numeric" autocomplete="off" placeholder="0" aria-label="Valor"><span class="medida" id="medida" aria-hidden="true"></span></label></div>
    <div class="presu" id="pP"><div class="barra"><i class="g" id="pG"></i><i class="e" id="pE"></i></div><div class="presu-t"><span id="pI"></span><b id="pD"></b></div></div>
  </section>

  <div class="tit"><span>¿En qué?</span><button class="ver" id="verCats" aria-expanded="false"></button></div>
  <div class="rejilla" id="cats"></div>
  <div class="todas oculto" id="todasCats"><label class="buscar"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg><input id="buscaCats" placeholder="Buscar categoría" autocomplete="off" enterkeyhint="search"></label><div id="listaCats"></div></div>

  <div class="tit" id="titNota"><span>¿De qué fue?</span></div>
  <label class="tarjeta campo" id="campoNota"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/></svg><input class="nota" id="nota" maxlength="120" placeholder="Comercio o detalle" autocomplete="off" enterkeyhint="done"></label>

  <div class="tit"><span>¿Con qué?</span><button class="ver" id="verMets" aria-expanded="false"></button></div>
  <div class="metodos" id="mets"></div>
  <div class="todas oculto" id="todosMets"><label class="buscar"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg><input id="buscaMets" placeholder="Buscar método" autocomplete="off" enterkeyhint="search"></label><div id="listaMets"></div></div>

  <div class="pie" id="pie">
    <div class="error oculto" id="error"></div>
    <button class="anotar" id="anotar" disabled><b id="aT">Confirmar</b><small id="aS" class="oculto"></small></button>
  </div>
</div></div>

<div class="listo" id="listo" role="status">
  <div class="listo-in">
    <svg class="check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="25"/><path d="M15.5 27l7 7 14-15.5"/></svg>
    <div class="l-ok">Anotado</div>
    <div class="l-monto" id="lMonto"></div>
    <div class="l-det" id="lDet"></div>
    <div class="l-fecha" id="lFecha"></div>
    <div class="tarjeta"><div class="presu" id="lP" style="margin-top:0"><div class="barra"><i class="g" id="lG"></i><i class="e" id="lE"></i></div><div class="presu-t"><span id="lI"></span><b id="lD"></b></div></div></div>
    <button class="denuevo" id="otro">Anotar otro</button>
    <div class="salir">Para salir, toca Close arriba a la izquierda</div>
  </div>
</div>
<script>
const {d, prefs, previo, llave, guardado} = ${datos};
// hoy siempre sale del reloj del teléfono (hora Colombia): lo guardado puede ser de otro día
try { d.hoy = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Bogota" }).format(new Date()) || d.hoy; } catch (e) { /* queda el del servidor */ }
const $ = (id) => document.getElementById(id);
const MESES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"];
const DIAS = ["domingo","lunes","martes","miércoles","jueves","viernes","sábado"];
const ENTERAS = ["COP","CLP","ARS"];
const SIMB = { COP: "$", USD: "US$", AED: "AED", BOB: "Bs", CLP: "CLP", ARS: "ARS" };
const CHECK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const FLECHA = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';
const ABAJO = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
const COLOR = { bdb: "#2563d6", bbva: "#1464a5", bancolombia: "#e0b000", falabella: "#22a05a", wio: "#6d48e6", enbd: "#1c76b8",
  usdt: "#26a17b", rappicard: "#ff6a4d", efectivo: "#2f9a62", chase: "#117aca" };
let tasas = d.tasas || {};
let resumen = d.resumen || {};
let mets = d.metodos_sugeridos.filter(m => m !== "Sin especificar");
const est = { moneda: (prefs && prefs.moneda && d.monedas.includes(prefs.moneda)) ? prefs.moneda : "COP", cat: null,
              met: (d.metodo_ultimo && d.metodo_ultimo !== "Sin especificar") ? d.metodo_ultimo : (mets[0] || ""), fecha: d.hoy };
const usd = (x) => "US$" + Math.round(x).toLocaleString("en-US");
const cap = (s) => String(s).charAt(0).toUpperCase() + String(s).slice(1);
const sinTildes = (s) => String(s || "").normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
function el(tag, cls, txt) { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
function diaUTC(iso) { return new Date(iso + "T12:00:00Z"); }
function sumarDias(iso, n) { const t = diaUTC(iso); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); }
function fechaLarga(iso) { const t = diaUTC(iso); return DIAS[t.getUTCDay()] + " " + t.getUTCDate() + " de " + MESES[t.getUTCMonth()]; }
function fechaCorta(iso) { const t = diaUTC(iso); return cap(DIAS[t.getUTCDay()].slice(0, 3)) + " " + t.getUTCDate() + " de " + MESES[t.getUTCMonth()]; }
const AYER = sumarDias(d.hoy, -1);
function bancoDe(m) { const w = String(m).split(" "); return ((w[0] === "Transferencia" && w[1]) ? w[1] : w[0]).toLowerCase(); }
const colorDe = (m) => COLOR[bancoDe(m)] || "#94a3b8";
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
function ajustarCifra() {   // el valor siempre cabe completo (más de 10 millones también): la letra se achica si hace falta
  $("medida").textContent = $("monto").value || "0";
  const caja = $("valor").clientWidth - 30;
  for (const t of [40, 36, 32, 28, 25, 22, 19]) {
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

// ----- en qué: las más usadas de esta persona -----
let cats = d.frecuentes.slice();
const repite = {}; d.categorias.forEach(c => { (repite[c.label] = repite[c.label] || new Set()).add(c.key); });
const grupoDe = (c) => (repite[c.label] && repite[c.label].size > 1 && c.grupo) ? c.grupo : "";
const catDe = (k) => d.categorias.find(x => x.key === k) || cats.find(x => x.key === k);
function elegirCat(c) {
  est.cat = c.key;
  if (!cats.slice(0, 8).some(x => x.key === c.key)) cats = [c].concat(cats.filter(x => x.key !== c.key));
  $("monto").blur(); pintarCats(); revisar();
}
function pintarCats() {
  const cont = $("cats"); cont.innerHTML = "";
  cats.slice(0, 8).forEach(c => {
    const on = est.cat === c.key, b = el("button", "cat" + (on ? " on" : ""));
    b.setAttribute("aria-pressed", on);
    b.appendChild(el("span", "emo", c.icon)); b.appendChild(el("span", "et", c.label));
    const g = grupoDe(c); if (g) b.appendChild(el("span", "gr", g));
    b.onclick = () => elegirCat(c); cont.appendChild(b);
  });
  // una sola letra para todas las casillas: la que deja caber completa la palabra más larga
  const ancho = (cont.firstChild ? cont.firstChild.clientWidth : 78) - 6;
  const palabras = [...cont.querySelectorAll(".et")].flatMap(e => e.textContent.split(" "));
  const med = el("span"); med.style.cssText = "position:absolute;visibility:hidden;white-space:nowrap;font-weight:600"; document.body.appendChild(med);
  let t = 11.5;
  for (const tam of [11.5, 11, 10.5, 10]) {
    t = tam; med.style.fontSize = tam + "px";
    if (palabras.every(w => { med.textContent = w; return med.getBoundingClientRect().width <= ancho; })) break;
  }
  med.remove(); cont.style.setProperty("--et", t + "px");
}

// ----- con qué: los 4 que más usa esta persona, sin deslizar -----
function pintarMets() {
  let vis = mets.slice(0, 4);
  if (est.met && !vis.includes(est.met)) vis = [est.met].concat(vis.slice(0, 3));
  const cont = $("mets"); cont.innerHTML = "";
  vis.forEach(m => {
    const on = est.met === m, b = el("button", "chip" + (on ? " on" : "")); b.setAttribute("aria-pressed", on);
    const p = el("i", "punto"); p.style.background = colorDe(m); b.appendChild(p); b.appendChild(el("span", "", m));
    b.onclick = () => { est.met = m; pintarMets(); revisar(); };
    cont.appendChild(b);
  });
}

// ----- ver todas / ver todos: se despliega aquí mismo, como el menú de Telegram -----
const GRUPOS = d.grupos || [];
const hijos = {}; d.categorias.forEach(c => { if (c.padre && GRUPOS.some(g => g.key === c.padre)) (hijos[c.padre] = hijos[c.padre] || []).push(c); });
Object.values(hijos).forEach(l => l.sort((a, b) => a.label.localeCompare(b.label, "es")));
const ARRIBA = GRUPOS.filter(g => hijos[g.key]).map(g => ({ grupo: g }))
  .concat(d.categorias.filter(c => !(c.padre && hijos[c.padre])).map(c => ({ hoja: c })))
  .sort((a, b) => (a.grupo || a.hoja).label.localeCompare((b.grupo || b.hoja).label, "es", { sensitivity: "base" }));
let desplegado = null, abierto = null;   // "cats" | "mets" | null; el grupo de categorías abierto
const VER = { cats: "Ver todas", mets: "Ver todos" };
function pintarVer() {
  [["cats", "verCats"], ["mets", "verMets"]].forEach(([k, id]) => {
    const ab = desplegado === k;
    $(id).innerHTML = ""; $(id).appendChild(document.createTextNode(ab ? "Ocultar" : VER[k]));
    const ic = el("span"); ic.style.display = "inline-flex"; ic.innerHTML = ab ? ABAJO : FLECHA; $(id).appendChild(ic);
    $(id).setAttribute("aria-expanded", ab);
  });
  $("cats").classList.toggle("oculto", desplegado === "cats"); $("todasCats").classList.toggle("oculto", desplegado !== "cats");
  $("mets").classList.toggle("oculto", desplegado === "mets"); $("todosMets").classList.toggle("oculto", desplegado !== "mets");
}
function desplegar(k) {
  desplegado = desplegado === k ? null : k;
  $("monto").blur(); $("nota").blur(); $("buscaCats").blur(); $("buscaMets").blur();
  if (desplegado === "cats") { $("buscaCats").value = ""; abierto = (est.cat && catDe(est.cat)) ? (catDe(est.cat).padre || null) : null; pintarTodasCats(); }
  if (desplegado === "mets") { $("buscaMets").value = ""; pintarTodosMets(); }
  pintarVer();
  if (desplegado === "cats" && abierto) mostrarAbierto();                         // el grupo de la categoría marcada queda a la vista
  if (desplegado === "mets") $("marco").scrollTop = $("titNota").offsetTop - 4;   // la lista de métodos queda a la vista
  else if (!desplegado) $("marco").scrollTop = 0;
}
function plegar() { desplegado = null; abierto = null; pintarVer(); $("marco").scrollTop = 0; }
$("verCats").onclick = () => desplegar("cats");
$("verMets").onclick = () => desplegar("mets");
function vacio(cont, q) { cont.appendChild(el("div", "capa-vacio", "Nada con «" + q + "»")); }
function subCat(c) {
  const b = el("button", "sub" + (est.cat === c.key ? " on" : ""));
  b.appendChild(el("span", "", c.icon)); b.appendChild(el("span", "", c.label));
  b.onclick = () => { elegirCat(c); plegar(); };
  return b;
}
function pintarTodasCats() {
  const crudo = $("buscaCats").value.trim(), q = sinTildes(crudo), cont = $("listaCats"); cont.innerHTML = "";
  if (q) {   // al buscar: lo que coincide, agrupado por su categoría
    const hits = d.categorias.filter(c => sinTildes(c.label + " " + c.grupo).includes(q));
    if (!hits.length) return vacio(cont, crudo);
    const por = {}; hits.forEach(c => { const g = hijos[c.padre] ? c.grupo : ""; (por[g] = por[g] || []).push(c); });
    Object.keys(por).sort((a, b) => a.localeCompare(b, "es")).forEach(g => {
      const gi = GRUPOS.find(x => x.label === g);
      cont.appendChild(el("div", "capa-g", g ? (gi ? gi.icon + " " : "") + g : "Sin grupo"));
      const w = el("div", "chips"); por[g].forEach(c => w.appendChild(subCat(c))); cont.appendChild(w);
    });
    return;
  }
  const caja = el("div", "lista-g");
  ARRIBA.forEach(it => {
    if (it.hoja) {
      const c = it.hoja, on = est.cat === c.key, f = el("button", "fila" + (on ? " on" : ""));
      f.appendChild(el("span", "emo", c.icon)); f.appendChild(el("span", "t", c.label));
      if (on) { const k = el("span", "flecha"); k.innerHTML = CHECK; k.style.color = "var(--oro)"; f.appendChild(k); }
      f.onclick = () => { elegirCat(c); plegar(); };
      caja.appendChild(f); return;
    }
    const g = it.grupo, ab = abierto === g.key, tiene = hijos[g.key].some(c => c.key === est.cat);
    const f = el("button", "fila" + (ab ? " abierto" : "") + (tiene ? " on" : "")); f.setAttribute("aria-expanded", ab);
    f.appendChild(el("span", "emo", g.icon)); f.appendChild(el("span", "t", g.label));
    f.appendChild(el("span", "cuenta", String(hijos[g.key].length)));
    const fl = el("span", "flecha"); fl.innerHTML = ab ? ABAJO : FLECHA; f.appendChild(fl);
    f.onclick = () => { abierto = ab ? null : g.key; pintarTodasCats(); mostrarAbierto(); };
    caja.appendChild(f);
    if (ab) { const w = el("div", "subs"); hijos[g.key].forEach(c => w.appendChild(subCat(c))); caja.appendChild(w); }
  });
  cont.appendChild(caja);
}
function mostrarAbierto() {   // el grupo que se abre queda completo a la vista, encima del botón
  const M = $("marco"), f = $("listaCats").querySelector(".fila.abierto"); if (!f) return;
  const mr = M.getBoundingClientRect(), tope = mr.top + 4, piso = mr.bottom - $("pie").offsetHeight - 6;
  const fin = (f.nextElementSibling || f).getBoundingClientRect().bottom, ini = f.getBoundingClientRect().top;
  if (fin > piso) M.scrollTop += Math.min(fin - piso, ini - tope);
  else if (ini < tope) M.scrollTop -= tope - ini;
}
function pintarTodosMets() {
  const crudo = $("buscaMets").value.trim(), q = sinTildes(crudo), cont = $("listaMets"); cont.innerHTML = "";
  const lista = d.metodos.filter(m => m !== "Sin especificar" && (!q || sinTildes(m).includes(q)));
  if (!lista.length) return vacio(cont, crudo);
  const DUENO = { dani: "Daniel", mado: "Mado" };
  const duenoDe = (m) => { const w = m.split(" "); return w.length > 1 && w[0] !== "Transferencia" ? DUENO[w[w.length - 1].toLowerCase()] || null : null; };
  const grupoMet = (m) => {
    const w = m.split(" "), du = duenoDe(m);
    if (du) return du === d.persona ? "Tus tarjetas" : "Tarjetas de " + du;
    if (w[0] === "Transferencia") return "Transferencias";
    return w.length === 1 ? "Efectivo y otros" : "Otras tarjetas";
  };
  const ORDEN = (g) => g === "Tus tarjetas" ? 0 : g.startsWith("Tarjetas de") ? 1 : g === "Transferencias" ? 2 : g === "Otras tarjetas" ? 3 : 4;
  const por = {}; lista.forEach(m => { const g = grupoMet(m); (por[g] = por[g] || []).push(m); });
  Object.keys(por).sort((a, b) => ORDEN(a) - ORDEN(b) || a.localeCompare(b, "es")).forEach(g => {
    cont.appendChild(el("div", "capa-g", g));
    const w = el("div", "pares");
    por[g].forEach(m => {
      const b = el("button", "sub" + (est.met === m ? " on" : "")), pt = el("i", "punto"); pt.style.background = colorDe(m);
      b.appendChild(pt); b.appendChild(el("span", "", m));
      b.onclick = () => { est.met = m; if (!mets.includes(m)) mets.unshift(m); pintarMets(); revisar(); plegar(); };
      w.appendChild(b);
    });
    cont.appendChild(w);
  });
}
$("buscaCats").addEventListener("input", pintarTodasCats);
$("buscaMets").addEventListener("input", pintarTodosMets);
$("buscaCats").addEventListener("keydown", (e) => { if (e.key === "Enter") $("buscaCats").blur(); });
$("buscaMets").addEventListener("keydown", (e) => { if (e.key === "Enter") $("buscaMets").blur(); });

// ----- cuándo -----
function pintarDias() {
  $("dHoy").className = est.fecha === d.hoy ? "on" : ""; $("dAyer").className = est.fecha === AYER ? "on" : "";
  const otro = est.fecha !== d.hoy && est.fecha !== AYER;
  $("dOtro").className = "otro-dia" + (otro ? " on" : "");
  $("dOtroT").textContent = otro ? diaUTC(est.fecha).getUTCDate() + " " + MESES[diaUTC(est.fecha).getUTCMonth()].slice(0, 3) : "";
  $("dOtroI").style.display = otro ? "none" : "";
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
  const n = parse($("monto").value, est.moneda), c = est.cat ? catDe(est.cat) : null, nota = $("nota").value.trim();
  const listoYa = n > 0 && !!c && !!nota && !!est.met;   // todo lleno: recién ahí el botón se pinta y se puede tocar
  $("anotar").disabled = !listoYa;
  $("campoNota").classList.toggle("ok", !!nota);
  $("aT").textContent = listoYa ? "Confirmar " + fmt(n, est.moneda) : "Confirmar";
  $("aS").textContent = listoYa ? c.icon + " " + c.label + " · " + est.met + (est.fecha !== d.hoy ? " · " + (est.fecha === AYER ? "ayer" : fechaCorta(est.fecha)) : "") : "";
  $("aS").classList.toggle("oculto", !listoYa);
  const mismo = est.fecha.slice(0, 7) === String(resumen.mes || ""), tasa = tasas[est.moneda];
  pintarPresu("p", resumen, mismo && n > 0 && tasa ? n / tasa : 0, mismo ? "" : "Este gasto va a " + MESES[Number(est.fecha.slice(5, 7)) - 1]);
  ajustarCifra();
}

// ----- lo que llega de Apple Pay -----
if (previo && previo.monto) {
  $("origen").classList.remove("oculto");
  if (previo.moneda && d.monedas.includes(previo.moneda)) est.moneda = previo.moneda;
  $("monto").value = String(ENTERAS.includes(est.moneda) ? Math.round(previo.monto) : previo.monto);
  if (previo.comercio) $("nota").value = previo.comercio;
  if (previo.tarjeta) {
    // primero los métodos de quien anota, luego los que llevan su nombre, luego todos
    const t = String(previo.tarjeta).toLowerCase(), yo = String(d.persona || "").toLowerCase();
    const orden = [...new Set(mets.concat(d.metodos.filter(x => yo && x.toLowerCase().includes(yo)), d.metodos))].filter(x => x !== "Sin especificar");
    const m = orden.find(x => x.toLowerCase().includes(t) || t.includes(x.toLowerCase().replace(/^\\S+\\s/, "")));
    if (m) est.met = m;
  }
  if (previo.categoria) { const c = d.categorias.find(x => x.key === previo.categoria); if (c) elegirCat(c); }
  else if (previo.comercio) {   // la categoría que esta persona suele usar en ese comercio
    fetch("/api/anotar/sugerir", { method: "POST", headers: { "Content-Type": "application/json", "X-Equipo": llave }, body: JSON.stringify({ comercio: String(previo.comercio) }) })
      .then(r => r.ok ? r.json() : null)
      .then(j => { if (!j || !j.categoria || est.cat) return; const c = catDe(j.categoria); if (c) elegirCat(c); })
      .catch(() => {});
  }
}

// ----- arranque -----
$("avatar").textContent = String(d.persona || "?").charAt(0).toUpperCase();
$("quien").textContent = "Family Office GG · " + d.persona;
pintarMonedas(); puntos(); pintarCats(); pintarMets(); pintarDias(); pintarVer(); revisar();
$("monto").addEventListener("input", () => { puntos(); revisar(); });
$("nota").addEventListener("input", revisar);
$("nota").addEventListener("keydown", (e) => { if (e.key === "Enter") $("nota").blur(); });
window.addEventListener("resize", ajustarCifra);
window.addEventListener("load", () => {
  const l = document.createElement("link"); l.rel = "stylesheet";
  l.href = "https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,400..600&display=swap";
  l.onload = () => { if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { pintarCats(); ajustarCifra(); }); };
  document.head.appendChild(l);
});
// si abrió con lo guardado, el presupuesto se pide ya mismo y se muestra cuando llega (nunca una cifra vieja)
if (guardado) {
  $("pP").style.visibility = "hidden";
  fetch("/api/anotar/datos", { headers: { "X-Equipo": llave } })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((n) => { resumen = n.resumen || resumen; tasas = n.tasas || tasas; $("pP").style.visibility = ""; revisar(); })
    .catch((st) => {
      if (st === 401) fallo("Este teléfono ya no está activo: pide un código nuevo en Gastos del mes → Ajustes → Teléfonos y Macs.");
      else { $("pP").style.visibility = ""; revisar(); }
    });
}
setTimeout(() => { if (!previo || !previo.monto) $("monto").focus(); }, 300);

// ----- anotar -----
let enviando = false;
$("anotar").onclick = () => {
  if (enviando || $("anotar").disabled) return;
  enviando = true; $("anotar").disabled = true; $("aT").textContent = "Guardando…"; $("error").classList.add("oculto");
  const c = catDe(est.cat);
  const datos = { monto: parse($("monto").value, est.moneda), moneda: est.moneda, categoria: est.cat,
                  metodo_pago: est.met, nota: $("nota").value.trim(), fecha: est.fecha };
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
  $("lDet").textContent = (c ? c.icon + " " + c.label : (r.icon || "") + " " + (r.categoria_label || "")) + " · " + datos.nota;
  $("lFecha").textContent = datos.metodo_pago + " · " + (datos.fecha === d.hoy ? "hoy" : fechaLarga(datos.fecha)) + " · a nombre de " + d.persona;
  if (r.resumen) { pintarPresu("l", r.resumen, 0, ""); if (r.resumen.mes === resumen.mes) resumen = r.resumen; }
  else $("lP").classList.add("oculto");
  $("monto").blur(); $("nota").blur();
  $("listo").classList.remove("si"); void $("listo").offsetWidth; $("listo").classList.add("si");
}
function fallo(msg) { enviando = false; revisar(); $("error").textContent = msg; $("error").classList.remove("oculto"); }
$("otro").onclick = () => {
  $("listo").classList.remove("si"); enviando = false;
  est.cat = null; est.fecha = d.hoy; $("monto").value = ""; $("nota").value = ""; $("origen").classList.add("oculto");
  desplegado = null; abierto = null; pintarVer();
  pintarCats(); pintarDias(); revisar(); $("marco").scrollTop = 0;
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

  // los últimos datos (categorías, métodos, presupuesto) quedan en este teléfono para abrir al instante
  const DATOS = fm.joinPath(fm.documentsDirectory(), "anotar-datos.json");
  const guardar = (x) => { try { fm.writeString(DATOS, JSON.stringify(x)); } catch (e) { /* no importa */ } };
  let datos = null;
  try { if (fm.fileExists(DATOS)) datos = JSON.parse(fm.readString(DATOS)); } catch (e) { datos = null; }
  if (!Keychain.contains(CLAVE)) {
    if (fm.fileExists(DATOS)) fm.remove(DATOS);
    datos = null;
    if (!(await emparejar())) return;
  }
  const guardado = !!(datos && datos.persona && datos.categorias);
  if (guardado) {
    // se abre ya con lo guardado; esto trae lo nuevo para la próxima vez (sin tocar la hoja)
    api("/api/anotar/datos").then(({ code, j }) => {
      if (code === 200 && j) guardar(j);
      else if (code === 401) { Keychain.remove(CLAVE); if (fm.fileExists(DATOS)) fm.remove(DATOS); }
    }).catch(() => {});
  } else {
    let { code, j } = await api("/api/anotar/datos");
    if (code === 401) {
      Keychain.remove(CLAVE);
      await avisar("Este teléfono ya no está activo", "Se quitó en Ajustes → Teléfonos y Macs. Pide un código nuevo para activarlo otra vez.");
      if (!(await emparejar())) return;
      ({ code, j } = await api("/api/anotar/datos"));
    }
    if (code !== 200 || !j) {
      await avisar("Sin conexión", "No pude hablar con P&G GG. Revisa el internet y vuelve a intentar.");
      return;
    }
    datos = j; guardar(j);
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
  await wv.loadHTML(hoja(datos, prefs, entrada(), Keychain.get(CLAVE), guardado), BASE);
  await wv.present(false);
}

if (typeof Script !== "undefined") {
  await main();
  Script.complete();
}
