# iReal / MusicXML / TXT → PDF + MIDI

Convierte progresiones de acordes en **lead sheet PDF** (estilo iReal Pro) y en **MIDI** (con voice leading).

Acepta 3 fuentes; todas se normalizan a un `.txt` propio y de ahí se generan los archivos.

## Uso

```bash
# GUI
python ireal_pdf.py

# CLI: archivo .txt, .musicxml/.xml, o enlace iReal
python ireal_pdf.py micancion.txt
python ireal_pdf.py Fujita.musicxml
python ireal_pdf.py "irealb://..."
```

Por cada canción se crea una carpeta en `salida/<Cancion>/` con:

```
salida/Fujita/
├─ Fujita.txt        # .txt canónico (generado siempre)
├─ Fujita.musicxml   # la fuente original (.musicxml / .ireal; en txt es el propio .txt)
├─ Fujita.pdf
└─ Fujita.mid
```

Dependencias: `pip install -r requirements.txt`

---

## Cómo escribir un `.txt`

### Argumentos principales (cabecera)

Al **principio del archivo**, una línea por variable con la forma `clave=valor`.
Las claves no distinguen mayúsculas. **Todos los valores van entre comillas
`"..."` excepto `bpm`**, que es el único puramente numérico y va suelto. Todas
las variables son opcionales; este es el conjunto completo (no hay más):

| Clave    | Tipo        | Por defecto | Qué hace                                            |
|----------|-------------|-------------|-----------------------------------------------------|
| `tune`   | texto       | `cancion`   | título; da nombre a la carpeta y a los archivos     |
| `artist` | texto       | (vacío)     | compositor; aparece arriba a la derecha             |
| `bpm`    | número      | `120`       | tempo del MIDI                                       |
| `key`    | texto       | (vacío)     | tonalidad (`Eb`, `Gm`, `F#m`…); cabecera del PDF y origen para transponer |
| `sig`    | `n/d`       | `4/4`       | compás; reparte los beats por compás en el MIDI     |
| `transpose` / `trans` | tono, nº o `grados` | (vacío) | transpone (ver abajo) |

```
tune="Mi Tema"
artist="Yo"
bpm=120
key="Eb"
sig="4/4"
```

(Solo `bpm` va sin comillas.)

Tras la cabecera va una **línea en blanco** y luego los acordes.

### Transponer (opcional)

`transpose=` mueve todos los acordes (y la `key`) a otra tonalidad. **Solo
funciona si `key` está definida** (hace falta saber el tono de origen); si no, se
avisa y no se transpone. También hay un campo "Transponer a" en la interfaz.

- **Tonalidad destino**: `trans=Gm`, `trans=Db`, `trans=C#`, `trans=Abmin`…
- **Semitonos**: `trans=+3`, `trans=-2`.
- **Grados** (números romanos / función): `trans=grados`. Cada acorde se muestra
  como su grado relativo a la tonalidad: **mayúscula = mayor, minúscula = menor**
  (con su `m`), `°`=disminuido, `ø7`=semidisminuido. Las alteraciones van delante
  del numeral en la línea base (`♭VII`, `♯IV`); medido respecto a la escala mayor
  de la tónica, así que en menor los III/VI/VII salen como `♭III`/`♭VI`/`♭VII`.
  El MIDI sigue siendo el acorde real (los grados son solo para el PDF). Requiere
  `key` definida.

(`trans` y `transpose` son lo mismo.) Al transponer, el **PDF y el MIDI** salen con
el tono entre paréntesis en el nombre: `Mi Tema (Gm).pdf` / `Mi Tema (Gm).mid`. En
modo `grados` el PDF es `Mi Tema (grados).pdf` pero el **MIDI lleva el tono original**
(`Mi Tema (Em).mid`), porque suena en el tono original.

Desde la interfaz, lo que pongas en "Transponer a" y en "BPM" se guarda en el `.txt`
(como `trans=` / `bpm=`, sobrescribiendo lo que trajera la fuente) y de ahí se
generan el PDF y el MIDI. El campo **BPM** es opcional: vacío = el tempo de la fuente.

La **calidad mayor/menor la manda el tema de origen** (transponer no convierte
mayor en menor). Si escribes una tonalidad de la otra calidad, se usa su
**relativa** (misma armadura): con un tema en Em, `transpose=C` y `transpose=Am`
dan lo mismo (Am); `transpose=D` = `transpose=Bm`. Cada tono coge su grafía
correcta (sostenidos o bemoles).

### Acordes

- **Un acorde = un compás.** Separados por espacios.
- **Cada línea = una fila** en el PDF.
- **`_`** une varios acordes en el **mismo compás**, repartiendo los beats a partes
  iguales: en 4/4, `Dm7_G7` = 2 beats cada uno; `Am_nan_Dm_G` = 1 beat cada uno.
- **`nan`** (o `n`) es un hueco: en el **PDF** deja un espacio vacío y en el **MIDI**
  el acorde anterior **sigue sonando** ese beat. Así `Am_nan_Dm_G` en 4/4 = Am 2 beats,
  Dm 1, G 1. En 3/4, `Am_Dm_G` = 1 beat cada uno.

### Secciones (opcional)

Una línea que **empieza por `=`** marca el inicio de una sección. La etiqueta se dibuja sobre el primer compás de la fila siguiente.

```
= Estribillo
C Am F G
```

Si no pones secciones, no aparece ninguna.

### Repetir una sección N veces

Añade **`xN`** al final de la línea de la sección. Esa sección se repite `N`
veces tanto en el **PDF** (se dibuja N veces, cada una con su etiqueta) como en
el **MIDI** (suena N veces). La sección llega hasta el siguiente `=` o el final.

```
= A x2
C Am F G

= B
Dm7 G7 C C
```

Aquí la sección **A** sale dos veces y la **B** una. Sin `xN` (o `x1`) no se repite.

> La repetición es de los `.txt`. Las fuentes MusicXML e iReal generan sus
> propias secciones, pero sin este `xN`.

---

## Ejemplos

### Mínimo

```
tune="Blues en C"
bpm=120

C7 F7 C7 C7
F7 F7 C7 C7
G7 F7 C7 G7
```

### Con varios acordes por compás (`_`) y compás 3/4

```
tune="Vals"
artist="Juan"
bpm=90
key="G"
sig="3/4"

G Em Am_D7
G Em A7_D7
```

### Con secciones

```
tune="Mi Cancion"
artist="Yo"
bpm=140
key="Am"

= Intro
Am F C G

= Verso
Am Am Dm Dm
F F E7 E7

= Estribillo
C G Am F
C G Dm_E7 Am
```

---

## Notación de acordes

Se escriben en notación legible normal. Para el PDF se convierten a símbolos:

| Escribes  | PDF   | Significado            |
|-----------|-------|------------------------|
| `Cmaj7`   | `C△7` | mayor séptima          |
| `Dm7`     | `Dm7` | menor séptima          |
| `Ddim7`   | `D°7` | disminuido             |
| `Dm7b5`   | `Dø`  | semidisminuido         |
| `G7`      | `G7`  | dominante              |
| `F/G`     | `F/G` | con bajo (slash chord) |
| `A#`, `Bb`| `A#`  | alteraciones           |

La raíz va grande y el resto (extensión) en subíndice, como en iReal.

> El `.txt` canónico guarda siempre los nombres legibles (`Dm7b5`) para que el MIDI funcione (usa `pychord`); los símbolos `△ ° ø` son solo del PDF.
