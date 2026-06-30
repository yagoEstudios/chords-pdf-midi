import os
import re
import sys
import shutil
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from types import SimpleNamespace
import customtkinter as ctk
from tkinter import filedialog, messagebox
from pyRealParser import Tune
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ========================
# COLOR PALETTE (Material dark)
# ========================
COLOR_PRIMARY = "#BB86FC"
COLOR_PRIMARY_VARIANT = "#3700B3"
COLOR_SECONDARY = "#03DAC5"
COLOR_BG = "#121212"
COLOR_SURFACE = "#1E1E1E"
COLOR_TEXT = "#E6E6E6"
COLOR_PLACEHOLDER = "#8A8A8A"

ctk.set_appearance_mode("dark")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), "Downloads")


# ========================
# Conversión de acordes Real -> legible
# ========================
def render_chord(c: str) -> str:
    """Convierte un acorde de la notación interna de Real a símbolo legible."""
    c = c.lstrip("QfU")         # quita marcadores de coda (Q) / fermata (f) / End (U) pegados
    if not c:
        return ""
    if c in ("x", "X"):
        return "%"          # repetir compás anterior
    if c == "r":
        return "%%"         # repetir 2 compases anteriores
    if c in ("n", "N"):
        return "N.C."       # sin acorde
    if c in ("p", "U", "s", "l", "Y"):
        return ""           # marcas de relleno/tamaño/espacio
    s = c
    s = s.replace("^", "Δ")     # C^7 -> CΔ7 (triángulo, se dibuja con fuente Symbol)
    s = s.replace("-", "m")     # C-7 -> Cm7
    s = s.replace("h", "ø")     # semidisminuido
    s = s.replace("o", "°")     # disminuido
    # extensiones 9/11/13 (con su alteración) entre paréntesis,
    # protegiendo la alteración de la nota raíz (p. ej. Eb13 -> Eb(13))
    mtch = re.match(r"([A-G][b#]?)(.*)", s)
    if mtch and "7" in mtch.group(2):  # solo si la 7 aparece explícita
        root, rest = mtch.group(1), mtch.group(2)
        rest = re.sub(r"((?:[b#]?(?:13|11|9))+)", r"(\1)", rest)
        s = root + rest
    return s


def readable_chord(c: str) -> str:
    """Convierte un acorde Real interno a nombre legible (pychord/.txt):
    ^->maj, -->m, h->m7b5, o->dim. Sin paréntesis ni símbolos."""
    c = c.lstrip("QfU")         # quita marcadores de coda (Q) / fermata (f) / End (U) pegados
    if not c:
        return ""
    if c in ("x", "X"):
        return "%"
    if c == "r":
        return "%%"
    if c in ("n", "N"):
        return "n"
    if c in ("p", "U", "s", "l", "Y"):
        return ""
    return (c.replace("^", "maj").replace("-", "m")
             .replace("h7", "m7b5").replace("h", "m7b5").replace("o", "dim"))


def safe_filename(name: str) -> str:
    name = "".join(c for c in name if c.isalnum() or c in " -_()[]").strip()
    return (name or "ireal_chart").replace(" ", "_")


# ========================
# Análisis de compases (sin expandir repeticiones)
# ========================
_OPEN = {"{", "["}
_CLOSE = {"}", "]", "|", "Z"}
_TOKEN_RE = re.compile(r"T\d\d|N\d|\*\w|<[^>]*>|\||\{|\}|\[|\]|Z|[^|{}\[\]ZT*N<]+")


def split_measures(chord_string):
    """Divide el chord_string en compases conservando barras, repeticiones,
    secciones (A/B...), finales (1./2.) y varios acordes por compás."""
    chord_string = re.sub(r"\([^)]*\)", "", chord_string)  # quita acordes alternativos
    measures = []

    def new():
        return {"chords": [], "raw": [], "left": "|", "right": "|", "section": None,
                "ending": None, "coda": False, "fermata": False, "end": False}

    cur = new()
    for tok in _TOKEN_RE.findall(chord_string):
        if tok in _OPEN:
            if cur["chords"] or cur["section"]:
                measures.append(cur)
                cur = new()
            cur["left"] = tok
        elif tok in _CLOSE:
            cur["right"] = tok
            if cur["chords"] or cur["section"] or cur["ending"]:
                measures.append(cur)
            cur = new()
        elif tok.startswith("*"):
            cur["section"] = tok[1:].upper()
        elif re.fullmatch(r"N\d", tok):
            cur["ending"] = tok[1]
        elif re.fullmatch(r"T\d\d", tok):
            pass  # el compás métrico ya va en la cabecera
        elif tok.startswith("<"):
            pass  # anotación de texto, se omite
        else:
            for ch in tok.split():
                while ch[:1] in ("Q", "f", "U"):   # coda (Q) / fermata (f) / End (U) pegados
                    if ch[0] == "Q":           # Q al inicio = Coda (izq.), al final = To Coda (der.)
                        cur["coda"] = "left" if not cur["chords"] else "right"
                    elif ch[0] == "U":         # marcador End de Real: fin real de la pieza
                        cur["end"] = True
                    else:
                        cur["fermata"] = True
                    ch = ch[1:]
                if not ch:
                    continue
                pretty = render_chord(ch)
                if pretty:
                    cur["chords"].append(pretty)
                    cur["raw"].append(readable_chord(ch))
    if cur["chords"] or cur["section"]:
        measures.append(cur)
    # fusiona compases vacíos (solo marca de sección) con el siguiente
    cleaned, carry = [], None
    for m in measures:
        if not m["chords"] and m["left"] == "|" and m["right"] == "|":
            carry = carry or m["section"]
            continue
        if carry and not m["section"]:
            m["section"] = carry
        carry = None
        cleaned.append(m)
    return cleaned


def expand_repeats(measures):
    """Expande las repeticiones marcadas con { } (Real) o forward/backward
    (musicxml): duplica el tramo repetido. `times` (en el compás de cierre) =
    nº de veces que suena el tramo (por defecto 2). Quita las marcas { }."""
    out, stack = [], []
    for mz in measures:
        mz = dict(mz)
        opening, closing = mz.get("left") == "{", mz.get("right") == "}"
        if opening:
            mz["left"] = "|"
        if closing:
            mz["right"] = "|"
        out.append(mz)
        if opening:
            stack.append(len(out) - 1)
        if closing:
            if stack:                       # pareja { ... }
                start = stack.pop()
            elif mz.get("times"):           # backward suelto con cuenta explícita
                start = 0
            else:
                start = None                # backward suelto = barra final, no repetir
            if start is not None:
                times = max(2, int(mz.get("times") or 2))
                span = out[start:]
                for _ in range(times - 1):
                    copia = [dict(s) for s in span]
                    copia[0]["newrow"] = True
                    out.extend(copia)
    return out


def mark_repeats(measures):
    """Marca las repeticiones SIN duplicar (para el PDF): empareja { } y pone
    `times` en el compás de cierre. Un backward suelto (sin forward) repite
    desde el principio."""
    stack = []
    for i, m in enumerate(measures):
        if m.get("left") == "{":
            stack.append(i)
        if m.get("right") == "}":
            start = stack.pop() if stack else 0
            measures[start]["left"] = "{"
            if not m.get("times"):
                m["times"] = 2
    return measures


# ========================
# Renderizado a PDF
# ========================
MEASURES_PER_ROW = 4
# tokens "vacíos": dejan hueco en el PDF y mantienen sonando el acorde anterior en el MIDI
_HOLD = {"n", "nan"}
# color por orden de aparición de sección: verde, rojo, azul, naranja, rosa, marrón
SECTION_COLORS = [(0.0, 0.42, 0.0), (0.6, 0.0, 0.0), (0.0, 0.0, 0.55),
                  (0.9, 0.45, 0.0), (0.9, 0.2, 0.55), (0.45, 0.25, 0.05)]


def draw_barline(c, x, y_top, y_bot, kind, side, color=(0, 0, 0)):
    cy = (y_top + y_bot) / 2
    c.setStrokeColorRGB(*color)
    c.setFillColorRGB(*color)
    if kind == "|":
        c.setLineWidth(1)
        c.line(x, y_bot, x, y_top)
    elif kind == "S":  # fin de sección: un poco más gruesa
        c.setLineWidth(1.7)
        c.line(x, y_bot, x, y_top)
    elif kind in ("[", "]"):  # barra doble de inicio: dos líneas iguales
        c.setLineWidth(1.2)
        c.line(x, y_bot, x, y_top)
        x2 = x + 2.4 if side == "left" else x - 2.4
        c.line(x2, y_bot, x2, y_top)
    elif kind == "Z":  # barra final doble: fina + hueco + gruesa
        c.setLineWidth(1.0)
        c.line(x - 4.5, y_bot, x - 4.5, y_top)
        c.setLineWidth(2.4)
        c.line(x, y_bot, x, y_top)
    elif kind == "{":  # inicio de repetición  ||:
        c.setLineWidth(2.2)
        c.line(x, y_bot, x, y_top)
        c.setLineWidth(0.8)
        c.line(x + 2.4, y_bot, x + 2.4, y_top)
        c.circle(x + 4.8, cy + 3, 1.0, stroke=0, fill=1)
        c.circle(x + 4.8, cy - 3, 1.0, stroke=0, fill=1)
    elif kind == "}":  # fin de repetición  :||
        c.setLineWidth(2.2)
        c.line(x, y_bot, x, y_top)
        c.setLineWidth(0.8)
        c.line(x - 2.4, y_bot, x - 2.4, y_top)
        c.circle(x - 4.8, cy + 3, 1.0, stroke=0, fill=1)
        c.circle(x - 4.8, cy - 3, 1.0, stroke=0, fill=1)


# ========================
# Glifos ♭ y ♯ desde SVG embebido (sin archivos ni librerías externas)
# ========================
_FLAT_PATH = ("m 27,41 -1,-66 v -11 c 0,-22 1,-44 4,-66 45,38 93,80 93,139 "
              "0,33 -14,67 -43,67 C 49,104 28,74 27,41 z m -42,-179 -12,595 "
              "c 8,5 18,8 27,8 9,0 19,-3 27,-8 L 20,112 c 25,21 58,34 91,34 "
              "52,0 89,-48 89,-102 0,-80 -86,-117 -147,-169 -15,-13 -24,-38 "
              "-45,-38 -13,0 -23,11 -23,25 z")
_FLAT_MATRIX = (0.004, 0, 0, -0.004, 0.108, 1.86)
_SHARP_PATH = ("m 196.34201,34.561338 -11,2 -2,60 -45,12.000002 -1,-57.000002 "
               "-11,1 v 59.000002 l -42.000003,13 v 36 l 42.000003,-11 v 57 "
               "l -42.000003,12 v 36 l 42.000003,-10 v 59 l 11,-1 2,-63 44,-13 "
               "2,61 11,-2 v -61 l 42,-13 v -36 l -42,11 v -57 l 42,-13 "
               "V 81.561338 l -42,10 z m -13.67188,98.919922 0.14454,55.17969 "
               "-39.37305,11.35351 -1.72461,-54.32031 z")


def _parse_svg_path(d):
    toks = re.findall(r"[a-zA-Z]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    i = cx = cy = sx = sy = 0
    subpaths, cur, cmd = [], None, None

    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]; i += 1
        c = cmd
        if c in "mM":
            x, y = num(), num()
            if c == "m":
                x += cx; y += cy
            cx, cy = sx, sy = x, y
            cur = [("M", cx, cy)]; subpaths.append(cur)
            cmd = "l" if c == "m" else "L"
        elif c in "lL":
            x, y = num(), num()
            if c == "l":
                x += cx; y += cy
            cx, cy = x, y; cur.append(("L", cx, cy))
        elif c in "hH":
            x = num(); cx = cx + x if c == "h" else x
            cur.append(("L", cx, cy))
        elif c in "vV":
            y = num(); cy = cy + y if c == "v" else y
            cur.append(("L", cx, cy))
        elif c in "cC":
            x1, y1, x2, y2, x, y = (num() for _ in range(6))
            if c == "c":
                x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            cur.append(("C", x1, y1, x2, y2, x, y)); cx, cy = x, y
        elif c in "zZ":
            cur.append(("Z",)); cx, cy = sx, sy
        else:
            i += 1
    return subpaths


def _glyph(path, matrix):
    """(subpaths, matrix, bbox) listo para dibujar/medir."""
    subpaths = _parse_svg_path(path)
    pts = []
    for sp in subpaths:
        for s in sp:
            if s[0] in ("M", "L"):
                pts.append((s[1], s[2]))
            elif s[0] == "C":
                pts += [(s[1], s[2]), (s[3], s[4]), (s[5], s[6])]
    if matrix:
        a, b, cc, d, e, f = matrix
        pts = [(a*px + cc*py + e, b*px + d*py + f) for px, py in pts]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return subpaths, matrix, (min(xs), min(ys), max(xs), max(ys))


def _draw_glyph(c, g, x, y, height):
    """Dibuja el glifo relleno con su base en `y` y altura `height`. Devuelve el ancho."""
    subpaths, matrix, (minx, miny, maxx, maxy) = g
    scale = height / (maxy - miny)

    def tf(px, py):
        if matrix:
            a, b, cc, d, e, f = matrix
            px, py = a*px + cc*py + e, b*px + d*py + f
        return x + (px - minx) * scale, y + (maxy - py) * scale

    p = c.beginPath()
    for sp in subpaths:
        for s in sp:
            if s[0] == "M":
                p.moveTo(*tf(s[1], s[2]))
            elif s[0] == "L":
                p.lineTo(*tf(s[1], s[2]))
            elif s[0] == "C":
                p.curveTo(*tf(s[1], s[2]), *tf(s[3], s[4]), *tf(s[5], s[6]))
            elif s[0] == "Z":
                p.close()
    c.setFillColorRGB(0, 0, 0)
    c.drawPath(p, stroke=0, fill=1)
    return (maxx - minx) * scale


_FLAT = _glyph(_FLAT_PATH, _FLAT_MATRIX)
_SHARP = _glyph(_SHARP_PATH, None)
_FLAT_H, _SHARP_H = 1.0, 0.95   # altura del glifo en proporción al tamaño
_FLAT_W = (_FLAT[2][2] - _FLAT[2][0]) / (_FLAT[2][3] - _FLAT[2][1]) * _FLAT_H
_SHARP_W = (_SHARP[2][2] - _SHARP[2][0]) / (_SHARP[2][3] - _SHARP[2][1]) * _SHARP_H


# ========================
# Grados (números romanos embebidos)
# ========================
_GRADOS_SVG = {
    'I': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 115.15583,122.44417 41.54355,119.89325"/>\n    <path d="M 75.798773,92.561963 194.23435,93.655214"/>\n    <path d="M 23.566047,30.40813 C 189.60797,19.764931 191.17723,33.432343 191.17723,33.432343 L 195.21381,55.7839 22.858381,53.513348 Z"/>\n    <path d="m 19.86431,244.96655 c 166.04193,-10.6432 167.61119,3.02421 167.61119,3.02421 l 4.03657,22.35155 -172.355427,-2.27058 z"/>\n    <path d="M 90.226719,258.57542 C 71.245458,45.595882 91.891983,43.675762 91.891983,43.675762 L 125.63172,38.650423 125.1887,259.63849 Z"/>\n  </g>\n</svg>',
    'II': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="M 67.102467,122.44417 87.850973,242.33742"/>\n    <path d="m 47.445982,92.561963 59.151448,1.093251"/>\n    <path d="m 21.358875,30.40813 c 82.927965,-10.643199 83.711715,3.024213 83.711715,3.024213 L 107.08662,55.7839 21.005439,53.513348 Z"/>\n    <path d="m 19.51008,244.96655 c 82.92796,-10.6432 83.71171,3.02421 83.71171,3.02421 l 2.01603,22.35155 -86.081177,-2.27058 z"/>\n    <path d="M 52.663223,258.06006 C 39.354114,45.080518 53.830857,43.160398 53.830857,43.160398 L 77.488181,38.135059 77.177548,259.12313 Z"/>\n    <path d="M 143.8731,261.53592 C 130.56399,48.556383 145.04074,46.636263 145.04074,46.636263 l 23.65732,-5.025339 -0.31063,220.988066 z"/>\n    <path d="m 159.01794,123.42691 20.74851,119.89325"/>\n    <path d="m 139.36146,93.544701 59.15145,1.09325"/>\n    <path d="m 113.27435,31.390868 c 82.92797,-10.643199 83.71172,3.024213 83.71172,3.024213 l 2.01603,22.35156 -86.08118,-2.27055 z"/>\n    <path d="m 111.42555,245.94929 c 82.92797,-10.6432 83.71172,3.02421 83.71172,3.02421 l 2.01603,22.35155 -86.08118,-2.27058 z"/>\n  </g>\n</svg>',
    'III': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="M 49.241355,122.44417 62.260483,242.33742"/>\n    <path d="m 36.90744,92.561963 37.115939,1.093251"/>\n    <path d="M 20.538484,34.806865 C 72.573546,26.0086 73.065328,37.306849 73.065328,37.306849 L 74.330332,55.7839 20.316713,53.906934 Z"/>\n    <path d="m 19.378415,244.163 c 52.035059,-8.5347 52.526841,2.42509 52.526841,2.42509 l 1.265004,17.92355 -54.013617,-1.82077 z"/>\n    <path d="M 36.79448,258.57542 C 25.430859,45.595882 37.791434,43.675762 37.791434,43.675762 L 57.9906,38.650423 57.725374,259.63849 Z"/>\n    <path d="m 109.80711,125.03442 13.01913,119.89325"/>\n    <path d="m 97.473198,95.152217 37.115942,1.093251"/>\n    <path d="m 81.104242,37.397119 c 52.035068,-8.798265 52.526848,2.499984 52.526848,2.499984 l 1.265,18.477051 -54.013619,-1.876966 z"/>\n    <path d="m 79.944173,246.75325 c 52.035057,-8.5347 52.526847,2.42509 52.526847,2.42509 l 1.265,17.92355 -54.013619,-1.82077 z"/>\n    <path d="M 97.360238,261.16567 C 85.996617,48.186136 98.357188,46.266016 98.357188,46.266016 l 20.199172,-5.025339 -0.26523,220.988063 z"/>\n    <path d="m 172.59866,127.52361 13.01913,119.89325"/>\n    <path d="m 160.26475,97.641404 37.11594,1.093251"/>\n    <path d="m 143.8958,39.886306 c 52.03506,-8.798265 52.52684,2.499984 52.52684,2.499984 l 1.265,18.477051 -54.01361,-1.876966 z"/>\n    <path d="m 142.73573,249.24244 c 52.03505,-8.5347 52.52684,2.42509 52.52684,2.42509 l 1.265,17.92355 -54.01361,-1.82077 z"/>\n    <path d="M 160.15179,263.65486 C 148.78817,50.675323 161.14875,48.755203 161.14875,48.755203 l 20.19916,-5.025339 -0.26523,220.988066 z"/>\n  </g>\n</svg>',
    'IV': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="M 56.795193,125.02099 77.543699,244.91424"/>\n    <path d="m 37.138708,95.138782 59.151448,1.093251"/>\n    <path d="M 6.9286913,32.984949 C 89.856656,22.34175 90.640406,36.009162 90.640406,36.009162 l 2.01603,22.351557 -86.0811807,-2.270552 z"/>\n    <path d="m 5.0798961,247.54337 c 82.9279599,-10.6432 83.7117099,3.02421 83.7117099,3.02421 l 2.01603,22.35155 -86.0811769,-2.27058 z"/>\n    <path d="M 37.717675,260.63688 C 24.408566,47.657337 38.885309,45.737217 38.885309,45.737217 L 62.542633,40.711878 62.232,261.69995 Z"/>\n    <path d="m 139.36146,93.544701 59.15145,1.09325"/>\n    <path d="m 387.61844,91.548073 5.84349,3.895663"/>\n    <g transform="matrix(0.97298352,0,0,1.2831278,2.0671918,-39.755278)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
    'V': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 163.04056,113.71929 17.8299,133.45762"/>\n    <path d="m 146.14907,80.456296 50.83087,1.216937"/>\n    <path d="m 115.05115,43.394248 -8.76118,-9.752399 z"/>\n    <path d="m 111.44361,37.083872 c 3.60754,24.667829 3.60754,24.667829 3.60754,24.667829"/>\n    <g transform="matrix(1.6925503,0,0,1.3851172,-151.98961,-55.239459)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
    'VI': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 155.74503,126.05172 20.7485,119.89325"/>\n    <path d="m 136.08854,96.169509 59.15145,1.093251"/>\n    <path d="m 117.73188,35.561767 c 82.92797,-10.643199 83.71172,3.024213 83.71172,3.024213 l 2.01603,22.351557 -86.08118,-2.270552 z"/>\n    <path d="m 115.88309,250.12019 c 82.92796,-10.6432 83.71171,3.02421 83.71171,3.02421 l 2.01603,22.35155 -86.08118,-2.27058 z"/>\n    <path d="M 149.03623,263.2137 C 135.72712,50.234155 150.20387,48.314035 150.20387,48.314035 l 23.65732,-5.025339 -0.31063,220.988074 z"/>\n    <path d="m 139.36146,93.544701 59.15145,1.09325"/>\n    <g transform="matrix(0.93696154,0,0,1.2430625,-86.517409,-33.46746)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
    'VII': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 140.22702,128.62854 10.19373,119.89325"/>\n    <path d="m 130.56979,98.746328 29.06109,1.093251"/>\n    <path d="m 117.7532,36.592495 c 40.74249,-10.643199 41.12754,3.024213 41.12754,3.024213 l 0.99048,22.351557 -42.29166,-2.270552 z"/>\n    <path d="m 116.84489,251.15092 c 40.74248,-10.6432 41.12753,3.02421 41.12753,3.02421 l 0.99048,22.35155 -42.29166,-2.27058 z"/>\n    <path d="M 129.91388,264.24443 C 120.45548,51.264883 130.74369,49.344763 130.74369,49.344763 L 147.55628,44.319424 147.33552,265.3075 Z"/>\n    <path d="m 164.72452,38.26305 c 40.74248,-10.64319 41.12753,3.02422 41.12753,3.02422 l 0.99048,22.35155 -42.29166,-2.27055 z"/>\n    <path d="m 163.81621,252.82148 c 40.74248,-10.6432 41.12753,3.02421 41.12753,3.02421 l 0.99047,22.35155 -42.29165,-2.27058 z"/>\n    <path d="M 178.16397,265.39963 C 169.19219,52.420076 178.9511,50.499956 178.9511,50.499956 l 15.94762,-5.02534 -0.20941,220.988084 z"/>\n    <path d="m 163.07232,96.12152 42.59648,1.09325"/>\n    <g transform="matrix(0.97298352,0,0,1.2831278,-90.986901,-42.002488)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
    'im': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 115.15583,122.44417 41.54355,119.89325"/>\n    <path d="M 75.798773,92.561963 194.23435,93.655214"/>\n    <path d="M 87.854999,282.19427 C 71.035706,128.54442 89.330593,127.15918 89.330593,127.15918 l 29.896777,-3.62544 -0.39256,159.42744 z"/>\n    <ellipse cx="103.40145" cy="96.591934" rx="17.773073" ry="17.190212"/>\n  </g>\n</svg>',
    'iim': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 115.15583,122.44417 41.54355,119.89325"/>\n    <path d="M 75.798773,92.561963 194.23435,93.655214"/>\n    <path d="M 64.147734,272.7606 C 48.238038,127.36279 65.543527,126.05195 65.543527,126.05195 l 28.27994,-3.43073 -0.371331,150.86511 z"/>\n    <ellipse cx="78.853424" cy="97.126373" rx="16.811895" ry="16.266983"/>\n    <path d="m 113.71829,271.3894 c -15.909692,-145.39781 1.3958,-146.70865 1.3958,-146.70865 l 28.27993,-3.43073 -0.37134,150.86511 z"/>\n    <ellipse cx="128.424" cy="95.75515" rx="16.811895" ry="16.266983"/>\n  </g>\n</svg>',
    'iiim': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="M 92.561965,126.81718 134.10552,246.71043"/>\n    <path d="M 53.204908,96.934969 171.64049,98.02822"/>\n    <path d="M 41.553869,277.13361 C 25.644173,131.7358 42.949662,130.42496 42.949662,130.42496 l 28.27994,-3.43073 -0.371331,150.86511 z"/>\n    <ellipse cx="56.25956" cy="101.49938" rx="16.811895" ry="16.266983"/>\n    <path d="m 139.28852,274.765 c -15.90969,-145.3978 1.3958,-146.70865 1.3958,-146.70865 l 28.27994,-3.43073 -0.37133,150.86511 z"/>\n    <ellipse cx="153.9942" cy="99.130775" rx="16.811895" ry="16.266983"/>\n    <path d="m 91.124425,275.76241 c -15.909692,-145.39781 1.3958,-146.70865 1.3958,-146.70865 l 28.279935,-3.43073 -0.37134,150.86511 z"/>\n    <ellipse cx="105.83013" cy="100.12816" rx="16.811895" ry="16.266983"/>\n  </g>\n</svg>',
    'ivm': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 55.173076,106.657 17.8299,140.21304"/>\n    <path d="m 38.281586,71.710284 50.83087,1.278537"/>\n    <path d="m 115.05115,43.394248 -8.76118,-9.752399 z"/>\n    <path d="m 111.44361,37.083872 c 3.60754,24.667829 3.60754,24.667829 3.60754,24.667829"/>\n    <path d="m 38.693656,282.73166 c -15.90969,-152.75762 1.3958,-154.13482 1.3958,-154.13482 l 28.27993,-3.60438 -0.37134,158.50167 z"/>\n    <ellipse cx="53.399368" cy="98.207047" rx="16.811895" ry="17.090393"/>\n    <g transform="matrix(0.92204074,0,0,0.99226891,-3.7915934,36.633358)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
    'vm': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 163.04056,113.71929 17.8299,133.45762"/>\n    <path d="m 146.14907,80.456296 50.83087,1.216937"/>\n    <path d="m 115.05115,43.394248 -8.76118,-9.752399 z"/>\n    <path d="m 111.44361,37.083872 c 3.60754,24.667829 3.60754,24.667829 3.60754,24.667829"/>\n    <path d="m 133.01227,252.90552 c 0.15583,4.79989 0.54662,9.58779 0.54662,14.39447 0,0.30369 -0.0144,-0.60769 0,-0.91104 0.0463,-0.97258 0.0378,-1.95243 0.18221,-2.91534 0.22087,-1.47249 0.44019,-2.96044 0.91105,-4.373 1.38323,-4.1497 4.25531,-8.08988 7.83496,-10.5681 0.4604,-0.31873 0.3068,1.08289 0.36442,1.63988 0.12562,1.2143 0.25389,2.4284 0.36442,3.64417 0.47195,2.83565 0.30908,5.71327 0.54662,8.5638 0.0663,0.7961 0.2172,1.58354 0.36442,2.3687 0.0354,0.18879 0,0.48591 0.18221,0.54663 0.16297,0.0543 0.28759,-0.21076 0.36442,-0.36441 0.41372,-0.82744 0.68965,-1.71849 1.09325,-2.55092 0.91852,-1.89445 2.7528,-5.39315 4.1908,-6.92392 1.68756,-1.79644 5.9346,-5.31322 8.38159,-2.18651 0.61735,0.78884 0.59104,1.92318 0.72884,2.91534 0.74808,5.3862 1.09325,9.08283 1.09325,14.03006 0,0.18222 -0.0136,-0.36491 0,-0.54663 0.16862,-2.2483 0.18188,-4.51681 0.54662,-6.74171 0.24564,-1.49841 0.58493,-3.02071 1.27546,-4.37301 0.78999,-1.54707 1.92375,-2.91029 3.09755,-4.19079 0.86096,-0.93924 1.53414,0.38238 1.63988,0.91104 0.21866,1.09334 0.1822,2.17879 0.1822,3.27975"/>\n    <path d="m 133.2481,249.86551 c 0.18396,2.32043 0.0961,4.64976 0.0288,6.97365 -0.0592,1.84748 -0.11108,3.69532 -0.19469,5.54192 -0.0509,1.12486 -0.0751,2.25421 -0.27243,3.36411 -0.93915,2.69047 -0.26312,0.56108 7.9011,0.0466 0.11897,-0.007 0.003,-0.23844 0.009,-0.35751 0.0236,-0.45529 0.0756,-0.9091 0.12149,-1.3625 0.11214,-0.88512 0.087,-1.77717 0.12886,-2.66683 0.28686,-2.46842 1.22661,-4.82111 2.4361,-6.97771 0.35769,-0.6378 0.76554,-1.24613 1.14831,-1.8692 1.68645,-2.28388 0.8219,-1.19552 2.58448,-3.27232 0,0 -7.23395,-0.49469 -7.23395,-0.49469 v 0 c -1.6755,2.15126 -0.83807,1.02923 -2.50453,3.37185 -0.39412,0.63897 -0.80623,1.26719 -1.18236,1.91691 -1.31107,2.26473 -2.4732,4.6816 -3.00375,7.25796 -0.11094,0.88504 -0.17564,1.77137 -0.20746,2.66273 -0.0361,0.44492 -0.0693,0.89143 -0.15534,1.33035 -0.0221,0.11274 -0.19681,0.32753 -0.0821,0.33478 8.41428,0.53168 9.94867,1.83319 7.81285,0.0729 -0.0399,-0.19764 -0.10258,-0.39203 -0.11982,-0.59293 -0.0409,-0.47693 -0.0445,-2.38133 -0.0425,-2.7444 0.01,-1.84396 0.0739,-3.68735 0.17573,-5.52843 0.14692,-2.33934 0.32311,-4.67548 0.56172,-7.0072 z"/>\n    <g transform="matrix(1.2971813,0,0,0.91452747,-92.984122,59.59994)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
    'vim': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 162.31173,107.38583 17.8299,140.21304"/>\n    <path d="m 145.42024,72.439118 50.83087,1.278537"/>\n    <path d="m 115.05115,43.394248 -8.76118,-9.752399 z"/>\n    <path d="m 111.44361,37.083872 c 3.60754,24.667829 3.60754,24.667829 3.60754,24.667829"/>\n    <path d="m 145.83231,283.46049 c -15.90969,-152.75762 1.3958,-154.13482 1.3958,-154.13482 l 28.27993,-3.60438 -0.37134,158.50167 z"/>\n    <ellipse cx="160.53802" cy="98.935883" rx="16.811895" ry="17.090393"/>\n    <g transform="matrix(0.92204074,0,0,0.99226891,-70.107571,32.004664)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
    'viim': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297">\n  <g>\n    <path d="m 162.31173,86.468632 17.8299,157.076158"/>\n    <path d="m 145.42024,72.439118 50.83087,1.278537"/>\n    <path d="m 115.05115,43.394248 -8.76118,-9.752399 z"/>\n    <path d="m 111.44361,37.083872 c 3.60754,24.667829 3.60754,24.667829 3.60754,24.667829"/>\n    <path d="m 114.41186,280.61342 c -12.27132,-139.0819 1.07658,-140.3358 1.07658,-140.3358 l 21.81261,-3.2817 -0.28642,144.31171 z"/>\n    <ellipse cx="125.7545" cy="112.60849" rx="12.967187" ry="15.560366"/>\n    <path d="m 154.47807,278.74159 c -12.27132,-139.0819 1.07658,-140.33581 1.07658,-140.33581 l 21.81261,-3.2817 -0.28642,144.31172 z"/>\n    <ellipse cx="165.82071" cy="110.73665" rx="12.967187" ry="15.560366"/>\n    <g transform="matrix(0.81378734,0,0,0.93560808,-67.792318,45.348531)">\n      <path d="M 143.1031,245.98919 C 84.52243,67.268376 96.62681,62.478917 96.62681,62.478917 l 19.3634,-9.436363 48.56332,188.515566 z"/>\n      <path d="M 210.04719,60.183018 C 174.77445,244.92245 161.86451,243.25266 161.86451,243.25266 l -21.51129,-1.11838 48.77995,-188.459624 z"/>\n    </g>\n  </g>\n</svg>',
}
_GRADOS_CACHE = {}
_ROMAN_FILE = {(1, True): "I", (2, True): "II", (3, True): "III", (4, True): "IV",
               (5, True): "V", (6, True): "VI", (7, True): "VII",
               (1, False): "im", (2, False): "iim", (3, False): "iiim", (4, False): "ivm",
               (5, False): "vm", (6, False): "vim", (7, False): "viim"}


def _parse_transform(t):
    """[a,b,c,d,e,f] de un transform SVG (matrix/translate/scale) o None."""
    if not t:
        return None
    m = re.match(r"matrix\(([-\d.,\seE+]+)\)", t)
    if m:
        return [float(v) for v in re.split(r"[,\s]+", m.group(1).strip())]
    m = re.match(r"translate\(([-\d.,\seE+]+)\)", t)
    if m:
        v = [float(x) for x in re.split(r"[,\s]+", m.group(1).strip())]
        return [1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0]
    m = re.match(r"scale\(([-\d.,\seE+]+)\)", t)
    if m:
        v = [float(x) for x in re.split(r"[,\s]+", m.group(1).strip())]
        return [v[0], 0, 0, (v[1] if len(v) > 1 else v[0]), 0, 0]
    return None


def _mat_mul(m1, m2):
    """Composición afín: aplicar m2 y luego m1."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [a1*a2 + c1*b2, b1*a2 + d1*b2, a1*c2 + c1*d2, b1*c2 + d1*d2,
            a1*e2 + c1*f2 + e1, b1*e2 + d1*f2 + f1]


def _load_svg_glyph(xml_str):
    """Parsea un SVG embebido (varios <path> cerrados + <ellipse>) aplicando los
    transform de cada elemento -> (elems, bbox)."""
    root = ET.fromstring(xml_str)
    elems, pts = [], []

    def tfp(mat, px, py):
        a, b, c, d, e, f = mat
        return (a*px + c*py + e, b*px + d*py + f)

    def walk(el, mat):
        t = _parse_transform(el.get("transform"))
        if t:
            mat = _mat_mul(mat, t)
        tag = el.tag.split("}")[-1]
        if tag == "path" and el.get("d"):
            sp = _parse_svg_path(el.get("d"))
            if any(s[0] == "Z" for spp in sp for s in spp):  # solo formas cerradas
                tsp = []
                for spp in sp:
                    seg = []
                    for s in spp:
                        if s[0] in ("M", "L"):
                            p = tfp(mat, s[1], s[2]); seg.append((s[0], *p)); pts.append(p)
                        elif s[0] == "C":
                            p1, p2, p3 = (tfp(mat, s[1], s[2]), tfp(mat, s[3], s[4]),
                                          tfp(mat, s[5], s[6]))
                            seg.append(("C", *p1, *p2, *p3)); pts.extend([p1, p2, p3])
                        else:
                            seg.append(s)
                    tsp.append(seg)
                elems.append(("path", tsp))
        elif tag == "ellipse":
            cx, cy = tfp(mat, float(el.get("cx")), float(el.get("cy")))
            rx, ry = float(el.get("rx")) * abs(mat[0]), float(el.get("ry")) * abs(mat[3])
            elems.append(("ellipse", cx, cy, rx, ry))
            pts.extend([(cx - rx, cy - ry), (cx + rx, cy + ry)])
        for child in el:
            walk(child, mat)

    walk(root, [1, 0, 0, 1, 0, 0])
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return elems, (min(xs), min(ys), max(xs), max(ys))


def _draw_svg_glyph(c, glyph, x, y, height):
    """Dibuja un glifo multi-elemento con base en `y`, altura `height`. Ancho dibujado."""
    elems, (minx, miny, maxx, maxy) = glyph
    scale = height / (maxy - miny)

    def tf(px, py):
        return x + (px - minx) * scale, y + (maxy - py) * scale

    c.setFillColorRGB(0, 0, 0)
    for e in elems:
        if e[0] == "path":
            p = c.beginPath()
            for sp in e[1]:
                for s in sp:
                    if s[0] == "M":
                        p.moveTo(*tf(s[1], s[2]))
                    elif s[0] == "L":
                        p.lineTo(*tf(s[1], s[2]))
                    elif s[0] == "C":
                        p.curveTo(*tf(s[1], s[2]), *tf(s[3], s[4]), *tf(s[5], s[6]))
                    elif s[0] == "Z":
                        p.close()
            c.drawPath(p, stroke=0, fill=1)
        else:
            _, cx, cy, rx, ry = e
            X, Y = tf(cx, cy)
            c.ellipse(X - rx * scale, Y - ry * scale, X + rx * scale, Y + ry * scale,
                      stroke=0, fill=1)
    return (maxx - minx) * scale


def _degree_glyph(num, upper):
    k = (num, upper)
    if k not in _GRADOS_CACHE:
        key = _ROMAN_FILE.get(k)
        _GRADOS_CACHE[k] = _load_svg_glyph(_GRADOS_SVG[key]) if key in _GRADOS_SVG else None
    return _GRADOS_CACHE[k]


_DEG_CAP = 1.05   # altura del numeral MAYÚSCULA respecto a root_size


def _deg_ref():
    """Altura nativa de la 'I' = referencia común; el resto se escala con el
    mismo factor (los minúsculos i/ii/iii salen naturalmente más pequeños)."""
    g = _degree_glyph(1, True)
    return (g[1][3] - g[1][1]) if g else 250.0


def _deg_height(g, root_size):
    """Altura de dibujo de un glifo escalado con el factor común."""
    return (g[1][3] - g[1][1]) / _deg_ref() * (root_size * _DEG_CAP)


# intervalo (semitonos sobre la tónica) -> (nº de grado 1-7, alteración).
# Siempre respecto a la escala MAYOR de la tónica: en menor, los diatónicos
# III/VI/VII salen como bIII/bVI/bVII (con bemol), que es lo correcto.
_DEG_MAJOR = {0: (1, ""), 1: (2, "b"), 2: (2, ""), 3: (3, "b"), 4: (3, ""),
              5: (4, ""), 6: (4, "#"), 7: (5, ""), 8: (6, "b"), 9: (6, ""),
              10: (7, "b"), 11: (7, "")}
_ROMAN_TXT = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII"}
_ROMAN_NUM = {v: k for k, v in _ROMAN_TXT.items()}


def chord_to_degree(name, tonic_pc, minor_key):
    """'Em7' -> 'im7', 'G7' -> 'V7', 'Cmaj7' -> 'VIΔ7'… (grado romano).
    Slash chords ('F/G') -> el bajo también como grado ('IV/V')."""
    if "/" in name:
        chord_part, bass = name.split("/", 1)
        deg = chord_to_degree(chord_part, tonic_pc, minor_key)
        bm = re.match(r"([A-G][b#]*)(.*)", bass)
        if bm and _note_pc(bm.group(1)) is not None:
            bnum, bacc = _DEG_MAJOR[(_note_pc(bm.group(1)) - tonic_pc) % 12]
            return deg + "/" + bacc + _ROMAN_TXT[bnum] + parse_chord_token(bm.group(2))
        return deg + "/" + bass
    m = re.match(r"([A-G][b#]*)(.*)", name)
    if not m:
        return name
    root, quality = m.group(1), m.group(2)
    interval = (_note_pc(root) - tonic_pc) % 12
    num, acc = _DEG_MAJOR[interval]
    minor_chord = bool(re.match(r"m(?!aj)|min|dim", quality))
    roman = _ROMAN_TXT[num].lower() if minor_chord else _ROMAN_TXT[num]
    return acc + roman + parse_chord_token(quality)


def apply_degrees(measures, key):
    """Cambia `chords` (display) a grados; conserva `raw` (notas) para el MIDI."""
    tonic = _note_pc(_key_parts(key)[0])
    minor = _key_parts(key)[1]
    if tonic is None:
        return False
    for mz in measures:
        mz["chords"] = [chord_to_degree(r, tonic, minor) for r in mz["raw"]]
    return True


def _draw_ext(c, x, y, text, size):
    """Dibuja `text` desde (x, y) a tamaño `size`. Δ (maj), ♭ y ♯ se dibujan
    como vectores porque Helvetica no los tiene. Devuelve la x final."""
    c.setStrokeColorRGB(0, 0, 0)
    for ch in text:
        if ch == "Δ":
            w = size * 0.62
            c.setLineWidth(size * 0.07)
            p = c.beginPath()
            p.moveTo(x, y)
            p.lineTo(x + w, y)
            p.lineTo(x + w / 2, y + w)
            p.close()
            c.drawPath(p, stroke=1, fill=0)
            x += w + size * 0.08
        elif ch == "b":  # bemol ♭ (glifo SVG)
            x += _draw_glyph(c, _FLAT, x, y, size * _FLAT_H) + size * 0.06
        elif ch == "#":  # sostenido ♯ (glifo SVG)
            x += _draw_glyph(c, _SHARP, x, y, size * _SHARP_H) + size * 0.06
        elif ch == "°":  # disminuido: círculo vectorial (no el 'grado' diminuto)
            r = size * 0.22
            c.setLineWidth(size * 0.08)
            c.circle(x + r + size * 0.04, y + size * 0.42, r, stroke=1, fill=0)
            x += 2 * r + size * 0.12
        else:
            c.setFont("Helvetica-Bold", size)
            c.drawString(x, y, ch)
            x += c.stringWidth(ch, "Helvetica-Bold", size)
    return x


def _ext_width(c, text, size):
    """Ancho de una extensión (igual que la dibuja _draw_ext: Δ, ♭, ♯ vectoriales)."""
    w = 0
    for ch in text:
        if ch == "Δ":
            w += size * 0.70
        elif ch == "b":
            w += size * _FLAT_W + size * 0.06
        elif ch == "#":
            w += size * _SHARP_W + size * 0.06
        elif ch == "°":
            w += size * 0.56
        else:
            w += c.stringWidth(ch, "Helvetica-Bold", size)
    return w


def _chord_parts(chord):
    """(letra, accidental, extensión, bajo) o None si no es un acorde con raíz."""
    m = re.match(r"([A-G])([b#]*)(.*)", chord)
    if not m:
        return None
    letter, acc, rest = m.group(1), m.group(2), m.group(3)
    bass = ""
    if "/" in rest:
        rest, bass = rest.split("/", 1)
    return letter, acc, rest, bass


def degree_width(c, token, root_size, ext_size):
    if not token or token in ("%", "%%", "N.C."):
        return c.stringWidth(token, "Helvetica-Bold", root_size * 0.75)
    m = re.match(r"([b#]?)([IiVv]+)(.*)", token)
    if not m:
        return c.stringWidth(token, "Helvetica-Bold", root_size)
    acc, roman, ext = m.group(1), m.group(2), m.group(3)
    w = (_ext_width(c, acc, root_size * 0.75) + root_size * 0.04) if acc else 0
    g = _degree_glyph(_ROMAN_NUM[roman.upper()], roman.isupper())
    if g:
        (minx, miny, maxx, maxy) = g[1]
        w += (maxx - minx) / (maxy - miny) * _deg_height(g, root_size) + root_size * 0.15
    else:
        w += c.stringWidth(roman, "Helvetica-Bold", root_size)
    return w + _ext_width(c, ext, ext_size)


def _draw_degree(c, x, baseline, token, root_size, ext_size):
    if not token or token in ("%", "%%", "N.C."):
        c.setFont("Helvetica-Bold", root_size * 0.75)
        c.drawString(x, baseline, token)
        return
    m = re.match(r"([b#]?)([IiVv]+)(.*)", token)
    if not m:
        c.setFont("Helvetica-Bold", root_size)
        c.drawString(x, baseline, token)
        return
    acc, roman, ext = m.group(1), m.group(2), m.group(3)
    xr = x
    if acc:  # alteración delante del numeral, en la línea base (estilo de libro)
        xr = _draw_ext(c, xr, baseline, acc, root_size * 0.75) + root_size * 0.04
    g = _degree_glyph(_ROMAN_NUM[roman.upper()], roman.isupper())
    if g:
        xr += _draw_svg_glyph(c, g, xr, baseline, _deg_height(g, root_size)) + root_size * 0.15
    else:
        c.setFont("Helvetica-Bold", root_size)
        c.drawString(xr, baseline, roman)
        xr += c.stringWidth(roman, "Helvetica-Bold", root_size)
    _draw_ext(c, xr, baseline - root_size * 0.08, ext, ext_size)


def chord_width(c, chord, root_size, ext_size, degree=False):
    """Ancho que ocupará el acorde dibujado por draw_chord."""
    if degree:
        return degree_width(c, chord, root_size, ext_size)
    if chord in _HOLD:   # placeholder: deja un hueco del ancho de un acorde
        return c.stringWidth("C", "Helvetica-Bold", root_size)
    if not chord or chord in ("%", "%%", "N.C."):
        return c.stringWidth(chord, "Helvetica-Bold", root_size * 0.75)
    p = _chord_parts(chord)
    if not p:
        return c.stringWidth(chord, "Helvetica-Bold", root_size)
    letter, acc, rest, bass = p
    lw = c.stringWidth(letter, "Helvetica-Bold", root_size)
    accw = _ext_width(c, acc, ext_size)
    bassw = _ext_width(c, "/" + bass, ext_size) if bass else 0
    return lw + max(accw, _ext_width(c, rest, ext_size), bassw)


def draw_chord(c, x, baseline, chord, root_size, ext_size, degree=False):
    """Acorde estilo Real: raíz grande; bemol/sostenido de la raíz en
    superíndice apilado sobre la extensión (subíndice); bajo (slash) debajo."""
    if degree:
        _draw_degree(c, x, baseline, chord, root_size, ext_size)
        return
    if chord in _HOLD:   # placeholder: no dibuja nada, deja el hueco
        return
    if not chord or chord in ("%", "%%", "N.C."):
        c.setFont("Helvetica-Bold", root_size * 0.75)
        c.drawString(x, baseline, chord)
        return
    p = _chord_parts(chord)
    if not p:
        c.setFont("Helvetica-Bold", root_size)
        c.drawString(x, baseline, chord)
        return
    letter, acc, rest, bass = p
    c.setFont("Helvetica-Bold", root_size)
    c.drawString(x, baseline, letter)
    xr = x + c.stringWidth(letter, "Helvetica-Bold", root_size)
    if acc:  # bemol/sostenido de la raíz: superíndice
        _draw_ext(c, xr, baseline + root_size * 0.36, acc, ext_size)
    _draw_ext(c, xr, baseline - root_size * 0.08, rest, ext_size)  # extensión: subíndice
    if bass:  # bajo justo debajo de la extensión, sin solaparse
        bsz = ext_size * 0.8
        y_bass = (baseline - root_size * 0.08) - 0.7 * bsz - 1
        _draw_ext(c, xr, y_bass, "/" + bass, bsz)


def _draw_coda(c, cx, cy, r=2.3 * mm):
    """Símbolo de coda ⊕ (círculo con cruz) centrado en (cx, cy)."""
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.0)
    c.circle(cx, cy, r, stroke=1, fill=0)
    c.line(cx - r - 1.2, cy, cx + r + 1.2, cy)
    c.line(cx, cy - r - 1.2, cx, cy + r + 1.2)


def _draw_fermata(c, cx, cy, w=4.5 * mm):
    """Símbolo de calderón 𝄐 (arco con punto) centrado en (cx, cy)."""
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.2)
    c.arc(cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2, startAng=20, extent=140)
    c.setFillColorRGB(0, 0, 0)
    c.circle(cx, cy, 0.7, stroke=0, fill=1)


def draw_tune(c, tune, page_w, page_h):
    margin = 18 * mm
    usable_w = page_w - 2 * margin
    cell_w = usable_w / MEASURES_PER_ROW
    cell_h = 19 * mm
    row_gap = 11 * mm   # hueco encima de cada fila para secciones/coda/fermata

    y = page_h - margin

    # Cabecera estilo partitura: título centrado, estilo izq., compositor der.
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(page_w / 2, y, tune.title or "Sin título")
    ts = tune.time_signature
    bpm = getattr(tune, "bpm", "")
    # izquierda en negrita: estilo + BPM (el compás va junto al 1er acorde)
    left = [p for p in [f"({tune.style})" if tune.style else "",
                        f"{bpm} BPM" if bpm else ""] if p]
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y - 6 * mm, " · ".join(left))
    # derecha: compositor (en negrita)
    c.setFont("Helvetica-Bold", 11)
    if tune.composer:
        c.drawRightString(page_w - margin, y - 6 * mm, tune.composer)
    # centro: "Key: " en negro + la tonalidad coloreada (verde mayor / azul menor)
    if tune.key:
        kk = tune.key.replace("-", "m")
        c.setFont("Helvetica-Bold", 12)
        wl = c.stringWidth("Key: ", "Helvetica-Bold", 12)
        wk = c.stringWidth(kk, "Helvetica-Bold", 12)
        x0 = page_w / 2 - (wl + wk) / 2
        yk = y - 7.5 * mm
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x0, yk, "Key: ")
        if kk.endswith("m"):
            c.setFillColorRGB(0.0, 0.0, 0.55)   # menor: azul oscuro
        else:
            c.setFillColorRGB(0.0, 0.42, 0.0)   # mayor: verde oscuro
        c.drawString(x0 + wl, yk, kk)
        c.setFillColorRGB(0, 0, 0)
    y -= 18 * mm

    measures = getattr(tune, "measures", None) or split_measures(tune.chord_string)
    deg = getattr(tune, "degrees", False)   # modo grados (números romanos)
    section_order = []
    for mz in measures:
        s = mz.get("section")
        if s and s not in section_order:
            section_order.append(s)

    def sec_color(s):
        return SECTION_COLORS[section_order.index(s) % len(SECTION_COLORS)]

    cur_section = None
    col = 0
    for i, mz in enumerate(measures):
        if mz.get("section"):
            cur_section = mz["section"]
        if i != 0 and (mz.get("newrow") or col >= MEASURES_PER_ROW):
            col = 0
            gap = row_gap if (mz.get("section") or mz.get("coda") or mz.get("fermata")) else row_gap / 2
            y -= cell_h + gap
        if y - cell_h < margin:
            c.showPage()
            y = page_h - margin
        x = margin + col * cell_w
        y_top = y
        y_bot = y - cell_h

        # compás (4/4) en vertical, a la izquierda del primer acorde
        if i == 0 and ts:
            cy = (y_top + y_bot) / 2
            xt = margin - 3.5 * mm
            num, den = str(ts[0]), str(ts[1])
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 24)
            c.drawCentredString(xt, cy + 4, num)        # numerador (arriba de la barra)
            c.drawCentredString(xt, cy - 21, den)       # denominador (debajo)
            w = max(c.stringWidth(num, "Helvetica-Bold", 24),
                    c.stringWidth(den, "Helvetica-Bold", 24)) / 2
            c.setLineWidth(1.0)
            c.setStrokeColorRGB(0, 0, 0)
            c.line(xt - w, cy, xt + w, cy)              # barra de fracción

        # marca de sección: texto en un cuadro que se adapta a su ancho
        if mz["section"]:
            label = mz["section"]
            col_sec = sec_color(label)
            c.setStrokeColorRGB(*col_sec)
            c.setFillColorRGB(*col_sec)
            fs = 10
            c.setFont("Helvetica-Bold", fs)
            pad_h, box_h = 1.6 * mm, 4.6 * mm
            box_w = max(box_h, c.stringWidth(label, "Helvetica-Bold", fs) + 2 * pad_h)
            by = y_top + 2.0 * mm             # aire entre el cuadro y la celda
            c.setLineWidth(1.2)               # grosor fijo (si no, hereda el anterior)
            c.rect(x, by, box_w, box_h, stroke=1, fill=0)
            c.drawCentredString(x + box_w / 2, by + box_h / 2 - fs * 0.35, label)
            c.setStrokeColorRGB(0, 0, 0)
            c.setFillColorRGB(0, 0, 0)

        # corchete de final (1. / 2.)
        if mz["ending"]:
            ey = y_top + 4.5 * mm
            c.setLineWidth(0.8)
            c.setStrokeColorRGB(0, 0, 0)
            c.line(x, ey, x + cell_w, ey)
            c.line(x, ey, x, y_top)
            c.setFont("Helvetica", 8)
            c.drawString(x + 1.5 * mm, ey + 1.2, mz["ending"] + ".")

        # barras: la 1ª (izq. del 1er compás) y la última (dcha. del último) de
        # CADA sección, en el color de su sección
        is_section_end = (i == len(measures) - 1) or \
                         (i + 1 < len(measures) and measures[i + 1].get("section"))
        left_color = sec_color(mz["section"]) if mz.get("section") else (0, 0, 0)
        right_color = sec_color(cur_section) if (is_section_end and cur_section) else (0, 0, 0)
        draw_barline(c, x, y_top, y_bot, mz["left"], "left", left_color)
        right_kind = mz["right"]
        if mz.get("end"):
            right_kind = "Z"   # marcador End: barra final gorda en este compás
        elif right_kind == "|" and is_section_end:
            right_kind = "S"   # fin de sección: barra un poco más gruesa
        draw_barline(c, x + cell_w, y_top, y_bot, right_kind, "right", right_color)

        # marca de repetición "xN" a la derecha de la barra de cierre
        if mz.get("times") and mz["times"] > 1:
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(x + cell_w + 2, (y_top + y_bot) / 2 - 5, f"x{mz['times']}")

        # acorde(s): se escalan para caber en el compás y se reparten
        c.setFillColorRGB(0, 0, 0)   # los acordes siempre en negro
        chords = mz["chords"] or [""]
        n = len(chords)
        root_size, ext_size = 30, 17
        pad, min_gap = 2 * mm, 3 * mm
        avail = cell_w - 2 * pad
        widths = [chord_width(c, ch, root_size, ext_size, deg) for ch in chords]
        needed = sum(widths) + min_gap * (n - 1)
        if needed > avail:
            scale = avail / needed
            root_size, ext_size = root_size * scale, ext_size * scale
            widths = [w * scale for w in widths]
        baseline = (y_top + y_bot) / 2 - 30 * 0.35
        gap = (avail - sum(widths)) / n   # deja hueco también antes de la barra
        xc = x + pad
        for j, ch in enumerate(chords):
            draw_chord(c, xc, baseline, ch, root_size, ext_size, deg)
            xc += widths[j] + gap

        # coda (⊕) arriba (izq. o der. del compás) y fermata (𝄐) sobre el acorde
        if mz.get("fermata"):
            _draw_fermata(c, x + pad + widths[0] / 2, y_top + 3 * mm)
        if mz.get("coda") == "left":
            cxc = x + (8.5 * mm if mz["section"] else 3 * mm)   # esquiva el cuadro de sección
            _draw_coda(c, cxc, y_top + 7 * mm)
        elif mz.get("coda") == "right":
            _draw_coda(c, x + cell_w - 3 * mm, y_top + 7 * mm)
        col += 1

    c.showPage()


def parse_irealbook(url):
    """Formato antiguo, legible: irealbook://Title=Composer=Style=Key=n=BODY (uno o varios)."""
    body = urllib.parse.unquote(url)[len("irealbook://"):]
    tunes = []
    for song in body.split("==="):
        if not song.strip():
            continue
        parts = song.split("=")
        if len(parts) < 6:
            continue
        title, composer, style, key = parts[0], parts[1], parts[2], parts[3]
        chord_string = Tune._cleanup_chord_string(parts[5])
        tunes.append(SimpleNamespace(
            title=title, composer=composer, style=style, key=key,
            time_signature=Tune._get_time_signature(chord_string),
            chord_string=chord_string,
        ))
    return tunes


def _parse_ireal_tune(song):
    """Parsea una canción del formato irealb:// (robusto al campo opcional de
    transposición, que rompe a pyRealParser cuando está presente)."""
    parts = re.split(r"=+", song)
    title, composer, style, key = parts[0], parts[1], parts[2], parts[3]
    prefix = Tune._chords_prefix
    body_idx = 4 if prefix in parts[4] else 5  # parts[4] = transpose si no trae el prefijo
    scrambled = parts[body_idx].split(prefix, 1)[1]
    chord_string = Tune._cleanup_chord_string(Tune._unscramble_chord_string(scrambled))
    return SimpleNamespace(
        title=title, composer=composer, style=style, key=key,
        time_signature=Tune._get_time_signature(chord_string), chord_string=chord_string)


def parse_irealb(url):
    body = urllib.parse.unquote(url)[len("irealb://"):]
    return [_parse_ireal_tune(s) for s in body.split("===") if s.strip()]


def parse_any(url):
    if url.startswith("irealbook://"):
        return parse_irealbook(url)
    if url.startswith("irealb://"):
        return parse_irealb(url)
    return Tune.parse_ireal_url(url)


# ========================
# Formato propio .txt
# ========================
def parse_chord_token(tok):
    """Acorde legible (Cmaj7, Ddim7, Gmmaj7) -> notación del renderizador."""
    return tok.replace("maj", "Δ").replace("dim", "°").replace("m7b5", "ø7")


# ========================
# Transposición (array cíclico de 12 notas)
# ========================
_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_CHROM_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_CHROM_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
# (clase de altura del tónica, es_menor) -> (nombre canónico, usa sostenidos)
_KEY_TABLE = {
    (0, False): ("C", True), (1, False): ("Db", False), (2, False): ("D", True),
    (3, False): ("Eb", False), (4, False): ("E", True), (5, False): ("F", False),
    (6, False): ("Gb", False), (7, False): ("G", True), (8, False): ("Ab", False),
    (9, False): ("A", True), (10, False): ("Bb", False), (11, False): ("B", True),
    (0, True): ("Cm", False), (1, True): ("C#m", True), (2, True): ("Dm", False),
    (3, True): ("Ebm", False), (4, True): ("Em", True), (5, True): ("Fm", False),
    (6, True): ("F#m", True), (7, True): ("Gm", False), (8, True): ("G#m", True),
    (9, True): ("Am", True), (10, True): ("Bbm", False), (11, True): ("Bm", True),
}


def _note_pc(note):
    """Clase de altura (0-11) de una nota tipo 'E', 'Eb', 'F#', 'Bbb'."""
    pc = _PITCH.get(note[:1])
    if pc is None:
        return None
    for ch in note[1:]:
        pc += 1 if ch == "#" else -1 if ch == "b" else 0
    return pc % 12


def _key_parts(key):
    """('G', True) para 'Gm'/'G-'/'Gmin'; ('Eb', False) para 'Eb'/'Ebmaj'."""
    k = key.strip()
    low = k.lower()
    if low.endswith("min"):
        return k[:-3], True
    if low.endswith("maj"):
        return k[:-3], False
    if k.endswith(("m", "-")):
        return k[:-1], True
    return k, False


def transpose_chord(name, shift, names):
    """Transpone la raíz (y el bajo) de un acorde legible `shift` semitonos."""
    m = re.match(r"([A-G][b#]*)(.*)", name)
    if not m:
        return name
    root, rest = m.group(1), m.group(2)
    bass = ""
    if "/" in rest:
        rest, bass = rest.split("/", 1)
    out = names[(_note_pc(root) + shift) % 12] + rest
    bm = re.match(r"([A-G][b#]*)(.*)", bass)
    if bm:
        out += "/" + names[(_note_pc(bm.group(1)) + shift) % 12] + bm.group(2)
    elif bass:
        out += "/" + bass
    return out


def apply_transpose(measures, key, target):
    """Transpone los compases a `target` (tonalidad p.ej. 'Gm'/'Db'/'C#' o nº de
    semitonos p.ej. '+3'). La calidad (mayor/menor) la manda el tema de origen;
    el nombre y la grafía salen del diccionario `_KEY_TABLE`. Solo si hay key."""
    if not target or not key:
        return key
    src_root, src_minor = _key_parts(key)
    src_pc = _note_pc(src_root)
    if src_pc is None:
        return key
    if re.fullmatch(r"[+-]?\d+", target):
        tgt_pc = (src_pc + int(target)) % 12
    else:
        tgt_root, tgt_minor = _key_parts(target)
        tgt_pc = _note_pc(tgt_root)
        if tgt_pc is None:
            return key
        # si la calidad escrita no coincide con la del tema, usa la relativa
        # (misma armadura): mayor->su relativo menor (-3), menor->su mayor (+3)
        if tgt_minor != src_minor:
            tgt_pc = (tgt_pc - 3) % 12 if src_minor else (tgt_pc + 3) % 12
    new_key, sharp = _KEY_TABLE[(tgt_pc, src_minor)]
    names = _CHROM_SHARP if sharp else _CHROM_FLAT
    shift = (tgt_pc - src_pc) % 12
    if shift:
        for mz in measures:
            mz["raw"] = [transpose_chord(r, shift, names) for r in mz["raw"]]
            mz["chords"] = [parse_chord_token(r) for r in mz["raw"]]
    return new_key


def parse_song_txt(path, transpose=""):
    """Lee un .txt propio: cabecera clave=valor, secciones que empiezan por '='
    (con repetición opcional 'xN'), y líneas de acordes (un acorde = un compás,
    cada línea = una fila). `transpose` (tonalidad/semitonos) tiene prioridad
    sobre el campo `transpose=` del archivo."""
    title = composer = key = bpm = file_transpose = ""
    sig = (4, 4)
    # cada bloque agrupa los compases de una sección con su nº de repeticiones
    blocks = [{"section": None, "repeat": 1, "measures": []}]
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("="):
                label = line[1:].strip()
                repeat = 1
                m = re.search(r"\s*[xX]\s*(\d+)\s*$", label)  # '= Estribillo x3'
                if m:
                    repeat = max(1, int(m.group(1)))
                    label = label[:m.start()].strip()
                blocks.append({"section": label, "repeat": repeat, "measures": []})
            elif re.match(r"^\w+\s*=", line):
                k, v = line.split("=", 1)
                k, v = k.strip().lower(), v.strip().strip('"')
                if k == "tune":
                    title = v
                elif k == "artist":
                    composer = v
                elif k == "key":
                    key = v
                elif k == "bpm":
                    bpm = v
                elif k == "sig":
                    n, d = v.split("/")
                    sig = (int(n), int(d))
                elif k in ("transpose", "trans"):
                    file_transpose = v
            else:
                for j, tok in enumerate(line.split()):
                    coda, fermata, end = False, False, False
                    raws, prettys = [], []
                    for p in tok.split("_"):
                        while p[:1] in ("Q", "f", "U"):   # coda / fermata / End
                            if p[0] == "Q":
                                coda = "left" if not raws else "right"
                            elif p[0] == "U":
                                end = True
                            else:
                                fermata = True
                            p = p[1:]
                        if p:
                            raws.append(p)
                            prettys.append(parse_chord_token(p))
                    if not raws:
                        raws, prettys = [""], [""]
                    blocks[-1]["measures"].append(
                        {"chords": prettys, "raw": raws, "left": "|", "right": "|",
                         "section": None, "ending": None, "newrow": j == 0,
                         "coda": coda, "fermata": fermata, "end": end})

    # marcar (sin duplicar): la etiqueta y la repetición xN van al bloque
    measures = []
    for b in blocks:
        if not b["measures"]:
            continue
        span = [dict(mz) for mz in b["measures"]]
        span[0]["newrow"] = True
        span[0]["section"] = b["section"]
        if b["repeat"] > 1:
            span[0]["left"] = "{"
            span[-1]["right"] = "}"
            span[-1]["times"] = b["repeat"]
        measures.extend(span)
    if not measures:
        raise ValueError("El archivo no contiene acordes.")
    if measures[0]["left"] == "|":
        measures[0]["left"] = "["
    if not any(m.get("end") for m in measures) and measures[-1]["right"] == "|":
        measures[-1]["right"] = "Z"   # si hay marcador End (U), la barra final va ahí
    src_key = key
    effective = (transpose or file_transpose).strip()
    degrees = False
    if effective.lower() in ("grados", "grado", "deg", "degrees"):
        degrees = apply_degrees(measures, key)        # True si hay tonalidad
        failed, transposed = not degrees, False
    else:
        key = apply_transpose(measures, key, effective)
        failed = bool(effective and not src_key)
        transposed = bool(effective and src_key)
    return SimpleNamespace(title=title, composer=composer, style="",
                           key=key, bpm=bpm, time_signature=sig,
                           measures=measures, chord_string="", degrees=degrees,
                           transpose_failed=failed, transposed=transposed)


def _out_name(tune, midi=False):
    """Nombre base + sufijo. En grados: PDF '(grados)', pero el MIDI lleva la
    tonalidad original (suena en el tono original). Transposición: '(tono nuevo)'."""
    name = safe_filename(tune.title or "cancion")
    key = getattr(tune, "key", "").replace("-", "m")
    if getattr(tune, "degrees", False):
        name += f" ({key})" if (midi and key) else " (grados)"
    elif getattr(tune, "transposed", False):
        name += f" ({key})"
    return name


def convert_txt_to_pdf(path, output_folder, transpose=""):
    tune = parse_song_txt(path, transpose)
    os.makedirs(output_folder, exist_ok=True)
    out_path = os.path.join(output_folder, _out_name(tune) + ".pdf")
    c = canvas.Canvas(out_path, pagesize=A4)
    page_w, page_h = A4
    draw_tune(c, tune, page_w, page_h)
    c.save()
    return out_path, 1


# ========================
# Exportar a MIDI (motor de text_to_midi/mainDEF1.py, con voice leading)
# ========================
LETTER_SEMITONE = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
ROOT_LOW = 41   # F2: rango en el que se ancla la nota más grave
ROOT_HIGH = 53  # F3


def note_to_midi(note_str):
    """Acepta cualquier alteración (Cb, E#, C##, Bbb...) y octava opcional."""
    s = note_str
    octave = 4
    if s[-1].isdigit():
        octave, s = int(s[-1]), s[:-1]
    semitone = LETTER_SEMITONE.get(s[0])
    if semitone is None:
        raise ValueError(f"Nota no reconocida: {note_str!r}")
    for ch in s[1:]:
        semitone += 1 if ch == '#' else -1 if ch == 'b' else 0
    return semitone + (octave * 12) + 12


def get_root_voicing(chord_name, root_pitch=3):
    from pychord import Chord
    notes = Chord(chord_name).components_with_pitch(root_pitch=root_pitch)
    return sorted(note_to_midi(n) for n in notes)


def anchor_bass(voicing, low=ROOT_LOW, high=ROOT_HIGH):
    """Octava el voicing hasta que su nota más grave caiga en [F2, F3]."""
    bass = min(voicing)
    while bass > high:
        voicing = sorted(n - 12 for n in voicing)
        bass = min(voicing)
    while bass < low:
        voicing = sorted(n + 12 for n in voicing)
        bass = min(voicing)
    return voicing


def smooth_voicing(base_voicing, prev_voicing, jump_threshold=7):
    """Voice leading: mueve de octava una nota solo si su salto respecto al
    acorde anterior supera el umbral y existe octava más cercana."""
    if prev_voicing is None:
        return base_voicing
    pv, bv = sorted(prev_voicing), sorted(base_voicing)
    if len(pv) != len(bv):
        return bv
    result = []
    for note, prev_note in zip(bv, pv):
        if abs(note - prev_note) > jump_threshold:
            best = note
            for candidate in (note + 12, note - 12):
                if abs(candidate - prev_note) < abs(best - prev_note) and 29 <= candidate <= 65:
                    best = candidate
            result.append(best)
        else:
            result.append(note)
    return anchor_bass(sorted(result))


def tune_to_progression(tune):
    """(nombre_acorde, duración_en_beats) desde los compases del tune."""
    beats = tune.time_signature[0] if tune.time_signature else 4
    prog = []
    for mz in getattr(tune, "measures", []) or []:
        names = mz.get("raw") or mz["chords"]
        n = len(names) or 1
        each = beats / n
        for name in names:
            if name in _HOLD:               # 'nan'/'n': el acorde anterior sigue sonando
                if prog:
                    prog[-1] = (prog[-1][0], prog[-1][1] + each)
                else:
                    prog.append(("", each))  # nada antes: silencio
            else:
                prog.append((name, each))
    return prog


def convert_txt_to_midi(path, output_folder, volume=100, program=0,
                        root_pitch=3, jump_threshold=7, transpose=""):
    from midiutil import MIDIFile

    tune = parse_song_txt(path, transpose)
    tune.measures = expand_repeats(tune.measures)   # el MIDI sí repite N veces
    progression = tune_to_progression(tune)
    tempo = int(tune.bpm) if str(tune.bpm).isdigit() else 120

    midi = MIDIFile(1)
    midi.addTempo(0, 0, tempo)
    midi.addProgramChange(0, 0, 0, program)

    warnings = []
    time = 0.0
    prev = None
    prev_name = None
    for chord_name, duration in progression:
        name = prev_name if chord_name in ("%", "%%") else chord_name  # repetir compás
        if name:
            try:
                voicing = smooth_voicing(anchor_bass(get_root_voicing(name, root_pitch)),
                                         prev, jump_threshold)
                prev = voicing
                for note in voicing:
                    midi.addNote(0, 0, note, time, duration, volume)
                prev_name = name
            except Exception as e:
                warnings.append(f"Acorde ignorado {name!r}: {e}")
        time += duration

    os.makedirs(output_folder, exist_ok=True)
    out_path = os.path.join(output_folder, _out_name(tune, midi=True) + ".mid")
    with open(out_path, "wb") as f:
        midi.writeFile(f)
    return out_path, warnings


# ========================
# Pipeline: fuente -> .txt canónico -> carpeta con txt + src + pdf + midi
# ========================
def _inject_trans(content, value):
    """Pone (o reemplaza) la línea `trans=...` al principio de un .txt."""
    lines = [ln for ln in content.splitlines()
             if not re.match(r"\s*(trans|transpose)\s*=", ln, re.I)]
    return f'trans="{value}"\n' + "\n".join(lines) + "\n"


def _inject_field(content, key, value):
    """Pone (o reemplaza) la línea `key=value` al principio de un .txt."""
    lines = [ln for ln in content.splitlines()
             if not re.match(rf"\s*{key}\s*=", ln, re.I)]
    return f"{key}={value}\n" + "\n".join(lines) + "\n"


def tune_to_txt(tune, transpose=""):
    """Serializa un tune (con .measures y metadatos) al .txt canónico."""
    lines = []
    if tune.title:
        lines.append(f'tune="{tune.title}"')
    if getattr(tune, "composer", ""):
        lines.append(f'artist="{tune.composer}"')
    if getattr(tune, "bpm", ""):
        lines.append(f"bpm={tune.bpm}")
    if getattr(tune, "key", ""):
        lines.append(f'key="{tune.key.replace("-", "m")}"')   # menor con 'm', no '-'
    if tune.time_signature:
        lines.append(f'sig="{tune.time_signature[0]}/{tune.time_signature[1]}"')
    if transpose:
        lines.append(f'trans="{transpose}"')
    lines.append("")

    # nº de veces de cada repetición, asociado a su compás de inicio ({)
    rep, stack = {}, []
    for i, mz in enumerate(tune.measures):
        if mz.get("left") == "{":
            stack.append(i)
        if mz.get("right") == "}" and stack:
            rep[stack.pop()] = mz.get("times", 2)

    row, count = [], 0
    for i, mz in enumerate(tune.measures):
        n = rep.get(i)
        if mz.get("section") or n:
            if row:
                lines.append(" ".join(row))
                row, count = [], 0
            label = mz.get("section") or ""
            if n and n > 1:
                label = (label + f" x{n}").strip()
            lines.append(f"= {label}")
        names = mz.get("raw") or mz["chords"]
        cell = "_".join(names) if names else "n"
        if mz.get("coda") == "right":
            cell = cell + "_Q"
        if mz.get("coda") == "left":
            cell = "Q" + cell
        if mz.get("fermata"):
            cell = "f" + cell
        if mz.get("end"):
            cell = "U" + cell
        row.append(cell)
        count += 1
        if count >= MEASURES_PER_ROW:
            lines.append(" ".join(row))
            row, count = [], 0
    if row:
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"


_FIFTHS_KEY = {0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
               -1: "F", -2: "Bb", -3: "Eb", -4: "Ab", -5: "Db", -6: "Gb", -7: "Cb"}
_ALTER = {0: "", 1: "#", 2: "##", -1: "b", -2: "bb"}
_KIND_MAP = {
    "major": "", "minor": "m", "dominant": "7", "major-seventh": "maj7",
    "minor-seventh": "m7", "diminished": "dim", "diminished-seventh": "dim7",
    "half-diminished": "m7b5", "augmented": "aug", "major-sixth": "6",
    "minor-sixth": "m6", "dominant-ninth": "9", "major-ninth": "maj9",
    "minor-ninth": "m9", "suspended-fourth": "sus4", "suspended-second": "sus2",
}


def _mxl_harmony_name(h):
    root = (h.findtext("root/root-step") or "") + _ALTER.get(int(h.findtext("root/root-alter") or 0), "")
    kind_el = h.find("kind")
    text = kind_el.get("text") if kind_el is not None else None
    if text is None:
        text = _KIND_MAP.get((kind_el.text or "").strip() if kind_el is not None else "", "")
    name = root + text
    for deg in h.findall("degree"):
        da = int(deg.findtext("degree-alter") or 0)
        name += ("b" if da < 0 else "#" if da > 0 else "") + (deg.findtext("degree-value") or "")
    bass = h.findtext("bass/bass-step")
    if bass:
        name += "/" + bass + _ALTER.get(int(h.findtext("bass/bass-alter") or 0), "")
    return name


def parse_musicxml(path):
    """MusicXML -> tune con .measures (un <measure> = un compás; varias
    <harmony> en el compás = varios acordes)."""
    root = ET.parse(path).getroot()
    title = root.findtext("work/work-title") or os.path.splitext(os.path.basename(path))[0]
    composer = ""
    for cr in root.findall(".//creator"):
        if cr.get("type") == "composer":
            composer = cr.text or ""
    fifths = root.findtext(".//attributes/key/fifths")
    key = _FIFTHS_KEY.get(int(fifths), "") if fifths is not None else ""
    beats = root.findtext(".//attributes/time/beats")
    beat_type = root.findtext(".//attributes/time/beat-type")
    sig = (int(beats), int(beat_type)) if beats and beat_type else (4, 4)

    measures = []
    for m in root.findall(".//part/measure"):
        raws = [_mxl_harmony_name(h) for h in m.findall("harmony")]
        if not raws:
            continue
        # barras de repetición: forward = inicio { , backward = fin } (times = nº)
        left, right, times = "|", "|", None
        for rep in m.findall("barline/repeat"):
            if rep.get("direction") == "forward":
                left = "{"
            elif rep.get("direction") == "backward":
                right = "}"
                if rep.get("times"):
                    times = int(rep.get("times"))
        measures.append({"chords": [parse_chord_token(r) for r in raws], "raw": raws,
                         "left": left, "right": right, "times": times,
                         "section": m.findtext("direction/direction-type/rehearsal"),
                         "ending": None,
                         "coda": "left" if m.find(".//coda") is not None else False,
                         "fermata": m.find(".//fermata") is not None})
    measures = mark_repeats(measures)
    return SimpleNamespace(title=title, composer=composer, style="", key=key,
                           bpm="", time_signature=sig, measures=measures, chord_string="")


def ireal_to_tunes(url):
    """Real url -> lista de tunes con .measures (y .raw para el MIDI)."""
    return [SimpleNamespace(
        title=t.title, composer=getattr(t, "composer", ""), style=getattr(t, "style", ""),
        key=getattr(t, "key", ""), bpm="", time_signature=t.time_signature,
        measures=mark_repeats(split_measures(t.chord_string)), chord_string=t.chord_string)
        for t in parse_any(url)]


def load_tunes(src):
    """Devuelve (kind, [tunes]). src = url Real o ruta a .musicxml/.xml/.txt."""
    if src.startswith("irealb://") or src.startswith("irealbook://"):
        return "ireal", ireal_to_tunes(src)
    ext = os.path.splitext(src)[1].lower()
    if ext in (".musicxml", ".xml"):
        return "musicxml", [parse_musicxml(src)]
    if ext == ".txt":
        return "txt", [parse_song_txt(src)]
    raise ValueError(f"Fuente no reconocida: {src!r}")


def process_source(src, dest_root, transpose="", bpm="", output="full"):
    """Convierte la fuente y crea, por canción, una carpeta con txt+src+pdf+midi.
    `output`: 'full' (defecto) crea subcarpeta por canción con todos los archivos;
    'pdf'/'midi' guarda solo ese archivo en dest_root sin crear subcarpeta.
    Devuelve (carpetas, avisos)."""
    kind, tunes = load_tunes(src)
    if not tunes:
        raise ValueError("No se ha leído ninguna canción de la fuente.")
    folders, warnings = [], []
    for tune in tunes:
        name = safe_filename(tune.title or "cancion")

        if bpm:
            tune.bpm = bpm   # override del tempo (afecta a ireal/musicxml via tune_to_txt)
        if transpose and not getattr(tune, "key", ""):
            warnings.append(f"'{tune.title}': no se pudo transponer (la canción no tiene tonalidad).")

        if output == "full":
            folder = os.path.join(dest_root, name)
            if os.path.exists(folder):
                raise FileExistsError(f"La carpeta ya existe: {folder}")
            os.makedirs(folder)

            # 1) .txt canónico (con trans=/bpm= si se pidieron)
            txt_path = os.path.join(folder, name + ".txt")
            if kind == "txt":
                content = open(src, encoding="utf-8-sig").read()
                if transpose:
                    content = _inject_trans(content, transpose)
                if bpm:
                    content = _inject_field(content, "bpm", bpm)
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(tune_to_txt(tune, transpose))

            # 2) fuente original
            if kind == "ireal":
                with open(os.path.join(folder, name + ".ireal"), "w", encoding="utf-8") as f:
                    f.write(src)
            elif kind == "musicxml":
                shutil.copyfile(src, os.path.join(folder, os.path.basename(src)))

            # 3) PDF + MIDI desde el .txt canónico (la transposición ya va en el txt)
            convert_txt_to_pdf(txt_path, folder)
            convert_txt_to_midi(txt_path, folder)
            folders.append(folder)

        else:
            import tempfile
            os.makedirs(dest_root, exist_ok=True)
            tmp_dir = tempfile.mkdtemp()
            try:
                txt_path = os.path.join(tmp_dir, name + ".txt")
                if kind == "txt":
                    content = open(src, encoding="utf-8-sig").read()
                    if transpose:
                        content = _inject_trans(content, transpose)
                    if bpm:
                        content = _inject_field(content, "bpm", bpm)
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(content)
                else:
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(tune_to_txt(tune, transpose))
                if output == "pdf":
                    convert_txt_to_pdf(txt_path, dest_root)
                elif output == "midi":
                    convert_txt_to_midi(txt_path, dest_root)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            folders.append(dest_root)

    return folders, warnings


# ========================
# GUI
# ========================
def select_folder():
    folder = filedialog.askdirectory()
    if folder:
        entry_folder.delete(0, ctk.END)
        entry_folder.insert(0, folder)


def clear_url():
    entry_url.delete(0, ctk.END)
    entry_url.focus_set()


def select_source():
    path = filedialog.askopenfilename(
        filetypes=[("Fuentes", "*.musicxml *.xml *.txt"), ("Todos", "*.*")])
    if path:
        entry_url.delete(0, ctk.END)
        entry_url.insert(0, path)


def convert():
    src = entry_url.get().strip().strip('"')
    folder = entry_folder.get().strip() or DEFAULT_OUTPUT

    if not src:
        messagebox.showwarning("Campo vacío",
                               "Pega un enlace Real o elige un archivo .musicxml/.txt.")
        return
    is_url = src.startswith("irealb://") or src.startswith("irealbook://")
    if not is_url and not os.path.isfile(src):
        messagebox.showwarning("Fuente no válida",
                               "Debe ser un enlace irealb:// / irealbook:// o un "
                               "archivo .musicxml/.xml/.txt existente.")
        return

    transpose = entry_transpose.get().strip()
    bpm = entry_bpm.get().strip()
    out_opts = {"Completo": "full", "Solo PDF": "pdf", "Solo MIDI": "midi"}
    output = out_opts.get(seg_output.get(), "full")

    btn_convert.configure(state="disabled")
    status_label.configure(text="Convirtiendo...")
    gen_label = "Generando txt, PDF y MIDI..." if output == "full" else f"Generando {seg_output.get()}..."
    detail_label.configure(text=gen_label)

    def task():
        try:
            folders, warnings = process_source(src, folder, transpose, bpm, output)
            root.after(0, lambda: done_ok(folders, warnings))
        except Exception as e:
            msg = str(e)
            root.after(0, lambda: done_err(msg))

    threading.Thread(target=task, daemon=True).start()


def done_ok(folders, warnings=()):
    status_label.configure(text="¡Listo!")
    detail = f"{len(folders)} canción(es).\n{folders[0]}"
    if warnings:
        detail += "\n⚠ " + "\n⚠ ".join(warnings)
    detail_label.configure(text=detail)
    btn_convert.configure(state="normal")
    if warnings:
        messagebox.showwarning("Aviso", "\n".join(warnings))
    if messagebox.askyesno("Hecho", f"Creado en:\n{folders[0]}\n\n¿Abrir la carpeta?"):
        os.startfile(folders[0])


def done_err(msg):
    status_label.configure(text="Error")
    detail_label.configure(text="")
    btn_convert.configure(state="normal")
    messagebox.showerror("Error", f"No se pudo convertir:\n\n{msg}")


def main():
    global root, entry_url, entry_folder, entry_transpose, entry_bpm, seg_output, btn_convert, status_label, detail_label

    root = ctk.CTk()
    root.title("Acordes → PDF + MIDI")
    root.geometry("760x740")
    root.minsize(720, 700)
    root.configure(fg_color=COLOR_BG)

    ctk.CTkLabel(
        root, text="🎼 Acordes  →  PDF + MIDI",
        font=ctk.CTkFont(size=24, weight="bold"), text_color=COLOR_PRIMARY,
    ).pack(pady=(24, 4))
    ctk.CTkLabel(
        root, text="Real · MusicXML · TXT",
        font=ctk.CTkFont(size=13), text_color=COLOR_SECONDARY,
    ).pack(pady=(0, 16))

    card = ctk.CTkFrame(root, fg_color=COLOR_SURFACE, corner_radius=12)
    card.pack(fill="x", padx=24, pady=8)

    ctk.CTkLabel(card, text="Enlace Real o archivo (.musicxml / .txt)",
                 text_color=COLOR_TEXT, anchor="w").pack(fill="x", padx=16, pady=(16, 4))
    frame_url = ctk.CTkFrame(card, fg_color="transparent")
    frame_url.pack(fill="x", padx=16, pady=(0, 12))

    entry_url = ctk.CTkEntry(frame_url, placeholder_text="irealb://, o ruta a .musicxml / .txt",
                             height=38, fg_color=COLOR_BG, border_color=COLOR_PRIMARY_VARIANT,
                             border_width=2, text_color=COLOR_TEXT,
                             placeholder_text_color=COLOR_PLACEHOLDER)
    entry_url.pack(side="left", fill="x", expand=True)

    btn_file = ctk.CTkButton(frame_url, text="📄 Archivo", command=select_source, width=110, height=38,
                             fg_color="transparent", border_color=COLOR_SECONDARY, border_width=2,
                             text_color=COLOR_SECONDARY, hover_color=COLOR_SURFACE)
    btn_file.pack(side="left", padx=(8, 0))

    btn_clear = ctk.CTkButton(frame_url, text="Limpiar", command=clear_url, width=90, height=38,
                              fg_color="transparent", border_color=COLOR_SECONDARY, border_width=2,
                              text_color=COLOR_SECONDARY, hover_color=COLOR_SURFACE)
    btn_clear.pack(side="left", padx=(8, 0))

    ctk.CTkLabel(card, text="Carpeta de salida (se crea una subcarpeta por canción)",
                 text_color=COLOR_TEXT, anchor="w").pack(fill="x", padx=16, pady=(4, 4))
    frame_folder = ctk.CTkFrame(card, fg_color="transparent")
    frame_folder.pack(fill="x", padx=16, pady=(0, 16))

    entry_folder = ctk.CTkEntry(frame_folder, placeholder_text="Selecciona una carpeta...", height=38,
                                fg_color=COLOR_BG, border_color=COLOR_PRIMARY_VARIANT, border_width=2,
                                text_color=COLOR_TEXT, placeholder_text_color=COLOR_PLACEHOLDER)
    entry_folder.pack(side="left", fill="x", expand=True)
    entry_folder.insert(0, DEFAULT_OUTPUT)

    btn_select = ctk.CTkButton(frame_folder, text="📁 Elegir…", command=select_folder, width=130, height=38,
                               fg_color="transparent", border_color=COLOR_SECONDARY, border_width=2,
                               text_color=COLOR_SECONDARY, hover_color=COLOR_SURFACE)
    btn_select.pack(side="left", padx=(8, 0))

    ctk.CTkLabel(card, text="Transponer a (opcional: Gm, Abmin, C#, F… ; +3 semitonos; o 'grados')",
                 text_color=COLOR_TEXT, anchor="w").pack(fill="x", padx=16, pady=(4, 4))
    entry_transpose = ctk.CTkEntry(card, placeholder_text="vacío = sin transponer", height=38,
                                   fg_color=COLOR_BG, border_color=COLOR_PRIMARY_VARIANT, border_width=2,
                                   text_color=COLOR_TEXT, placeholder_text_color=COLOR_PLACEHOLDER)
    entry_transpose.pack(fill="x", padx=16, pady=(0, 12))

    ctk.CTkLabel(card, text="BPM (opcional: sobrescribe el tempo de la fuente)",
                 text_color=COLOR_TEXT, anchor="w").pack(fill="x", padx=16, pady=(4, 4))
    entry_bpm = ctk.CTkEntry(card, placeholder_text="vacío = el de la fuente", height=38,
                             fg_color=COLOR_BG, border_color=COLOR_PRIMARY_VARIANT, border_width=2,
                             text_color=COLOR_TEXT, placeholder_text_color=COLOR_PLACEHOLDER)
    entry_bpm.pack(fill="x", padx=16, pady=(0, 12))

    ctk.CTkLabel(card, text="Salida (opcional)",
                 text_color=COLOR_TEXT, anchor="w").pack(fill="x", padx=16, pady=(4, 4))
    seg_output = ctk.CTkSegmentedButton(card, values=["Completo", "Solo PDF", "Solo MIDI"],
                                        fg_color=COLOR_BG, selected_color=COLOR_PRIMARY,
                                        selected_hover_color=COLOR_PRIMARY_VARIANT,
                                        unselected_color=COLOR_BG, text_color=COLOR_TEXT,
                                        unselected_hover_color=COLOR_SURFACE)
    seg_output.set("Completo")
    seg_output.pack(fill="x", padx=16, pady=(0, 16))

    btn_convert = ctk.CTkButton(root, text="🎼 Convertir (PDF + MIDI)", command=convert, height=46,
                                font=ctk.CTkFont(size=15, weight="bold"), fg_color=COLOR_PRIMARY,
                                hover_color=COLOR_PRIMARY_VARIANT, text_color=COLOR_BG)
    btn_convert.pack(fill="x", padx=24, pady=(8, 4))

    status_label = ctk.CTkLabel(root, text="Listo para convertir",
                                font=ctk.CTkFont(size=14, weight="bold"), text_color=COLOR_SECONDARY)
    status_label.pack(pady=(16, 4))

    detail_label = ctk.CTkLabel(root, text="", font=ctk.CTkFont(size=12),
                                text_color=COLOR_TEXT, wraplength=680)
    detail_label.pack(pady=(0, 16))

    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\nCerrado.")
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # python ireal_def.py <archivo-o-url> [transpose] [bpm]
        transpose = sys.argv[2] if len(sys.argv) > 2 else ""
        bpm = sys.argv[3] if len(sys.argv) > 3 else ""
        folders, warnings = process_source(sys.argv[1].strip('"'), DEFAULT_OUTPUT, transpose, bpm)
        for fo in folders:
            print("Carpeta:", fo)
        for w in warnings:
            print("  ⚠", w)
        os.startfile(folders[0])
    else:
        main()
