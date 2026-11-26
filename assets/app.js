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
