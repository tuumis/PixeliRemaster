/* PixeliRemaster – käyttöliittymä */
"use strict";

const AVAIN = document.querySelector('meta[name="pixeli-avain"]').content;
const $ = (s, juuri = document) => juuri.querySelector(s);
const $$ = (s, juuri = document) => [...juuri.querySelectorAll(s)];

let TILA = null;
let KIELI = "fi";
let valittu = null;              // valitun videon id
let edellinenTyoAllekirjoitus = "";
const nakymat = {};              // per video: {kohta, tila, zoom, x, oikea, saato, saatoUrl}
const tunnetutValmiit = new Set();
let ensimmainenHaku = true;

/* ------------------------------------------------------------ tekstit ja muotoilu */
function t(avain, p = {}) {
  const s = (KAANNOKSET[KIELI] && KAANNOKSET[KIELI][avain]) ?? KAANNOKSET.fi[avain] ?? avain;
  return s.replace(/\{(\w+)\}/g, (_, k) => (p[k] ?? ""));
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function luku(x, d = 0) {
  return Number(x).toLocaleString(KIELI === "fi" ? "fi-FI" : "en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
}
function fpsTeksti(murtoluku) {
  const [a, b] = String(murtoluku).split("/").map(Number);
  const f = b ? a / b : a;
  return Math.abs(f - Math.round(f)) < 0.01 ? luku(Math.round(f)) : luku(f, 3);
}
const onKuva = x => !!x && x.laji === "kuva";
function asetusNimi(x, n) { return t((onKuva(x) ? "kasetus." : "asetus.") + n); }
function asetusKuvaus(x, n) { return t((onKuva(x) ? "kasetus." : "asetus.") + n + ".k"); }
function murto(s) { const [a, b] = String(s).split("/").map(Number); return b ? a / b : a; }
function kesto(sekunnit) {
  if (sekunnit == null || !isFinite(sekunnit)) return "–";
  const s = Math.max(0, Math.round(sekunnit));
  if (s < 60) return t("kesto.alle_min");
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  if (h) return t("kesto.h_min", {h, m: String(m).padStart(2, "0")});
  return t("kesto.min", {m});
}
function koko(tavut) {
  if (!tavut) return "–";
  const gt = tavut / 1e9;
  return gt >= 1 ? `${luku(gt, 2)} Gt`.replace("Gt", KIELI === "fi" ? "Gt" : "GB") : `${luku(tavut / 1e6)} ${KIELI === "fi" ? "Mt" : "MB"}`;
}
function kaannaStaattiset() {
  document.documentElement.lang = KIELI;
  $$("[data-t]").forEach(e => { e.textContent = t(e.dataset.t); });
}

/* ------------------------------------------------------------ palvelin */
async function api(polku, data = {}) {
  const r = await fetch(`/api/${polku}`, {method: "POST", headers: {"X-Pixeli-Avain": AVAIN, "Content-Type": "application/json"},
                                         body: JSON.stringify(data)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const avain = `virhe.${j.virhe}`;
    if (KAANNOKSET.fi[avain]) ilmoita(t(avain));
    throw new Error(j.virhe || r.status);
  }
  return j;
}
function media(id, polku, versio = "") {
  return `/media/${encodeURIComponent(id)}/${polku.split("/").map(encodeURIComponent).join("/")}?a=${encodeURIComponent(AVAIN)}${versio ? "&v=" + encodeURIComponent(versio) : ""}`;
}

async function hae() {
  try {
    const r = await fetch("/api/tila", {headers: {"X-Pixeli-Avain": AVAIN}, cache: "no-store"});
    if (r.ok) {
      TILA = await r.json();
      const k = TILA.jono.asetukset.kieli || "auto";
      const uusiKieli = k === "auto" ? TILA.kieli_jarjestelma : k;
      if (uusiKieli !== KIELI || ensimmainenHaku) { KIELI = uusiKieli; kaannaStaattiset(); edellinenTyoAllekirjoitus = ""; }
      piirra();
      ensimmainenHaku = false;
    }
  } catch (e) { /* palvelin käynnistyy tai sulkeutuu */ }
  const kiire = TILA && (TILA.ymparisto.asennus.kaynnissa || TILA.jono.kasiteltava ||
    TILA.jono.tyot.some(x => ["analysoidaan", "kasitellaan"].includes(x.tila) || ["jonossa", "kaynnissa"].includes(x.koe?.tila) || ["jonossa", "kaynnissa"].includes(x.liike?.tila)));
  setTimeout(hae, kiire ? 600 : 1500);
}

/* ------------------------------------------------------------ ilmoitukset */
let ilmoitusAjastin = null;
function ilmoita(teksti) {
  const e = $("#ilmoitus");
  e.textContent = teksti;
  e.hidden = false;
  clearTimeout(ilmoitusAjastin);
  ilmoitusAjastin = setTimeout(() => { e.hidden = true; }, 4200);
}
function kilahdus() {
  try {
    const ac = new AudioContext();
    [0, 0.16].forEach((dt, i) => {
      const o = ac.createOscillator(), g = ac.createGain();
      o.frequency.value = i ? 1046 : 784; o.type = "sine";
      g.gain.setValueAtTime(0.0001, ac.currentTime + dt);
      g.gain.exponentialRampToValueAtTime(0.18, ac.currentTime + dt + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + dt + 0.35);
      o.connect(g).connect(ac.destination); o.start(ac.currentTime + dt); o.stop(ac.currentTime + dt + 0.4);
    });
  } catch (e) { /* ei ääntä */ }
}
function lisaysTulos(r) {
  if (!r) return;
  const n = (r.lisatyt || []).length;
  if (n) {
    ilmoita(n === 1 ? t("ilmoitus.lisatty1") : t("ilmoitus.lisatty", {n}));
    valittu = r.lisatyt[r.lisatyt.length - 1];
    edellinenTyoAllekirjoitus = "";
  } else if ((r.ohitetut || []).length) {
    ilmoita(t("ilmoitus.ohitettu", {n: r.ohitetut.length}));
  }
  hae();
}
window.pixeliPudotettu = lisaysTulos;

/* ------------------------------------------------------------ pääpiirto */
function piirra() {
  const ymp = TILA.ymparisto;
  const valmis = ymp.valmis;
  $("#asennus").hidden = valmis;
  $("#paa").hidden = !$("#asennus").hidden;
  if (!$("#asennus").hidden) { piirraAsennus(ymp); return; }
  piirraJono();
  piirraTyo();
  piirraAlapalkki();
  ilmoitaValmiit();
}

function ilmoitaValmiit() {
  for (const x of TILA.jono.tyot) {
    if (x.tila === "valmis" && !tunnetutValmiit.has(x.id)) {
      if (!ensimmainenHaku) { ilmoita(t("ilmoitus.valmis", {nimi: x.nimi})); kilahdus(); }
      tunnetutValmiit.add(x.id);
    }
  }
}

/* ------------------------------------------------------------ asennus */
function piirraAsennus(y) {
  const a = y.asennus;
  const vaiheet = [];
  const gpuOk = !!(y.gpu && y.gpu.ajuri_ok);
  vaiheet.push({nimi: t("asennus.gpu"), kuvaus: y.gpu ? `${esc(y.gpu.nimi)}${y.gpu.ajuri ? `, ${KIELI === "fi" ? "ajuri" : "driver"} ${esc(y.gpu.ajuri)}` : ""}` : t("asennus.gpu_kuvaus"),
    tila: !y.tarkistettu ? "kaynnissa" : gpuOk ? "ok" : "virhe", teksti: !y.tarkistettu ? t("asennus.tila_tarkistetaan") : gpuOk ? t("asennus.tila_ok") : ""});
  const vaihe = a.kaynnissa ? a.vaihe : null;
  const ffOk = y.ffmpeg;
  vaiheet.push({nimi: t("asennus.ffmpeg"), kuvaus: t("asennus.ffmpeg_kuvaus"), tila: ffOk ? "ok" : vaihe === "ffmpeg" ? "kaynnissa" : "",
    teksti: ffOk ? t("asennus.tila_ok") : vaihe === "ffmpeg" ? (a.tieto && a.tieto.koko ? t("asennus.ladattu", {a: koko(a.tieto.tehty), b: koko(a.tieto.koko)}) : t("asennus.tila_asennetaan")) : t("asennus.tila_odottaa"),
    edistys: vaihe === "ffmpeg" ? a.edistys : null});
  const toOk = y.torch === "ok";
  const toKay = vaihe === "torch" || vaihe === "torch_asennus";
  const malliKay = vaihe === "mallit";           // kohinanpoistomallin lataus kuuluu tähän vaiheeseen
  vaiheet.push({nimi: t("asennus.torch"), kuvaus: t("asennus.torch_kuvaus"), tila: toOk && !malliKay ? "ok" : toKay || malliKay ? "kaynnissa" : y.torch === "ei_cudaa" ? "virhe" : "",
    teksti: malliKay ? (a.tieto && a.tieto.koko ? t("asennus.ladattu", {a: koko(a.tieto.tehty), b: koko(a.tieto.koko)}) : t("asennus.tila_asennetaan"))
      : toOk ? t("asennus.tila_ok") : y.torch === "tarkistetaan" ? t("asennus.tila_tarkistetaan") : toKay ? (vaihe === "torch" && a.tieto && a.tieto.koko ? t("asennus.ladattu", {a: koko(a.tieto.tehty), b: koko(a.tieto.koko)}) : t("asennus.tila_asennetaan")) : t("asennus.tila_odottaa"),
    edistys: malliKay ? a.edistys : toKay ? (vaihe === "torch" ? a.edistys : -1) : null});
  const itOk = y.itsetesti && y.itsetesti.ok;
  vaiheet.push({nimi: t("asennus.itsetesti"), kuvaus: itOk && y.itsetesti.nopeus && y.itsetesti.nopeus["2"] ? t("asennus.nopeus", {n: luku(y.itsetesti.nopeus["2"], 1)}) : t("asennus.itsetesti_kuvaus"),
    tila: itOk ? "ok" : vaihe === "itsetesti" ? "kaynnissa" : (y.itsetesti && !y.itsetesti.ok) ? "virhe" : "",
    teksti: itOk ? t("asennus.tila_ok") : vaihe === "itsetesti" ? `${luku((a.edistys || 0) * 100)} %` : t("asennus.tila_odottaa"),
    edistys: vaihe === "itsetesti" ? a.edistys : null});

  $("#asennus-vaiheet").innerHTML = vaiheet.map(v => `
    <li class="${v.tila}"><span class="merkintä" aria-hidden="true"></span>
      <span class="nimi">${esc(v.nimi)}</span><span class="tila num">${esc(v.teksti || "")}</span>
      <span class="kuvaus">${v.kuvaus}</span>
      ${v.edistys != null ? `<div class="edistys ${v.edistys < 0 || v.edistys == null ? "epamaarainen" : ""}"><i style="width:${Math.max(0, (v.edistys || 0) * 100)}%"></i></div>` : ""}
    </li>`).join("");

  const viesti = $("#asennus-viesti");
  const nappi = $("#asennus-aloita");
  let viestiHtml = "";
  if (y.tarkistettu && !y.gpu) viestiHtml = `<strong>${t("asennus.ei_gpu_otsikko")}</strong><p>${t("asennus.ei_gpu")}</p>`;
  else if (y.gpu && !y.gpu.ajuri_ok) viestiHtml = `<strong>${t("asennus.ei_gpu_otsikko")}</strong><p>${t("asennus.ajuri_vanha", {v: esc(y.gpu.ajuri)})}</p>`;
  else if (a.virhe) viestiHtml = `<p>${t("asennus.virhe." + a.virhe)}</p>`;
  viesti.hidden = !viestiHtml;
  viesti.className = "viesti virhe";
  viesti.innerHTML = viestiHtml;

  const estetty = !y.tarkistettu || !gpuOk;
  if (y.tarkistettu && !gpuOk) { nappi.textContent = t("asennus.tarkista"); nappi.disabled = false; nappi.dataset.toiminto = "tarkista"; }
  else { nappi.textContent = a.virhe ? t("asennus.uudelleen") : t("asennus.aloita"); nappi.disabled = estetty || a.kaynnissa; nappi.dataset.toiminto = "asenna"; }
}

/* ------------------------------------------------------------ jono (sivupalkki) */
function tilaTeksti(x) {
  if (x.tila === "kasitellaan") {
    const e = x.edistys || {};
    return t("tila.kasitellaan", {p: e.kaikki ? `${luku(100 * e.tehty / e.kaikki)} %` : ""});
  }
  return t("tila." + x.tila);
}
function piirraJono() {
  const ul = $("#jono-lista");
  const tyot = TILA.jono.tyot;
  if (!valittu || !tyot.some(x => x.id === valittu)) valittu = tyot.length ? tyot[0].id : null;
  const html = tyot.map(x => {
    const tun = x.koe && x.koe.tunniste;
    const kuva = onKuva(x) ? (x.analyysi ? media(x.id, "pikku.png", String(x.lisatty)) : "")
      : x.tila === "kasitellaan" && x.pikkukuva ? media(x.id, "pikkukuva.png", String(Math.floor(x.pikkukuva)))
      : tun ? media(x.id, `koe_${tun}/v1_k2.png`, tun) : "";
    const e = x.edistys || {};
    const pros = x.tila === "kasitellaan" && e.kaikki ? 100 * e.tehty / e.kaikki : null;
    return `<li class="kohta" data-id="${esc(x.id)}" aria-current="${x.id === valittu}" tabindex="0">
      <div class="pikku" style="${kuva ? `background-image:url('${kuva}')` : ""}">${kuva ? "" : esc((x.nimi.split(".").pop() || "").toUpperCase())}</div>
      <div><div class="nimi" title="${esc(x.nimi)}">${esc(x.nimi)}</div>
        <div class="tila ${x.tila}"><span class="pallo"></span>${esc(tilaTeksti(x))}</div></div>
      ${pros != null ? `<div class="edistys"><i style="width:${pros}%"></i></div>` : ""}
    </li>`;
  }).join("");
  if (ul.dataset.html !== html) { ul.innerHTML = html; ul.dataset.html = html; }
  $("#tyhjenna").hidden = !tyot.some(x => x.tila === "valmis");
  $("#pudota-vihje").textContent = TILA.ikkuna === "pywebview" ? t("jono.pudota_vihje") : t("jono.pudota_vihje_selain");
}

/* ------------------------------------------------------------ valittu video */
function nakyma(id) {
  if (!nakymat[id]) {
    const x = TILA && TILA.jono.tyot.find(y => y.id === id);
    nakymat[id] = {kohta: onKuva(x) ? 1 : 2, tila: "jakaja", zoom: "sovita", x: 50, oikea: true, saato: null, saatoUrl: {}};
  }
  return nakymat[id];
}
/* Oletussäädöt: asetuksen perusarvot, joiden päälle tälle tiedostolle mitatut (lähteen laatuun sovitetut). */
function oletusSaato(n, x) {
  const e = ((onKuva(x) ? TILA.esiasetukset_kuva : TILA.esiasetukset) || {})[String(n)] || {};
  const o = (x && x.koe && x.koe.oletukset && x.koe.oletukset[String(n)]) || {};
  const s = {ai: o.ai ?? e.ai, rae: o.rae ?? (e.rae || 0), halo: o.halo ?? (e.halo || 0), kohina: o.kohina ?? (e.kohina || 0)};
  if (onKuva(x)) s.kasvot = 0;
  return s;
}
function kohtienMaara(x) { return onKuva(x) ? ((x.koe && x.koe.alueet) || [1]).length : 3; }
function nykyinenAsetus(x) { return Number((x.valinta || {}).asetus || (x.analyysi || {}).suositus || 2); }
function arvioAika(x, n) {
  const an = x.analyysi; if (!an) return null;
  if (onKuva(x)) return (x.koe && x.koe.aika && x.koe.aika[String(n)]) ?? null;
  const kuvia = an.kesto * murto(an.R);
  const nop = x.koe && x.koe.nopeus && x.koe.nopeus[String(n)];
  if (nop) return kuvia / (nop * 1.15);         // erissä hieman nopeampi kuin yksittäin mitattuna
  if (n === nykyinenAsetus(x) && x.arvio_nopeus) return kuvia / x.arvio_nopeus;
  return null;
}

function tyoAllekirjoitus(x) {
  if (!x) return "tyhja:" + KIELI + TILA.ikkuna;
  return JSON.stringify([KIELI, x.id, x.tila, !!x.analyysi, x.koe && x.koe.tila, x.koe && x.koe.tunniste,
    (x.valinta || {}).asetus, (x.valinta || {}).dar, (x.valinta || {}).skaala, (x.valinta || {}).kierto, (x.valinta || {}).muoto,
    (x.valinta || {}).laatu, (x.valinta || {}).aanet, (x.valinta || {}).tekstit, (x.valinta || {}).ulkoiset, x.geo && [x.geo.ow, x.geo.oh], x.virhe,
    (x.valinta || {}).naarmut, (x.valinta || {}).vari,
    x.tulos && x.tulos.ulos, TILA.ymparisto.valmis, x.liike && x.liike.tila, x.koe && x.koe.aika,
    x.koe && x.koe.versio, x.koe && !!x.koe.siirretaan,
    TILA.jono.tyot.filter(y => y.laji === x.laji && ["odottaa", "virhe", "keskeytetty"].includes(y.tila)).length]);
}

function piirraTyo() {
  const alue = $("#tyo");
  const x = TILA.jono.tyot.find(y => y.id === valittu);
  const allek = tyoAllekirjoitus(x);
  if (allek !== edellinenTyoAllekirjoitus) {
    edellinenTyoAllekirjoitus = allek;
    alue.innerHTML = x ? tyoHtml(x) : tyhjaHtml();
    if (x) kytkeTyo(x); else $("#tyhja-valitse")?.addEventListener("click", valitseTiedostot);
  }
  if (x) paivitaDynaamiset(x);
  if (x && x.analyysi && x.tila !== "hd" && TILA.ymparisto.valmis && x.koe && (x.koe.tila === "ei" || !x.koe.tila)) {
    x.koe.tila = "jonossa";
    api("esikatsele", {id: x.id}).catch(() => {});
  }
}

function tyhjaHtml() {
  return `<div class="tyhja"><div class="tyhja-alue">
    <h1>${t("tyhja.otsikko")}</h1><p>${t("tyhja.teksti")}</p>
    <button class="painike ensisijainen iso" id="tyhja-valitse">${t("tyhja.valitse")}</button>
    <p class="tuetut">${t("tyhja.tuetut")}</p></div></div>`;
}

function skaalaTeksti(v, w, h) {
  return v === "naytto" ? t("koko.naytto", {w, h}) : v === "1" ? t("koko.yksi", {w, h}) : t("koko.kerroin", {k: v, w, h});
}
const B_KOODIT = {ger: "de", fre: "fr", dut: "nl", chi: "zh", cze: "cs", gre: "el", per: "fa", rum: "ro", slo: "sk",
  ice: "is", wel: "cy", arm: "hy", baq: "eu", mac: "mk", may: "ms", alb: "sq", bur: "my", geo: "ka", tib: "bo"};
function kielenNimi(koodi) {
  if (!koodi || koodi === "und") return t("raita.tuntematon");
  try { return new Intl.DisplayNames([KIELI], {type: "language"}).of(B_KOODIT[koodi] || koodi) || koodi; }
  catch (e) { return koodi; }
}
function raitaTeksti(r) {
  const osat = [kielenNimi(r.kieli)];
  if (r.koodekki) osat.push(String(r.koodekki).toUpperCase().replace("DVD_SUBTITLE", "DVD").replace("HDMV_PGS_SUBTITLE", "PGS").replace("SUBRIP", "SRT"));
  if (r.kanavat) osat.push(r.kanavat === 6 ? "5.1" : r.kanavat === 2 ? "stereo" : r.kanavat === 1 ? "mono" : `${r.kanavat} ch`);
  return osat.join(" · ") + (r.nimi ? ` – ${r.nimi}` : "");
}
function raidatHtml(x, muokattava) {
  const an = x.analyysi, v = x.valinta || {};
  const aanet = an.aaniraidat || [], tekstit = an.tekstiraidat || [], ulk = x.ulkoiset_tekstit || [];
  if (aanet.length < 2 && !tekstit.length && !ulk.length) return "";
  const ei = muokattava ? "" : "disabled";
  const valittu = (lista, i) => lista == null || lista.includes(i);
  const rivi = (laji, r) => `<label class="valinta"><input type="checkbox" data-raita="${laji}" value="${r.i}" ${valittu(v[laji], r.i) ? "checked" : ""} ${ei}><span>${esc(raitaTeksti(r))}</span></label>`;
  const nA = v.aanet == null ? aanet.length : v.aanet.length;
  const nT = (v.tekstit == null ? tekstit.length : v.tekstit.length) + (v.ulkoiset === false ? 0 : ulk.length);
  return `<details class="osio raidat" id="raidat"><summary><span class="raidat-otsikko">${t("raita.otsikko")}</span>
      <span class="selite">${t("raita.yhteenveto", {a: nA, aa: aanet.length, s: nT, ss: tekstit.length + ulk.length})}</span></summary>
    ${aanet.length ? `<div class="raita-ryhma">${t("raita.aanet")}</div>${aanet.map(r => rivi("aanet", r)).join("")}` : ""}
    ${tekstit.length || ulk.length ? `<div class="raita-ryhma">${t("raita.tekstit")}</div>${tekstit.map(r => rivi("tekstit", r)).join("")}
      ${ulk.map(n => `<label class="valinta"><input type="checkbox" data-raita="ulkoiset" ${v.ulkoiset === false ? "" : "checked"} ${ei}><span>${esc(n)} (${t("raita.ulkoinen")})</span></label>`).join("")}` : ""}
  </details>`;
}
function laatuTeksti(k) {
  return [t("laatu.kohina." + k.kohina), t("laatu.lohkot." + k.lohkot), t("laatu.teravyys." + k.teravyys)].join(" · ");
}
function faktatKuvaHtml(x) {
  const an = x.analyysi, geo = x.geo;
  let lahde = `${an.leveys}×${an.korkeus} ${esc(String(an.muoto || "").toUpperCase())}`;
  if (an.laatu) lahde += `, ${t("fakta.laatu", {q: an.laatu})}`;
  if (an.alfa) lahde += `, ${t("fakta.lapinakyva")}`;
  const rivit = [[t("fakta.lahde"), lahde], [t("fakta.tyyppi"), t("ktyyppi." + an.tyyppi)]];
  if (x.koe && x.koe.kuvaus) rivit.push([t("fakta.laatu_lahde"), laatuTeksti(x.koe.kuvaus)]);
  if (geo) {
    rivit.push([t("fakta.tulos"), `${geo.ow}×${geo.oh}`]);
    const m_ = (x.valinta || {}).muoto;
    rivit.push([t("fakta.tallennus"), m_ ? m_.toUpperCase().replace("JPG", "JPEG") : ["jpeg", "heic"].includes(an.muoto) ? "JPEG" : "PNG"]);
  }
  const huomiot = (an.huomiot || []).map(h => `<p class="huomio">${t("huomio." + h)}</p>`).join("");
  return `<dl class="faktat num">${rivit.map(([a, b]) => `<div><dt>${a}</dt><dd>${b}</dd></div>`).join("")}</dl>${huomiot}`;
}
function faktatHtml(x) {
  const an = x.analyysi, geo = x.geo;
  if (!an) return "";
  if (onKuva(x)) return faktatKuvaHtml(x);
  const fps = fpsTeksti(an.fps), R = fpsTeksti(an.R);
  let nopeus = t("fakta.kuvaa_s", {n: fps});
  if (an.lomitus === "desimointi") nopeus = t("fakta.desimointi", {a: fps, b: R});
  else if (an.lomitus === "lomitettu") nopeus = t("fakta.lomitettu", {a: fps, b: R});
  else if (an.lomitus === "ivtc") nopeus = t("fakta.ivtc", {a: fps, b: R});
  const rivit = [
    [t("fakta.lahde"), `${an.leveys}×${an.korkeus} ${t("tyyppi." + an.tyyppi)} (${esc(an.koodekki)})`],
    [t("fakta.kesto"), kesto(an.kesto)],
    [t("fakta.kuvanopeus"), nopeus],
  ];
  if (geo) {
    const suhde = luku(geo.suhde, 2) + ":1";
    const rajattu = an.rajaus && (an.rajaus[2] !== an.leveys || an.rajaus[3] !== an.korkeus);
    rivit.push([t("fakta.kuvasuhde"), suhde + (rajattu ? `, ${t("fakta.palkit")}` : "")]);
    rivit.push([t("fakta.tulos"), `${geo.ow}×${geo.oh}`]);
  }
  if (x.koe && x.koe.kuvaus) rivit.push([t("fakta.laatu_lahde"), laatuTeksti(x.koe.kuvaus) + (x.koe.vaihtelee ? ` · ${t("laatu.vaihtelee")}` : "")]);
  const huomiot = (an.huomiot || []).map(h => `<p class="huomio">${t("huomio." + h)}</p>`).join("")
    + (x.tila === "hd" ? `<p class="huomio">${t("huomio.hd")}</p>` : "");
  return `<dl class="faktat num">${rivit.map(([a, b]) => `<div><dt>${a}</dt><dd>${b}</dd></div>`).join("")}</dl>${huomiot}`;
}

function tyoHtml(x) {
  const n = nykyinenAsetus(x);
  const nk = nakyma(x.id);
  // käyttäjän säätämättömät arvot seuraavat oletuksia (ne tarkentuvat, kun lähde on mitattu)
  if (nk.saato === null || (!nk.muokattu && !(x.valinta || {}).saato)) nk.saato = Object.assign(oletusSaato(n, x), (x.valinta || {}).saato || {});
  const otsikko = `<div class="tyo-otsikko"><div class="tyo-nimi"><h1 title="${esc(x.polku)}">${esc(x.nimi)}</h1>
      ${x.analyysi ? faktatHtml(x) : `<div class="polku">${esc(x.polku)}</div>`}</div>
    <div class="tyo-toiminnot"><button class="painike pieni haamu" id="poista">${t("toiminto.poista")}</button>${toimintoHtml(x)}</div></div>`;
  let tila = "";
  if (x.tila === "kasitellaan" || (x.tila === "keskeytetty" && x.edistys)) {
    tila = `<div class="kaynnissa" id="kaynnissa"><div class="prosentti" id="k-pros">–</div>
      <div class="rivit"><span id="k-jaljella"></span><span id="k-nopeus" class="num"></span></div>
      <div class="edistys"><i id="k-palkki"></i></div></div>`;
  } else if (x.tila === "valmis" && x.tulos) {
    const r = x.tulos;
    const lisat = [];
    if (onKuva(x) && r.koko) lisat.push(t("valmis.koko", {w: r.koko[0], h: r.koko[1]}));
    if (r.aika_s) lisat.push(t("valmis.aika", {aika: kesto(r.aika_s)}));
    if ((r.ulkoiset_tekstitykset || []).length) lisat.push(t("valmis.tekstit", {t: r.ulkoiset_tekstitykset.join(", ")}));
    if (r.huomio) lisat.push(t("valmis.huomio." + r.huomio));
    const napit = `<button class="painike ${onKuva(x) ? "toissijainen" : "ensisijainen"}" id="nayta-kansio">${t("toiminto.nayta")}</button>`
      + (onKuva(x) ? `<button class="painike ensisijainen" id="avaa-kuva">${t("toiminto.avaa_kuva")}</button>` : "");
    tila = `<div class="valmis-kortti"><div class="valmis-teksti"><h3>${t("valmis.otsikko")}</h3>
        <p class="polku-rivi" title="${esc(r.ulos)}">${esc(r.ulos)}</p><p>${lisat.map(esc).join(" ")}</p></div>
      <div class="toiminnot">${napit}</div></div>`;
  } else if (x.tila === "virhe" && x.virhe) {
    tila = `<div class="virhe-kortti"><h3>${t("virhe.otsikko")}</h3><p>${t("virhe." + x.virhe.avain)}</p></div>`;
  }

  if (!x.analyysi) {
    return `${otsikko}${tila}<div class="tyoalue ilman-paneelia"><div class="katselu"><div class="vertailu"><div class="vertailu-tyhja"><div>${x.tila === "virhe" ? "" : t(onKuva(x) ? "vertailu.analysoidaan_kuva" : "vertailu.analysoidaan")}
      ${x.tila === "virhe" ? "" : '<div class="edistys epamaarainen"><i></i></div>'}</div></div></div></div></div>`;
  }
  const muokattava = !["kasitellaan", "jonossa", "valmis", "hd"].includes(x.tila) || (onKuva(x) && x.tila === "valmis");
  const paneeli = x.tila === "hd" ? "" : paneeliHtml(x, n, muokattava);
  return `${otsikko}${tila}
    <div class="tyoalue ${paneeli ? "" : "ilman-paneelia"}">
      <div class="katselu">
        <div class="vertailu ${nk.zoom === "todellinen" ? "zoom" : ""} ${x.koe && x.koe.siirretaan ? "laskee" : ""}" id="vertailu">${vertailuHtml(x)}<div class="lataa-huntu"></div></div>
        ${x.koe && x.koe.tila === "valmis" ? ohjaimetHtml(x) + aikajanaHtml(x) : ""}
      </div>
      ${paneeli}
    </div>`;
}

function vertailuHtml(x) {
  const k = x.koe || {};
  if (k.tila === "valmis") {
    return `<div class="nayttamo" id="nayttamo"><img id="kuva-o" alt=""><div class="vasen"><img id="kuva-v" alt=""></div>
      <div class="jako"></div><span class="lappu v" id="lappu-v">${t("vertailu.alkuperainen")}</span><span class="lappu o" id="lappu-o"></span></div>`;
  }
  if (!TILA.ymparisto.valmis) return `<div class="vertailu-tyhja"><div>${t("vertailu.odottaa_asennusta")}</div></div>`;
  if (k.tila === "virhe") return `<div class="vertailu-tyhja"><div>${t(String(k.tieto || "").includes("mallin_lataus") ? "virhe.mallin_lataus" : "vertailu.virhe")}<br><br><button class="painike toissijainen" id="koe-uudelleen">${t("vertailu.uudelleen")}</button></div></div>`;
  return `<div class="vertailu-tyhja"><div>${t("vertailu.luodaan")}<div class="edistys"><i id="koe-palkki"></i></div></div></div>`;
}

function ohjaimetHtml(x) {
  const nk = nakyma(x.id);
  const nappi = (ryhma, arvo, teksti, val) => `<button type="button" data-${ryhma}="${arvo}" aria-pressed="${val === arvo}">${teksti}</button>`;
  return `<div class="ohjaimet">
    <span class="ryhma" role="group">${nappi("tila", "jakaja", t("vertailu.jakaja"), nk.tila)}${nappi("tila", "vuoro", t("vertailu.vuorottele"), nk.tila)}</span>
    <span class="ryhma" role="group">${nappi("zoom", "sovita", t("vertailu.sovita"), nk.zoom)}${nappi("zoom", "todellinen", t("vertailu.todellinen"), nk.zoom)}</span>
    <span class="vihje" id="vertailu-vihje">${nk.tila === "vuoro" ? t("vertailu.vihje_vuoro") : t("vertailu.vihje_jakaja")}</span>
    ${onKuva(x) ? "" : `<button type="button" class="painike toissijainen pieni" id="liike-nappi">${t("vertailu.liike")}</button>`}
  </div>`;
}

function aikaTeksti(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
function aikajanaHtml(x) {
  if (onKuva(x) || !x.koe || !x.koe.kohdat || !(x.analyysi.kesto > 0)) return "";
  const nk = nakyma(x.id), K = x.analyysi.kesto;
  // lähekkäiset merkit eri korkeudelle, jotta kaikkia voi napsauttaa
  const ala = x.koe.kohdat.map((k, i) => x.koe.kohdat.some((m_, j) => j < i && Math.abs(m_.t - k.t) < 0.03 * K));
  const merkit = x.koe.kohdat.map((k, i) => `<button type="button" class="aj-merkki ${nk.kohta === i + 1 ? "valittu" : ""} ${k.laji} ${ala[i] ? "ala" : ""}"
      data-kohta="${i + 1}" style="left:${Math.min(100, 100 * k.t / K)}%" aria-pressed="${nk.kohta === i + 1}"
      title="${esc(t("aikajana.merkki_" + k.laji, {k: i + 1, aika: aikaTeksti(k.t)}))}">${i + 1}</button>`).join("");
  const valittu = x.koe.kohdat[nk.kohta - 1] || x.koe.kohdat[0];
  return `<div class="aikajana">
    <div class="aj-palkki" id="aj-palkki" title="${esc(t("aikajana.ohje", {k: nk.kohta}))}"><i class="aj-osoitin" id="aj-osoitin"></i>${merkit}</div>
    <p class="selite" id="aj-selite">${t("aikajana.kohta_" + valittu.laji, {k: nk.kohta, aika: aikaTeksti(valittu.t)})} · ${t("aikajana.ohje", {k: nk.kohta})}</p></div>`;
}

function rajattuEsikatselu(x) {
  const al = (x.koe && x.koe.alueet) || [];
  return onKuva(x) && x.koe && x.koe.tila === "valmis" && x.geo && al.some(a => a[2] < x.geo.rajaus[2] || a[3] < x.geo.rajaus[3]);
}
function navigaattoriHtml(x) {
  if (!rajattuEsikatselu(x)) return "";
  const nk = nakyma(x.id), W = x.geo.rajaus[2], H = x.geo.rajaus[3];
  const pros = (a, b) => `${(100 * a / b).toFixed(3)}%`;
  const alueet = x.koe.alueet.map((a, i) => {
    // siirto kesken: merkki näytetään jo uudessa paikassa
    if (x.koe.siirretaan === i + 1 && nk.siirto && nk.siirto.k === i + 1) a = [nk.siirto.x, nk.siirto.y, a[2], a[3]];
    return `<button type="button" class="navi-alue ${nk.kohta === i + 1 ? "valittu" : ""}" data-navi="${i + 1}"
      style="left:${pros(a[0], W)};top:${pros(a[1], H)};width:${pros(a[2], W)};height:${pros(a[3], H)}" title="${esc(t("navi.kohta", {k: i + 1}))}"><span>${i + 1}</span></button>`;
  }).join("");
  return `<div class="navi"><h3>${t("navi.otsikko")}</h3>
    <div class="navi-kuva" id="navi" style="aspect-ratio:${W}/${H};width:min(100%, calc(190px * ${W} / ${H}))"><img src="${media(x.id, `koe_${x.koe.tunniste}/yleis.png`, x.koe.tunniste)}" alt="">${alueet}</div>
    <p class="selite">${t("navi.ohje", {k: nk.kohta})}</p></div>`;
}

function paneeliHtml(x, n, muokattava) {
  const nk = nakyma(x.id);
  const suositus = x.analyysi.suositus;
  const esivalinnat = (onKuva(x) ? [1, 2, 3, 4, 5] : [1, 2, 3, 4, 5, 6]).map(i => {
    const aika = arvioAika(x, i);
    return `<label class="esivalinta ${i === n ? "valittu" : ""}" title="${esc(asetusKuvaus(x, i))}">
      <input type="radio" name="asetus" value="${i}" ${i === n ? "checked" : ""} ${muokattava ? "" : "disabled"}>
      <span class="n">${asetusNimi(x, i)}${i === suositus ? `<span class="merkki-suositus">${t("asetus.suositus")}</span>` : ""}</span>
      <span class="aika num">${aika ? (aika < 60 ? (onKuva(x) ? t("kesto.s", {s: Math.max(1, Math.round(aika))}) : kesto(aika)) : t("asetus.aika", {aika: kesto(aika)})) : ""}</span>
      ${i === n ? `<span class="k">${asetusKuvaus(x, i)}</span>` : ""}</label>`;
  }).join("");
  const s = nk.saato;
  const aiKaytossa = n !== 1;
  const liuku = (avain, arvo, max, kaytossa = true) => `<div class="saato"><label for="s-${avain}"><span>${t("saato." + avain)}</span><output id="o-${avain}" class="num">${luku(arvo * 100)} %</output></label>
    <input type="range" id="s-${avain}" data-saato="${avain}" min="0" max="${max}" step="0.05" value="${arvo}" ${kaytossa && muokattava ? "" : "disabled"}></div>`;
  const darAuto = x.analyysi.dar_levy ? x.analyysi.dar_levy.replace("/", ":") : "";
  const dar = (x.valinta || {}).dar || "";
  let lisavalinta;
  if (onKuva(x)) {
    const va = (x.geo && x.geo.vaihtoehdot) || {};
    const valittuSk = (x.geo && x.geo.skaala) || "2";
    const kierto = (x.valinta || {}).kierto || 0, muoto = (x.valinta || {}).muoto || "";
    const autoMuoto = ["jpeg", "heic"].includes(x.analyysi.muoto) ? "JPEG" : "PNG";
    const ei = muokattava ? "" : "disabled";
    lisavalinta = `<div class="osio valinnat">
      <div class="valinta-rivi"><label for="skaala">${t("koko.otsikko")}</label>
      <select id="skaala" ${ei} title="${esc(t("koko.selite"))}">${["1", "2", "4", "naytto"].filter(v => va[v]).map(v =>
        `<option value="${v}" ${v === valittuSk ? "selected" : ""} ${va[v].ok ? "" : "disabled"}>${esc(skaalaTeksti(v, va[v].ow, va[v].oh))}${va[v].ok ? "" : " – " + t("koko.liian_suuri")}</option>`).join("")}</select></div>
      <div class="valinta-rivi"><label>${t("kierto.otsikko")}</label><span class="kierto-rivi">
        <button type="button" class="painike toissijainen pieni" data-kierto="-90" ${ei} title="${esc(t("kierto.vasen"))}" aria-label="${esc(t("kierto.vasen"))}">↺</button>
        <button type="button" class="painike toissijainen pieni" data-kierto="90" ${ei} title="${esc(t("kierto.oikea"))}" aria-label="${esc(t("kierto.oikea"))}">↻</button>
        <span class="selite">${kierto ? `${kierto}°` : t("kierto.ei")}</span></span></div>
      <div class="valinta-rivi"><label for="muoto">${t("muoto.otsikko")}</label>
      <select id="muoto" ${ei}><option value="" ${!muoto ? "selected" : ""}>${t("muoto.auto", {m: autoMuoto})}</option>
        <option value="jpg" ${muoto === "jpg" ? "selected" : ""}>JPEG</option><option value="png" ${muoto === "png" ? "selected" : ""}>${t("muoto.png")}</option></select></div>
      <div class="valinta-rivi vanha"><label>${t("vanha.otsikko")}</label><span class="valintaruudut">
        <label class="ruutu" title="${esc(t("vanha.naarmut_selite"))}"><input type="checkbox" id="naarmut" ${(x.valinta || {}).naarmut ? "checked" : ""} ${ei}> ${t("vanha.naarmut")}</label>
        <label class="ruutu" title="${esc(t("vanha.vari_selite"))}"><input type="checkbox" id="vari" ${(x.valinta || {}).vari ? "checked" : ""} ${ei}> ${t("vanha.vari")}</label>
      </span></div>
      ${x.analyysi.harmaa && !(x.valinta || {}).vari ? `<p class="selite vihje-vari">${t("vanha.harmaa_vihje")}</p>` : ""}
    </div>`;
  } else {
    const laatu = (x.valinta || {}).laatu || "";
    lisavalinta = `<div class="osio valinnat"><div class="valinta-rivi"><label for="dar">${t("kuvasuhde.otsikko")}</label>
      <select id="dar" ${muokattava ? "" : "disabled"}>
        <option value="" ${!dar ? "selected" : ""}>${t("kuvasuhde.auto", {v: darAuto})}</option>
        <option value="4:3" ${dar === "4:3" ? "selected" : ""}>${t("kuvasuhde.43")}</option>
        <option value="16:9" ${dar === "16:9" ? "selected" : ""}>${t("kuvasuhde.169")}</option></select></div>
      <div class="valinta-rivi"><label for="laatu">${t("vlaatu.otsikko")}</label>
      <select id="laatu" ${muokattava ? "" : "disabled"} title="${esc(t("vlaatu.selite"))}"><option value="" ${!laatu ? "selected" : ""}>${t("vlaatu.paras")}</option>
        <option value="pieni" ${laatu === "pieni" ? "selected" : ""}>${t("vlaatu.pieni")}</option></select></div></div>
      ${raidatHtml(x, muokattava)}`;
  }
  const saadot = `<div class="osio saadot"><div class="osio-otsikko"><h3>${t("saato.otsikko")}</h3>
      <button type="button" class="linkki" id="palauta" ${muokattava ? "" : "disabled"}>${t("saato.palauta")}</button></div>
      ${x.koe && x.koe.oletukset ? `<p class="selite sovitettu">${t("saato.sovitettu")}</p>` : ""}
      ${liuku("kohina", s.kohina ?? 0, 1.5, aiKaytossa)}${liuku("ai", s.ai ?? 1, 1, aiKaytossa)}${liuku("halo", s.halo, 1.5)}${liuku(onKuva(x) ? "rae_kuva" : "rae", s.rae, 1.5)}
      ${onKuva(x) ? liuku("kasvot", s.kasvot ?? 0, 1) + `<p class="selite saato-selite">${t("saato.kasvot_selite")}</p>` : ""}
      <p class="selite mallilataus" id="mallilataus" hidden></p></div>`;
  const asetukset = `<div class="osio"><h3>${t("asetus.otsikko")}</h3><div class="esivalinnat" id="esivalinnat">${esivalinnat}</div></div>`;
  // muut saman lajin käsittelemättömät tiedostot: asetukset voi kopioida kerralla kaikkiin
  const muita = TILA.jono.tyot.filter(y => y.id !== x.id && y.laji === x.laji && ["odottaa", "virhe", "keskeytetty"].includes(y.tila)
    && !(y.tila === "keskeytetty" && y.aloitettu)).length;
  const kaikkiin = muokattava && muita ? `<button type="button" class="painike toissijainen pieni kaikkiin" id="kaikkiin" title="${esc(t("kaikkiin.selite"))}">${t(onKuva(x) ? "kaikkiin.kuvat" : "kaikkiin.videot", {n: muita})}</button>` : "";
  // kuvissa koko ja yleiskuva ensin (tärkeimmät valinnat), videoissa kuvasuhde lopussa (harvoin tarpeen)
  return `<div class="paneeli">${onKuva(x) ? navigaattoriHtml(x) + lisavalinta + asetukset + saadot : asetukset + saadot + lisavalinta}${kaikkiin}</div>`;
}

function toimintoHtml(x) {
  if (!x.analyysi || x.tila === "hd") return "";
  const aika = arvioAika(x, nykyinenAsetus(x));
  const arvio = aika && ["odottaa", "virhe", "jonossa"].includes(x.tila) ? `<span class="arvio num">${t("toiminto.arvio", {aika: onKuva(x) && aika < 60 ? t("kesto.s", {s: Math.max(1, Math.round(aika))}) : kesto(aika)})}</span>` : "";
  const nappi = (luokka, toiminto, teksti) => `<button class="painike ${luokka}" id="paa-toiminto" data-toiminto="${toiminto}">${teksti}</button>`;
  let html = "";
  if (x.tila === "odottaa" || x.tila === "virhe") html = nappi("ensisijainen iso", "aloita", t("toiminto.kasittele"));
  else if (x.tila === "keskeytetty") html = nappi("ensisijainen iso", "aloita", t("toiminto.jatka"));
  else if (x.tila === "jonossa") html = nappi("toissijainen iso", "keskeyta", t("toiminto.jonossa"));
  else if (x.tila === "kasitellaan") html = nappi("vaara iso", "keskeyta", t("toiminto.keskeyta"));
  else if (x.tila === "valmis" && onKuva(x)) html = nappi("toissijainen iso", "aloita", t("toiminto.uudelleen"));
  const jatkuu = ["kasitellaan", "keskeytetty"].includes(x.tila) && !onKuva(x) ? `<span class="arvio">${t("toiminto.jatkuu")}</span>` : "";
  return html ? `<div class="paa-toiminto">${html}${arvio}${jatkuu}</div>` : "";
}

/* ------------------------------------------------------------ vertailun kuvat */
function saatoPoikkeaa(x, n, s) {
  const o = oletusSaato(n, x);
  return ["ai", "rae", "halo", "kohina", "kasvot"].some(k => o[k] != null && s[k] != null && Math.abs(o[k] - s[k]) > 0.001);
}
let saatoAjastin = null;
function paivitaKuvat(x, viive = 0) {
  const k = x.koe; if (!k || k.tila !== "valmis") return;
  const nk = nakyma(x.id), n = nykyinenAsetus(x), tun = k.tunniste + "." + (k.versio || 0);
  const v = $("#kuva-v"), o = $("#kuva-o"); if (!v || !o) return;
  v.src = media(x.id, `koe_${k.tunniste}/v1_k${nk.kohta}.png`, tun);
  $("#lappu-o").textContent = saatoPoikkeaa(x, n, nk.saato) ? t("vertailu.saadot_lappu", {n: asetusNimi(x, n)}) : asetusNimi(x, n);
  $("#lappu-v").classList.toggle("nyt", nk.tila === "vuoro" && !nk.oikea);
  $("#lappu-o").classList.toggle("nyt", nk.tila === "vuoro" && nk.oikea);
  if (!saatoPoikkeaa(x, n, nk.saato)) { o.src = media(x.id, `koe_${k.tunniste}/v${n}_k${nk.kohta}.png`, tun); return; }
  const avain = JSON.stringify([n, nk.saato, nk.kohta, k.versio || 0]);
  if (nk.saatoUrl[avain]) { o.src = media(x.id, nk.saatoUrl[avain], tun); return; }
  clearTimeout(saatoAjastin);
  saatoAjastin = setTimeout(async () => {
    $("#vertailu")?.classList.add("laskee");
    try {
      const r = await api("saatokuva", {id: x.id, asetus: n, saato: nk.saato, k: nk.kohta});
      nk.saatoUrl[avain] = r.url;
      // vastaus voi tulla vasta, kun käyttäjä on jo siirtynyt toiseen tiedostoon: näytetään vain oikealle
      const nyt = TILA.jono.tyot.find(y => y.id === valittu);
      if (valittu === x.id && nyt && nyt.koe && nyt.koe.tunniste === k.tunniste
          && JSON.stringify([nykyinenAsetus(nyt), nk.saato, nk.kohta, (nyt.koe.versio || 0)]) === avain) $("#kuva-o").src = media(x.id, r.url, tun);
    } catch (e) { /* ilmoitettu */ }
    if (valittu === x.id) $("#vertailu")?.classList.remove("laskee");
  }, viive);
}
function paivitaNayttamo(x) {
  const nk = nakyma(x.id), st = $("#nayttamo"); if (!st) return;
  st.style.setProperty("--x", nk.x + "%");
  st.classList.toggle("vuoro", nk.tila === "vuoro");
  st.classList.toggle("oikea-nakyy", nk.tila === "vuoro" && nk.oikea);
  const img = $("#kuva-o");
  img.onload = sovitaNayttamo; sovitaNayttamo();
  $("#lappu-v")?.classList.toggle("nyt", nk.tila === "vuoro" && !nk.oikea);
  $("#lappu-o")?.classList.toggle("nyt", nk.tila === "vuoro" && nk.oikea);
}

/* Sovita: koko kuva näkyy kerralla (leveys ja korkeus). 100 %: yksi kuvan pikseli = yksi näytön pikseli. */
function sovitaNayttamo() {
  const st = $("#nayttamo"), ve = $("#vertailu"), img = $("#kuva-o");
  if (!st || !ve || !img || !img.naturalWidth || !valittu) return;
  const nk = nakyma(valittu);
  if (nk.zoom === "todellinen") { st.style.width = img.naturalWidth / devicePixelRatio + "px"; return; }
  const s = Math.min((ve.clientWidth - 2) / img.naturalWidth, (ve.clientHeight - 2) / img.naturalHeight);
  st.style.width = Math.max(40, Math.floor(img.naturalWidth * s)) + "px";
}
let vertailuSeuraaja = null;

/* ------------------------------------------------------------ tapahtumat */
function kytkeTyo(x) {
  const id = x.id;
  $("#poista")?.addEventListener("click", async () => { await api("poista", {id}); valittu = null; hae(); });
  $("#nayta-kansio")?.addEventListener("click", () => api("avaa", {id}));
  $("#koe-uudelleen")?.addEventListener("click", () => api("esikatsele", {id}).then(hae));
  $("#paa-toiminto")?.addEventListener("click", async e => {
    const toiminto = e.currentTarget.dataset.toiminto;
    await api(toiminto, {id}).catch(() => {});
    hae();
  });
  $$('#esivalinnat input[name="asetus"]').forEach(r => r.addEventListener("change", async () => {
    const n = Number(r.value);
    const nk = nakyma(id);
    nk.saato = oletusSaato(n, TILA.jono.tyot.find(y => y.id === id));
    nk.muokattu = false;
    await api("valinta", {id, asetus: n, saato: null}).catch(() => {});
    hae();
  }));
  $$("[data-saato]").forEach(el => {
    el.addEventListener("input", () => {
      const nk = nakyma(id);
      nk.saato[el.dataset.saato] = Number(el.value);
      nk.muokattu = true;
      $(`#o-${el.dataset.saato}`).textContent = `${luku(Number(el.value) * 100)} %`;
      const xx = TILA.jono.tyot.find(y => y.id === id); if (xx) paivitaKuvat(xx, 280);
    });
    el.addEventListener("change", () => tallennaSaato(id));
  });
  $("#palauta")?.addEventListener("click", () => {
    const xx = TILA.jono.tyot.find(y => y.id === id);
    const nk = nakyma(id); nk.saato = oletusSaato(nykyinenAsetus(xx), xx); nk.muokattu = false;
    edellinenTyoAllekirjoitus = ""; tallennaSaato(id); piirraTyo();
  });
  $("#dar")?.addEventListener("change", async e => { await api("valinta", {id, dar: e.target.value || null}).catch(() => {}); hae(); });
  $("#skaala")?.addEventListener("change", async e => {
    const nk = nakyma(id); nk.saatoUrl = {}; nk.kohta = 1;
    await api("valinta", {id, skaala: e.target.value}).catch(() => {}); hae();
  });
  $("#avaa-kuva")?.addEventListener("click", () => api("avaa", {id, mita: "tiedosto"}).catch(() => {}));
  $$("[data-kierto]").forEach(b => b.addEventListener("click", async () => {
    const xx = TILA.jono.tyot.find(y => y.id === id);
    const uusi = ((((xx.valinta || {}).kierto || 0) + Number(b.dataset.kierto)) % 360 + 360) % 360;
    const nk = nakyma(id); nk.saatoUrl = {}; nk.kohta = 1;
    await api("valinta", {id, kierto: uusi}).catch(() => {}); hae();
  }));
  $("#muoto")?.addEventListener("change", async e => { await api("valinta", {id, muoto: e.target.value || null}).catch(() => {}); hae(); });
  for (const k of ["naarmut", "vari"]) {
    $("#" + k)?.addEventListener("change", async e => {
      const nk = nakyma(id); nk.saatoUrl = {}; nk.kohta = 1;
      await api("valinta", {id, [k]: e.target.checked}).catch(() => {}); hae();
    });
  }
  $("#laatu")?.addEventListener("change", async e => { await api("valinta", {id, laatu: e.target.value || null}).catch(() => {}); hae(); });
  $("#raidat")?.addEventListener("toggle", e => { nakyma(id).raidatAuki = e.currentTarget.open; });
  if (nakyma(id).raidatAuki && $("#raidat")) $("#raidat").open = true;
  $$("[data-raita]").forEach(el => el.addEventListener("change", async () => {
    const xx = TILA.jono.tyot.find(y => y.id === id); if (!xx) return;
    const kaikki = laji => (laji === "aanet" ? xx.analyysi.aaniraidat : xx.analyysi.tekstiraidat) || [];
    const valitut = laji => $$(`[data-raita="${laji}"]`).filter(c => c.checked).map(c => Number(c.value));
    const d = {id};
    for (const laji of ["aanet", "tekstit"]) {
      if (!$$(`[data-raita="${laji}"]`).length) continue;
      const v = valitut(laji);
      d[laji] = v.length === kaikki(laji).length ? null : v;
    }
    const u = $$('[data-raita="ulkoiset"]');
    if (u.length) d.ulkoiset = u.every(c => c.checked) ? null : false;
    await api("valinta", d).catch(() => {}); hae();
  }));
  $("#kaikkiin")?.addEventListener("click", async () => {
    await tallennaSaato(id);
    const r = await api("valinta_kaikkiin", {id}).catch(() => null);
    if (r) { ilmoita(t("kaikkiin.tehty", {n: r.n})); Object.keys(nakymat).forEach(k => { if (k !== id) delete nakymat[k]; }); hae(); }
  });
  $$("[data-kohta]").forEach(b => b.addEventListener("click", e => {
    e.stopPropagation();
    nakyma(id).kohta = Number(b.dataset.kohta); paivitaOhjaimet(id); paivitaAikajana(id);
  }));
  const aj = $("#aj-palkki");
  if (aj) {
    const osuus = e => { const r = aj.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); };
    aj.addEventListener("mousemove", e => {
      const xx = TILA.jono.tyot.find(y => y.id === id); if (!xx) return;
      const o = $("#aj-osoitin"); o.style.left = (100 * osuus(e)) + "%"; o.dataset.aika = aikaTeksti(osuus(e) * xx.analyysi.kesto);
    });
    aj.addEventListener("click", e => {
      const xx = TILA.jono.tyot.find(y => y.id === id); if (!xx || !xx.koe || xx.koe.siirretaan) return;
      const nk = nakyma(id); nk.saatoUrl = {};
      xx.koe.siirretaan = nk.kohta;
      $("#vertailu")?.classList.add("laskee");
      api("kohta", {id, k: nk.kohta, aika: osuus(e) * xx.analyysi.kesto}).then(hae).catch(() => {});
    });
  }
  $$("[data-tila]").forEach(b => b.addEventListener("click", () => { const nk = nakyma(id); nk.tila = b.dataset.tila; nk.oikea = true; paivitaOhjaimet(id); }));
  $$("[data-zoom]").forEach(b => b.addEventListener("click", () => { nakyma(id).zoom = b.dataset.zoom; $("#vertailu").classList.toggle("zoom", b.dataset.zoom === "todellinen"); paivitaOhjaimet(id); }));
  if (vertailuSeuraaja) vertailuSeuraaja.disconnect();
  if ($("#vertailu") && window.ResizeObserver) { vertailuSeuraaja = new ResizeObserver(() => sovitaNayttamo()); vertailuSeuraaja.observe($("#vertailu")); }
  $("#navi")?.addEventListener("click", e => {
    const nk = nakyma(id);
    const alue = e.target.closest("[data-navi]");
    if (alue) {
      nk.kohta = Number(alue.dataset.navi);
      $$(".navi-alue").forEach(b => b.classList.toggle("valittu", b === alue));
      $(".navi .selite").textContent = t("navi.ohje", {k: nk.kohta});
      paivitaOhjaimet(id); return;
    }
    const xx = TILA.jono.tyot.find(y => y.id === id); if (!xx || !xx.koe || xx.koe.siirretaan) return;
    const r = e.currentTarget.getBoundingClientRect();
    const W = xx.geo.rajaus[2], H = xx.geo.rajaus[3];
    const px = (e.clientX - r.left) / r.width * W, py = (e.clientY - r.top) / r.height * H;
    const valittuAlue = $(`.navi-alue[data-navi="${nk.kohta}"]`), a = xx.koe.alueet[nk.kohta - 1];
    if (valittuAlue && a) {   // siirretään merkki heti, kuvat päivittyvät kun ne on laskettu
      nk.siirto = {k: nk.kohta, x: Math.max(0, Math.min(W - a[2], px - a[2] / 2)), y: Math.max(0, Math.min(H - a[3], py - a[3] / 2))};
      valittuAlue.style.left = `${100 * nk.siirto.x / W}%`;
      valittuAlue.style.top = `${100 * nk.siirto.y / H}%`;
    }
    $("#vertailu")?.classList.add("laskee");
    api("alue", {id, k: nk.kohta, x: px, y: py}).then(hae).catch(() => {});
  });
  $("#liike-nappi")?.addEventListener("click", () => {
    const xx = TILA.jono.tyot.find(y => y.id === id), nk = nakyma(id), n = nykyinenAsetus(xx);
    const saato = saatoPoikkeaa(xx, n, nk.saato) ? nk.saato : null;
    liikeOdottaa = {id, n, saato: JSON.stringify(saato)};
    api("liike", {id, asetus: n, saato}).then(hae).catch(() => {});
  });
  const st = $("#nayttamo");
  if (st) {
    let veto = false;
    const aseta = e => { const r = st.getBoundingClientRect(); nakyma(id).x = Math.min(100, Math.max(0, (e.clientX - r.left) / r.width * 100)); st.style.setProperty("--x", nakyma(id).x + "%"); };
    st.addEventListener("pointerdown", e => {
      const nk = nakyma(id);
      if (nk.tila === "vuoro") { nk.oikea = !nk.oikea; paivitaNayttamo(TILA.jono.tyot.find(y => y.id === id)); return; }
      veto = true; st.setPointerCapture(e.pointerId); aseta(e);
    });
    st.addEventListener("pointermove", e => { if (veto) aseta(e); });
    st.addEventListener("pointerup", () => { veto = false; });
    paivitaKuvat(x); paivitaNayttamo(x);
  }
}
function paivitaAikajana(id) {
  const x = TILA.jono.tyot.find(y => y.id === id); if (!x || !x.koe || !x.koe.kohdat) return;
  const nk = nakyma(id), k = x.koe.kohdat[nk.kohta - 1]; if (!k) return;
  $$(".aj-merkki").forEach(b => { const v = Number(b.dataset.kohta) === nk.kohta; b.classList.toggle("valittu", v); b.setAttribute("aria-pressed", String(v)); });
  const s = $("#aj-selite"); if (s) s.textContent = `${t("aikajana.kohta_" + k.laji, {k: nk.kohta, aika: aikaTeksti(k.t)})} · ${t("aikajana.ohje", {k: nk.kohta})}`;
  const p = $("#aj-palkki"); if (p) p.title = t("aikajana.ohje", {k: nk.kohta});
}
function paivitaOhjaimet(id) {
  const x = TILA.jono.tyot.find(y => y.id === id); if (!x) return;
  const nk = nakyma(id);
  $$("[data-kohta]").forEach(b => b.setAttribute("aria-pressed", String(nk.kohta) === b.dataset.kohta));
  $$("[data-tila]").forEach(b => b.setAttribute("aria-pressed", nk.tila === b.dataset.tila));
  $$("[data-zoom]").forEach(b => b.setAttribute("aria-pressed", nk.zoom === b.dataset.zoom));
  const v = $("#vertailu-vihje"); if (v) v.textContent = nk.tila === "vuoro" ? t("vertailu.vihje_vuoro") : t("vertailu.vihje_jakaja");
  paivitaKuvat(x); paivitaNayttamo(x);
}
async function tallennaSaato(id) {
  const x = TILA.jono.tyot.find(y => y.id === id); if (!x) return;
  const nk = nakyma(id), n = nykyinenAsetus(x);
  const s = saatoPoikkeaa(x, n, nk.saato) ? Object.fromEntries(Object.entries(nk.saato).filter(([, v]) => v != null)) : null;
  await api("valinta", {id, saato: s}).catch(() => {});
}

let liikeOdottaa = null;
function paivitaDynaamiset(x) {
  // lisämallin lataus (esim. kasvomallit ensimmäisellä käytöllä)
  const ml = $("#mallilataus");
  if (ml) {
    const l = Object.values(TILA.mallit || {}).map(m => m.lataus).find(l => l && !l.virhe);
    ml.hidden = !l;
    if (l) ml.textContent = t("malli.ladataan", {p: luku(100 * l.tehty / Math.max(1, l.koko))});
  }
  // esikatselun edistyminen
  const kp = $("#koe-palkki");
  if (kp) kp.style.width = `${Math.round(100 * ((x.koe && x.koe.edistys) || 0))}%`;
  // käsittelyn edistyminen
  if ($("#kaynnissa")) {
    const e = x.edistys || {};
    const pros = e.kaikki ? 100 * e.tehty / e.kaikki : 0;
    $("#k-pros").textContent = `${luku(Math.min(99.9, pros), pros < 10 ? 1 : 0)} %`;
    $("#k-palkki").style.width = pros + "%";
    $("#k-jaljella").innerHTML = e.yhdistetaan ? t("kaynnissa.yhdistetaan") : e.tallennetaan ? t("kaynnissa.tallennetaan")
      : e.jaljella_s != null ? t("kaynnissa.jaljella", {aika: esc(onKuva(x) && e.jaljella_s < 60 ? t("kesto.s", {s: Math.max(1, Math.round(e.jaljella_s))}) : kesto(e.jaljella_s))})
      : t(onKuva(x) ? "kaynnissa.kasitellaan_kuva" : "kaynnissa.alkaa");
    $("#k-nopeus").textContent = e.nopeus ? t("kaynnissa.nopeus", {n: luku(e.nopeus, 1)}) : "";
  }
  // liikepätkä
  const ln = $("#liike-nappi");
  if (ln && x.liike) {
    const l = x.liike;
    if (l.tila === "jonossa" || l.tila === "kaynnissa") { ln.disabled = true; ln.textContent = t("vertailu.liike_luodaan", {p: `${luku(100 * (l.edistys || 0))} %`}); }
    else { ln.disabled = false; ln.textContent = t("vertailu.liike"); }
    if (l.tila === "valmis" && liikeOdottaa && liikeOdottaa.id === x.id) {
      liikeOdottaa = null;
      $("#liike-otsikko").textContent = `${x.nimi} – ${asetusNimi(x, l.asetus)}`;
      const vid = $("#liike-video");
      vid.src = media(x.id, l.url, l.url);
      $("#liike").showModal(); vid.play().catch(() => {});
    }
  }
}

/* ------------------------------------------------------------ alapalkki */
function piirraAlapalkki() {
  const j = TILA.jono;
  const valmiina = j.tyot.filter(x => ["odottaa", "keskeytetty"].includes(x.tila) && x.analyysi);
  const kesken = j.tyot.filter(x => ["jonossa", "kasitellaan"].includes(x.tila));
  let teksti;
  if (kesken.length) {
    const kaikki = kesken.length + j.tyot.filter(x => x.tila === "valmis" && x.valmistui && x.valmistui > (Date.now() / 1000 - 86400)).length;
    const i = Math.max(1, kaikki - kesken.length + 1);
    teksti = t("jono.yhteenveto_kaynnissa", {i, n: kaikki, aika: esc(kesto(j.jono_jaljella_s))});
  } else if (valmiina.length) teksti = valmiina.length === 1 ? t("jono.yhteenveto_yksi_valmiina") : t("jono.yhteenveto_valmiina", {n: valmiina.length});
  else teksti = t("jono.yhteenveto_tyhja");
  if (j.sammutus_ajastettu) teksti += ` <strong>${t("jono.sammutus_ajastettu")}</strong> <button class="painike pieni toissijainen" id="peru-sammutus">${t("jono.peru_sammutus")}</button>`;
  const yv = $("#jono-yhteenveto");
  if (yv.dataset.html !== teksti) { yv.innerHTML = teksti; yv.dataset.html = teksti; $("#peru-sammutus")?.addEventListener("click", () => api("peru_sammutus").then(hae)); }
  const nappi = $("#jono-nappi");
  if (kesken.length) { nappi.textContent = t("jono.keskeyta_kaikki"); nappi.className = "painike vaara"; nappi.dataset.toiminto = "keskeyta"; nappi.disabled = false; }
  else { nappi.textContent = t("jono.kasittele_kaikki"); nappi.className = "painike ensisijainen"; nappi.dataset.toiminto = "aloita"; nappi.disabled = !valmiina.length; }
  const s = $("#sammuta");
  if (document.activeElement !== s) s.checked = !!j.sammuta_valmiina;
}

/* ------------------------------------------------------------ asetukset-dialogi */
function avaaAsetukset() {
  const a = TILA.jono.asetukset, k = TILA.jono.kohde;
  const kohteet = [["auto", t("asetukset.kohde_auto", {v: `${k[0]}×${k[1]}`})], ["1920x1080", "1920×1080 (Full HD)"],
    ["2560x1440", "2560×1440 (QHD)"], ["3840x2160", "3840×2160 (4K)"]];
  $("#kohde-vaihtoehdot").innerHTML = kohteet.map(([v, n]) => `<label class="radio"><input type="radio" name="kohde" value="${v}" ${(a.kohde || "auto") === v ? "checked" : ""}><span>${esc(n)}</span></label>`).join("");
  $$('input[name="tallennus"]').forEach(r => { r.checked = (r.value === "kansio") === !!a.ulos_kansio; });
  $("#ulos-kansio").textContent = a.ulos_kansio || t("asetukset.ei_kansiota");
  $$('input[name="kieli"]').forEach(r => { r.checked = r.value === (a.kieli || "auto"); });
  const y = TILA.ymparisto, it = y.itsetesti || {};
  $("#tietoja-sisalto").innerHTML = `<p>${t("tietoja.versio", {v: esc(TILA.versio)})}<br>${t("tietoja.gpu", {g: esc((y.gpu && y.gpu.nimi) || "–")})}<br>${t("tietoja.data", {p: esc(TILA.datakansio)})}</p>
    <p>${t("tietoja.lisenssit")}</p><ul>
    <li>2xLiveActionV1_SPAN – jcj83429 (Apache 2.0)</li><li>2xVHS2HD – asterixcool (CC BY-SA 4.0)</li>
    <li>2x_AniSD_AC_G6i2b_SPAN – Sirosky (CC BY-NC 4.0)</li><li>PyTorch (BSD), NumPy (BSD), pywebview (BSD)</li>
    <li>FFmpeg (GPL, ladataan erikseen / downloaded separately)</li><li>Barlow-kirjasin / typeface – Jeremy Tribby (OFL)</li></ul>`;
  $("#asetukset").showModal();
}
function kytkeAsetukset() {
  $("#asetukset-nappi").addEventListener("click", avaaAsetukset);
  $("#asetukset").addEventListener("change", async e => {
    const el = e.target;
    if (el.name === "kohde") await api("asetukset", {kohde: el.value});
    if (el.name === "kieli") await api("asetukset", {kieli: el.value});
    if (el.name === "tallennus") {
      if (el.value === "viereen") { await api("asetukset", {ulos_kansio: null}); $("#ulos-kansio").textContent = t("asetukset.ei_kansiota"); }
      else if (!TILA.jono.asetukset.ulos_kansio) await valitseKansio();
    }
    await hae_kerran();
  });
  $("#valitse-kansio").addEventListener("click", valitseKansio);
  $("#avaa-loki").addEventListener("click", () => api("avaa", {mita: "loki"}));
}
async function valitseKansio() {
  const r = await api("valitse", {laji: "kansio"}).catch(() => ({}));
  if (r.kansio) { await api("asetukset", {ulos_kansio: r.kansio}); $("#ulos-kansio").textContent = r.kansio; $$('input[name="tallennus"]').forEach(x => { x.checked = x.value === "kansio"; }); }
  else if (!TILA.jono.asetukset.ulos_kansio) $$('input[name="tallennus"]').forEach(x => { x.checked = x.value === "viereen"; });
}
async function hae_kerran() {
  const r = await fetch("/api/tila", {headers: {"X-Pixeli-Avain": AVAIN}});
  if (r.ok) {
    TILA = await r.json();
    const k = TILA.jono.asetukset.kieli || "auto";
    const uusi = k === "auto" ? TILA.kieli_jarjestelma : k;
    if (uusi !== KIELI) { KIELI = uusi; kaannaStaattiset(); edellinenTyoAllekirjoitus = ""; if ($("#asetukset").open) avaaAsetukset(); }
    piirra();
  }
}

/* ------------------------------------------------------------ käynnistys */
async function valitseTiedostot() {
  const r = await api("valitse", {laji: "tiedostot"}).catch(() => null);
  lisaysTulos(r);
}
function kytke() {
  kytkeAsetukset();
  $("#lisaa").addEventListener("click", valitseTiedostot);
  $("#lisaa-kansio").addEventListener("click", async () => lisaysTulos(await api("valitse", {laji: "lisaa_kansio"}).catch(() => null)));
  $("#tyhjenna").addEventListener("click", () => api("tyhjenna_valmiit").then(hae_kerran));
  $("#jono-lista").addEventListener("click", e => {
    const li = e.target.closest(".kohta"); if (!li) return;
    valittu = li.dataset.id; edellinenTyoAllekirjoitus = ""; piirraJono(); piirraTyo();
  });
  $("#jono-lista").addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { const li = e.target.closest(".kohta"); if (li) { e.preventDefault(); li.click(); } }
  });
  $("#jono-nappi").addEventListener("click", async e => { await api(e.currentTarget.dataset.toiminto).catch(() => {}); hae_kerran(); });
  $("#sammuta").addEventListener("change", e => api("sammutus", {paalla: e.target.checked}));
  $("#asennus-aloita").addEventListener("click", async e => { await api(e.currentTarget.dataset.toiminto).catch(() => {}); hae_kerran(); });
  $("#asennus-loki").addEventListener("click", () => api("avaa", {mita: "loki"}));
  $("#liike").addEventListener("close", () => { const v = $("#liike-video"); v.pause(); v.removeAttribute("src"); v.load(); });

  // välilyönti vaihtaa vertailussa
  document.addEventListener("keydown", e => {
    if (e.code !== "Space" || !valittu || $("dialog[open]")) return;
    const ohjainpainike = e.target.tagName === "BUTTON" && e.target.closest(".ohjaimet");
    if (!ohjainpainike && /INPUT|SELECT|TEXTAREA|BUTTON|VIDEO/.test(e.target.tagName)) return;
    const st = $("#nayttamo"); if (!st) return;
    e.preventDefault();
    const nk = nakyma(valittu);
    if (nk.tila !== "vuoro") { nk.tila = "vuoro"; nk.oikea = false; } else nk.oikea = !nk.oikea;
    paivitaOhjaimet(valittu);
  });

  // vedä ja pudota: korostus (pywebview lisää tiedostot itse)
  let syvyys = 0;
  const onTiedostoja = e => [...(e.dataTransfer?.types || [])].includes("Files");
  document.addEventListener("dragenter", e => { if (!onTiedostoja(e)) return; e.preventDefault(); syvyys++; $("#pudotus").hidden = false; });
  document.addEventListener("dragover", e => { if (onTiedostoja(e)) e.preventDefault(); });
  document.addEventListener("dragleave", () => { syvyys = Math.max(0, syvyys - 1); if (!syvyys) $("#pudotus").hidden = true; });
  document.addEventListener("drop", e => {
    e.preventDefault(); syvyys = 0; $("#pudotus").hidden = true;
    if (TILA && TILA.ikkuna !== "pywebview") ilmoita(t("jono.pudota_vihje_selain"));
  });
}

kytke();
hae();
