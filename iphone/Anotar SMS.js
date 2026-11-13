// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: blue; icon-glyph: sms;

// Anotar SMS · P&G mensual (Family Office GG)
// Lo corre sola una automatización de Atajos cada vez que llega un SMS de compra de Banco de Bogotá (85264,
// «Tu compra por …»). Le manda el texto al bot, que:
//   · si el gasto ya estaba anotado (Apple Pay, el bot, el tablero) → lo une y no suma nada (no avisa);
//   · si es un fijo de esa tarjeta (Netflix, iCloud…) → lo marca en Tarjetas › Por confirmar;
//   · si no estaba → lo deja en «Del banco · por anotar» y avisa con una notificación.
// Seguridad: usa la misma llave de «Anotar gasto» (guardada en el llavero del iPhone). Este archivo no trae ninguna llave.

const BASE = "https://expense-bot-production-04c4.up.railway.app";
const CLAVE = "pyg.llave";

async function avisar(titulo, cuerpo, abrir) {
  const n = new Notification();
  n.title = titulo;
  n.body = cuerpo;
  if (abrir) n.openURL = BASE + "/#resumen";
  await n.schedule();
}

async function main() {
  let texto = "";
  if (typeof args !== "undefined") {
    if (args.shortcutParameter) texto = String(args.shortcutParameter);
    else if (args.plainTexts && args.plainTexts.length) texto = String(args.plainTexts[0]);
  }
  texto = texto.trim();
  if (!texto) { Script.setShortcutOutput("sin texto"); return; }
  if (!Keychain.contains(CLAVE)) {
    await avisar("Anotar SMS", "Falta emparejar este iPhone: abre una vez «Anotar gasto» y pon el código de P&G GG › Ajustes › Teléfonos y Macs.", false);
    Script.setShortcutOutput("sin llave");
    return;
  }
  const r = new Request(BASE + "/api/anotar/sms");
  r.method = "POST";
  r.headers = { "Content-Type": "application/json", "X-Equipo": Keychain.get(CLAVE) };
  r.body = JSON.stringify({ texto });
  r.timeoutInterval = 20;
  try {
    const j = await r.loadJSON();
    const code = r.response ? r.response.statusCode : 0;
    if (code === 401) {
      await avisar("Anotar SMS", "La llave de este iPhone ya no sirve. Abre «Anotar gasto» para emparejarlo de nuevo.", false);
      Script.setShortcutOutput("llave inválida");
      return;
    }
    if (j && j.estado === "pendiente") await avisar("📩 " + j.titulo, j.mensaje, true);
    else if (j && j.estado === "fijo") await avisar("📩 " + j.titulo, j.mensaje, true);
    // «unido», «repetido» y «no_entendido»: nada que hacer, sin avisos
    Script.setShortcutOutput(j && j.mensaje ? j.mensaje : "listo");
  } catch (e) {
    await avisar("Anotar SMS", "No se pudo enviar al bot. El SMS sigue en Mensajes; puedes anotarlo a mano.", false);
    Script.setShortcutOutput("error");
  }
}

await main();
Script.complete();
