// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: yellow; icon-glyph: receipt;

// Anotar gasto · P&G mensual (Family Office GG)
// Abre una hoja con el diseño de FO GG: valor, moneda, en qué, con qué y una nota. Queda anotado en el bot
// a nombre de quien emparejó este teléfono. Se usa desde un atajo (pantalla bloqueada, Siri, Botón de Acción).
// Seguridad: este archivo no trae ninguna llave. La primera vez pide el código de un solo uso de
// P&G GG → Ajustes → Teléfonos y Macs. La llave queda en el llavero del iPhone y se puede quitar desde ahí.
// Desde un atajo se le puede pasar {monto, comercio, tarjeta} (por ejemplo, al pagar con Apple Pay):
// la hoja abre con eso puesto y todo se puede cambiar antes de tocar Anotar.

const BASE = "https://expense-bot-production-04c4.up.railway.app";
const CLAVE = "pyg.llave";

// ---------- la hoja (HTML) ----------
function hoja(d, prefs, previo) {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const datos = JSON.stringify({ d, prefs, previo }).replace(/</g, "\\u003c");
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<style>
:root{--fondo:#f5f6f8;--hoja:#ffffff;--txt:#14171c;--apag:#6a717b;--borde:#e3e6ea;--chip:#f2f4f7;--oro:#eda100;--orot:#8a5f00;--orosuave:#fdf1d3;--alerta:#c63a2d;--seg:#e8ebef;--segon:#ffffff}
@media (prefers-color-scheme: dark){:root{--fondo:#0b0d11;--hoja:#14171c;--txt:#f1f2f4;--apag:#9aa1ab;--borde:#2a2f38;--chip:#1d2128;--oro:#eda100;--orot:#f0b429;--orosuave:rgba(237,161,0,.14);--alerta:#f07a6a;--seg:#1f232b;--segon:#3a3f48}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--fondo);color:var(--txt);font-family:-apple-system,"SF Pro Text",Inter,system-ui,sans-serif;font-variant-numeric:tabular-nums}
.hoja{max-width:520px;margin:0 auto;padding:16px 20px calc(24px + env(safe-area-inset-bottom))}
.cab{display:flex;justify-content:space-between;align-items:center;gap:12px}
.marca{display:flex;align-items:center;gap:8px;font-size:18px;font-weight:600}
.quien{font-size:13px;color:var(--apag)}
.cerrar{border:0;background:var(--chip);color:var(--txt);width:34px;height:34px;border-radius:17px;font-size:17px;line-height:34px;text-align:center;padding:0}
.aviso{margin-top:12px;border-radius:10px;background:var(--orosuave);color:var(--orot);font-size:13px;padding:7px 11px}
.valor{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-top:16px}
.monto{flex:1;min-width:0;border:0;background:transparent;color:var(--txt);font:600 44px/1 -apple-system,"SF Pro Display",Inter,system-ui,sans-serif;letter-spacing:-.03em;padding:0;outline:none}
.monto::placeholder{color:var(--apag);opacity:.5}
.seg{display:flex;gap:2px;padding:2px;border-radius:9px;background:var(--seg);flex-shrink:0}
.seg button{border:0;background:transparent;color:var(--apag);font-size:13px;padding:7px 10px;border-radius:7px;font-weight:500}
.seg button.on{background:var(--segon);color:var(--txt);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.raya{height:1px;background:var(--borde);margin:16px 0 14px}
.tit{font-size:11px;font-weight:600;letter-spacing:.05em;color:var(--apag);margin-top:16px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{border:1px solid var(--borde);background:var(--chip);color:var(--txt);font-size:14px;padding:9px 13px;border-radius:19px;min-height:38px}
.chip.on{border-color:var(--oro);background:var(--orosuave);color:var(--orot);font-weight:600}
.chip.otra{background:transparent;border-style:dashed;color:var(--apag)}
.campo{width:100%;margin-top:8px;border-radius:12px;background:var(--chip);border:1px solid var(--borde);color:var(--txt);padding:11px 14px;font-size:15px;outline:none}
.oculto{display:none}
.anotar{width:100%;margin-top:18px;height:50px;border:0;border-radius:14px;background:var(--oro);color:#14171c;font-size:16px;font-weight:600}
.anotar:disabled{opacity:.45}
.pie{font-size:12.5px;color:var(--apag);text-align:center;margin-top:10px;min-height:16px}
.error{color:var(--alerta);font-weight:500}
.listo{position:fixed;inset:0;background:var(--fondo);display:none;align-items:center;justify-content:center;padding:24px}
.listo.si{display:flex}
.listo .caja{max-width:360px;width:100%;border-radius:20px;background:var(--hoja);border:1px solid var(--borde);padding:18px;display:flex;gap:12px;align-items:flex-start}
.listo .ok{width:36px;height:36px;border-radius:18px;background:var(--oro);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.listo b{display:block;font-size:16px}
.listo span{display:block;font-size:14px;color:var(--apag);margin-top:3px}
.listo .oro{color:var(--orot)}
</style></head><body>
<div class="hoja">
  <div class="cab">
    <div class="marca"><svg width="22" height="22" viewBox="0 0 64 64" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="5"><path d="M45.38 19.96 A18 18 0 1 0 50 32 L44.24 32"/><path d="M39.06 25.64 A9.5 9.5 0 1 0 41.5 32 L36.27 32"/></g></svg>Anotar gasto</div>
    <div style="display:flex;align-items:center;gap:10px"><span class="quien" id="quien"></span><button class="cerrar" id="cerrar" aria-label="Cerrar">✕</button></div>
  </div>
  <div class="aviso oculto" id="aviso">Llegó de Apple Pay · todo se puede cambiar antes de Anotar</div>
  <div class="valor">
    <input class="monto" id="monto" inputmode="decimal" autocomplete="off" placeholder="0" aria-label="Valor">
    <div class="seg" id="monedas"></div>
  </div>
  <div class="raya"></div>
  <div class="tit">EN QUÉ</div>
  <div class="chips" id="cats"></div>
  <select class="campo oculto" id="catsel" aria-label="Otra categoría"></select>
  <div class="tit">CON QUÉ</div>
  <div class="chips" id="mets"></div>
  <select class="campo oculto" id="metsel" aria-label="Otro método"></select>
  <div class="tit">COMERCIO O NOTA</div>
  <input class="campo" id="nota" maxlength="120" placeholder="Opcional" autocomplete="off">
  <button class="anotar" id="anotar" disabled>Anotar</button>
  <div class="pie" id="pie"></div>
</div>
<div class="listo" id="listo"><div class="caja"><div class="ok"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#14171c" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10"/></svg></div><div><b id="l1"></b><span id="l2"></span><span class="oro" id="l3"></span></div></div></div>
<script>
const {d, prefs, previo} = ${datos};
const $ = (id) => document.getElementById(id);
const MESES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"];
const ENTERAS = ["COP","CLP","ARS"];
const est = { moneda: (prefs && prefs.moneda && d.monedas.includes(prefs.moneda)) ? prefs.moneda : "COP", cat: null, met: d.metodo_ultimo || (d.metodos_sugeridos[0] || "Sin especificar") };
const usd = (x) => "US$" + Math.round(x).toLocaleString("en-US");
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
function pintarMonedas() {
  const prin = ["COP","USD","AED"].filter(m => d.monedas.includes(m));
  if (!prin.includes(est.moneda)) prin.push(est.moneda);
  $("monedas").innerHTML = "";
  prin.forEach(m => { const b = document.createElement("button"); b.textContent = m; b.className = m === est.moneda ? "on" : ""; b.onclick = () => { est.moneda = m; pintarMonedas(); if (typeof puntos === "function") puntos(); revisar(); }; $("monedas").appendChild(b); });
}
function chip(cont, texto, on, fn, otra) { const b = document.createElement("button"); b.className = "chip" + (on ? " on" : "") + (otra ? " otra" : ""); b.textContent = texto; b.onclick = fn; cont.appendChild(b); }
let cats = d.frecuentes.slice();
function pintarCats() {
  $("cats").innerHTML = "";
  cats.forEach(c => chip($("cats"), c.icon + " " + c.label, est.cat === c.key, () => { est.cat = c.key; pintarCats(); revisar(); }));
  chip($("cats"), "Otra…", false, () => { $("catsel").classList.toggle("oculto"); }, true);
}
function llenarSelectCats() {
  const s = $("catsel"); s.innerHTML = '<option value="">Elige la categoría…</option>';
  const grupos = {};
  d.categorias.forEach(c => { (grupos[c.grupo || "Otras"] = grupos[c.grupo || "Otras"] || []).push(c); });
  Object.keys(grupos).forEach(g => { const og = document.createElement("optgroup"); og.label = g; grupos[g].forEach(c => { const o = document.createElement("option"); o.value = c.key; o.textContent = c.icon + " " + c.label; og.appendChild(o); }); s.appendChild(og); });
  s.onchange = () => { const c = d.categorias.find(x => x.key === s.value); if (!c) return; if (!cats.some(x => x.key === c.key)) cats.unshift(c); est.cat = c.key; s.classList.add("oculto"); pintarCats(); revisar(); };
}
let mets = d.metodos_sugeridos.slice();
function pintarMets() {
  if (est.met && !mets.includes(est.met)) mets.unshift(est.met);
  $("mets").innerHTML = "";
  mets.forEach(m => chip($("mets"), m, est.met === m, () => { est.met = m; pintarMets(); }));
  chip($("mets"), "Otro…", false, () => { $("metsel").classList.toggle("oculto"); }, true);
}
function llenarSelectMets() {
  const s = $("metsel"); s.innerHTML = '<option value="">Elige el método…</option>';
  d.metodos.forEach(m => { const o = document.createElement("option"); o.value = m; o.textContent = m; s.appendChild(o); });
  s.onchange = () => { if (!s.value) return; est.met = s.value; s.classList.add("oculto"); pintarMets(); };
}
function revisar() {
  const n = parse($("monto").value, est.moneda);
  const b = $("anotar");
  b.disabled = !(n > 0 && est.cat);
  b.textContent = n > 0 ? "Anotar " + fmt(n, est.moneda) : "Anotar";
}
function pieBase() {
  const r = d.resumen || {}; const mes = MESES[Number((r.mes || d.hoy).slice(5, 7)) - 1];
  $("pie").className = "pie";
  $("pie").textContent = r.queda_usd != null ? "Quedan " + usd(r.queda_usd) + " del presupuesto de " + mes : "";
}
// lo que llega de Apple Pay
if (previo && previo.monto) {
  $("aviso").classList.remove("oculto");
  $("monto").value = String(previo.monto);
  if (previo.comercio) $("nota").value = previo.comercio;
  if (previo.tarjeta) { const t = String(previo.tarjeta).toLowerCase(); const m = d.metodos.find(x => x.toLowerCase().includes(t) || t.includes(x.toLowerCase().replace(/^\\S+\\s/, ""))); if (m) est.met = m; }
  if (previo.categoria) { const c = d.categorias.find(x => x.key === previo.categoria); if (c) { if (!cats.some(x => x.key === c.key)) cats.unshift(c); est.cat = c.key; } }
}
$("quien").textContent = d.persona + " · hoy " + d.hora;
pintarMonedas(); pintarCats(); llenarSelectCats(); pintarMets(); llenarSelectMets(); pieBase(); revisar();
function puntos() {   // en pesos el valor se ve con puntos de miles mientras se escribe
  if (!ENTERAS.includes(est.moneda)) return;
  const n = $("monto").value.replace(/[^0-9]/g, "");
  $("monto").value = n ? Number(n).toLocaleString("de-DE") : "";
}
puntos();
$("monto").addEventListener("input", () => { puntos(); revisar(); });
setTimeout(() => { if (!previo || !previo.monto) $("monto").focus(); }, 250);
let enviando = false;
$("anotar").onclick = () => {
  if (enviando || $("anotar").disabled) return;
  enviando = true; $("anotar").disabled = true; $("anotar").textContent = "Anotando…";
  const datos = { monto: parse($("monto").value, est.moneda), moneda: est.moneda, categoria: est.cat, metodo_pago: est.met || "Sin especificar", nota: $("nota").value.trim() };
  if (window.enviar) window.enviar(datos);
};
$("cerrar").onclick = () => { if (window.enviar) window.enviar(null); };
function listo(r) {
  const mes = MESES[Number(r.fecha.slice(5, 7)) - 1];
  $("l1").textContent = "Anotado: " + fmt(parse(String(r.moneda === "COP" ? r.monto_cop : $("monto").value), r.moneda), r.moneda);
  $("l2").textContent = r.icon + " " + r.categoria_label + " · " + (est.met || "Sin especificar");
  $("l3").textContent = r.resumen && r.resumen.queda_usd != null ? "Quedan " + usd(r.resumen.queda_usd) + " del presupuesto de " + mes : "";
  $("listo").classList.add("si");
}
function fallo(msg) { enviando = false; revisar(); $("pie").className = "pie error"; $("pie").textContent = msg; }
</script></body></html>`;
}

// ---------- llave del teléfono ----------
async function emparejar() {
  const a = new Alert();
  a.title = "Activar Anotar gasto";
  a.message = "En P&G GG ve a Ajustes → Teléfonos y Macs → «Código para un iPhone» y escribe aquí el código (vale 10 minutos y una sola vez).";
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
  await avisar("No se pudo activar", j.detail || "Revisa el código y vuelve a intentar.");
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
function entrada() {
  let p = typeof args !== "undefined" ? args.shortcutParameter : null;
  if (typeof p === "string") { try { p = JSON.parse(p); } catch (e) { p = null; } }
  if (!p || typeof p !== "object") return null;
  const monto = Number(String(p.monto || p.amount || "").replace(/[^0-9.,]/g, "").replace(",", "."));
  return monto > 0 ? { monto, comercio: p.comercio || p.merchant || "", tarjeta: p.tarjeta || p.card || "", categoria: p.categoria || "" } : null;
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
  await wv.loadHTML(hoja(datos, prefs, entrada()));
  let cerrada = false;
  const vista = wv.present(false).then(() => { cerrada = true; return null; });
  while (!cerrada) {
    const pedido = await Promise.race([wv.evaluateJavaScript("window.enviar = (x) => completion(x); 0;", true), vista]);
    if (!pedido) break;
    const { code: c2, j: res } = await api("/api/anotar/gasto", pedido);
    if (c2 === 200 && res && res.ok) {
      try { fm.writeString(PREF, JSON.stringify({ moneda: pedido.moneda })); } catch (e) { /* no importa */ }
      await wv.evaluateJavaScript("listo(" + JSON.stringify(res) + ")", false);
      const n = new Notification();
      n.title = "Anotado en P&G GG";
      const valor = ["COP", "CLP", "ARS"].includes(pedido.moneda)
        ? (pedido.moneda === "COP" ? "$" : pedido.moneda + " ") + Math.round(pedido.monto).toLocaleString("de-DE")
        : pedido.moneda + " " + pedido.monto.toLocaleString("en-US", { maximumFractionDigits: 2 });
      n.body = valor + " · " + (res.icon || "") + " " + (res.categoria_label || res.categoria) + " · " + pedido.metodo_pago;
      n.schedule();
      await new Promise((ok) => Timer.schedule(1600, false, ok));
      break;
    }
    const msg = c2 === 401 ? "Este teléfono ya no está activo: pide un código nuevo en Ajustes." :
      (res && res.detail ? String(res.detail) : "No se pudo anotar. Revisa el internet y vuelve a intentar.");
    await wv.evaluateJavaScript("fallo(" + JSON.stringify(msg) + ")", false);
  }
}

if (typeof Script !== "undefined") {
  await main();
  Script.complete();
}
