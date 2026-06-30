# Chords → PDF + MIDI

Turns a chord progression into a **PDF lead sheet** (Real Pro style) and a **MIDI** with voice leading.

**▶ Web: https://yagoestudios.github.io/chords-pdf-midi/**

Input: a Real link, a `.musicxml`/`.xml` file, or your own `.txt`. Everything is normalized to a `.txt`, and the PDF and MIDI are generated from it.

## What it looks like

This `.txt`:

```
tune="Fujita"
artist="Lewis"
key="C"
sig="4/4"
bpm=200

= x2
Cmaj7 Bm7b5_E7 Am7 Gm7_C7
Fmaj7 Em7b5_A7 Dm7 Fm_F/G
```

produces this PDF:

![Fujita example](ejemplo-fujita.png)

## Usage (web)

1. Open the website.
2. Paste a link, upload a `.musicxml`/`.txt`, or paste the text of a `.txt`.
3. (Optional) *Transpose to* and *BPM*.
4. Output: **Full** (`.zip` with txt + source + pdf + mid) or **PDF only** / **MIDI only**.
5. **Generate**.

## Local usage (optional)

Desktop app with the same logic (`local.py`):

```bash
pip install reportlab pychord midiutil pyRealParser customtkinter

python local.py                  # graphical interface
python local.py mysong.txt       # CLI (.txt / .musicxml / link)
```

Creates `output/<Song>/` with the `.txt`, the source, the `.pdf` and the `.mid`.

---

# `.txt` format

## Header

One line per variable, `key=value`, at the top of the file. All optional. **Values in quotes except `bpm`.** Then a blank line and the chords.

| Key | Default | What it does |
|-------|-------------|----------|
| `tune` | `cancion` | title; names the folder and files |
| `artist` | (empty) | composer (top right) |
| `bpm` | `120` | MIDI tempo (no quotes) |
| `key` | (empty) | key (`Eb`, `Gm`, `F#m`…); required to transpose |
| `sig` | `4/4` | time signature; distributes the beats in the MIDI |
| `trans` | (empty) | transpose (see below) |

## Chords

- **One chord = one bar.** Separated by spaces.
- **Each line = one row** of the PDF.
- **`_`** joins chords in the **same bar**, splitting the beats: in 4/4, `Dm7_G7` = 2 beats each.
- **`nan`** (or `n`) is a gap: in the PDF it leaves empty space; in the MIDI the previous chord keeps sounding that beat. `Am_nan_Dm_G` in 4/4 = Am 2, Dm 1, G 1.

## Sections and repeats

A line that **starts with `=`** marks a section (its label is drawn above the next row). Add **`xN`** to repeat it N times (in PDF and MIDI):

```
= A x2
C Am F G

= B
Dm7 G7 C C
```

A plays twice, B once.

## Transpose

`trans=` shifts all chords and the `key`. **Requires `key` defined.** There is also a "Transpose to" field on the web.

- **Target key**: `trans=Gm`, `trans=Db`, `trans=Abmin`…
- **Semitones**: `trans=+3`, `trans=-2`.
- **Degrees** (roman numerals): `trans=deg` → uppercase = major, lowercase = minor, `°` diminished, `ø7` half-diminished. The MIDI still plays the real chords.

The major/minor quality is set by the source tune; if you ask for the other one, its relative (same key signature) is used. The PDF/MIDI come out with the key in the name: `Fujita (Gm).pdf`.

## Chord notation

Plain readable notation; the PDF converts it to symbols:

| You write | PDF | |
|----------|-----|--|
| `Cmaj7` | `C△7` | major seventh |
| `Dm7` | `Dm7` | minor seventh |
| `Ddim7` | `D°7` | diminished |
| `Dm7b5` | `Dø` | half-diminished |
| `G7` | `G7` | dominant |
| `F/G` | `F/G` | slash chord |

The `.txt` always stores the readable names (`Dm7b5`) so the MIDI works; the `△ ° ø` symbols are PDF-only.
