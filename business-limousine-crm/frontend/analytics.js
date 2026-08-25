/* =========================================================================
   Business Limousine — Analytics & Quoting
   ------------------------------------------------------------------------
   Charts, pricing tables, the quote calculator and the review composer.

   Data comes from GET /api/analytics (session-authenticated) rather than
   being baked into the page: the underlying file names real clients,
   chauffeurs and passengers, so it must never be a static asset.

   Rendering is deferred until a view is first opened — the SVG charts size
   themselves off their container's clientWidth, which is 0 while a section
   is `hidden`. `Analytics.show(view)` is the entry point the shell calls.
   ========================================================================= */
(function (global) {
"use strict";

let DATA = null;          // payload from /api/analytics
let IS_SAMPLE = false;    // true when the server fell back to the fabricated sample
const built = new Set();  // views already rendered once


  /* ============================================================
     UTILITIES
     ============================================================ */
  const $ = (sel, root) => (root||document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root||document).querySelectorAll(sel));
  const SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs){
    const el = document.createElementNS(SVGNS, tag);
    if(attrs) for(const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }
  function cssVar(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  /* The dispatch console is a fixed light work surface — no theme switch. Kept as a
     function because the sequential palette picker calls it; hard-coded rather than
     reading prefers-color-scheme, which would hand dark ramps to a light page. */
  function isDarkTheme(){ return false; }
  function hexLuminance(hex){
    const c = hex.replace("#","");
    const r = parseInt(c.substr(0,2),16)/255, g = parseInt(c.substr(2,2),16)/255, b = parseInt(c.substr(4,2),16)/255;
    const lin = v => v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4);
    return 0.2126*lin(r) + 0.7152*lin(g) + 0.0722*lin(b);
  }
  const SEQ_STEPS_LIGHT = ["#cde2fb","#9ec5f4","#6da7ec","#3987e5","#256abf","#184f95","#0d366b"];
  const SEQ_STEPS_DARK  = ["#13233c","#153a63","#184f95","#1c5cab","#2a78d6","#5598e7","#9ec5f4"];

  function fmtEUR(n, compact){
    n = Number(n);
    if(compact){
      if(Math.abs(n) >= 1000000) return "€" + (n/1000000).toFixed(2).replace(/\.00$/,"") + "M";
      if(Math.abs(n) >= 1000) return "€" + (n/1000).toFixed(1).replace(/\.0$/,"") + "K";
      return "€" + Math.round(n);
    }
    return "€" + Math.round(n).toLocaleString("en-US");
  }
  function fmtEURfull(n){
    return "€" + n.toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  function fmtEUR0(n){ return "€" + Math.round(n).toLocaleString("en-US"); }
  function fmtInt(n){ return Number(n).toLocaleString("en-US"); }
  function fmtPct(n, digits){ return n.toFixed(digits==null?1:digits) + "%"; }

  const MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

  /* Consistent category color maps (fixed order = fixed hue assignment) */
  const SERVICE_COLOR = {
    "Airport Transfer": "var(--s1)",
    "City Transfer": "var(--s2)",
    "Station Transfer": "var(--s3)",
    "Custom Itinerary / Multi-stop": "var(--s4)",
    "Hourly Disposal": "var(--s5)",
    "Other / Special": "var(--s6)"
  };
  const VEHICLE_COLOR = {
    "Sedan (E-Class)": "var(--s1)",
    "Van (V-Class)": "var(--s2)",
    "Minibus": "var(--s3)",
    "Luxury Sedan (S-Class)": "var(--s4)",
    "Full-size Bus": "var(--s5)",
    "Unknown/Unspecified": "var(--muted)"
  };

  /* Shared tooltip */
  const tooltip = $("#chart-tooltip");
  function showTooltip(x, y, titleText, rows){
    tooltip.innerHTML = "";
    const t = document.createElement("div");
    t.className = "tt-title";
    t.textContent = titleText;
    tooltip.appendChild(t);
    rows.forEach(r => {
      const row = document.createElement("div");
      row.className = "tt-row";
      const key = document.createElement("div");
      key.className = "key";
      if(r.color){
        const line = document.createElement("span");
        line.className = "line";
        line.style.background = r.color;
        key.appendChild(line);
      }
      const label = document.createElement("span");
      label.textContent = r.label;
      key.appendChild(label);
      row.appendChild(key);
      const val = document.createElement("div");
      val.className = "tt-val";
      val.textContent = r.value;
      row.appendChild(val);
      tooltip.appendChild(row);
    });
    const pad = 14;
    let left = x + pad, top = y + pad;
    const vw = window.innerWidth, vh = window.innerHeight;
    tooltip.style.left = "0px"; tooltip.style.top = "0px"; tooltip.classList.add("show");
    const rect = tooltip.getBoundingClientRect();
    if(left + rect.width > vw - 8) left = x - rect.width - pad;
    if(top + rect.height > vh - 8) top = y - rect.height - pad;
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }
  function hideTooltip(){ tooltip.classList.remove("show"); }

  /* Registry of renderers so we can redraw on resize */
  const renderers = [];
  function registerChart(fn){ renderers.push(fn); fn(); }
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => renderers.forEach(fn => fn()), 120);
  });

  /* ============================================================
     CHART: horizontal bar (rankings, categories)
     ============================================================ */
  function horizontalBarChart(container, opts){
    const render = () => {
      container.innerHTML = "";
      const width = container.clientWidth || 480;
      const data = opts.data;
      const barH = opts.barHeight || 22;
      const gap = opts.gap != null ? opts.gap : 12;
      const labelW = opts.labelWidth != null ? opts.labelWidth : 150;
      const topPad = 6, bottomPad = 6;
      const chartH = data.length * (barH + gap) - gap + topPad + bottomPad;
      const plotW = Math.max(80, width - labelW - 70);
      const maxVal = Math.max(...data.map(opts.value)) * 1.08;

      const svg = svgEl("svg", {class:"chart", viewBox:`0 0 ${width} ${chartH}`, role:"img", "aria-label":opts.ariaLabel||""});

      data.forEach((d, i) => {
        const y = topPad + i * (barH + gap);
        const val = opts.value(d);
        const w = maxVal > 0 ? (val/maxVal) * plotW : 0;
        const color = opts.color ? opts.color(d) : "var(--accent)";
        const muted = opts.isMuted ? opts.isMuted(d) : false;

        const label = svgEl("text", {x: labelW - 10, y: y + barH/2 + 4, class:"bar-label", "text-anchor":"end"});
        label.textContent = opts.label(d);
        svg.appendChild(label);

        const track = svgEl("rect", {x: labelW, y, width: plotW, height: barH, rx: 4, fill: "var(--surface-2)"});
        svg.appendChild(track);

        const rect = svgEl("rect", {
          x: labelW, y, width: Math.max(3,w), height: barH, rx: 4,
          fill: muted ? "var(--muted)" : color,
          class:"bar-rect", tabindex:"0", opacity: muted ? 0.55 : 1
        });
        svg.appendChild(rect);

        const valText = svgEl("text", {x: labelW + Math.max(3,w) + 8, y: y + barH/2 + 4, class:"bar-value"});
        valText.textContent = opts.valueLabel(d);
        const valX = labelW + Math.max(3,w) + 8;
        if(valX > width - 8){
          valText.setAttribute("x", labelW + Math.max(3,w) - 8);
          valText.setAttribute("text-anchor","end");
          valText.setAttribute("fill", "white");
          valText.style.fill = "#fff";
        }
        svg.appendChild(valText);

        const hit = svgEl("rect", {x: labelW, y, width: plotW, height: barH, fill:"transparent", style:"cursor:pointer;"});
        function onEnter(evt){
          const pt = evt.touches ? evt.touches[0] : evt;
          showTooltip(pt.clientX, pt.clientY, opts.label(d), opts.tooltipRows(d));
          rect.style.opacity = 0.82;
        }
        function onMove(evt){
          const pt = evt.touches ? evt.touches[0] : evt;
          showTooltip(pt.clientX, pt.clientY, opts.label(d), opts.tooltipRows(d));
        }
        function onLeave(){ hideTooltip(); rect.style.opacity = muted? 0.55:1; }
        hit.addEventListener("pointerenter", onEnter);
        hit.addEventListener("pointermove", onMove);
        hit.addEventListener("pointerleave", onLeave);
        rect.addEventListener("focus", (e)=> onEnter({clientX: rect.getBoundingClientRect().right, clientY: rect.getBoundingClientRect().top}));
        rect.addEventListener("blur", onLeave);
        svg.appendChild(hit);
      });

      container.appendChild(svg);
    };
    registerChart(render);
    return render;
  }

  /* ============================================================
     CHART: vertical column chart (time-ordered / ordinal)
     ============================================================ */
  function columnChart(container, opts){
    const render = () => {
      container.innerHTML = "";
      const width = container.clientWidth || 480;
      const data = opts.data;
      const height = opts.height || 240;
      const padL = 46, padR = 10, padT = 14, padB = 30;
      const plotW = width - padL - padR;
      const plotH = height - padT - padB;
      const maxVal = Math.max(...data.map(opts.value)) * 1.12 || 1;
      const n = data.length;
      const slot = plotW / n;
      const barW = Math.min(opts.maxBarWidth || 26, slot * 0.6);

      const svg = svgEl("svg", {class:"chart", viewBox:`0 0 ${width} ${height}`, role:"img", "aria-label":opts.ariaLabel||""});

      const ticks = 4;
      for(let t=0;t<=ticks;t++){
        const val = (maxVal/ticks)*t;
        const y = padT + plotH - (val/maxVal)*plotH;
        svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:y, y2:y, class:"grid-line"}));
        const lbl = svgEl("text", {x: padL-8, y: y+3, class:"axis-label", "text-anchor":"end"});
        lbl.textContent = opts.yFormat ? opts.yFormat(val) : Math.round(val);
        svg.appendChild(lbl);
      }
      svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:padT+plotH, y2:padT+plotH, class:"baseline"}));

      data.forEach((d,i) => {
        const val = opts.value(d);
        const barH = (val/maxVal) * plotH;
        const x = padL + i*slot + (slot-barW)/2;
        const y = padT + plotH - barH;
        const muted = opts.isMuted ? opts.isMuted(d) : false;
        const color = opts.color ? opts.color(d) : "var(--accent)";

        const rect = svgEl("rect", {x, y, width:barW, height:Math.max(1,barH), rx:4, fill: muted ? color : color, opacity: muted?0.5:1, class:"bar-rect", tabindex:"0"});
        svg.appendChild(rect);

        if(opts.showXLabel ? opts.showXLabel(d,i) : true){
          const lbl = svgEl("text", {x: x+barW/2, y: padT+plotH+18, class:"axis-label", "text-anchor":"middle"});
          lbl.textContent = opts.label(d);
          svg.appendChild(lbl);
        }
        if(opts.directLabel && opts.directLabel(d,i)){
          const dl = svgEl("text", {x: x+barW/2, y: y-6, class:"bar-value", "text-anchor":"middle"});
          dl.textContent = opts.valueLabel(d);
          svg.appendChild(dl);
        }

        const hit = svgEl("rect", {x: padL + i*slot, y:padT, width:slot, height:plotH, fill:"transparent", style:"cursor:pointer;"});
        function onEnter(evt){
          const pt = evt.touches ? evt.touches[0] : evt;
          showTooltip(pt.clientX, pt.clientY, opts.label(d), opts.tooltipRows(d));
          rect.style.opacity = 0.8;
        }
        function onMove(evt){
          const pt = evt.touches ? evt.touches[0] : evt;
          showTooltip(pt.clientX, pt.clientY, opts.label(d), opts.tooltipRows(d));
        }
        function onLeave(){ hideTooltip(); rect.style.opacity = muted?0.5:1; }
        hit.addEventListener("pointerenter", onEnter);
        hit.addEventListener("pointermove", onMove);
        hit.addEventListener("pointerleave", onLeave);
        rect.addEventListener("focus", ()=> onEnter({clientX: rect.getBoundingClientRect().right, clientY: rect.getBoundingClientRect().top}));
        rect.addEventListener("blur", onLeave);
        svg.appendChild(hit);
      });

      container.appendChild(svg);
    };
    registerChart(render);
    return render;
  }

  /* ============================================================
     CHART: area/line chart with crosshair tooltip (time series)
     ============================================================ */
  function areaLineChart(container, opts){
    const render = () => {
      container.innerHTML = "";
      const width = container.clientWidth || 480;
      const height = opts.height || 280;
      const padL = 52, padR = 16, padT = 16, padB = 28;
      const plotW = width - padL - padR;
      const plotH = height - padT - padB;
      const data = opts.data;
      const n = data.length;
      const maxVal = Math.max(...data.map(opts.value)) * 1.12 || 1;
      const minVal = 0;
      const xAt = i => padL + (n===1?0:(i/(n-1)) * plotW);
      const yAt = v => padT + plotH - ((v-minVal)/(maxVal-minVal)) * plotH;

      const svg = svgEl("svg", {class:"chart", viewBox:`0 0 ${width} ${height}`, role:"img", "aria-label":opts.ariaLabel||""});

      const ticks = 4;
      for(let t=0;t<=ticks;t++){
        const val = (maxVal/ticks)*t;
        const y = yAt(val);
        svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:y, y2:y, class:"grid-line"}));
        const lbl = svgEl("text", {x: padL-8, y: y+3, class:"axis-label", "text-anchor":"end"});
        lbl.textContent = opts.yFormat ? opts.yFormat(val) : Math.round(val);
        svg.appendChild(lbl);
      }
      svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:padT+plotH, y2:padT+plotH, class:"baseline"}));

      let areaPath = `M ${xAt(0)} ${yAt(opts.value(data[0]))}`;
      let linePath = `M ${xAt(0)} ${yAt(opts.value(data[0]))}`;
      for(let i=1;i<n;i++){
        areaPath += ` L ${xAt(i)} ${yAt(opts.value(data[i]))}`;
        linePath += ` L ${xAt(i)} ${yAt(opts.value(data[i]))}`;
      }
      areaPath += ` L ${xAt(n-1)} ${padT+plotH} L ${xAt(0)} ${padT+plotH} Z`;

      const gradId = "grad-" + Math.random().toString(36).slice(2,9);
      const defs = svgEl("defs");
      const grad = svgEl("linearGradient", {id:gradId, x1:"0", y1:"0", x2:"0", y2:"1"});
      grad.appendChild(svgEl("stop", {offset:"0%", "stop-color":"var(--accent)", "stop-opacity":"0.22"}));
      grad.appendChild(svgEl("stop", {offset:"100%", "stop-color":"var(--accent)", "stop-opacity":"0.02"}));
      defs.appendChild(grad);
      svg.appendChild(defs);

      svg.appendChild(svgEl("path", {d:areaPath, fill:`url(#${gradId})`, stroke:"none"}));
      svg.appendChild(svgEl("path", {d:linePath, fill:"none", stroke:"var(--accent)", "stroke-width":"2", "stroke-linejoin":"round", "stroke-linecap":"round"}));

      // x labels (sparse)
      const labelEvery = opts.labelEvery || Math.ceil(n/8);
      data.forEach((d,i) => {
        if(i % labelEvery === 0 || i === n-1){
          const lbl = svgEl("text", {x:xAt(i), y: padT+plotH+18, class:"axis-label", "text-anchor": i===0?"start":(i===n-1?"end":"middle")});
          lbl.textContent = opts.label(d);
          svg.appendChild(lbl);
        }
      });

      // reference marker at end
      const endDot = svgEl("circle", {cx:xAt(n-1), cy:yAt(opts.value(data[n-1])), r:4, fill:"var(--accent)", stroke:"var(--surface)", "stroke-width":2});
      svg.appendChild(endDot);

      // crosshair
      const crosshair = svgEl("line", {x1:0,x2:0,y1:padT,y2:padT+plotH, stroke:"var(--baseline)", "stroke-width":1, opacity:0});
      svg.appendChild(crosshair);
      const hoverDot = svgEl("circle", {r:5, fill:"var(--accent)", stroke:"var(--surface)", "stroke-width":2, opacity:0});
      svg.appendChild(hoverDot);

      const hitArea = svgEl("rect", {x:padL, y:padT, width:plotW, height:plotH, fill:"transparent"});
      function handleMove(evt){
        const rect = svg.getBoundingClientRect();
        const pt = evt.touches ? evt.touches[0] : evt;
        const svgX = ((pt.clientX - rect.left) / rect.width) * width;
        let idx = Math.round(((svgX - padL) / plotW) * (n-1));
        idx = Math.max(0, Math.min(n-1, idx));
        const d = data[idx];
        crosshair.setAttribute("x1", xAt(idx));
        crosshair.setAttribute("x2", xAt(idx));
        crosshair.setAttribute("opacity", 1);
        hoverDot.setAttribute("cx", xAt(idx));
        hoverDot.setAttribute("cy", yAt(opts.value(d)));
        hoverDot.setAttribute("opacity", 1);
        showTooltip(pt.clientX, pt.clientY, opts.label(d), opts.tooltipRows(d));
      }
      hitArea.addEventListener("pointermove", handleMove);
      hitArea.addEventListener("pointerenter", handleMove);
      hitArea.addEventListener("pointerleave", () => { crosshair.setAttribute("opacity",0); hoverDot.setAttribute("opacity",0); hideTooltip(); });
      svg.appendChild(hitArea);

      container.appendChild(svg);
    };
    registerChart(render);
    return render;
  }

  /* ============================================================
     CHART: pareto / cumulative line
     ============================================================ */
  function paretoChart(container, opts){
    const render = () => {
      container.innerHTML = "";
      const width = container.clientWidth || 480;
      const height = opts.height || 280;
      const padL = 44, padR = 16, padT = 16, padB = 28;
      const plotW = width - padL - padR;
      const plotH = height - padT - padB;
      const data = opts.data;
      const n = data.length;
      const xAt = i => padL + (data[i].x/100) * plotW;
      const yAt = v => padT + plotH - (v/100) * plotH;

      const svg = svgEl("svg", {class:"chart", viewBox:`0 0 ${width} ${height}`, role:"img"});

      [0,25,50,75,100].forEach(v => {
        const y = yAt(v);
        svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:y, y2:y, class:"grid-line"}));
        const lbl = svgEl("text", {x: padL-8, y: y+3, class:"axis-label", "text-anchor":"end"});
        lbl.textContent = v+"%";
        svg.appendChild(lbl);
      });
      svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:padT+plotH, y2:padT+plotH, class:"baseline"}));

      // 80% reference line
      const y80 = yAt(80);
      svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:y80, y2:y80, stroke:"var(--critical)", "stroke-width":1, "stroke-dasharray":"3,3", opacity:0.55}));
      const refLbl = svgEl("text", {x:padL+plotW, y:y80-5, class:"axis-label", "text-anchor":"end"});
      refLbl.textContent = "80% of revenue";
      refLbl.style.fill = "var(--critical)";
      svg.appendChild(refLbl);

      let path = `M ${xAt(0)} ${yAt(data[0].y)}`;
      for(let i=1;i<n;i++) path += ` L ${xAt(i)} ${yAt(data[i].y)}`;
      svg.appendChild(svgEl("path", {d:path, fill:"none", stroke:"var(--accent)", "stroke-width":2, "stroke-linecap":"round", "stroke-linejoin":"round"}));

      const entity = opts.entityLabel || "clients";
      ["0%","50%","100%"].forEach((t,ti) => {
        const x = padL + (ti===0?0:ti===1?plotW/2:plotW);
        const lbl = svgEl("text", {x, y:padT+plotH+18, class:"axis-label", "text-anchor": ti===0?"start":(ti===2?"end":"middle")});
        lbl.textContent = ti===0?("Smallest "+entity):(ti===1?("% of "+entity):("Largest "+entity));
        svg.appendChild(lbl);
      });

      const hitArea = svgEl("rect", {x:padL, y:padT, width:plotW, height:plotH, fill:"transparent"});
      const hoverDot = svgEl("circle", {r:5, fill:"var(--accent)", stroke:"var(--surface)", "stroke-width":2, opacity:0});
      const crosshair = svgEl("line", {x1:0,x2:0,y1:padT,y2:padT+plotH, stroke:"var(--baseline)", "stroke-width":1, opacity:0});
      svg.appendChild(crosshair); svg.appendChild(hoverDot);
      function handleMove(evt){
        const rect = svg.getBoundingClientRect();
        const pt = evt.touches ? evt.touches[0] : evt;
        const svgX = ((pt.clientX - rect.left) / rect.width) * width;
        const xPct = Math.max(0, Math.min(100, ((svgX - padL) / plotW) * 100));
        let idx = 0, best = Infinity;
        data.forEach((d,i) => { const diff = Math.abs(d.x - xPct); if(diff < best){best = diff; idx = i;} });
        const d = data[idx];
        crosshair.setAttribute("x1", xAt(idx)); crosshair.setAttribute("x2", xAt(idx)); crosshair.setAttribute("opacity",1);
        hoverDot.setAttribute("cx", xAt(idx)); hoverDot.setAttribute("cy", yAt(d.y)); hoverDot.setAttribute("opacity",1);
        showTooltip(pt.clientX, pt.clientY, "Top " + d.x.toFixed(1) + "% of " + entity, [{label:"Cumulative revenue", value: d.y.toFixed(1)+"%", color:"var(--accent)"}]);
      }
      hitArea.addEventListener("pointermove", handleMove);
      hitArea.addEventListener("pointerenter", handleMove);
      hitArea.addEventListener("pointerleave", () => { crosshair.setAttribute("opacity",0); hoverDot.setAttribute("opacity",0); hideTooltip(); });
      svg.appendChild(hitArea);

      container.appendChild(svg);
    };
    registerChart(render);
    return render;
  }

  /* ============================================================
     CHART: heatmap (service x vehicle)
     ============================================================ */
  function heatmapChart(container, opts){
    const render = () => {
      container.innerHTML = "";
      const width = Math.max(container.clientWidth || 600, 620);
      const rows = opts.rows, cols = opts.cols;
      const labelW = 170, topLabelH = 95;
      const cellGap = 2;
      const plotW = width - labelW - 10;
      const cellW = plotW / cols.length;
      const cellH = Math.min(48, cellW * 0.85);
      const height = topLabelH + rows.length * cellH + 10;

      const svg = svgEl("svg", {class:"chart", viewBox:`0 0 ${width} ${height}`, role:"img"});
      svg.style.width = width + "px";
      svg.style.maxWidth = "none";

      const maxVal = Math.max(...rows.flatMap(r => cols.map(c => opts.value(r,c))));

      cols.forEach((c,ci) => {
        const x = labelW + ci*cellW + cellW/2;
        const g = svgEl("g", {transform:`translate(${x}, ${topLabelH-10}) rotate(-30)`});
        const t = svgEl("text", {class:"axis-label", "text-anchor":"start"});
        t.textContent = c;
        g.appendChild(t);
        svg.appendChild(g);
      });

      rows.forEach((r, ri) => {
        const y = topLabelH + ri*cellH;
        const rowLabel = svgEl("text", {x: labelW-10, y: y+cellH/2+4, class:"bar-label", "text-anchor":"end"});
        rowLabel.textContent = r;
        svg.appendChild(rowLabel);

        cols.forEach((c, ci) => {
          const x = labelW + ci*cellW;
          const val = opts.value(r,c);
          const t = maxVal>0 ? Math.pow(val/maxVal, 0.55) : 0;
          const stepHexes = isDarkTheme() ? SEQ_STEPS_DARK : SEQ_STEPS_LIGHT;
          const stepIdx = val<=0 ? 0 : Math.min(stepHexes.length-1, Math.max(0, Math.round(t*(stepHexes.length-1))));
          const fillHex = stepHexes[stepIdx];
          const fill = val<=0 ? "var(--surface-2)" : fillHex;

          const rect = svgEl("rect", {
            x:x+cellGap/2, y:y+cellGap/2, width:cellW-cellGap, height:cellH-cellGap, rx:5,
            fill, class:"cell-rect", tabindex: val>0 ? "0":"-1"
          });
          svg.appendChild(rect);

          if(val>0 && cellW > 40){
            const needsWhite = hexLuminance(fillHex) < 0.4;
            const label = svgEl("text", {x:x+cellW/2, y:y+cellH/2+4, class:"cell-text", "text-anchor":"middle", fill: needsWhite ? "#fff" : "#0b0b0b"});
            label.textContent = fmtEUR(val, true);
            svg.appendChild(label);
          }

          function onEnter(evt){
            const pt = evt.touches ? evt.touches[0] : evt;
            const meta = opts.meta(r,c);
            showTooltip(pt.clientX, pt.clientY, r + " × " + c, [
              {label:"Revenue", value: fmtEUR(val)},
              {label:"Trips", value: fmtInt(meta.count)}
            ]);
          }
          function onLeave(){ hideTooltip(); }
          rect.addEventListener("pointerenter", onEnter);
          rect.addEventListener("pointermove", onEnter);
          rect.addEventListener("pointerleave", onLeave);
          if(val>0){
            rect.addEventListener("focus", (e)=> onEnter({clientX: rect.getBoundingClientRect().right, clientY: rect.getBoundingClientRect().top}));
            rect.addEventListener("blur", onLeave);
          }
        });
      });

      container.appendChild(svg);
    };
    registerChart(render);
    return render;
  }

  /* ============================================================
     BUILD: KPI rows
     ============================================================ */
  function buildKpis(){
    const row = $("#kpi-row");
    const k = DATA.kpis;
    const y2026 = DATA.revenue_by_year.find(y=>y.Year===2026).monthly_avg;
    const y2025 = DATA.revenue_by_year.find(y=>y.Year===2025).monthly_avg;
    const runRateDelta = ((y2026-y2025)/y2025)*100;
    const items = [
      {label:"Total revenue", value: fmtEUR(k.total_revenue, true), sub:"ex-VAT, Apr 2022 – Aug 2026"},
      {label:"Total trips", value: fmtInt(k.total_trips), sub:"clean mission legs"},
      {label:"Avg. trip value", value: fmtEUR(k.avg_trip_value), sub:"median " + fmtEUR(k.median_trip_value)},
      {label:"Active clients", value: fmtInt(k.unique_clients), sub: k.unique_partners + " subcontracted partners"},
      {label:"2026 monthly run-rate", value: fmtEUR(y2026, true) + "/mo", delta: runRateDelta, deltaLabel:"vs 2025 avg"},
      {label:"Data span", value:"4y 5m", sub: DATA.kpis.date_min + " → " + DATA.kpis.date_max}
    ];
    items.forEach(it => {
      const el = document.createElement("div");
      el.className = "kpi";
      let deltaHtml = "";
      if(it.delta != null){
        const cls = it.delta > 0.5 ? "up" : (it.delta < -0.5 ? "down" : "flat");
        const arrow = it.delta > 0.5 ? "▲" : (it.delta < -0.5 ? "▼" : "•");
        deltaHtml = `<div class="delta ${cls}">${arrow} ${Math.abs(it.delta).toFixed(1)}% <span style="color:var(--muted); font-weight:500;">${it.deltaLabel}</span></div>`;
      }
      el.innerHTML = `<div class="label">${it.label}</div><div class="value tabular">${it.value}</div>${deltaHtml}${it.sub?`<div class="sub">${it.sub}</div>`:""}`;
      row.appendChild(el);
    });
  }

  function buildClientKpis(){
    const row = $("#client-kpi-row");
    const c = DATA.client_concentration_summary;
    const ip = DATA.internal_vs_partner;
    const internal = ip.find(x=>x["Internal?"]==="Y");
    const partner = ip.find(x=>x["Internal?"]==="N");
    const items = [
      {label:"Clients for 50% of revenue", value: c.n_for_50pct, sub:"of " + c.total_clients + " total clients"},
      {label:"Clients for 80% of revenue", value: c.n_for_80pct, sub:"of " + c.total_clients + " total clients"},
      {label:"Top 10 clients", value: fmtPct(c.top10_pct_revenue), sub:"of total revenue"},
      {label:"Internal fleet share", value: fmtPct(internal.sum/(internal.sum+partner.sum)*100), sub:"of revenue"},
      {label:"Subcontracted partners", value: DATA.kpis.unique_partners, sub: fmtEUR(partner.sum,true) + " routed out"},
      {label:"Largest single account", value:"15.1%", sub:"Fluxology bv/srl"}
    ];
    items.forEach(it => {
      const el = document.createElement("div");
      el.className = "kpi";
      el.innerHTML = `<div class="label">${it.label}</div><div class="value tabular">${it.value}</div>${it.sub?`<div class="sub">${it.sub}</div>`:""}`;
      row.appendChild(el);
    });
  }

  /* ============================================================
     BUILD: Overview charts
     ============================================================ */
  function buildOverview(){
    areaLineChart($("#chart-monthly-trend"), {
      data: DATA.revenue_by_month,
      value: d => d.sum,
      label: d => { const [y,m] = d.YearMonth.split("-"); return MONTH_NAMES[+m-1] + " " + y.slice(2); },
      yFormat: v => fmtEUR(v, true),
      labelEvery: 6,
      height: 260,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color:"var(--accent)"},
        {label:"Trips", value: fmtInt(d.count)}
      ]
    });

    horizontalBarChart($("#chart-overview-service"), {
      data: DATA.revenue_by_service,
      label: d => d["Service Category"],
      value: d => d.sum,
      valueLabel: d => fmtEUR(d.sum, true),
      color: d => SERVICE_COLOR[d["Service Category"]],
      labelWidth: 152,
      barHeight: 22, gap: 10,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color: SERVICE_COLOR[d["Service Category"]]},
        {label:"Share", value: fmtPct(d.pct_revenue)},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.mean)}
      ]
    });

    horizontalBarChart($("#chart-overview-vehicle"), {
      data: DATA.revenue_by_vehicle,
      label: d => d["Vehicle Category"],
      value: d => d.sum,
      valueLabel: d => fmtEUR(d.sum, true),
      color: d => VEHICLE_COLOR[d["Vehicle Category"]],
      isMuted: d => d["Vehicle Category"] === "Unknown/Unspecified",
      labelWidth: 152,
      barHeight: 22, gap: 10,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color: VEHICLE_COLOR[d["Vehicle Category"]]},
        {label:"Share", value: fmtPct(d.pct_revenue)},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.mean)}
      ]
    });
  }

  /* ============================================================
     BUILD: Growth charts
     ============================================================ */
  let yearChartRender;
  function buildYearChart(metric){
    const container = $("#chart-year");
    yearChartRender = columnChart(container, {
      data: DATA.revenue_by_year,
      value: d => d[metric],
      label: d => d.Year,
      yFormat: v => fmtEUR(v, true),
      isMuted: d => d.months_active < 12,
      maxBarWidth: 64,
      height: 260,
      directLabel: () => true,
      valueLabel: d => fmtEUR(d[metric], true) + (d.months_active < 12 ? " *" : ""),
      tooltipRows: d => [
        {label: metric==="sum" ? "Total revenue" : "Monthly average", value: fmtEUR(d[metric]), color:"var(--accent)"},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Months of data", value: d.months_active + (d.months_active<12 ? " (partial year)" : " (full year)")}
      ],
      ariaLabel: "Revenue by year"
    });
  }

  function buildGrowth(){
    buildYearChart("sum");
    $$("#year-metric-toggle button").forEach(btn => {
      btn.addEventListener("click", () => {
        $$("#year-metric-toggle button").forEach(b=>b.classList.remove("active"));
        btn.classList.add("active");
        buildYearChart(btn.dataset.metric);
      });
    });

    columnChart($("#chart-seasonality"), {
      data: DATA.seasonality,
      value: d => d.avg_revenue_per_year,
      label: d => MONTH_NAMES[d.MonthNum-1],
      yFormat: v => fmtEUR(v, true),
      height: 240,
      maxBarWidth: 30,
      tooltipRows: d => [
        {label:"Avg. revenue", value: fmtEUR(d.avg_revenue_per_year), color:"var(--accent)"},
        {label:"Total across years", value: fmtEUR(d.sum)},
        {label:"Years of data", value: d.years_count}
      ]
    });

    const weekdayOrder = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
    columnChart($("#chart-weekday"), {
      data: DATA.revenue_by_weekday,
      value: d => d.sum,
      label: d => d.Weekday,
      yFormat: v => fmtEUR(v, true),
      height: 240,
      maxBarWidth: 40,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color:"var(--accent)"},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.mean)}
      ]
    });
  }

  /* ============================================================
     BUILD: Fleet charts
     ============================================================ */
  function buildFleet(){
    horizontalBarChart($("#chart-fleet-service"), {
      data: DATA.revenue_by_service,
      label: d => d["Service Category"],
      value: d => d.sum,
      valueLabel: d => fmtEUR(d.sum, true) + "  ·  " + fmtPct(d.pct_revenue),
      color: d => SERVICE_COLOR[d["Service Category"]],
      labelWidth: 160, barHeight: 26, gap: 12,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color: SERVICE_COLOR[d["Service Category"]]},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.mean)}
      ]
    });

    horizontalBarChart($("#chart-fleet-vehicle"), {
      data: DATA.revenue_by_vehicle,
      label: d => d["Vehicle Category"],
      value: d => d.sum,
      valueLabel: d => fmtEUR(d.sum, true) + "  ·  " + fmtPct(d.pct_revenue),
      color: d => VEHICLE_COLOR[d["Vehicle Category"]],
      isMuted: d => d["Vehicle Category"] === "Unknown/Unspecified",
      labelWidth: 160, barHeight: 26, gap: 12,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color: VEHICLE_COLOR[d["Vehicle Category"]]},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.mean)}
      ]
    });

    const services = ["Airport Transfer","City Transfer","Station Transfer","Custom Itinerary / Multi-stop","Hourly Disposal","Other / Special"];
    const vehicles = ["Sedan (E-Class)","Van (V-Class)","Minibus","Luxury Sedan (S-Class)","Full-size Bus","Unknown/Unspecified"];
    const matrix = DATA.service_vehicle_matrix;
    function findCell(s,v){ return matrix.find(m => m["Service Category"]===s && m["Vehicle Category"]===v) || {sum:0,count:0}; }

    heatmapChart($("#chart-heatmap"), {
      rows: services, cols: vehicles,
      value: (r,c) => findCell(r,c).sum,
      meta: (r,c) => findCell(r,c)
    });
  }

  /* ============================================================
     BUILD: Clients charts
     ============================================================ */
  function buildClients(){
    horizontalBarChart($("#chart-top-clients"), {
      data: DATA.top_clients.slice(0,15),
      label: d => d.Client.length > 26 ? d.Client.slice(0,25)+"…" : d.Client,
      value: d => d.sum,
      valueLabel: d => fmtEUR(d.sum, true),
      color: () => "var(--accent)",
      labelWidth: 172, barHeight: 19, gap: 8,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color:"var(--accent)"},
        {label:"Share of total", value: fmtPct(d.pct_revenue)},
        {label:"Trips", value: fmtInt(d.count)}
      ]
    });

    const paretoPts = DATA.client_pareto.map(p => ({x:p.client_rank_pct, y:p.cum_pct}));
    paretoChart($("#chart-pareto"), {data: paretoPts, height: 260});

    horizontalBarChart($("#chart-partners"), {
      data: DATA.top_partners.slice(0,10),
      label: d => d.Partner.length > 24 ? d.Partner.slice(0,23)+"…" : d.Partner,
      value: d => d.sum,
      valueLabel: d => fmtEUR(d.sum, true),
      color: () => "var(--s2)",
      labelWidth: 152, barHeight: 20, gap: 9,
      tooltipRows: d => [
        {label:"Revenue routed", value: fmtEUR(d.sum), color:"var(--s2)"},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.mean)}
      ]
    });

    // internal vs partner proportion viz
    const ip = DATA.internal_vs_partner;
    const internal = ip.find(x=>x["Internal?"]==="Y");
    const partner = ip.find(x=>x["Internal?"]==="N");
    const total = internal.sum + partner.sum;
    const pInternal = internal.sum/total*100;
    const wrap = $("#internal-partner-viz");
    wrap.innerHTML = `
      <div style="display:flex; height:34px; border-radius:8px; overflow:hidden; margin-bottom:16px;">
        <div style="width:${pInternal}%; background:var(--accent); display:flex; align-items:center; justify-content:center; color:var(--accent-ink); font-size:12px; font-weight:700;">${pInternal.toFixed(1)}%</div>
        <div style="width:${100-pInternal}%; background:var(--surface-2); display:flex; align-items:center; justify-content:center; color:var(--ink-2); font-size:12px; font-weight:700;">${(100-pInternal).toFixed(1)}%</div>
      </div>
      <div class="grid grid-2" style="gap:12px;">
        <div>
          <div class="legend-item"><span class="swatch" style="background:var(--accent);"></span><b style="color:var(--ink);">Internal fleet</b></div>
          <div style="font-size:1.4rem; font-weight:700; margin-top:4px;" class="tabular">${fmtEUR(internal.sum,true)}</div>
          <div style="font-size:12px; color:var(--muted);">${fmtInt(internal.count)} trips · avg ${fmtEUR(internal.mean)}</div>
        </div>
        <div>
          <div class="legend-item"><span class="swatch" style="background:var(--surface-2); border:1px solid var(--border);"></span><b style="color:var(--ink);">Subcontracted</b></div>
          <div style="font-size:1.4rem; font-weight:700; margin-top:4px;" class="tabular">${fmtEUR(partner.sum,true)}</div>
          <div style="font-size:12px; color:var(--muted);">${fmtInt(partner.count)} trips · avg ${fmtEUR(partner.mean)}</div>
        </div>
      </div>
    `;
  }

  /* ============================================================
     BUILD: Drivers
     ============================================================ */
  function buildDrivers(){
    const dk = DATA.driver_kpis;

    $("#drv-coverage-trips").textContent = fmtInt(dk.n_trips_with_driver) + " of " + fmtInt(dk.n_real_trips_total);
    $("#drv-coverage-trips-pct").textContent = fmtPct(dk.trip_coverage_pct);
    $("#drv-coverage-rev-pct").textContent = fmtPct(dk.revenue_coverage_pct);
    $("#driver-chart-sub").textContent = "of " + dk.n_drivers + " drivers on record";

    const topDriver = DATA.top_drivers[0];
    const row = $("#driver-kpi-row");
    const items = [
      {label:"Named drivers", value: fmtInt(dk.n_drivers), sub:"in the raw booking export"},
      {label:"Trips with driver recorded", value: fmtInt(dk.n_trips_with_driver), sub: fmtPct(dk.trip_coverage_pct,0) + " of real trips"},
      {label:"Revenue attributed", value: fmtEUR(dk.revenue_with_driver, true), sub: fmtPct(dk.revenue_coverage_pct,0) + " of export revenue"},
      {label:"Avg. fare, driver-attributed", value: fmtEUR(dk.revenue_with_driver/dk.n_trips_with_driver), sub:"per trip"},
      {label:"Most active driver", value: topDriver.Driver, sub: fmtInt(topDriver.count) + " trips · " + fmtEUR(topDriver.sum,true)},
      {label:"Data window", value:"Apr 2022 – Aug 2026", sub:"export pulled 24 Aug 2026"}
    ];
    items.forEach(it => {
      const el = document.createElement("div");
      el.className = "kpi";
      el.innerHTML = `<div class="label">${it.label}</div><div class="value tabular" style="font-size:${it.label==="Most active driver"?"1.2rem":"1.7rem"};">${it.value}</div>${it.sub?`<div class="sub">${it.sub}</div>`:""}`;
      row.appendChild(el);
    });

    horizontalBarChart($("#chart-top-drivers"), {
      data: DATA.top_drivers.slice(0,15),
      label: d => d.Driver,
      value: d => d.sum,
      valueLabel: d => fmtEUR(d.sum, true),
      color: () => "var(--accent)",
      labelWidth: 152, barHeight: 20, gap: 9,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color:"var(--accent)"},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.mean)},
        {label:"Primary vehicle", value: d.top_vehicle},
        {label:"Active", value: d.first_trip + " → " + d.last_trip}
      ]
    });

    const driverParetoPts = DATA.driver_pareto.map(p => ({x:p.rank_pct, y:p.cum_pct}));
    paretoChart($("#chart-driver-pareto"), {data: driverParetoPts, height: 260, entityLabel: "drivers"});

    const dc = DATA.driver_concentration_summary;
    $("#driver-concentration-insight").innerHTML = `Just <b>${dc.n_for_50pct} drivers</b> account for half of all driver-attributed revenue; it takes <b>${dc.n_for_80pct}</b> of ${dc.total_drivers} to reach 80%. That's a tighter concentration than the client base — a small core of drivers carries most of the workload. <b>${topDriver.Driver}</b> alone is ${fmtPct(topDriver.sum/dk.revenue_with_driver*100)} of driver-attributed revenue.`;

    const t = $("#table-drivers");
    t.innerHTML = `<thead><tr>
      <th>Driver</th><th class="num">Trips</th><th class="num">Revenue</th><th class="num">Avg. fare</th><th>Primary vehicle</th><th>Affiliation</th><th>Active period</th>
    </tr></thead>`;
    const tb = document.createElement("tbody");
    DATA.top_drivers.forEach(d => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="strong">${d.Driver}</td>
        <td class="num">${fmtInt(d.count)}</td>
        <td class="num strong">${fmtEUR(d.sum)}</td>
        <td class="num">${fmtEUR(d.mean)}</td>
        <td>${d.top_vehicle}</td>
        <td>${d.affiliation}</td>
        <td>${d.first_trip} → ${d.last_trip}</td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
  }

  /* ============================================================
     BUILD: Operations charts
     ============================================================ */
  function buildOps(){
    columnChart($("#chart-hour"), {
      data: DATA.start_hour_distribution,
      value: d => d.count,
      label: d => String(d.StartHour).padStart(2,"0"),
      showXLabel: (d,i) => i % 3 === 0,
      yFormat: v => Math.round(v),
      height: 240,
      maxBarWidth: 16,
      tooltipRows: d => [
        {label:"Trips", value: fmtInt(d.count), color:"var(--accent)"},
        {label:"Revenue", value: fmtEUR(d.sum)}
      ],
      ariaLabel: "Trips by start hour"
    });

    columnChart($("#chart-pax"), {
      data: DATA.pax_distribution,
      value: d => d.sum,
      label: d => d.pax_bucket + " pax",
      yFormat: v => fmtEUR(v, true),
      height: 240,
      maxBarWidth: 40,
      tooltipRows: d => [
        {label:"Revenue", value: fmtEUR(d.sum), color:"var(--accent)"},
        {label:"Trips", value: fmtInt(d.count)},
        {label:"Avg. fare", value: fmtEUR(d.sum/d.count)}
      ]
    });

    const hourly = DATA.base_prices_hourly;
    const t = $("#table-hourly");
    t.innerHTML = `<thead><tr>
      <th>Vehicle</th><th class="num">N trips</th><th class="num">Median duration</th><th class="num">Median €/hr</th><th class="num">P25–P75</th><th>Confidence</th>
    </tr></thead>`;
    const tb = document.createElement("tbody");
    hourly.forEach(r => {
      const tr = document.createElement("tr");
      const conf = confChip(r.Confidence);
      tr.innerHTML = `
        <td class="strong">${r["Vehicle Category"]}</td>
        <td class="num">${fmtInt(r["N Trips"])}</td>
        <td class="num">${r["Median Duration (hr)"]!=null ? (Math.round(r["Median Duration (hr)"]*10)/10)+" hr" : "—"}</td>
        <td class="num">${r["Median (€/hr)"]!=null ? "€"+Math.round(r["Median (€/hr)"]) : "—"}</td>
        <td class="num">${r["P25 (€/hr)"]!=null ? "€"+Math.round(r["P25 (€/hr)"])+" – €"+Math.round(r["P75 (€/hr)"]) : "—"}</td>
        <td>${conf}</td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
  }

  function confChip(label){
    if(/High/i.test(label)) return `<span class="chip good"><span class="dot"></span>High</span>`;
    if(/Medium/i.test(label)) return `<span class="chip warn"><span class="dot"></span>Medium</span>`;
    return `<span class="chip crit"><span class="dot"></span>Low</span>`;
  }

  /* ============================================================
     BUILD: Pricing tables + calculator
     ============================================================ */
  function buildPricing(){
    const rows = DATA.base_prices_transfers;
    const t = $("#table-transfers");
    t.innerHTML = `<thead><tr>
      <th>Service</th><th>Vehicle</th><th class="num">N trips</th><th class="num">Median</th><th class="num">P25–P75</th><th class="num">Recommended</th><th>Confidence</th>
    </tr></thead>`;
    const tb = document.createElement("tbody");
    rows.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${r["Service Category"]}</td>
        <td class="strong">${r["Vehicle Category"]}</td>
        <td class="num">${fmtInt(r["N Trips (2022-2026)"])}</td>
        <td class="num">€${Math.round(r["Median (€ ex-VAT)"])}</td>
        <td class="num">€${Math.round(r["P25 (€)"])} – €${Math.round(r["P75 (€)"])}</td>
        <td class="num strong">€${Math.round(r["Recommended Base Price (€ ex-VAT)"])}</td>
        <td>${confChip(r.Confidence)}</td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);

    const t2 = $("#table-hourly-pricing");
    t2.innerHTML = `<thead><tr>
      <th>Vehicle</th><th class="num">Recommended €/hr</th><th class="num">4h package</th><th class="num">8h / full day</th><th>Confidence</th>
    </tr></thead>`;
    const tb2 = document.createElement("tbody");
    DATA.base_prices_hourly.forEach(r => {
      const tr = document.createElement("tr");
      const rate = r["Recommended Rate (€/hr)"];
      tr.innerHTML = `
        <td class="strong">${r["Vehicle Category"]}</td>
        <td class="num">${rate==="n/a" ? "n/a" : "€"+rate}</td>
        <td class="num">${r["Recommended 4h Package"]==="n/a" ? "n/a" : "€"+r["Recommended 4h Package"]}</td>
        <td class="num">${r["Recommended 8h (Full Day)"]==="n/a" ? "n/a" : "€"+r["Recommended 8h (Full Day)"]}</td>
        <td>${confChip(r.Confidence)}</td>`;
      tb2.appendChild(tr);
    });
    t2.appendChild(tb2);
  }

  /* ============================================================
     BUILD: Quote Calculator
     ============================================================ */
  function suggestVehicle(pax){
    const cap = DATA.quote_vehicle_capacity;
    for(const v of DATA.quote_vehicles){
      if(pax >= cap[v].min && pax <= cap[v].max) return v;
    }
    if(pax < 1) return DATA.quote_vehicles[0];
    return null; // exceeds largest known vehicle
  }

  function buildCalculator(){
    const ENG = DATA.quote_engine;
    const VEH = ["Sedan (E-Class)", "Van (V-Class)", "Luxury Sedan (S-Class)", "Minibus"];
    const TIER_LABEL = {
      net:      "Partner / wholesale net",
      standard: "Standard client",
      premium:  "Premium / retail"
    };

    /* ---------- the model ---------- */
    function priceTransfer(veh, km){
      const c = ENG.transfer_curves[veh];
      if(!c) return null;
      const xs = c.km, ys = c.price, n = xs.length;
      if(km <= xs[0]) return ys[0];
      if(km >= xs[n-1]){
        const m = (ys[n-1] - ys[n-2]) / Math.max(xs[n-1] - xs[n-2], 1e-6);
        return ys[n-1] + m * (km - xs[n-1]);
      }
      for(let i=0;i<n-1;i++){
        if(km >= xs[i] && km <= xs[i+1]){
          const t = (km - xs[i]) / Math.max(xs[i+1] - xs[i], 1e-6);
          return ys[i] + t * (ys[i+1] - ys[i]);
        }
      }
      return ys[n-1];
    }
    function priceDisposal(veh, hours, km){
      const f = ENG.disposal_fits[veh];
      if(!f) return null;
      return f.base + f.per_hour * Math.max(hours, f.min_hours) + f.per_km * (km || 0);
    }
    function roundTo5(v){ return Math.round(v / 5) * 5; }

    /* ---------- controls ---------- */
    const vehSel = $("#q-vehicle");
    VEH.forEach(v => {
      const o = document.createElement("option");
      o.value = v; o.textContent = v;
      vehSel.appendChild(o);
    });
    const coachOpt = document.createElement("option");
    coachOpt.value = "Coach (20-30 pax)"; coachOpt.textContent = "Coach (20-30 pax)";
    vehSel.appendChild(coachOpt);

    const tierSel = $("#q-tier");
    ["standard", "net", "premium"].forEach(k => {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = TIER_LABEL[k] + "  ·  ×" + ENG.tier_multipliers[k].toFixed(2);
      tierSel.appendChild(o);
    });
    tierSel.value = "standard";

    const paxInput = $("#q-pax");
    const suggestEl = $("#q-vehicle-suggest");
    let vehicleManuallySet = false;

    function applyPaxSuggestion(){
      const pax = Math.max(1, parseInt(paxInput.value || "1", 10));
      const s = suggestVehicle(pax);
      if(s){
        suggestEl.textContent = "· suggested: " + s;
        if(!vehicleManuallySet) vehSel.value = s;
      } else {
        suggestEl.textContent = "· above 30 pax — quote as multiple vehicles";
      }
    }
    paxInput.addEventListener("input", () => { applyPaxSuggestion(); recalc(); });
    vehSel.addEventListener("change", () => { vehicleManuallySet = true; recalc(); });
    tierSel.addEventListener("change", recalc);
    applyPaxSuggestion();

    /* destination dropdowns — both fed from the same road-distance table */
    function fillDest(sel, input, label){
      const o0 = document.createElement("option");
      o0.value = ""; o0.textContent = "— pick a place, or set the distance below —";
      sel.appendChild(o0);
      Object.entries(ENG.destinations).forEach(([name, km]) => {
        const o = document.createElement("option");
        o.value = km; o.textContent = name + " · " + km + " km";
        sel.appendChild(o);
      });
      sel.addEventListener("change", () => {
        if(sel.value === "") return;
        const km = Number(sel.value);
        input.value = Math.min(km, Number(input.max));
        label.textContent = input.value + " km";
        recalc();
      });
    }

    const distInput = $("#q-distance"), distVal = $("#q-distance-val");
    const excInput  = $("#q-exc-distance"), excVal = $("#q-exc-distance-val");
    const hoursInput = $("#q-hours"), hoursVal = $("#q-hours-val");
    const rtBox = $("#q-roundtrip");
    fillDest($("#q-dest"), distInput, distVal);
    fillDest($("#q-exc-dest"), excInput, excVal);
    [distInput, excInput, hoursInput].forEach(el => el.addEventListener("input", recalc));
    rtBox.addEventListener("change", recalc);

    /* rate-card lookup mode */
    const citySel = $("#q-city"), routeSel = $("#q-route");
    Object.keys(DATA.quote_known_routes).forEach(city => {
      const o = document.createElement("option");
      o.value = city; o.textContent = city;
      citySel.appendChild(o);
    });
    function fillRoutes(){
      routeSel.innerHTML = "";
      (DATA.quote_known_routes[citySel.value] || []).forEach((r, i) => {
        const o = document.createElement("option");
        o.value = i; o.textContent = r.label;
        routeSel.appendChild(o);
      });
    }
    citySel.addEventListener("change", () => { fillRoutes(); recalc(); });
    routeSel.addEventListener("change", recalc);
    fillRoutes();

    /* mode toggle */
    let mode = "transfer";
    const modeBtns = Array.from($("#calc-mode-toggle").querySelectorAll("button"));
    modeBtns.forEach(b => b.addEventListener("click", () => {
      modeBtns.forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      mode = b.dataset.mode;
      $("#mode-transfer-fields").classList.toggle("hidden", mode !== "transfer");
      $("#mode-disposal-fields").classList.toggle("hidden", mode !== "disposal");
      $("#mode-route-fields").classList.toggle("hidden", mode !== "route");
      $("#q-comparables").classList.toggle("hidden", mode === "route");
      recalc();
    }));

    /* ---------- multi-service quote sheet ----------
       recalc() prices exactly one service. Most real jobs are several — an event runs
       pickups, shuttles, a restaurant run and the return, over days. `currentLine` holds
       whatever the controls above are showing right now; "Add to sheet" copies it into
       `sheet`, which totals below and survives a page reload. */
    const SHEET_KEY = "bl-quote-sheet";
    let currentLine = null;
    let sheet = [];
    try{
      const raw = localStorage.getItem(SHEET_KEY);
      if(raw){
        const parsed = JSON.parse(raw);
        if(Array.isArray(parsed)) sheet = parsed.filter(l => l && typeof l.unit === "number" && l.qty > 0);
      }
    }catch(e){ sheet = []; }

    const vatSel = $("#q-vat");
    try{
      const v = localStorage.getItem(SHEET_KEY + "-vat");
      if(v !== null && ["0", "6", "21"].indexOf(v) >= 0) vatSel.value = v;
    }catch(e){}

    function saveSheet(){
      try{
        localStorage.setItem(SHEET_KEY, JSON.stringify(sheet));
        localStorage.setItem(SHEET_KEY + "-vat", vatSel.value);
      }catch(e){}
    }
    function esc(s){
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }
    function flash(sel, msg, bad){
      const el = $(sel);
      el.textContent = msg;
      el.style.color = bad ? "var(--critical)" : "var(--good)";
      clearTimeout(el._t);
      el._t = setTimeout(() => { el.textContent = ""; }, 3800);
    }

    /* Totals are computed from the already-rounded unit prices, so the sheet always
       reconciles with the headline figures the user actually saw when adding each line. */
    function sheetTotals(){
      let sub = 0, lo = 0, hi = 0, services = 0;
      sheet.forEach(l => {
        const line = l.unit * l.qty;
        sub += line;
        lo  += line * (l.lo || 1);
        hi  += line * (l.hi || 1);
        services += l.qty;
      });
      const vat = Number(vatSel.value);
      return {sub, lo, hi, services, vat, vatAmt: sub * vat / 100, gross: sub * (1 + vat / 100)};
    }

    function renderSheet(){
      const t = $("#table-quote-sheet"), note = $("#q-sheet-note"), countEl = $("#q-sheet-count");
      const copyBtn = $("#q-sheet-copy"), clearBtn = $("#q-sheet-clear");
      t.innerHTML = "";

      if(!sheet.length){
        countEl.textContent = "";
        copyBtn.disabled = true; clearBtn.disabled = true;
        t.innerHTML = '<tbody><tr><td class="q-empty">Nothing added yet — price a service above, then press <b>Add to sheet</b>.</td></tr></tbody>';
        note.innerHTML = "<b>What this is for.</b> The calculator above prices one leg. An event or a roadshow is a dozen: airport pickups on arrival day, the shuttle to the office each morning, the restaurant run and its return, the departures. Price each one, add it, and this sheet carries the running total — ex-VAT, VAT and gross — until you clear it. It stays put if you close the page.";
        return;
      }

      copyBtn.disabled = false; clearBtn.disabled = false;
      const T = sheetTotals();
      countEl.textContent = "· " + sheet.length + (sheet.length === 1 ? " line" : " lines")
                          + ", " + T.services + (T.services === 1 ? " service" : " services");

      const head = document.createElement("thead");
      head.innerHTML = '<tr><th>Service</th><th>Vehicle</th><th>Detail</th><th>Tier</th>'
                     + '<th class="num">Unit</th><th class="num">Qty</th><th class="num">Line, ex-VAT</th><th></th></tr>';
      t.appendChild(head);

      const tb = document.createElement("tbody");
      sheet.forEach((l, i) => {
        const tr = document.createElement("tr");
        const flag = l.flag === "good" ? ""
          : `<span class="chip ${l.flag === "crit" ? "crit" : "warn"}" style="margin-left:7px; padding:2px 7px 2px 5px; font-size:10px;"><span class="dot"></span>${l.flag === "crit" ? "confirm" : "range"}</span>`;
        tr.innerHTML =
          `<td class="strong" style="white-space:normal;">${esc(l.label)}${flag}</td>` +
          `<td>${esc(l.veh)}</td>` +
          `<td>${esc(l.detail)}</td>` +
          `<td>${l.tier ? esc(TIER_LABEL[l.tier]) : "Rate card"}</td>` +
          `<td class="num tabular">${fmtEUR0(l.unit)}</td>` +
          `<td class="num tabular">${l.qty}</td>` +
          `<td class="num tabular strong">${fmtEUR0(l.unit * l.qty)}</td>` +
          `<td class="num"><button type="button" class="q-del" data-i="${i}" title="Remove this line" aria-label="Remove ${esc(l.label)}">&times;</button></td>`;
        tb.appendChild(tr);
      });
      t.appendChild(tb);

      const tf = document.createElement("tfoot");
      tf.innerHTML =
        `<tr><td colspan="6" class="strong">Subtotal, ex-VAT</td><td class="num tabular strong">${fmtEUR0(T.sub)}</td><td></td></tr>` +
        `<tr><td colspan="6" style="color:var(--muted);">VAT ${T.vat}%</td><td class="num tabular" style="color:var(--muted);">${fmtEUR0(T.vatAmt)}</td><td></td></tr>` +
        `<tr class="grand"><td colspan="6">Total${T.vat ? ", incl. VAT" : ", VAT exempt"}</td><td class="num tabular">${fmtEUR0(T.gross)}</td><td></td></tr>`;
      t.appendChild(tf);

      const modelled = Math.round(T.lo) !== Math.round(T.sub) || Math.round(T.hi) !== Math.round(T.sub);
      const anyCrit = sheet.some(l => l.flag === "crit");
      const anyWarn = sheet.some(l => l.flag === "warn");
      note.innerHTML = "<b>Where this total can actually land.</b> "
        + (modelled
            ? "Half of comparable jobs settled between <span class='accent-text'>" + fmtEUR0(T.lo)
              + "</span> and <span class='accent-text'>" + fmtEUR0(T.hi)
              + "</span> ex-VAT for a basket like this. That is the sum of each line's own range, not a narrower statistical one — the errors don't cancel out, because the biggest single driver of price is which account it is, and that applies to every line at once."
            : "Every line here is a fixed rate-card price, so there is no modelled range around it.")
        + (anyCrit ? " <b>Lines marked <span class='chip crit' style='padding:2px 7px 2px 5px; font-size:10px;'><span class='dot'></span>confirm</span> need a supplier price</b> — coach work and Luxury Sedan at-disposal have too few bookings behind them to send from this page." : "")
        + (anyWarn && !anyCrit ? " Lines marked <b>range</b> are minibus or at-disposal work, where the itinerary itself moves the price by roughly ±10%." : "");
    }

    function quoteText(){
      const T = sheetTotals();
      const body = sheet.map((l, i) => {
        const line = l.unit * l.qty;
        return (i + 1) + ". " + l.label
          + "\n   " + l.veh + " · " + l.detail + " · " + (l.tier ? TIER_LABEL[l.tier] : "rate card")
          + (l.qty > 1 ? "\n   " + l.qty + " × " + fmtEUR0(l.unit) : "")
          + "\n   " + fmtEUR0(line);
      }).join("\n\n");
      let out = "QUOTE — Business Limousine\n==========================\n\n" + body
        + "\n\n--------------------------\n"
        + "Subtotal, ex-VAT: " + fmtEUR0(T.sub) + "\n"
        + "VAT " + T.vat + "%: " + fmtEUR0(T.vatAmt) + "\n"
        + "TOTAL: " + fmtEUR0(T.gross) + "\n";
      if(Math.round(T.lo) !== Math.round(T.sub) || Math.round(T.hi) !== Math.round(T.sub)){
        out += "\nComparable jobs settled between " + fmtEUR0(T.lo) + " and " + fmtEUR0(T.hi) + " ex-VAT.\n";
      }
      out += "\nPrices in 2026 euros, modelled on 8,032 executed bookings.\n";
      return out;
    }

    function copyToClipboard(text, okMsg){
      const done = () => flash("#q-sheet-toast", okMsg);
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(done).catch(() => legacy());
      } else { legacy(); }
      function legacy(){
        const ta = document.createElement("textarea");
        ta.value = text; ta.setAttribute("readonly", "");
        ta.style.position = "fixed"; ta.style.top = "-1000px";
        document.body.appendChild(ta); ta.select();
        let ok = false;
        try{ ok = document.execCommand("copy"); }catch(e){ ok = false; }
        document.body.removeChild(ta);
        ok ? done() : flash("#q-sheet-toast", "Clipboard blocked by the browser — select the table and copy manually.", true);
      }
    }

    /* ---------- sheet wiring ---------- */
    const labelInput = $("#q-label"), qtyInput = $("#q-qty");

    function addCurrent(){
      if(!currentLine){
        flash("#q-add-toast", "Nothing to add — this combination has no price.", true);
        return;
      }
      const qty = Math.max(1, Math.min(99, parseInt(qtyInput.value || "1", 10) || 1));
      const typed = labelInput.value.trim();
      sheet.push({
        label: typed || currentLine.auto,
        veh:   currentLine.veh,
        detail:currentLine.detail,
        tier:  currentLine.tier,
        unit:  currentLine.unit,
        qty:   qty,
        lo:    currentLine.lo,
        hi:    currentLine.hi,
        flag:  currentLine.flag
      });
      saveSheet();
      renderSheet();
      labelInput.value = ""; qtyInput.value = "1";
      flash("#q-add-toast", "Added" + (qty > 1 ? " ×" + qty : "") + " · " + fmtEUR0(currentLine.unit * qty) + " — see the sheet below.");
    }

    $("#q-add").addEventListener("click", addCurrent);
    labelInput.addEventListener("keydown", e => { if(e.key === "Enter"){ e.preventDefault(); addCurrent(); } });
    qtyInput.addEventListener("keydown", e => { if(e.key === "Enter"){ e.preventDefault(); addCurrent(); } });

    $("#table-quote-sheet").addEventListener("click", e => {
      const btn = e.target.closest(".q-del");
      if(!btn) return;
      const i = Number(btn.dataset.i);
      if(!(i >= 0 && i < sheet.length)) return;
      const removed = sheet.splice(i, 1)[0];
      saveSheet(); renderSheet();
      flash("#q-sheet-toast", "Removed “" + removed.label + "”.");
    });

    $("#q-sheet-clear").addEventListener("click", () => {
      if(!sheet.length) return;
      const n = sheet.length;
      sheet = [];
      saveSheet(); renderSheet();
      flash("#q-sheet-toast", "Cleared " + n + " line" + (n === 1 ? "" : "s") + ".");
    });

    $("#q-sheet-copy").addEventListener("click", () => {
      if(!sheet.length) return;
      copyToClipboard(quoteText(), "Quote copied — paste it into your mail or proposal.");
    });

    vatSel.addEventListener("change", () => { saveSheet(); renderSheet(); });

    /* ---------- the calculation ---------- */
    function recalc(){
      const veh = vehSel.value;
      const tier = tierSel.value;
      const mult = ENG.tier_multipliers[tier];
      const out = $("#q-output"), rangeEl = $("#q-range-text"), formulaEl = $("#q-formula-text");
      const chip = $("#q-source-chip"), chipText = $("#q-source-text"), note = $("#q-mode-note");
      const label = $("#q-result-label");

      distVal.textContent = distInput.value + " km";
      excVal.textContent = excInput.value + " km";
      hoursVal.textContent = Number(hoursInput.value).toFixed(1).replace(".0", "") + " h";

      const isCoach = veh === "Coach (20-30 pax)";

      /* -- rate-card lookup -- */
      if(mode === "route"){
        const r = (DATA.quote_known_routes[citySel.value] || [])[Number(routeSel.value) || 0];
        const p = r && r.prices[veh];
        label.textContent = "Rate-card price, ex-VAT";
        if(p == null){
          out.textContent = "—";
          rangeEl.textContent = "";
          formulaEl.textContent = isCoach
            ? "The published rate card doesn't cover coaches — use the reference figures in the pricing tab."
            : "This route isn't listed for that vehicle.";
          chip.className = "chip crit"; chipText.textContent = "Not on the card";
          currentLine = null;
        } else {
          out.textContent = fmtEUR0(p);
          rangeEl.textContent = "";
          formulaEl.textContent = "Published 2026 rate card — a lookup, not a calculation. The rate tier doesn't apply to card routes.";
          chip.className = "chip good"; chipText.textContent = "Official rate card";
          currentLine = {
            auto: citySel.value + " · " + r.label, veh: veh, detail: "Published route",
            tier: null, unit: p, lo: 1, hi: 1, flag: "good"
          };
        }
        note.innerHTML = "<b>Rate-card route.</b> The prices Business Limousine publishes for its named routes. Use this whenever the job matches a listed route — it's the number the client may already have seen.";
        syncAddButton();
        return;
      }

      /* -- coach: too little data for a curve -- */
      if(isCoach){
        const ref = DATA.quote_coach_reference;
        const flat = mode === "transfer" ? ref.local_airport_flat : ref.day_excursion_flat;
        const coachPrice = Math.round(flat * mult);
        label.textContent = "Reference figure, ex-VAT";
        out.textContent = fmtEUR0(coachPrice);
        rangeEl.textContent = "";
        formulaEl.textContent = "Flat reference from " + ref.n_quotes + " coach jobs on file — not a fitted curve. Confirm with a supplier before sending.";
        chip.className = "chip crit"; chipText.textContent = "Thin data · confirm manually";
        note.innerHTML = "<b>Coach work.</b> " + ref.note;
        // No fitted band exists for coach, so lo/hi stay at 1 rather than inventing a spread;
        // the "confirm" flag is what carries the uncertainty onto the sheet.
        currentLine = {
          auto: "Coach · " + (mode === "transfer" ? "airport transfer" : "day excursion"),
          veh: veh, detail: mode === "transfer" ? "Local airport, flat" : "Day excursion, flat",
          tier: tier, unit: coachPrice, lo: 1, hi: 1, flag: "crit"
        };
        buildComparables(null, null);
        syncAddButton();
        return;
      }

      let base, formula, acc, band, headline, autoLabel, detail;

      if(mode === "transfer"){
        const km = Number(distInput.value);
        const legs = rtBox.checked ? 2 : 1;
        const perLeg = priceTransfer(veh, km);
        base = perLeg * legs;
        acc = ENG.transfer_accuracy[veh];
        band = ENG.quote_bands["transfer|" + veh];
        autoLabel = "Transfer · " + km + " km" + (legs === 2 ? " return" : "");
        detail = km + " km" + (legs === 2 ? " × 2 legs" : " one way");
        label.textContent = legs === 2 ? "Return quote, ex-VAT" : "Quote, ex-VAT";
        formula = km + " km one way → " + fmtEUR0(Math.round(perLeg)) + " per leg"
                + (legs === 2 ? " × 2 legs" : "")
                + (mult !== 1 ? " × " + mult.toFixed(2) + " (" + TIER_LABEL[tier].toLowerCase() + ")" : "");
        note.innerHTML = "<b>Transfer.</b> Priced off the 2026 distance curve for a " + veh
          + ", fitted on " + fmtInt(acc.n) + " real bookings. <b>" + acc.w50
          + "% of those land within €50</b> of this number and the median miss is €" + acc.med_ae
          + ". Aller-retour is charged as two full legs — that's exactly what 379 two-leg dossiers in the book show.";
      } else {
        const hours = Number(hoursInput.value);
        const km = Number(excInput.value);
        base = priceDisposal(veh, hours, km);
        acc = ENG.disposal_accuracy[veh];
        band = ENG.quote_bands["disposal|" + veh];
        const f = ENG.disposal_fits[veh];
        autoLabel = "At disposal · " + hours + " h";
        detail = hours + " h" + (km > 0 ? " · " + km + " km out" : " · local");
        label.textContent = "Quote, ex-VAT";
        formula = "€" + f.base.toFixed(0) + " base + €" + f.per_hour.toFixed(0) + "/h × " + hours + "h"
                + (f.per_km > 0 && km > 0 ? " + €" + f.per_km.toFixed(2) + "/km × " + km + "km" : "")
                + (mult !== 1 ? ", × " + mult.toFixed(2) + " (" + TIER_LABEL[tier].toLowerCase() + ")" : "");
        note.innerHTML = "<b>At disposal / excursion.</b> Hours drive this price, not distance — the km term only covers a job that genuinely ranges out of town. Fitted on "
          + fmtInt(acc.n) + " real bookings; median miss €" + acc.med_ae
          + ", and <b>" + acc.wok + "% land within €50 or 10%</b>. On a full day the itinerary itself moves the price by about ±10%, so quote the range, not the point.";
      }

      const total = base * mult;
      headline = roundTo5(total);
      out.textContent = fmtEUR0(headline);

      const lo = roundTo5(total * band[0]), hi = roundTo5(total * band[1]);
      rangeEl.innerHTML = "Half of comparable jobs settled between <b>" + fmtEUR0(lo)
        + "</b> and <b>" + fmtEUR0(hi) + "</b>";
      formulaEl.textContent = formula;

      const tight = acc.w50 >= 80;
      chip.className = "chip " + (tight ? "good" : (acc.wok >= 40 ? "warn" : "crit"));
      chipText.textContent = tight
        ? acc.w50 + "% of real bookings within €50"
        : acc.wok + "% within €50 or 10% — read the range";

      currentLine = {
        auto: autoLabel, veh: veh, detail: detail, tier: tier, unit: headline,
        lo: band[0], hi: band[1],
        flag: tight ? "good" : (acc.wok >= 40 ? "warn" : "crit")
      };

      buildComparables(mode, veh);
      syncAddButton();
    }

    /* The add button is only live when the controls resolve to a real price — a route that
       isn't on the card for the chosen vehicle has nothing to put on the sheet. */
    function syncAddButton(){
      const btn = $("#q-add");
      btn.disabled = !currentLine;
      btn.title = currentLine ? "" : "No price for this combination";
    }

    /* ---------- comparables ---------- */
    function buildComparables(m, veh){
      const t = $("#table-comparables");
      t.innerHTML = "";
      const rows = (m && veh) ? (ENG.comparables[m + "|" + veh] || []) : [];
      if(!rows.length){
        t.innerHTML = '<tbody><tr><td style="color:var(--muted);">No comparable jobs on file for this combination.</td></tr></tbody>';
        return;
      }
      const isT = m === "transfer";
      const head = document.createElement("thead");
      head.innerHTML = isT
        ? '<tr><th>Real job</th><th class="num">One way</th><th class="num">Year</th><th class="num">Price, 2026 €</th></tr>'
        : '<tr><th>Real job</th><th class="num">Hours</th><th class="num">Range</th><th class="num">Year</th><th class="num">Price, 2026 €</th></tr>';
      t.appendChild(head);
      const tb = document.createElement("tbody");
      rows.forEach(r => {
        const tr = document.createElement("tr");
        tr.innerHTML = isT
          ? `<td>${r.route}</td><td class="num tabular">${r.km} km</td><td class="num tabular">${r.year}</td><td class="num tabular strong">${fmtEUR0(r.price)}</td>`
          : `<td>${r.route}</td><td class="num tabular">${r.hours} h</td><td class="num tabular">${r.km >= 3 ? r.km + " km" : "local"}</td><td class="num tabular">${r.year}</td><td class="num tabular strong">${fmtEUR0(r.price)}</td>`;
        tb.appendChild(tr);
      });
      t.appendChild(tb);
    }

    recalc();
    renderSheet();
    buildCurveChart();
    buildValidationTable();
    buildFormulaReference();
  }

  /* ============================================================
     Distance curve — one line per vehicle, real km on the x axis
     ============================================================ */
  function buildCurveChart(){
    const ENG = DATA.quote_engine;
    const container = $("#chart-curve");
    const series = Object.keys(ENG.transfer_curves);
    const colors = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];

    const render = () => {
      container.innerHTML = "";
      const width = Math.max(container.clientWidth || 480, 300);
      const height = 300;
      const padL = 56, padR = 14, padT = 14, padB = 40;
      const plotW = width - padL - padR, plotH = height - padT - padB;
      const maxKm = 220;
      let maxP = 0;
      series.forEach(s => {
        ENG.transfer_curves[s].km.forEach((k, i) => {
          if(k <= maxKm) maxP = Math.max(maxP, ENG.transfer_curves[s].price[i]);
        });
      });
      maxP *= 1.1;
      const xAt = k => padL + (k / maxKm) * plotW;
      const yAt = v => padT + plotH - (v / maxP) * plotH;

      const svg = svgEl("svg", {class:"chart", viewBox:`0 0 ${width} ${height}`, role:"img",
        "aria-label":"List price by one-way distance, per vehicle category"});

      for(let i=0;i<=4;i++){
        const val = (maxP/4)*i, y = yAt(val);
        svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:y, y2:y, class:"grid-line"}));
        const lb = svgEl("text", {x:padL-8, y:y+3, class:"axis-label", "text-anchor":"end"});
        lb.textContent = fmtEUR(val, true);
        svg.appendChild(lb);
      }
      [0, 50, 100, 150, 200].forEach(k => {
        const lb = svgEl("text", {x:xAt(k), y:padT+plotH+18, class:"axis-label", "text-anchor":"middle"});
        lb.textContent = k + " km";
        svg.appendChild(lb);
      });
      svg.appendChild(svgEl("line", {x1:padL, x2:padL+plotW, y1:padT+plotH, y2:padT+plotH, class:"baseline"}));

      series.forEach((s, si) => {
        const c = ENG.transfer_curves[s];
        let d = "";
        c.km.forEach((k, i) => {
          if(k > maxKm) return;
          d += (d ? " L " : "M ") + xAt(k) + " " + yAt(c.price[i]);
        });
        svg.appendChild(svgEl("path", {d, fill:"none", stroke:colors[si % colors.length],
          "stroke-width":"2", "stroke-linejoin":"round", "stroke-linecap":"round"}));
        c.km.forEach((k, i) => {
          if(k > maxKm) return;
          const dot = svgEl("circle", {cx:xAt(k), cy:yAt(c.price[i]), r:"4",
            fill:colors[si % colors.length], stroke:"var(--surface)", "stroke-width":"2"});
          dot.style.cursor = "pointer";
          dot.addEventListener("mouseenter", e => showTooltip(e.clientX, e.clientY, s, [
            {label:"One-way distance", value:Math.round(k) + " km"},
            {label:"2026 list price", value:fmtEUR0(c.price[i]), color:colors[si % colors.length]}
          ]));
          dot.addEventListener("mouseleave", hideTooltip);
          svg.appendChild(dot);
        });
      });
      container.appendChild(svg);

      const leg = document.createElement("div");
      leg.className = "legend";
      leg.innerHTML = series.map((s, i) =>
        `<div class="legend-item"><span class="swatch" style="background:${colors[i % colors.length]};"></span><b style="color:var(--ink);">${s}</b></div>`
      ).join("");
      container.appendChild(leg);
    };
    registerChart(render);
  }

  /* ============================================================
     Validation table — the office's own quotes, re-priced
     ============================================================ */
  function buildValidationTable(){
    const rows = DATA.quote_engine.validation;
    const t = $("#table-validation");
    t.innerHTML = "";
    const head = document.createElement("thead");
    head.innerHTML = '<tr><th>Executed quote</th><th class="num">Quoted</th><th class="num">Model</th><th class="num">Gap</th></tr>';
    t.appendChild(head);
    const tb = document.createElement("tbody");
    rows.forEach(r => {
      const within = Math.abs(r.diff) <= 50 || Math.abs(r.pct) <= 10;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${r.label}</td>
        <td class="num tabular">${fmtEUR0(r.actual)}</td>
        <td class="num tabular strong">${fmtEUR0(r.model)}</td>
        <td class="num tabular" style="color:${within ? "var(--good)" : "var(--warning)"}; font-weight:650; white-space:nowrap;">${r.diff > 0 ? "+" : ""}${r.pct}%</td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    const mean = (rows.reduce((a, r) => a + Math.abs(r.pct), 0) / rows.length).toFixed(1);
    const foot = document.createElement("tfoot");
    foot.innerHTML = `<tr><td colspan="3" class="strong">Average gap, all eight quotes</td><td class="num tabular strong">${mean}%</td></tr>`;
    t.appendChild(foot);
  }

  /* ============================================================
     Formula reference
     ============================================================ */
  function buildFormulaReference(){
    const ENG = DATA.quote_engine;
    const t = $("#table-formula-reference");
    t.innerHTML = "";
    const head = document.createElement("thead");
    head.innerHTML = `<tr>
      <th>Vehicle</th>
      <th class="num">Local (13 km)</th>
      <th class="num">Regional (50 km)</th>
      <th class="num">Long (150 km)</th>
      <th class="num">At disposal</th>
      <th class="num">8-hour day</th>
      <th class="num">Within €50</th>
    </tr>`;
    t.appendChild(head);
    const tb = document.createElement("tbody");

    function pt(veh, km){
      const c = ENG.transfer_curves[veh];
      const xs = c.km, ys = c.price, n = xs.length;
      if(km <= xs[0]) return ys[0];
      if(km >= xs[n-1]){
        const m = (ys[n-1]-ys[n-2]) / Math.max(xs[n-1]-xs[n-2], 1e-6);
        return ys[n-1] + m*(km-xs[n-1]);
      }
      for(let i=0;i<n-1;i++){
        if(km >= xs[i] && km <= xs[i+1]){
          const q = (km-xs[i])/Math.max(xs[i+1]-xs[i],1e-6);
          return ys[i] + q*(ys[i+1]-ys[i]);
        }
      }
      return ys[n-1];
    }

    Object.keys(ENG.transfer_curves).forEach(veh => {
      const f = ENG.disposal_fits[veh];
      const ta = ENG.transfer_accuracy[veh];
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="strong">${veh}</td>
        <td class="num tabular">${fmtEUR0(Math.round(pt(veh, 13)))}</td>
        <td class="num tabular">${fmtEUR0(Math.round(pt(veh, 50)))}</td>
        <td class="num tabular">${fmtEUR0(Math.round(pt(veh, 150)))}</td>
        <td class="num tabular">€${f ? f.base.toFixed(0) : "—"} + €${f ? f.per_hour.toFixed(0) : "—"}/h${f && f.per_km > 0 ? " + €" + f.per_km.toFixed(2) + "/km" : ""}</td>
        <td class="num tabular">${f ? fmtEUR0(Math.round(f.base + f.per_hour*8)) : "—"}</td>
        <td class="num tabular">${ta.w50}%</td>`;
      tb.appendChild(tr);
    });
    const ref = DATA.quote_coach_reference;
    const cr = document.createElement("tr");
    cr.innerHTML = `
      <td class="strong">Coach (20-30 pax)</td>
      <td class="num tabular">${fmtEUR0(ref.local_airport_flat)}</td>
      <td class="num tabular" colspan="2" style="color:var(--muted);">no curve — ${ref.n_quotes} jobs on file</td>
      <td class="num tabular" style="color:var(--muted);">—</td>
      <td class="num tabular">${fmtEUR0(ref.day_excursion_flat)}</td>
      <td class="num tabular" style="color:var(--muted);">n/a</td>`;
    tb.appendChild(cr);
    t.appendChild(tb);
  }

  /* ============================================================
     BUILD: Chauffeur hours & tranches
     ============================================================ */
  function buildDriverHours(){
    const DH = DATA.driver_hours;
    if(!DH) return;
    const D = 0, DATE = 1, START = 2, HRS = 3, VEH = 4, SVC = 5;

    const salaried = new Set(DH.salaried_default);

    /* ---- filter controls ---- */
    const fromEl = $("#dh-from"), toEl = $("#dh-to");
    const vehEl = $("#dh-vehicle"), svcEl = $("#dh-service");
    const minEl = $("#dh-min"), trEl = $("#dh-tranche"), overEl = $("#dh-over"), presetEl = $("#dh-preset");

    function fillSel(el, items, allLabel){
      const o = document.createElement("option");
      o.value = "-1"; o.textContent = allLabel;
      el.appendChild(o);
      items.forEach((n, i) => {
        const x = document.createElement("option");
        x.value = String(i); x.textContent = n;
        el.appendChild(x);
      });
    }
    fillSel(vehEl, DH.vehicles, "All vehicles");
    fillSel(svcEl, DH.services, "All services");

    const maxDate = DH.coverage.date_max, minDate = DH.coverage.date_min;
    function monthStart(iso, back){
      const d = new Date(iso + "T12:00:00");
      d.setMonth(d.getMonth() - (back || 0), 1);
      return d.toISOString().slice(0, 10);
    }
    function monthEnd(iso){
      const d = new Date(iso + "T12:00:00");
      d.setMonth(d.getMonth() + 1, 0);
      return d.toISOString().slice(0, 10);
    }
    const lastMonthStart = monthStart(maxDate, 1);
    const presets = [
      {label:"This month so far", from:monthStart(maxDate, 0), to:maxDate},
      {label:"Last full month", from:lastMonthStart, to:monthEnd(lastMonthStart)},
      {label:"Last 30 days", from:addDays(maxDate, -29), to:maxDate},
      {label:"Last 90 days", from:addDays(maxDate, -89), to:maxDate},
      {label:"Everything on file", from:minDate, to:maxDate}
    ];
    presets.forEach((p, i) => {
      const o = document.createElement("option");
      o.value = String(i); o.textContent = p.label;
      presetEl.appendChild(o);
    });
    const customOpt = document.createElement("option");
    customOpt.value = "custom"; customOpt.textContent = "Custom dates";
    presetEl.appendChild(customOpt);
    function addDays(iso, n){
      const d = new Date(iso + "T12:00:00");
      d.setDate(d.getDate() + n);
      return d.toISOString().slice(0, 10);
    }

    fromEl.min = minDate; fromEl.max = maxDate;
    toEl.min = minDate; toEl.max = maxDate;
    presetEl.value = "0";
    fromEl.value = presets[0].from; toEl.value = presets[0].to;

    presetEl.addEventListener("change", () => {
      const p = presets[Number(presetEl.value)];
      if(p){ fromEl.value = p.from; toEl.value = p.to; render(); }
    });
    [fromEl, toEl, vehEl, svcEl, minEl, trEl, overEl].forEach(el =>
      el.addEventListener("change", () => { if(el === fromEl || el === toEl) presetEl.value = "custom"; render(); }));

    /* ---- the calculation ---- */
    function shiftsFor(){
      const from = fromEl.value, to = toEl.value;
      const veh = Number(vehEl.value), svc = Number(svcEl.value);
      const byShift = new Map();
      for(const r of DH.rows){
        if(r[DATE] < from || r[DATE] > to) continue;
        if(veh >= 0 && r[VEH] !== veh) continue;
        if(svc >= 0 && r[SVC] !== svc) continue;
        const k = r[D] + "|" + r[DATE];
        let s = byShift.get(k);
        if(!s){ s = {d:r[D], date:r[DATE], missions:0, timed:0, minStart:null, maxStart:null, maxEnd:null}; byShift.set(k, s); }
        s.missions++;
        if(r[START] != null){
          if(s.minStart === null || r[START] < s.minStart) s.minStart = r[START];
          if(s.maxStart === null || r[START] > s.maxStart) s.maxStart = r[START];
        }
        if(r[HRS] != null && r[START] != null){
          s.timed++;
          const end = r[START] + r[HRS];
          if(s.maxEnd === null || end > s.maxEnd) s.maxEnd = end;
        }
      }
      return [...byShift.values()];
    }

    function billFor(span, minH, tranche, cap){
      let billed;
      if(span == null) billed = minH;                         // unclocked — the minimum stands
      else if(tranche <= 1) billed = Math.max(minH, span);
      else billed = Math.max(minH, Math.ceil(span / tranche) * tranche);
      if(cap) billed = Math.min(billed, 12);
      return billed;
    }

    function compute(){
      const minH = Number(minEl.value), tranche = Number(trEl.value);
      const cap = overEl.value === "cap";
      const shifts = shiftsFor();
      const per = new Map();
      let covTimed = 0;
      for(const s of shifts){
        const full = s.timed > 0 && s.timed === s.missions;
        // A shift with no end time is still bounded below by its own last pickup: a chauffeur
        // whose first job starts at 09:15 and whose last starts at 22:20 worked at least 13
        // hours, whether or not anyone closed the mission. Using that floor beats billing the
        // bare minimum, and it only ever uses times that were actually recorded.
        let span = null, floored = false;
        if(s.maxEnd != null && s.minStart != null) span = Math.max(s.maxEnd - s.minStart, 0);
        if(s.minStart != null && s.maxStart != null && s.maxStart > s.minStart){
          const bound = s.maxStart - s.minStart;
          if(span == null || bound > span){ span = bound; floored = true; }
        }
        if(s.maxEnd != null) covTimed++;
        const billed = billFor(span, minH, tranche, cap);
        let p = per.get(s.d);
        if(!p){ p = {d:s.d, shifts:0, missions:0, measured:0, partial:0, assumed:0,
                     spanSum:0, billed:0, t6:0, t12:0, t18:0}; per.set(s.d, p); }
        p.shifts++; p.missions += s.missions; p.billed += billed;
        if(span == null) p.assumed++;
        else { p.spanSum += span; (full && !floored) ? p.measured++ : p.partial++; }
        if(billed <= 6) p.t6++; else if(billed <= 12) p.t12++; else p.t18++;
      }
      return {per:[...per.values()], shifts, covTimed, minH, tranche, cap};
    }

    /* ---- driver chips ---- */
    function renderChips(activeDrivers){
      const box = $("#dh-driver-chips");
      box.innerHTML = "";
      if(!activeDrivers.length){
        box.innerHTML = '<span class="card-sub">No chauffeur worked in this period with these filters.</span>';
        return;
      }
      activeDrivers.forEach(i => {
        const isFixed = salaried.has(i);
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip " + (isFixed ? "warn" : "good");
        b.style.cursor = "pointer";
        b.style.border = "1px solid var(--border-strong)";
        b.title = isFixed ? "Fixed salary — excluded. Click to count as an extra."
                          : "Extra — counted. Click to mark as fixed salary.";
        b.innerHTML = `<span class="dot"></span>${DH.drivers[i]}${isFixed ? " · fixed" : ""}`;
        b.addEventListener("click", () => {
          if(salaried.has(i)) salaried.delete(i); else salaried.add(i);
          render();
        });
        box.appendChild(b);
      });
    }

    /* ---- render ---- */
    let lastText = "";
    function render(){
      const {per, shifts, covTimed, minH, tranche, cap} = compute();
      per.sort((a, b) => b.billed - a.billed);
      const active = [...new Set(shifts.map(s => s.d))].sort((a, b) => DH.drivers[a].localeCompare(DH.drivers[b]));
      renderChips(active);

      const extras = per.filter(p => !salaried.has(p.d));
      const totBilled = extras.reduce((a, p) => a + p.billed, 0);
      const totShifts = extras.reduce((a, p) => a + p.shifts, 0);
      const toConfirm = extras.reduce((a, p) => a + p.assumed + p.partial, 0);

      const kpis = [
        {label:"Extra chauffeurs", value:fmtInt(extras.length), sub:`${fmtInt(per.length - extras.length)} on fixed salary, excluded`},
        {label:"Shifts", value:fmtInt(totShifts), sub:`${fmtInt(extras.reduce((a,p)=>a+p.missions,0))} jobs`},
        {label:"Hours to pay", value:fmtInt(totBilled) + " h", sub:`minimum ${minH}h, ${tranche <= 1 ? "exact hours" : tranche + "h tranches"}${cap ? ", capped at 12h" : ""}`},
        {label:"Shifts to confirm", value:fmtInt(toConfirm), sub:"no end time recorded"}
      ];
      const kg = $("#dh-kpis");
      kg.innerHTML = "";
      kpis.forEach(k => {
        const d = document.createElement("div");
        d.className = "kpi";
        d.innerHTML = `<div class="label">${k.label}</div><div class="value tabular">${k.value}</div><div class="sub">${k.sub}</div>`;
        kg.appendChild(d);
      });

      const t = $("#dh-table");
      t.innerHTML = "";
      const head = document.createElement("thead");
      head.innerHTML = `<tr>
        <th>Chauffeur</th><th class="num">Jobs</th><th class="num">Shifts</th>
        <th class="num">6 h</th><th class="num">12 h</th><th class="num">Over 12 h</th>
        <th class="num">Measured</th><th class="num">To confirm</th><th class="num">Hours to pay</th></tr>`;
      t.appendChild(head);
      const tb = document.createElement("tbody");
      if(!extras.length){
        tb.innerHTML = '<tr><td colspan="9" style="color:var(--muted);">Nothing to show — every chauffeur who worked in this period is marked as fixed salary, or no shift matches these filters.</td></tr>';
      }
      extras.forEach(p => {
        const conf = p.assumed + p.partial;
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="strong">${DH.drivers[p.d]}</td>
          <td class="num tabular">${p.missions}</td>
          <td class="num tabular">${p.shifts}</td>
          <td class="num tabular">${p.t6 || "—"}</td>
          <td class="num tabular">${p.t12 || "—"}</td>
          <td class="num tabular">${p.t18 || "—"}</td>
          <td class="num tabular">${p.measured ? p.measured : "—"}</td>
          <td class="num tabular" style="${conf ? "color:var(--warning); font-weight:650;" : ""}">${conf || "—"}</td>
          <td class="num tabular strong">${fmtInt(p.billed)} h</td>`;
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      if(extras.length){
        const foot = document.createElement("tfoot");
        foot.innerHTML = `<tr><td class="strong">Total</td>
          <td class="num tabular strong">${fmtInt(extras.reduce((a,p)=>a+p.missions,0))}</td>
          <td class="num tabular strong">${fmtInt(totShifts)}</td>
          <td class="num tabular">${fmtInt(extras.reduce((a,p)=>a+p.t6,0))}</td>
          <td class="num tabular">${fmtInt(extras.reduce((a,p)=>a+p.t12,0))}</td>
          <td class="num tabular">${fmtInt(extras.reduce((a,p)=>a+p.t18,0))}</td>
          <td class="num tabular">${fmtInt(extras.reduce((a,p)=>a+p.measured,0))}</td>
          <td class="num tabular">${fmtInt(toConfirm)}</td>
          <td class="num tabular strong">${fmtInt(totBilled)} h</td></tr>`;
        t.appendChild(foot);
      }

      $("#dh-table-sub").textContent =
        `${fromEl.value} to ${toEl.value} · ${vehEl.options[vehEl.selectedIndex].textContent} · ${svcEl.options[svcEl.selectedIndex].textContent}`;
      $("#dh-cov-pct").textContent = shifts.length
        ? Math.round(100 * covTimed / shifts.length) + "% of the shifts in this period"
        : DH.coverage.pct_timed + "%";

      /* plain-text version for the reply email */
      const lines = [
        `Heures chauffeurs extra — ${fromEl.value} au ${toEl.value}`,
        `Minimum ${minH}h par service, tranches de ${tranche <= 1 ? "heures exactes" : tranche + "h"}${cap ? ", plafond 12h" : ""}`,
        ""
      ];
      extras.forEach(p => {
        const conf = p.assumed + p.partial;
        lines.push(`${DH.drivers[p.d]} : ${p.billed} h  (${p.shifts} service${p.shifts > 1 ? "s" : ""}` +
                   `, ${p.t6} × 6h, ${p.t12} × 12h${p.t18 ? ", " + p.t18 + " × +12h" : ""}` +
                   `${conf ? `, ${conf} à confirmer` : ""})`);
      });
      lines.push("", `TOTAL : ${totBilled} h sur ${totShifts} services`);
      if(toConfirm) lines.push(`${toConfirm} service${toConfirm > 1 ? "s" : ""} sans heure de fin enregistrée — comptés au minimum de ${minH}h, à confirmer.`);
      lastText = lines.join("\n");
    }

    $("#dh-copy").addEventListener("click", () => {
      const el = $("#dh-toast");
      const done = () => { el.textContent = "Copied — paste it into your reply."; el.style.color = "var(--good)";
                           clearTimeout(el._t); el._t = setTimeout(() => el.textContent = "", 4000); };
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(lastText).then(done).catch(() => fallback());
      } else fallback();
      function fallback(){
        const ta = document.createElement("textarea");
        ta.value = lastText; ta.style.position = "fixed"; ta.style.top = "-1000px";
        document.body.appendChild(ta); ta.select();
        let ok = false;
        try { ok = document.execCommand("copy"); } catch(e){ ok = false; }
        document.body.removeChild(ta);
        if(ok) done();
        else { el.textContent = "Clipboard blocked — the figures are in the table above."; el.style.color = "var(--critical)"; }
      }
    });

    /* ---- data-quality notes ---- */
    const mn = $("#dh-merged-note");
    if(DH.merged.length){
      const top = DH.merged.slice(0, 3).map(m => `<b>${m.kept}</b> (+${m.dropped.length})`).join(", ");
      mn.innerHTML = `${DH.merged.length} name${DH.merged.length > 1 ? "s were" : " was"} written more than one way and joined: ${top}${DH.merged.length > 3 ? ", and others" : ""}.`;
    } else {
      mn.textContent = "No duplicate spellings needed joining.";
    }
    const dz = $("#dh-dupes");
    if(DH.duplicates.length){
      dz.innerHTML = '<div class="card-sub" style="margin-bottom:6px;"><b>Left separate — check whether these are one person or two:</b></div>' +
        '<div class="legend" style="margin-top:0;">' +
        DH.duplicates.map(d =>
          `<div class="legend-item"><b style="color:var(--ink);">${d.a}</b> (${d.na} jobs) &nbsp;vs&nbsp; <b style="color:var(--ink);">${d.b}</b> (${d.nb} jobs)</div>`
        ).join("") + "</div>";
    }

    /* ---- what to fix at source ---- */
    const H = DH.health;
    if(H){
      const pctNoEnd = Math.round(100 * H.no_end_time / Math.max(H.with_driver, 1));
      const pctNoDrv = Math.round(100 * H.no_driver / Math.max(H.live_missions, 1));
      const steps = [
        {n:"1", t:"Export again, every time",
         b:`This file was pulled <b>${H.export_pulled}</b> and stops at dossier <b>${fmtInt(H.last_dossier)}</b>. Anything entered in Waynium afterwards is invisible here — including jobs whose service date is in the past but which were typed in later. If a chauffeur's hours look short, this is the first thing to check.`},
        {n:"2", t:"Put the chauffeur's hours in the export",
         b:`Waynium already shows an hours figure per mission — the 6 or 4 in the right-hand column of the mission list. That column is <b>not in this export</b>: the three fields that could carry it (<i>Heures réelles chauffeur</i>, <i>Stand by</i>, <i>Forfait net chauffeur</i>) are empty on all ${fmtInt(H.live_missions)} missions. Add it to the export template and the report below stops inferring anything — it just reads your own numbers.`},
        {n:"3", t:"Fill in the end time",
         b:`<b>${fmtInt(H.no_end_time)} missions</b> with a named chauffeur (${pctNoEnd}%) have a start time but no finish. That is the whole <b>To confirm</b> column. Closing the mission in Waynium when the chauffeur reports back is what turns an assumed 6-hour tranche into a measured one.`},
        {n:"4", t:"One spelling per chauffeur, and always a chauffeur",
         b:`<b>${fmtInt(H.no_driver)} missions</b> (${pctNoDrv}%) have no chauffeur recorded at all, so nobody can be paid for them from this data. And a driver written two ways is counted as two people — pick the spelling in Waynium's chauffeur list rather than typing the name.`}
      ];
      $("#dh-health").innerHTML = steps.map(x => `
        <div style="display:flex; gap:14px; padding:14px 0; border-top:1px solid var(--border);">
          <div style="flex:0 0 28px; height:28px; border-radius:50%; background:var(--accent-soft); color:var(--accent);
                      display:flex; align-items:center; justify-content:center; font-weight:700; font-size:13px;">${x.n}</div>
          <div><div class="strong" style="margin-bottom:3px;">${x.t}</div>
               <div class="card-sub" style="line-height:1.55;">${x.b}</div></div>
        </div>`).join("") +
        `<div class="card-sub" style="margin-top:14px; padding-top:12px; border-top:1px solid var(--border);">
           Reading this export: ${fmtInt(H.live_missions)} live missions, ${fmtInt(H.with_driver)} with a named chauffeur,
           latest service date ${H.last_mission_date}. ${H.unpriced_kept} unpriced legs are counted as worked shifts —
           a second vehicle on the same job still had somebody driving it.</div>`;
    }

    render();
  }

  /* ============================================================
     BUILD: Review requests
     ============================================================ */
  const RV_TEMPLATES = {
    en: {
      subject: n => "Thank you for travelling with Business Limousine",
      greet:   n => n ? `Dear ${n},` : "Dear guest,",
      body: (o) => [
        (o.date ? `Thank you for choosing Business Limousine for your journey on ${o.date}${o.routeClause}.`
                : `Thank you for choosing Business Limousine for your recent journey${o.routeClause}.`),
        o.driverClause
          ? `It was a pleasure to look after you — ${o.driverClause} enjoyed having you on board.`
          : `It was a pleasure to look after you.`,
        `If everything went well, would you take thirty seconds to say so on Google? Reviews are how smaller operators like us get found, and every one of them genuinely helps.`,
        o.link,
        `And if anything fell short, please reply to this email instead — we would much rather hear it from you directly and put it right.`,
        `With thanks,`
      ],
      routeWord: (a,b) => a && b ? `, from ${a} to ${b}` : "",
    },
    fr: {
      subject: n => "Merci d'avoir voyagé avec Business Limousine",
      greet:   n => n ? `Bonjour ${n},` : "Bonjour,",
      body: (o) => [
        (o.date ? `Merci d'avoir choisi Business Limousine pour votre trajet du ${o.date}${o.routeClause}.`
                : `Merci d'avoir choisi Business Limousine pour votre trajet récent${o.routeClause}.`),
        o.driverClause
          ? `Ce fut un plaisir de vous accompagner — ${o.driverClause} a été ravi de vous conduire.`
          : `Ce fut un plaisir de vous accompagner.`,
        `Si tout s'est bien passé, accepteriez-vous de le dire en trente secondes sur Google ? C'est ainsi qu'une maison de notre taille se fait connaître, et chaque avis compte vraiment.`,
        o.link,
        `Et si quelque chose n'a pas été à la hauteur, répondez plutôt à cet e-mail — nous préférons de loin l'apprendre directement de vous et y remédier.`,
        `Avec nos remerciements,`
      ],
      routeWord: (a,b) => a && b ? `, de ${a} à ${b}` : "",
    },
    nl: {
      subject: n => "Bedankt dat u met Business Limousine reisde",
      greet:   n => n ? `Beste ${n},` : "Beste reiziger,",
      body: (o) => [
        (o.date ? `Hartelijk dank dat u op ${o.date} voor Business Limousine koos${o.routeClause}.`
                : `Hartelijk dank dat u onlangs voor Business Limousine koos${o.routeClause}.`),
        o.driverClause
          ? `Het was een genoegen u te mogen rijden — ${o.driverClause} deed dat met plezier.`
          : `Het was een genoegen u te mogen rijden.`,
        `Als alles naar wens verliep, wilt u dat dan in dertig seconden op Google laten weten? Zo vinden nieuwe klanten een kleiner bedrijf als het onze, en elke beoordeling helpt echt.`,
        o.link,
        `En als iets niet in orde was, antwoord dan liever op deze e-mail — wij horen het veel liever rechtstreeks van u en lossen het op.`,
        `Met dank,`
      ],
      routeWord: (a,b) => a && b ? `, van ${a} naar ${b}` : "",
    }
  };

  function buildReviews(){
    const RV = DATA.reviews;
    const rides = RV.rides;
    const link = RV.review_url;
    const s = RV.stats;

    /* ---- headline numbers ---- */
    const kpis = [
      {label:"Rides you can ask about", value:fmtInt(s.direct_rides),
       sub:"driven by your own chauffeurs"},
      {label:"In the last 30 days", value:fmtInt(s.askable_last_30),
       sub:`to ${s.latest_ride}`},
      {label:"Customer emails on file", value:s.emails_in_export,
       sub:"in 6 MB of export — paste them in"},
      {label:"Typical gap between rides", value:s.median_days_between_rides + " days",
       sub:"for a repeat account — don't ask every time"}
    ];
    const kg = $("#review-kpis");
    kg.innerHTML = "";
    kpis.forEach(k => {
      const d = document.createElement("div");
      d.className = "kpi";
      d.innerHTML = `<div class="label">${k.label}</div><div class="value tabular">${k.value}</div><div class="sub">${k.sub}</div>`;
      kg.appendChild(d);
    });

    $("#rv-link-display").textContent = link;
    const own = $("#rv-own-count");
    if(own) own.textContent = fmtInt(s.direct_rides);

    /* ---- ride picker ---- */
    const rideSel = $("#rv-ride");
    const blank = document.createElement("option");
    blank.value = "-1";
    blank.textContent = "— no specific ride, write it generally —";
    rideSel.appendChild(blank);
    rides.forEach((r, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = `${r.date} · ${r.client}${r.passenger ? " · " + r.passenger : ""}`;
      rideSel.appendChild(o);
    });
    rideSel.value = rides.length ? "0" : "-1";

    const nameEl = $("#rv-name"), nameSrc = $("#rv-name-src"), emailEl = $("#rv-email");
    const langEl = $("#rv-lang"), drvEl = $("#rv-mention-driver"), signEl = $("#rv-signoff");
    let nameEdited = false;
    nameEl.addEventListener("input", () => { nameEdited = true; render(); });

    function currentRide(){
      const i = Number(rideSel.value);
      return i >= 0 ? rides[i] : null;
    }

    function syncName(){
      const r = currentRide();
      if(nameEdited) { nameSrc.textContent = ""; return; }
      if(r && r.passenger){
        nameEl.value = r.passenger;
        nameSrc.textContent = "· from the booking";
      } else {
        nameEl.value = "";
        nameSrc.textContent = r ? "· no passenger name on this booking" : "";
      }
    }

    rideSel.addEventListener("change", () => { nameEdited = false; syncName(); render(); });
    [langEl, drvEl, signEl].forEach(el => el.addEventListener("change", render));
    signEl.addEventListener("input", render);

    /* ---- compose ---- */
    function compose(){
      const t = RV_TEMPLATES[langEl.value] || RV_TEMPLATES.en;
      const r = currentRide();
      const name = nameEl.value.trim();
      const dateStr = r ? formatRideDate(r.date, langEl.value) : formatRideDate(null, langEl.value);
      let routeClause = "";
      if(r && r.route && r.route.indexOf("→") > -1){
        const seg = r.route.split("→").map(x => x.trim());
        // a there-and-back job reads oddly as "from X to X" — leave the route out
        if(seg[0] && seg[1] && seg[0].toLowerCase() !== seg[1].toLowerCase()) routeClause = t.routeWord(seg[0], seg[1]);
      }
      const driverClause = (r && r.driver && drvEl.checked) ? r.driver : "";
      const parts = t.body({date:dateStr, routeClause, driverClause, link});
      const body = [t.greet(name), "", parts[0], "", parts[1], "", parts[2], "", parts[3], "", parts[4], "", parts[5], signEl.value.trim()].join("\n");
      return {subject: t.subject(name), body};
    }

    function formatRideDate(iso, lang){
      if(!iso) return "";
      const d = new Date(iso + "T12:00:00");
      const loc = {en:"en-GB", fr:"fr-BE", nl:"nl-BE"}[lang] || "en-GB";
      return d.toLocaleDateString(loc, {day:"numeric", month:"long", year:"numeric"});
    }

    function render(){
      const {subject, body} = compose();
      $("#rv-subject").textContent = "Subject: " + subject;
      $("#rv-body").textContent = body;
      const r = currentRide();
      $("#rv-note").innerHTML = r
        ? `<b>${r.date} · ${r.client}.</b> ${r.vehicle ? r.vehicle + ", " : ""}${r.route}.` +
          (r.driver ? ` Driven by ${r.driver}.` : ` No chauffeur recorded on this booking, so the driver line is left out.`) +
          ` Paste the customer's address above, copy the message, and send it from your own mailbox.`
        : `<b>Writing generally.</b> No specific ride selected, so the message avoids naming a date or route. Pick a job above to make it personal — that is what makes people actually click.`;
    }
    /* ---- the buttons ---- */
    function toast(msg, bad){
      const el = $("#rv-toast");
      el.textContent = msg;
      el.style.color = bad ? "var(--critical)" : "var(--good)";
      clearTimeout(el._t);
      el._t = setTimeout(() => { el.textContent = ""; }, 4000);
    }

    function copyText(text, okMsg){
      const done = () => toast(okMsg);
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(done).catch(() => legacyCopy(text, done));
      } else {
        legacyCopy(text, done);
      }
    }
    function legacyCopy(text, done){
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed"; ta.style.top = "-1000px";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch(e) { ok = false; }
      document.body.removeChild(ta);
      if(ok){ done(); return; }
      // Last resort: select the preview for them so Ctrl+C works straight away.
      try {
        const pre = $("#rv-body");
        const rng = document.createRange();
        rng.selectNodeContents(pre);
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(rng);
        pre.scrollIntoView({block:"nearest"});
        toast("Clipboard blocked by the browser — the message is selected, press Ctrl+C.", true);
      } catch(e){
        toast("Clipboard blocked — select the preview text and press Ctrl+C.", true);
      }
    }

    $("#rv-copy").addEventListener("click", () => {
      const {subject, body} = compose();
      copyText("Subject: " + subject + "\n\n" + body, "Email copied — paste it into a new message.");
    });
    $("#rv-copylink").addEventListener("click", () => copyText(link, "Review link copied."));
    $("#rv-mail").addEventListener("click", () => {
      const {subject, body} = compose();
      const to = encodeURIComponent(emailEl.value.trim());
      const url = `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
      if(url.length > 1800){
        toast("Too long for a mailto link — use Copy email instead.", true);
        return;
      }
      try {
        const a = document.createElement("a");
        a.href = url; a.target = "_blank"; a.rel = "noopener";
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        toast("Asked your mail app to open. If nothing happened, use Copy email.");
      } catch(e){
        toast("The browser blocked that — use Copy email instead.", true);
      }
    });

    /* ---- guidance ---- */
    const pctSub = Math.round(100 * s.subcontracted_rides / s.total_rides);
    $("#rv-guidance").innerHTML = `
      <div class="legend" style="margin-top:0;">
        <div class="legend-item"><b style="color:var(--ink);">Ask once, soon.</b> Send within a day or two of the ride, while the chauffeur is still a person and not a receipt. After a week the reply rate collapses.</div>
        <div class="legend-item"><b style="color:var(--ink);">Not every ride, for repeat accounts.</b> A returning account books roughly every ${s.median_days_between_rides} days. Asking each time reads as spam — once a quarter per contact is plenty.</div>
        <div class="legend-item"><b style="color:var(--ink);">Ask the passenger, not the booker.</b> ${fmtInt(s.accounts)} accounts sit behind these rides, and on corporate bookings the person who arranged the car often never got in it. The review has to come from whoever sat in the back.</div>
        <div class="legend-item"><b style="color:var(--ink);">Skip subcontracted work.</b> ${pctSub}% of rides went out to a partner's driver. Those passengers didn't experience your chauffeur, so they're excluded from the picker above.</div>
        <div class="legend-item"><b style="color:var(--ink);">Best candidates are the long, memorable jobs.</b> A day excursion or a wedding leaves an impression an airport transfer doesn't. Sort your week by value and start at the top.</div>
      </div>`;

    syncName();
    render();
  }

  /* ============================================================
     BUILD: Data & Methodology
     ============================================================ */
  function buildQuality(){
    const dq = DATA.data_quality;
    const funnel = $("#funnel");
    const steps = [
      {label:"Raw export", val: dq.total_raw_rows_before_cleaning, color:"var(--muted)"},
      {label:"− Cancelled", val: dq.total_raw_rows_before_cleaning - dq.excluded_cancelled, color:"var(--s2)"},
      {label:"− Non-mission rows", val: dq.total_raw_rows_before_cleaning - dq.excluded_cancelled - dq.excluded_non_mission, color:"var(--s4)"},
      {label:"− Zero-price rows", val: dq.clean_rows, color:"var(--accent)"}
    ];
    const maxV = steps[0].val;
    steps.forEach(s => {
      const row = document.createElement("div");
      row.className = "funnel-row";
      const pct = (s.val/maxV*100);
      row.innerHTML = `<div class="funnel-label">${s.label}</div>
        <div class="funnel-bar-track"><div class="funnel-bar-fill" style="width:${pct}%; background:${s.color};"></div></div>
        <div class="funnel-val">${fmtInt(s.val)}</div>`;
      funnel.appendChild(row);
    });
    const note = document.createElement("p");
    note.style.cssText = "font-size:12.5px; color:var(--muted); margin-top:14px;";
    note.textContent = `Excluded: ${fmtInt(dq.excluded_cancelled)} cancelled bookings, ${dq.excluded_non_mission} non-transport rows (vehicle sales, lump-sum invoices), ${dq.excluded_zero_price} zero-price records outside the above. ${fmtInt(dq.clean_rows)} clean rows remain.`;
    funnel.appendChild(note);

    const meters = $("#completeness-meters");
    const items = [
      {name:"Vehicle category recorded", pct: 100-dq.pct_unknown_vehicle},
      {name:"Duration recorded (leg has end-time)", pct: 100-dq.pct_missing_duration},
      {name:"Purchase cost recorded", pct: dq.pct_cost_recorded},
      {name:"Odometer distance recorded", pct: dq.pct_distance_recorded}
    ];
    items.forEach(it => {
      const m = document.createElement("div");
      m.className = "meter";
      m.innerHTML = `<div class="meter-top"><span class="mname">${it.name}</span><span class="mval">${it.pct.toFixed(1)}%</span></div>
        <div class="meter-track"><div class="meter-fill" style="width:${it.pct}%;"></div></div>`;
      meters.appendChild(m);
    });

    const t = $("#table-outliers");
    t.innerHTML = `<thead><tr><th>Date</th><th>Client</th><th>Service</th><th>Vehicle</th><th class="num">Pax</th><th class="num">Sale price</th></tr></thead>`;
    const tb = document.createElement("tbody");
    DATA.top_trips.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.Date}</td><td class="strong">${r.Client}</td><td>${r["Service Category"]}</td><td>${r["Vehicle Category"]}</td><td class="num">${r.Pax}</td><td class="num strong">${fmtEURfull(r["Sale Price HT"])}</td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb);
  }


/* ============================================================
   MODULE ENTRY POINTS
   ============================================================ */

/* Which builders belong to which shell view. A view is built once, then only
   re-laid-out on resize — rebuilding on every switch would drop the calculator's
   quote sheet and the review composer's half-typed message. */
const VIEW_BUILDERS = {
  "analytics-overview": [buildKpis, buildOverview],
  "analytics-growth":   [buildGrowth],
  "analytics-fleet":    [buildFleet],
  "analytics-clients":  [buildClientKpis, buildClients],
  "analytics-drivers":  [buildDrivers, buildDriverHours],
  "analytics-ops":      [buildOps],
  "analytics-quality":  [buildQuality],
  "quotes-calculator":  [buildCalculator],
  "quotes-pricing":     [buildPricing],
  "reviews-compose":    [buildReviews],
};

async function load() {
  if (DATA) return DATA;
  const res = await fetch("/api/analytics", { credentials: "same-origin" });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body.detail || body.error || "";
    } catch (e) { /* non-JSON error body */ }
    const err = new Error(detail || `Analytics unavailable (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }
  const body = await res.json();
  DATA = body.data;
  IS_SAMPLE = Boolean(body.is_sample);
  return DATA;
}

/* Charts measure their container, so a view must be visible before it is built.
   The shell unhides the section, then calls this. */
function show(view) {
  if (!DATA || built.has(view)) return;
  const builders = VIEW_BUILDERS[view];
  if (!builders) {
    // A panel id that no builder is registered for renders as an empty screen with
    // no error — say so, rather than letting it look like a data problem.
    console.warn(
      `Analytics: no builder registered for panel "${view}". ` +
      `Expected one of: ${Object.keys(VIEW_BUILDERS).join(", ")}`
    );
    return;
  }
  builders.forEach((fn) => {
    try {
      fn();
    } catch (e) {
      console.error(`Analytics: ${view} builder failed`, e);
    }
  });
  built.add(view);
}

/* Redraw every chart already built. Window resizes are already handled inside the
   chart library; this is for the other case — a chart built while its section was
   visible, then re-shown at a different width after the sidebar or another view
   changed the layout. */
function relayout() {
  renderers.forEach((fn) => {
    try { fn(); } catch (e) { /* container not measurable right now — skip */ }
  });
}

global.Analytics = {
  load,
  show,
  relayout,
  isSample: () => IS_SAMPLE,
  isLoaded: () => DATA !== null,
};

})(window);
