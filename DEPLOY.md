# Despliegue: GitHub Pages (frontend) + PythonAnywhere (backend)

La app está partida en dos:

- **Frontend estático** → `docs/` (lo sirve GitHub Pages).
- **Backend Flask** → `web/app.py` (lo ejecuta PythonAnywhere).

El frontend llama al backend por HTTP, así que el backend tiene CORS abierto (`Access-Control-Allow-Origin: *`).

---

## 1) Backend en PythonAnywhere (gratis, sin tarjeta)

1. Crea una cuenta en https://www.pythonanywhere.com (plan **Beginner**, gratis).
2. **Sube el código.** En la pestaña **Consoles → Bash**:
   ```bash
   git clone https://github.com/TUUSUARIO/TUREPO.git
   ```
   (o sube a mano la carpeta `web/` con la pestaña *Files*).
3. **Crea el virtualenv e instala dependencias** (en la consola Bash):
   ```bash
   mkvirtualenv irealpdf --python=python3.10
   pip install flask reportlab pychord midiutil pyRealParser
   ```
4. **Crea la web app.** Pestaña **Web → Add a new web app**:
   - Framework: **Manual configuration** (no "Flask").
   - Versión de Python: la misma del virtualenv (3.10).
5. En la pestaña **Web**, configura:
   - **Source code**: `/home/TUUSUARIO/TUREPO/web`
   - **Virtualenv**: `/home/TUUSUARIO/.virtualenvs/irealpdf`
   - **WSGI configuration file**: pulsa para editarlo y deja SOLO esto:
     ```python
     import sys
     path = "/home/TUUSUARIO/TUREPO/web"
     if path not in sys.path:
         sys.path.insert(0, path)
     from app import app as application
     ```
6. Pulsa **Reload**. El backend queda en:
   `https://TUUSUARIO.pythonanywhere.com`
   (al abrir esa URL verás un texto confirmando que el backend está vivo; la
   interfaz real está en GitHub Pages. El endpoint `/convert` responde a POST).

> Nota: tras `git pull` para actualizar, hay que pulsar **Reload** en la pestaña Web.

---

## 2) Frontend en GitHub Pages

1. Edita **`docs/index.html`** y pon tu URL del backend (sin barra final):
   ```js
   const BACKEND_URL = "https://TUUSUARIO.pythonanywhere.com";
   ```
2. Haz commit y push a `main`.
3. En GitHub: **Settings → Pages**:
   - **Source**: *Deploy from a branch*
   - **Branch**: `main` · carpeta **`/docs`** · Save.
4. En 1-2 min la web estará en:
   `https://TUUSUARIO.github.io/TUREPO/`

---

## Comprobar que funciona

Abre la URL de Pages, pega un enlace o sube un `.musicxml`/`.txt`, y pulsa **Generar**.
Si falla con error de red/CORS, revisa que `BACKEND_URL` es correcto y que el backend
está *reloaded* en PythonAnywhere.
