// Builds docs/StaleGuard_pitch.pptx. Run: npm i pptxgenjs && node scripts/build_deck.js
const pptxgen = require("pptxgenjs");
const pres = new pptxgen(); pres.layout = "LAYOUT_WIDE"; pres.theme = { headFontFace: "Cambria", bodyFontFace: "Calibri" };
pres.title = "StaleGuard - Stale-Answer Detector"; pres.author = "Ishan Ahirwar";
pres.defineSlideMaster({ title: "DARK", background: { color: "1D1B16" },
  objects: [{ placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.2, w: 11.7, h: 1.4, fontSize: 54, bold: true, color: "FFFFFF", fontFace: "Cambria" }, text: "" } },
            { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 3.7, w: 11.7, h: 1.6, fontSize: 22, color: "F3F1EC", fontFace: "Calibri" }, text: "" } }] });
pres.defineSlideMaster({ title: "CONTENT", background: { color: "FFFFFF" }, slideNumber: { x: 12.4, y: 7.0, fontSize: 10, color: "8C857A" },
  objects: [{ placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: 12.1, h: 0.9, fontSize: 34, bold: true, color: "1D1B16", fontFace: "Cambria", valign: "top" }, text: "" } },
            { text: { text: "StaleGuard · Global AI Hackathon", options: { x: 0.6, y: 7.0, w: 6, h: 0.3, fontSize: 10, color: "8C857A" } } }] });

const tb = (s, text, o) => s.addText(text, Object.assign({ isTextBox: true, fontFace: "Calibri", color: "1D1B16", valign: "top", margin: 0 }, o));
const card = (s, x, y, w, h, fill) => s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.12, fill: { color: fill || "F3F1EC" }, line: { color: "F3F1EC" } });
const dot = (s, x, y, color, label) => { s.addShape(pres.shapes.OVAL, { x, y, w: 0.55, h: 0.55, fill: { color } }); tb(s, label, { x, y: y + 0.08, w: 0.55, h: 0.4, align: "center", bold: true, color: "FFFFFF", fontSize: 16 }); };

// 1 title
let s = pres.addSlide({ masterName: "DARK" });
s.addText([{ text: "Stale", options: {} }, { text: "Guard", options: { color: "B3261E" } }], { placeholder: "title" });
s.addText("Catch the facts in AI answers that have quietly gone out of date — before your users act on them.\nTavily for live re-verification · Bifrost for resilient, budgeted LLM routing", { placeholder: "body" });
tb(s, "Global AI Hackathon · Problem statement: Build a Stale-Answer Detector for AI Applications · Ishan Ahirwar (team isopod)", { x: 0.8, y: 6.4, w: 11.7, h: 0.4, fontSize: 13, color: "8C857A" });
s.addNotes("StaleGuard audits LLM answers for outdated claims and fixes them with cited, live sources.");

// 2 problem
s = pres.addSlide({ masterName: "CONTENT" }); s.addText("LLMs answer confidently from a frozen snapshot", { placeholder: "title" });
const probs = [["Pricing", "Quotes last year's plan and price to a paying customer", "B3261E"],
  ["Versions", "Recommends EOL runtimes and old major releases", "A86400"],
  ["Regulation", "Calls a law \"still being negotiated\" after it passed", "2B4ACB"],
  ["Companies", "Names the previous CEO, old funding, old headcount", "5B6472"]];
probs.forEach(([h, d, col], i) => { const x = 0.6 + i * 3.08; card(s, x, 1.7, 2.85, 2.6); s.addShape(pres.shapes.OVAL, { x: x + 0.3, y: 1.95, w: 0.45, h: 0.45, fill: { color: col } });
  tb(s, h, { x: x + 0.3, y: 2.6, w: 2.3, h: 0.45, fontSize: 20, bold: true, fontFace: "Cambria" }); tb(s, d, { x: x + 0.3, y: 3.1, w: 2.3, h: 1.1, fontSize: 15, color: "2A2620" }); });
tb(s, "The answer still sounds right. Nothing in a normal chatbot stack checks whether it is still true.", { x: 0.6, y: 4.8, w: 12, h: 0.6, fontSize: 22, italic: true, fontFace: "Cambria" });
tb(s, "StaleGuard adds that check: per-claim freshness scores, live evidence, and a corrected answer.", { x: 0.6, y: 5.5, w: 12, h: 0.5, fontSize: 16, color: "2A2620" });

// 3 solution / pipeline
s = pres.addSlide({ masterName: "CONTENT" }); s.addText("How it works: extract, re-verify, score, rewrite", { placeholder: "title" });
const steps = [["1", "Extract claims", "Cheap model via Bifrost splits the answer into atomic claims with category, value, exact span and an unbiased search query. Timeless facts are skipped."],
  ["2", "Re-verify live", "Tavily searches each time-sensitive claim with recency windows and publish dates, in parallel."],
  ["3", "Adjudicate", "Strong model via Bifrost labels each source supports / contradicts and returns verdict + current value + confidence."],
  ["4", "Score & rewrite", "Explainable 0–100 freshness score per claim and per answer; stale spans corrected with citations."]];
steps.forEach(([n, h, d], i) => { const x = 0.6 + i * 3.08; card(s, x, 1.6, 2.85, 3.9); dot(s, x + 0.3, 1.85, "B3261E", n);
  tb(s, h, { x: x + 0.3, y: 2.6, w: 2.4, h: 0.5, fontSize: 19, bold: true, fontFace: "Cambria" }); tb(s, d, { x: x + 0.3, y: 3.15, w: 2.35, h: 2.2, fontSize: 14, color: "2A2620" }); });
tb(s, "If every provider fails, a deterministic judge (version / price / status comparison) takes over. With no keys at all, offline mode runs on a bundled snapshot.", { x: 0.6, y: 5.9, w: 12, h: 0.7, fontSize: 15, color: "2A2620" });

// 4 architecture: both techs
s = pres.addSlide({ masterName: "CONTENT" }); s.addText("Both sponsor technologies do real work", { placeholder: "title" });
card(s, 0.6, 1.6, 5.9, 4.9); card(s, 6.8, 1.6, 5.9, 4.9);
tb(s, "Tavily — what's true now", { x: 0.9, y: 1.85, w: 5.4, h: 0.5, fontSize: 22, bold: true, fontFace: "Cambria", color: "1F7A4D" });
tb(s, [
  { text: "One /search per time-sensitive claim, advanced depth", options: { bullet: true, breakLine: true } },
  { text: "Category-aware start_date windows so stale pages can't confirm stale claims", options: { bullet: true, breakLine: true } },
  { text: "News topic for company and leadership claims", options: { bullet: true, breakLine: true } },
  { text: "Published dates drive the evidence-recency score", options: { bullet: true, breakLine: true } },
  { text: "Query cache avoids paying twice for duplicates", options: { bullet: true } }], { x: 0.9, y: 2.55, w: 5.3, h: 3.8, fontSize: 15, paraSpaceAfter: 8 });
tb(s, "Bifrost — resilient, governed LLM calls", { x: 7.1, y: 1.85, w: 5.4, h: 0.5, fontSize: 22, bold: true, fontFace: "Cambria", color: "2B4ACB" });
tb(s, [
  { text: "Every LLM call goes through /v1/chat/completions", options: { bullet: true, breakLine: true } },
  { text: "Gemini Flash-Lite extracts, Gemini Flash judges", options: { bullet: true, breakLine: true } },
  { text: "Automatic fallback to Groq on errors and rate limits", options: { bullet: true, breakLine: true } },
  { text: "Virtual key (x-bf-vk) scoped to needed models, monthly budget", options: { bullet: true, breakLine: true } },
  { text: "Routing info surfaced in the UI call trace", options: { bullet: true } }], { x: 7.1, y: 2.55, w: 5.3, h: 3.8, fontSize: 15, paraSpaceAfter: 8 });

// 5 prototype screenshot
s = pres.addSlide({ masterName: "CONTENT" }); s.addText("Working prototype", { placeholder: "title" });
s.addImage({ path: require("path").join(__dirname, "..", "docs", "screenshot.jpg"), x: 0.6, y: 1.45, w: 7.7, h: 5.5, objectName: "UI screenshot", altText: "StaleGuard web UI showing a stale answer scored 14/100 with highlighted claims" });
tb(s, "Demo answer → 14/100, Stale", { x: 8.7, y: 1.6, w: 4.1, h: 0.5, fontSize: 20, bold: true, fontFace: "Cambria", color: "B3261E" });
tb(s, [
  { text: "Python 3.11 \"latest\" → now 3.14", options: { bullet: true, breakLine: true } },
  { text: "React 18 \"current major\" → now 19", options: { bullet: true, breakLine: true } },
  { text: "Node 18 \"active LTS\" → EOL, 24 is LTS", options: { bullet: true, breakLine: true } },
  { text: "EU AI Act \"being negotiated\" → in force since Aug 2024", options: { bullet: true, breakLine: true } },
  { text: "Copilot $10/mo → confirmed", options: { bullet: true, breakLine: true } },
  { text: "Guido created Python → timeless, skipped", options: { bullet: true } }], { x: 8.7, y: 2.25, w: 4.1, h: 3.2, fontSize: 14, paraSpaceAfter: 6 });
tb(s, "Web UI · REST API · CLI with --fail-under CI gate · OpenAI-compatible guarded endpoint", { x: 8.7, y: 5.6, w: 4.1, h: 1.0, fontSize: 13, color: "2A2620" });

// 6 scoring
s = pres.addSlide({ masterName: "CONTENT" }); s.addText("An explainable freshness score", { placeholder: "title" });
s.addChart(pres.charts.BAR, [{ name: "Half-life (days)", labels: ["Pricing", "API versions", "Company info", "Products", "Statistics", "Regulation"], values: [120, 180, 240, 240, 365, 540] }],
  { x: 0.6, y: 1.5, w: 6.2, h: 4.9, barDir: "bar", chartColors: ["B3261E"], showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelFontFace: "+mn-lt",
    showTitle: true, title: "Prior drift: days until a fact is 50% likely to have changed", titleFontSize: 13, titleFontFace: "+mn-lt", showLegend: false,
    catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt", catAxisLabelColor: "5B6472", valAxisLabelColor: "5B6472", valGridLine: { color: "E2DCCD", size: 0.5 }, catGridLine: { style: "none" }, catAxisOrientation: "maxMin" });
const comps = [["Prior risk", "1 − 0.5^(age / half-life)"], ["Evidence recency", "Newest relevant source date"], ["Source agreement", "Share of decisive sources agreeing"], ["Confidence", "Judge confidence, low → lean on prior"]];
comps.forEach(([h, d], i) => { card(s, 7.2, 1.5 + i * 1.0, 5.5, 0.85); tb(s, h, { x: 7.45, y: 1.6 + i * 1.0, w: 2.2, h: 0.6, fontSize: 16, bold: true, valign: "middle" }); tb(s, d, { x: 9.6, y: 1.6 + i * 1.0, w: 3.0, h: 0.6, fontSize: 13, color: "2A2620", valign: "middle" }); });
tb(s, "Answer score = 0.6 · mean + 0.4 · min — one wrong price makes the whole answer unsafe.", { x: 7.2, y: 5.6, w: 5.5, h: 0.8, fontSize: 15, italic: true, fontFace: "Cambria" });

// 7 impact + engineering
s = pres.addSlide({ masterName: "CONTENT" }); s.addText("Built to drop into real AI products", { placeholder: "title" });
const stats = [["1 line", "base_url change to guard any OpenAI-SDK app"], ["24", "tests incl. HTTP-level Bifrost & Tavily mocks"], ["0 keys", "needed to try it — offline snapshot mode"]];
stats.forEach(([n, l], i) => { const x = 0.6 + i * 4.1; tb(s, n, { x, y: 1.6, w: 3.8, h: 1.1, fontSize: 60, bold: true, fontFace: "Cambria", color: "B3261E" }); tb(s, l, { x, y: 2.75, w: 3.6, h: 0.8, fontSize: 15, color: "2A2620" }); });
tb(s, [
  { text: "Support & sales bots: block or rewrite answers whose prices fail re-verification", options: { bullet: true, breakLine: true } },
  { text: "Developer assistants: stop recommending EOL runtimes", options: { bullet: true, breakLine: true } },
  { text: "Eval / RAG teams: freshness as a metric and a CI gate (--fail-under)", options: { bullet: true, breakLine: true } },
  { text: "Scales horizontally: stateless FastAPI, parallel claim checks, query cache, Bifrost budgets cap spend", options: { bullet: true } }], { x: 0.6, y: 3.9, w: 12, h: 2.6, fontSize: 17, paraSpaceAfter: 10 });

// 8 close
s = pres.addSlide({ masterName: "DARK" });
s.addText("Next: guard inside the gateway", { placeholder: "title" });
s.addText("Bifrost plugin so every response is freshness-checked in-line · domain allow-lists per category · streaming results · freshness dashboards for deployed bots\n\ngithub.com/ishan5608/staleguard", { placeholder: "body" });

pres.writeFile({ fileName: require("path").join(__dirname, "..", "docs", "StaleGuard_pitch.pptx") }).then(f => console.log("wrote", f));
