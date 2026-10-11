(() => {
  // ---- Contact: set this to receive enquiries by email. Leave empty to just copy the message. ----
  const CONTACT_EMAIL = "";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // Year + mobile nav
  $("#year").textContent = new Date().getFullYear();
  const btn = $(".menu-btn"), nav = $("#nav");
  btn.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    btn.setAttribute("aria-expanded", String(open));
  });
  nav.addEventListener("click", (e) => {
    if (e.target.closest("a")) { nav.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); }
  });

  // Pre-select the service when an offer button is clicked
  $$("[data-service]").forEach((el) => el.addEventListener("click", () => { $("#c-service").value = el.dataset.service; }));

  // Service finder
  const cards = $$(".svc"), chips = $$(".chip-btn"), q = $("#q"), count = $("#count"), empty = $("#empty");
  let cat = "all";
  const apply = () => {
    const term = q.value.trim().toLowerCase();
    let shown = 0;
    cards.forEach((c) => {
      const ok = (cat === "all" || c.dataset.cat === cat) && (!term || c.dataset.text.includes(term));
      c.hidden = !ok;
      if (ok) shown++;
    });
    count.textContent = shown === cards.length ? `Showing all ${shown} services` : `Showing ${shown} of ${cards.length} services`;
    empty.hidden = shown !== 0;
  };
  chips.forEach((b) => b.addEventListener("click", () => {
    cat = b.dataset.cat;
    chips.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    apply();
  }));
  q.addEventListener("input", apply);

  // Snippet checker
  const tt = $("#t-title"), td = $("#t-desc");
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s);
  const serp = () => {
    const t = tt.value, d = td.value;
    $("#p-title").textContent = clip(t || "Your page title", 60);
    $("#p-desc").textContent = clip(d || "Your meta description will appear here.", 160);
    const mt = $("#m-title"), md = $("#m-desc");
    mt.textContent = `${t.length} / 60`; mt.classList.toggle("over", t.length > 60);
    md.textContent = `${d.length} / 160`; md.classList.toggle("over", d.length > 160);
    const tips = [];
    if (t.length < 30) tips.push("Your title is short; add what you do and where.");
    if (t.length > 60) tips.push("Title may get cut off; aim for 60 characters or fewer.");
    if (d.length < 70) tips.push("Description is short; mention a benefit and a call to action.");
    if (d.length > 160) tips.push("Description may get cut off; aim for 160 characters or fewer.");
    $("#serp-hint").textContent = tips.length ? tips.join(" ") : "Nice. Both lengths look healthy. Search engines may still rewrite them, so treat this as a guide.";
  };
  tt.addEventListener("input", serp); td.addEventListener("input", serp); serp();

  // Contact form (no server needed)
  const form = $("#contact-form"), msg = $("#form-msg");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const name = (f.get("name") || "").trim(), email = (f.get("email") || "").trim(), about = (f.get("message") || "").trim();
    if (!name || !/^\S+@\S+\.\S+$/.test(email) || !about) {
      msg.textContent = "Please fill in your name, a valid email and a short message.";
      return;
    }
    const subject = `Enquiry: ${f.get("service")}`;
    const body = `Name: ${name}\nEmail: ${email}\nInterested in: ${f.get("service")}\n\n${about}`;
    if (CONTACT_EMAIL) {
      location.href = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
      msg.textContent = "Opening your email app. Just press send.";
    } else {
      try { await navigator.clipboard.writeText(`${subject}\n\n${body}`); msg.textContent = "Message copied. Paste it into an email or message to us."; }
      catch { msg.textContent = "Couldn't copy automatically. Please copy your message manually."; }
    }
  });
})();
