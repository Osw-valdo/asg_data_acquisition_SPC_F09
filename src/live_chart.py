"""Gráfica de torque en vivo (Streamlit Custom Component v2, canvas en JS).

El navegador conserva la gráfica entre refrescos: solo anima los puntos nuevos
en lugar de redibujar todo, por lo que no parpadea ni se cierra el zoom.
"""
import pandas as pd
import streamlit as st

_HTML = """
<div class="wrap">
  <div class="title"></div>
  <canvas></canvas>
  <div class="tip"></div>
</div>
"""

_CSS = """
.wrap { position: relative; width: 100%; font-family: var(--st-font, sans-serif); }
.title { font-size: 14px; font-weight: 600; margin: 2px 0 6px;
         color: var(--st-text-color, #222); }
canvas { width: 100%; height: 420px; display: block; }
.tip { position: absolute; pointer-events: none; display: none; padding: 4px 8px;
       border-radius: 6px; font-size: 12px; white-space: nowrap;
       background: var(--st-secondary-background-color, #f0f2f6);
       color: var(--st-text-color, #222); border: 1px solid rgba(128,128,128,.35); }
"""

_JS = """
const states = new WeakMap()
const ANIM_MS = 600

export default function (component) {
  const { data, parentElement } = component
  const canvas = parentElement.querySelector("canvas")
  const tip = parentElement.querySelector(".tip")
  parentElement.querySelector(".title").textContent = data.title || ""
  if (!canvas) return

  let s = states.get(parentElement)
  if (!s) {
    s = { points: [], lastKey: null, shift: 0, start: 0, raf: 0,
          yMin: null, yMax: null, hover: -1 }
    states.set(parentElement, s)

    canvas.addEventListener("mousemove", (e) => {
      const r = canvas.getBoundingClientRect()
      const g = s.geo
      if (!g || !s.points.length) return
      const x = e.clientX - r.left
      let best = -1, bd = 1e9
      s.points.forEach((p, i) => {
        const d = Math.abs(g.px(i, 1) - x)
        if (d < bd) { bd = d; best = i }
      })
      s.hover = bd < 30 ? best : -1
      if (s.hover >= 0) {
        const p = s.points[s.hover]
        tip.style.display = "block"
        tip.textContent = p.v.toFixed(2) + "  |  " + p.r + "  |  " + p.label
        tip.style.left = Math.min(x + 12, r.width - 190) + "px"
        tip.style.top = Math.max(g.py(p.v) - 34, 0) + "px"
      } else tip.style.display = "none"
      draw()
    })
    canvas.addEventListener("mouseleave", () => {
      s.hover = -1; tip.style.display = "none"; draw()
    })
  }

  const pts = data.points || []
  const newLast = pts.length ? pts[pts.length - 1].k : null
  if (newLast !== s.lastKey) {
    const idx = s.lastKey === null ? -1 : pts.findIndex((p) => p.k === s.lastKey)
    s.shift = idx >= 0 ? pts.length - 1 - idx : 0
    s.start = performance.now()
    s.lastKey = newLast
  }
  s.points = pts
  s.data = data
  const target = rangeOf(data)
  if (s.yMin === null) { s.yMin = target[0]; s.yMax = target[1] }
  s.target = target

  function rangeOf(d) {
    const vals = d.points.map((p) => p.v)
    ;[d.min, d.max, d.target].forEach((v) => { if (v !== null && v !== undefined) vals.push(v) })
    if (!vals.length) return [0, 1]
    const lo = Math.min(...vals), hi = Math.max(...vals)
    const pad = Math.max((hi - lo) * 0.2, 0.5)
    return [lo - pad, hi + pad]
  }

  function css(name, fallback) {
    const v = getComputedStyle(canvas).getPropertyValue(name).trim()
    return v || fallback
  }

  function draw() {
    const dpr = window.devicePixelRatio || 1
    const w = canvas.clientWidth, h = canvas.clientHeight
    if (!w || !h) return
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr)
    }
    const ctx = canvas.getContext("2d")
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)

    const text = css("--st-text-color", "#222")
    const grid = "rgba(128,128,128,.22)"
    const L = 52, R = 18, T = 10, B = 26
    const W = Math.max(s.data.window || 60, 2)
    const n = s.points.length
    const t = Math.min((performance.now() - s.start) / ANIM_MS, 1)
    const ease = 1 - Math.pow(1 - t, 3)
    const k = 0.18
    s.yMin += (s.target[0] - s.yMin) * k
    s.yMax += (s.target[1] - s.yMax) * k
    const yMin = s.yMin, yMax = s.yMax
    const px = (i, p) => L + ((i + (W - n) - s.shift * (1 - (p === undefined ? ease : p))) / (W - 1)) * (w - L - R)
    const py = (v) => T + (1 - (v - yMin) / (yMax - yMin || 1)) * (h - T - B)
    s.geo = { px, py }

    ctx.font = "11px sans-serif"
    ctx.fillStyle = text
    ctx.strokeStyle = grid
    ctx.lineWidth = 1
    for (let i = 0; i <= 5; i++) {
      const v = yMin + ((yMax - yMin) * i) / 5
      const y = py(v)
      ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(w - R, y); ctx.stroke()
      ctx.textAlign = "right"; ctx.textBaseline = "middle"
      ctx.fillText(v.toFixed(1), L - 6, y)
    }

    const d = s.data
    if (d.min !== null && d.max !== null && d.min !== undefined && d.max !== undefined) {
      ctx.fillStyle = "rgba(46,160,67,.12)"
      ctx.fillRect(L, py(d.max), w - L - R, py(d.min) - py(d.max))
      ctx.strokeStyle = "rgba(46,160,67,.55)"
      ctx.setLineDash([5, 4])
      ;[d.min, d.max].forEach((v) => { ctx.beginPath(); ctx.moveTo(L, py(v)); ctx.lineTo(w - R, py(v)); ctx.stroke() })
      ctx.setLineDash([])
    }
    if (d.target !== null && d.target !== undefined) {
      ctx.strokeStyle = "rgba(46,160,67,.9)"
      ctx.beginPath(); ctx.moveTo(L, py(d.target)); ctx.lineTo(w - R, py(d.target)); ctx.stroke()
    }

    ctx.save()
    ctx.beginPath(); ctx.rect(L, 0, w - L - R, h); ctx.clip()
    if (n) {
      ctx.strokeStyle = css("--st-primary-color", "#1f77b4")
      ctx.lineWidth = 2
      ctx.lineJoin = "round"
      ctx.beginPath()
      s.points.forEach((p, i) => {
        const x = px(i), y = py(p.v)
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)
      })
      ctx.stroke()
      s.points.forEach((p, i) => {
        ctx.fillStyle = p.r === "OK" ? "#2ea043" : "#e5484d"
        ctx.beginPath(); ctx.arc(px(i), py(p.v), i === s.hover ? 6 : 4, 0, 6.2832); ctx.fill()
      })
      const last = s.points[n - 1]
      const lx = px(n - 1), ly = py(last.v)
      const pulse = (performance.now() % 1400) / 1400
      ctx.strokeStyle = last.r === "OK" ? "rgba(46,160,67," + (1 - pulse) + ")" : "rgba(229,72,77," + (1 - pulse) + ")"
      ctx.lineWidth = 2
      ctx.beginPath(); ctx.arc(lx, ly, 5 + pulse * 11, 0, 6.2832); ctx.stroke()
    }
    ctx.restore()

    if (n) {
      const last = s.points[n - 1]
      ctx.fillStyle = text
      ctx.font = "bold 12px sans-serif"
      ctx.textAlign = "right"; ctx.textBaseline = "bottom"
      ctx.fillText(last.v.toFixed(2) + " " + (d.unit || "") + "  " + last.r, w - R, T + 14)
    }
    ctx.fillStyle = text
    ctx.font = "11px sans-serif"
    ctx.textAlign = "left"; ctx.textBaseline = "top"
    ctx.fillText("\u2190 " + W + " \u00faltimos tightenings", L, h - B + 8)
  }

  function loop() {
    if (!canvas.isConnected) return
    draw()
    s.raf = requestAnimationFrame(loop)
  }
  cancelAnimationFrame(s.raf)
  loop()
}
"""

_COMPONENT = st.components.v2.component(
    "asg_live_torque_chart",
    html=_HTML,
    css=_CSS,
    js=_JS,
)


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def live_torque_chart(df, device_id, *, window=60, key="live_torque_chart"):
    """Dibuja la gráfica en vivo de torque de una estación."""
    station_df = pd.DataFrame()
    if df is not None and not df.empty and "device_id" in df.columns:
        station_df = df[df["device_id"] == device_id]

    points = []
    min_v = target_v = max_v = None
    process = unit = ""

    if not station_df.empty:
        station_df = station_df[station_df["torque_actual"].notna()].tail(window)
        time_col = "plot_timestamp" if "plot_timestamp" in station_df.columns else "pc_timestamp"
        for _, row in station_df.iterrows():
            stamp = pd.to_datetime(row.get(time_col), errors="coerce")
            points.append(
                {
                    "k": f"{row.get('pc_timestamp', '')}|{row.get('tightening_id', '')}",
                    "v": float(row["torque_actual"]),
                    "r": str(row.get("result_label", "")),
                    "label": stamp.strftime("%H:%M:%S") if pd.notna(stamp) else "",
                }
            )
        if not station_df.empty:
            last = station_df.iloc[-1]
            min_v = _num(last.get("torque_min"))
            target_v = _num(last.get("torque_target"))
            max_v = _num(last.get("torque_max"))
            process = str(last.get("process", ""))
            unit = str(last.get("torque_unit", ""))

    _COMPONENT(
        key=key,
        data={
            "title": f"{device_id} | {process} | Torque (en vivo)",
            "points": points,
            "min": min_v,
            "target": target_v,
            "max": max_v,
            "unit": unit,
            "window": window,
        },
        height="content",
    )
