import { useEffect, useRef } from 'react'

function hash32(x: number, y: number, salt: number): number {
  let h = (x * 374761393 + y * 668265263 + salt * 1442695041) >>> 0
  h = (h ^ (h >>> 13)) >>> 0
  h = (h * 1274126177) >>> 0
  return h / 4294967296
}

function smoothNoise(a: number, b: number, z: number): number {
  return (
    Math.sin(a * 1.73 + z * 0.62) * Math.cos(b * 1.31 - z * 0.48) * 0.52 +
    Math.sin(a * 3.07 - b * 2.21 + z * 1.1) * 0.28 +
    Math.cos(a * 5.17 + b * 4.09 + z * 0.3) * 0.14
  )
}

/** Небольшой fBM для туманности и деформации панелей. */
function fbm(th: number, ph: number, t: number): number {
  let v = 0
  let amp = 0.55
  let f = 1
  for (let i = 0; i < 5; i++) {
    v += amp * smoothNoise(th * f, ph * f, t * 0.35 + i * 0.7)
    amp *= 0.52
    f *= 2.03
  }
  return v
}

function shadeFromLight(nx: number, ny: number, nz: number, t: number): number {
  const lx = Math.cos(t * 0.18) * 0.72
  const ly = Math.sin(t * 0.24) * 0.58
  const lz = 0.45
  const d = Math.max(0, nx * lx + ny * ly + nz * lz)
  return 0.22 + 0.78 * d
}

type PanelCell = { u: number; v: number }

function panelCell(
  theta: number,
  colat: number,
  rot: number,
  rot2: number,
  t: number,
): PanelCell {
  const w1 = fbm(theta * 1.9 + rot * 0.4, colat * 2.1, t * 0.12) * 0.38
  const w2 = fbm(theta * 2.7 + 2, colat * 2.7 + 1, t * 0.1) * 0.22
  const u = Math.floor((theta + rot) / (Math.PI * 2) * 52 + w1 + w2 * 0.5)
  const w3 = fbm(theta * 3.1, colat * 1.8 - rot2, t * 0.11) * 0.35
  const v = Math.floor((colat / Math.PI) * 34 - rot2 * 0.2 + w3)
  return { u, v }
}

const PALETTE = '·.,\'`-~:;+=*?%#@'

function charFromShade(sh: number, edgeBoost: number): string {
  const s = Math.min(1, Math.max(0, sh + edgeBoost))
  const i = Math.floor(s * (PALETTE.length - 1) + 0.0001)
  return PALETTE[i]!
}

function skyChar(nebula: number, extra: number): string {
  const SKY = '·.,:;~-=+*?%#'
  const s = Math.min(1, Math.max(0, 0.12 + nebula * 0.78 + extra))
  return SKY[Math.floor(s * (SKY.length - 1))]!
}

/** Фон: шумовая туманность + звёзды + редкие далёкие дуги. */
function renderSpaceBackdrop(
  elapsed: number,
  x: number,
  y: number,
  px: number,
  py: number,
  width: number,
  height: number,
  cx: number,
  cy: number,
  R: number,
): string {
  const nx = (px / width) * 2 - 1
  const ny = (py / height) * 2 - 1
  const ang = Math.atan2(py - cy, px - cx)
  const dist = Math.hypot(px - cx, py - cy)
  const nd = dist / Math.max(width, height)

  const nebA =
    fbm(nx * 3.2 + elapsed * 0.018, ny * 3.2 - elapsed * 0.014, elapsed * 0.08) * 0.5 +
    0.5
  const nebB =
    fbm(nx * 6.5 + 40, ny * 6.5 + elapsed * 0.012, elapsed * 0.06) * 0.5 + 0.5
  const nebula = nebA * 0.62 + nebB * 0.38
  const gasBias = Math.sin(nd * 14 - elapsed * 0.4) * 0.08
  let gas = Math.min(1, Math.max(0, nebula * 0.85 + gasBias + 0.06))

  const arcRing = nd * Math.max(width, height)
  const farArc =
    Math.abs(Math.sin(ang * 7 + elapsed * 0.45)) < 0.035 &&
    arcRing > R * 1.1 &&
    arcRing < R * 2.1
  if (farArc) {
    gas = Math.min(1, gas + 0.35)
  }

  const st = hash32(x, y, 501)
  const st2 = hash32(x + 17, y + 41, 909)
  if (st > 0.93) {
    const tw = Math.sin(elapsed * (1.2 + st2 * 4.5) + st * 80)
    const mag = st > 0.985 ? (tw > -0.25 ? '*' : '·') : st > 0.965 ? '·' : "'"
    if (tw > -0.55) {
      return mag
    }
  }

  if (st2 > 0.996 && st <= 0.93) {
    return ':'
  }

  const dust = hash32(x, y, 303)
  if (dust > 0.982) {
    return '.'
  }

  const streakPhase = px * 0.71 + py * 0.53 + elapsed * 42
  const streak = ((streakPhase % 200) + 200) % 200
  if ((streak < 2.2 || streak > 197.8) && dust > 0.91 && nd > 0.32) {
    return '-'
  }

  return skyChar(gas, farArc ? 0.18 : 0)
}

function renderFrame(
  elapsed: number,
  cols: number,
  rows: number,
  width: number,
  height: number,
): string {
  const cx = width / 2
  const cy = height / 2
  const R = Math.min(width, height) * 0.42
  const rot = elapsed * 0.18
  const rot2 = elapsed * 0.09
  const lines: string[] = []

  for (let y = 0; y < rows; y++) {
    let row = ''
    const py = (y + 0.5) * (height / rows)
    for (let x = 0; x < cols; x++) {
      const px = (x + 0.5) * (width / cols)
      const dx = px - cx
      const dy = py - cy
      const dist = Math.hypot(dx, dy)
      const nr = dist / R

      if (nr > 1.09) {
        row += renderSpaceBackdrop(elapsed, x, y, px, py, width, height, cx, cy, R)
        continue
      }

      if (nr < 0.08) {
        const pulse = 0.5 + 0.5 * Math.sin(elapsed * 4.5 + nr * 25)
        const ray = Math.abs(Math.sin(Math.atan2(dy, dx) * 7 + elapsed * 2.8)) < 0.35
        row +=
          pulse > 0.85 ? '@' : pulse > 0.6 ? '%' : ray && pulse > 0.3 ? '#' : '*'
        continue
      }

      if (nr >= 0.08 && nr < 0.22) {
        const rip = fbm(Math.atan2(dy, dx) * 5, nr * 14, elapsed * 0.6)
        const c = 0.5 + 0.5 * Math.sin(elapsed * 5.5 - nr * 30) + rip * 0.15
        row += charFromShade(c * 0.85, 0.15)
        continue
      }

      const nx = dx / R
      const ny = dy / R
      const rho2 = nx * nx + ny * ny
      if (rho2 > 1) {
        if (nr < 1.06) {
          const rimGlow = 0.45 + 0.25 * Math.sin(elapsed * 2 + angPhase(nx, ny))
          row += charFromShade(rimGlow * 0.5, 0.2)
        } else {
          row += renderSpaceBackdrop(elapsed, x, y, px, py, width, height, cx, cy, R)
        }
        continue
      }

      const z = Math.sqrt(1 - rho2)
      const theta = Math.atan2(ny, nx)
      const colat = Math.acos(z)
      const snx = nx
      const sny = ny
      const snz = z

      const dTh = 0.045 / Math.max(z, 0.18)
      const dCp = 0.055
      const c0 = panelCell(theta, colat, rot, rot2, elapsed)
      const c1 = panelCell(theta + dTh, colat, rot, rot2, elapsed)
      const c2 = panelCell(theta, colat + dCp, rot, rot2, elapsed)
      let edge =
        c0.u !== c1.u || c0.v !== c1.v || c0.u !== c2.u || c0.v !== c2.v ? 1 : 0
      const micro = fbm(theta * 14, colat * 14, elapsed * 0.4)
      if (micro > 0.72 && edge === 0) {
        edge = 0.35
      }

      const bulk =
        0.38 * fbm(theta * 6 + rot, colat * 5, elapsed * 0.25) +
        0.28 * Math.sin(theta * 17 + rot * 3 + colat * 8)
      const truss =
        Math.abs(micro) > 0.88 &&
        Math.abs(Math.sin(theta * 24 + colat * 20 - elapsed)) < 0.09

      const shade = shadeFromLight(snx, sny, snz, elapsed)
      const panelWash = 0.15 + 0.55 * shade + 0.22 * z + bulk * 0.12

      const collector =
        Math.abs(Math.sin((theta + rot) * 2.5)) < 0.06 &&
        colat > 0.35 &&
        colat < 1.15 &&
        micro < -0.35
      const port = hash32(c0.u, c0.v, Math.floor(elapsed * 2) % 17) > 0.992

      const glint =
        port ||
        (hash32(x, y, Math.floor(elapsed * 5.2) * 131) > 0.993 &&
          edge === 0 &&
          shade > 0.55)

      if (glint) {
        row += collector ? '*' : '+'
        continue
      }

      if (truss) {
        row += edge > 0 ? '+' : '='
        continue
      }

      const edgeBoost = edge > 0 ? 0.22 * edge + 0.08 : 0
      row += charFromShade(panelWash, edgeBoost)
    }
    lines.push(row)
  }

  return lines.join('\n')
}

function angPhase(nx: number, ny: number): number {
  return Math.atan2(ny, nx) * 5
}

/** ASCII-фон: туманность, звёздное поле и освещённая мегаструктура вокруг ядра. */
export function DysonSphereAsciiBg() {
  const wrapRef = useRef<HTMLDivElement>(null)
  const preRef = useRef<HTMLPreElement>(null)
  const dimsRef = useRef({ cols: 80, rows: 40, w: 800, h: 600 })
  const frameRef = useRef(0)

  useEffect(() => {
    const wrap = wrapRef.current
    const pre = preRef.current
    if (!wrap || !pre) {
      return
    }

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    const measure = () => {
      const r = wrap.getBoundingClientRect()
      const w = Math.max(1, r.width)
      const h = Math.max(1, r.height)
      const fs = Math.min(Math.max(w * 0.0065, 7), 11)
      const cw = fs * 0.62
      const ch = fs * 1.12
      const cols = Math.max(28, Math.floor(w / cw))
      const rows = Math.max(18, Math.floor(h / ch))
      dimsRef.current = { cols, rows, w, h }
    }

    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(wrap)

    const start = performance.now()
    const step = (now: number) => {
      const { cols, rows, w, h } = dimsRef.current
      const elapsed = reducedMotion ? 0.08 : (now - start) * 0.001
      pre.textContent = renderFrame(elapsed, cols, rows, w, h)
      frameRef.current = requestAnimationFrame(step)
    }

    frameRef.current = requestAnimationFrame(step)
    return () => {
      ro.disconnect()
      cancelAnimationFrame(frameRef.current)
    }
  }, [])

  return (
    <div ref={wrapRef} className="login-ascii-bg" aria-hidden>
      <pre ref={preRef} className="login-ascii-pre" />
    </div>
  )
}
