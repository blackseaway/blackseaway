/* Black Sea Way: news filters, language memory, map */
(function () {
  "use strict";

  // remember language choice
  document.querySelectorAll(".lang-switch a").forEach(function (a) {
    a.addEventListener("click", function () {
      try { localStorage.setItem("bsw-lang", a.getAttribute("hreflang")); } catch (e) {}
    });
  });

  // news filters
  var list = document.getElementById("news-list");
  if (list) {
    var items = Array.prototype.slice.call(list.children);
    var chips = document.querySelectorAll(".filters .chip");
    var q = document.getElementById("q");
    var more = document.getElementById("more");
    var empty = document.getElementById("empty");
    var PAGE = 40, limit = PAGE, topic = "";
    var params = new URLSearchParams(location.search);
    if (params.get("topic")) topic = params.get("topic");

    function apply() {
      var term = (q && q.value || "").trim().toLowerCase();
      var shown = 0, matched = 0;
      items.forEach(function (li) {
        var ok = (!topic || (" " + li.dataset.topics + " ").indexOf(" " + topic + " ") >= 0) &&
                 (!term || li.textContent.toLowerCase().indexOf(term) >= 0);
        if (ok) matched++;
        var vis = ok && shown < limit;
        if (vis) shown++;
        li.hidden = !vis;
      });
      chips.forEach(function (c) { c.setAttribute("aria-pressed", String(c.dataset.topic === topic)); });
      if (more) more.hidden = matched <= shown;
      if (empty) empty.hidden = matched > 0;
    }
    chips.forEach(function (c) {
      c.addEventListener("click", function () {
        topic = c.dataset.topic; limit = PAGE;
        var u = new URL(location.href);
        if (topic) u.searchParams.set("topic", topic); else u.searchParams.delete("topic");
        history.replaceState(null, "", u);
        apply();
      });
    });
    if (q) q.addEventListener("input", function () { limit = PAGE; apply(); });
    if (more) more.addEventListener("click", function () { limit += PAGE; apply(); });
    apply();
  }

  // map
  var mapEl = document.getElementById("map");
  if (mapEl && window.L) {
    var map = L.map(mapEl, { scrollWheelZoom: false }).setView([44.2, 33.5], 6);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 10, minZoom: 4,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    var pts = window.BSW_POINTS || [];
    var max = pts.reduce(function (m, p) { return Math.max(m, p.count); }, 1);
    pts.forEach(function (p) {
      L.circleMarker([p.lat, p.lon], {
        radius: 6 + 22 * Math.sqrt(p.count / max),
        color: "#ffffff", weight: 2, fillColor: "#d7263d", fillOpacity: 0.75
      }).addTo(map).bindTooltip("<b>" + p.name + "</b><br>" + p.count + " " + (window.BSW_MENTIONS || ""));
    });
  } else if (mapEl) {
    mapEl.hidden = true;
  }
})();

/* contact form: send in the background; if the form service is unreachable, open the email app */
(function () {
  var form = document.getElementById("contact-form");
  if (!form || !window.fetch) return;
  var status = document.getElementById("form-status");
  var btn = form.querySelector("button[type=submit]");
  function mailtoFallback(data) {
    var body = data.message + "\n\n— " + data.name + " <" + data.email + ">";
    status.textContent = form.dataset.fallback;
    setTimeout(function () {
      location.href = "mailto:" + form.dataset.email + "?subject=" + encodeURIComponent("Black Sea Way: " + data.topic) +
        "&body=" + encodeURIComponent(body);
    }, 1200);
  }
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (form.botcheck && form.botcheck.checked) return;
    var data = { access_key: form.access_key.value, subject: form.subject.value, from_name: form.from_name.value,
                 name: form.name.value, email: form.email.value, topic: form.topic.value,
                 message: form.message.value };
    btn.disabled = true; status.textContent = form.dataset.sending;
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 15000);
    fetch(form.dataset.ajax, { method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" },
                               body: JSON.stringify(data), signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        clearTimeout(timer);
        if (res.success === true || String(res.success) === "true") { location.href = form.dataset.thanks; }
        else { btn.disabled = false; mailtoFallback(data); }
      })
      .catch(function () { clearTimeout(timer); btn.disabled = false; mailtoFallback(data); });
  });
})();
