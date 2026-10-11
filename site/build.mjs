// Generates index.html from template.html + services.json (no dependencies).
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const dir = dirname(fileURLToPath(import.meta.url));
const { categories, services } = JSON.parse(readFileSync(join(dir, "services.json"), "utf8"));
const CORE = 5;
if (services.length < 50) throw new Error(`Need at least 50 extra services, found ${services.length}`);

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const label = Object.fromEntries(categories.map((c) => [c.id, c.label]));
for (const s of services) if (!label[s.cat]) throw new Error(`Unknown category: ${s.cat}`);

const chips = [{ id: "all", label: "All" }, ...categories]
  .map((c) => `<button class="chip-btn" type="button" data-cat="${c.id}" aria-pressed="${c.id === "all"}">${esc(c.label)}</button>`)
  .join("\n        ");

const cards = services
  .map((s) => `<li class="svc" data-cat="${s.cat}" data-text="${esc((s.title + " " + s.desc + " " + label[s.cat]).toLowerCase())}"><span class="tag">${esc(label[s.cat])}</span><h3>${esc(s.title)}</h3><p>${esc(s.desc)}</p></li>`)
  .join("\n      ");

const html = readFileSync(join(dir, "template.html"), "utf8")
  .replaceAll("{{COUNT}}", String(services.length))
  .replaceAll("{{TOTAL}}", String(services.length + CORE))
  .replace("{{CHIPS}}", chips)
  .replace("{{CARDS}}", cards);

writeFileSync(join(dir, "index.html"), html);
console.log(`index.html built with ${services.length} extra services (${services.length + CORE} total).`);
