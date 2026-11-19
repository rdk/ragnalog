/* The page of one entry's exact text (/sets/{ref}/entries/{id}/raw): rendered by the shared
   entry card (entry-card.js), so a stack trace is highlighted as in the cards: application frames
   marked, library frames dimmed; every frame is shown (a raw view folds nothing away). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let data = {};
  try { data = JSON.parse($("raw-data").textContent || "{}"); } catch (e) { data = {}; }
  const card = window.RagnalogEntryCard.create({});
  const body = $("raw-body");
  body.innerHTML = card.rawHtml({ raw: data.raw || "", preamble: data.preamble || "" });
  body.querySelectorAll(".fold").forEach((f) => f.classList.add("open"));
  body.querySelectorAll(".unfold").forEach((b) => b.remove());

  const toast = $("raw-toast");
  let timer = 0;
  function say(text) {
    toast.textContent = text;
    toast.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => { toast.hidden = true; }, 1400);
  }
  $("raw-copy").addEventListener("click", async () => {
    const text = card.fullText({ raw: data.raw || "", preamble: data.preamble || "" });
    try { await navigator.clipboard.writeText(text); say("Copied"); return; } catch (e) { /* fall through */ }
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); say("Copied"); } catch (e) { say("Copy failed"); }
    ta.remove();
  });
})();
