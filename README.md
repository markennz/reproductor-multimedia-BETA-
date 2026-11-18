# Mini Reproductor Windows 🎵

Widget flotante, ligero y personalizable para Windows que muestra y controla la música o vídeos que suenan en tu PC (Spotify, YouTube, navegadores, VLC, etc.) mediante la API multimedia nativa del sistema (**SMTC**).

Reconstruido sobre **PyQt6** con renderizado vectorial nativo para verse nítido en cualquier escala DPI (100 %, 125 %, 150 %...) y configuración multimonitor.

![Mini Reproductor Windows](https://via.placeholder.com/600x300.png?text=A%C3%B1ade+aqui+una+captura+de+tu+reproductor)

---

## ✨ Características Principales

* **Integración nativa con Windows (SMTC):** Detecta automáticamente título, artista, álbum, progreso y carátula de cualquier fuente multimedia compatible en Windows.
* **4 Diseños visuales (*Skins*) intercambiables:**
  * **Clásico oscuro:** Adapta dinámicamente su color de acento a la carátula de la canción actual.
  * **Frutiger Aero:** Estética de cristal azul brillante estilo Windows XP/Vista.
  * **NeoY2K:** Diseño cromado futurista de los 2000 con trama *halftone*, pantalla tipo iPod y adornos que sobresalen del recuadro.
  * **Thriller · M. Jackson:** Tema especial nocturno con luna llena, niebla, destellos animados y tipografía dorada.
* **Modo «Auto por artista»:** Activa automáticamente diseños temáticos cuando suena un artista compatible y regresa a tu diseño habitual al cambiar de tema.
* **Letras sincronizadas (Karaoke):** Descarga letras desde **LRCLIB** con caché local, seguimiento automático suave y salto de tiempo al hacer doble clic en cualquier línea.
* **Volumen por aplicación (`pycaw`):** Ajusta el volumen específico del programa que reproduce el audio sin alterar el mezclador general del sistema.
* **Modo Mini y Bandeja del Sistema:** Colapsa el widget a un formato compacto con doble clic en la portada o minimízalo al área de notificación (*System Tray*) con icono dinámico.
* **Siempre encima y opacidad:** Mantén el reproductor visible sobre otras ventanas y ajusta su transparencia entre el 30 % y el 100 %.

---

## 🎮 Controles y Atajos

### Ratón
* **Mover ventana:** Arrastra desde cualquier zona libre (incluye imán a los bordes de la pantalla).
* **Redimensionar:** Arrastra desde la esquina inferior derecha (`◢`).
* **Modo Mini:** Doble clic sobre la carátula.
* **Volumen:** Rueda del ratón sobre la carátula o sobre la barra de volumen.
* **Silenciar / Restaurar:** Clic en el icono del altavoz.
* **Saltar en la canción:** Clic o arrastre en la barra de progreso, o doble clic sobre una línea de la letra sincronizada.

### Teclado (con la ventana activa)
* `Espacio`: Reproducir / Pausar
* `←` / `→`: Canción anterior / siguiente
* `↑` / `↓`: Subir / Bajar volumen
* `L`: Mostrar / Ocultar panel de letra
* `M`: Activar / Desactivar Modo Mini
