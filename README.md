# Mini Reproductor Windows 🎵

Un widget flotante, ligero que muestra y controla la música que está sonando en tu PC (Spotify, YouTube, navegadores, etc.) a través del sistema multimedia nativo de Windows (SMTC).

## ✨ Características Principales

* **Integración Nativa (SMTC):** Funciona con cualquier aplicación de Windows que reporte multimedia (Spotify, Chrome, Edge, VLC, etc.).
* **Letras Sincronizadas:** Busca y muestra automáticamente la letra de la canción (con efecto karaoke) gracias a la API de LRCLib.
* **Diseño Fluido y Escalable:** Construido sobre PyQt6, soporta escalado DPI nativo. Nada se ve borroso, sin importar la resolución de tu monitor.
* **Modo Mini:** Haz doble clic en la carátula para colapsar el reproductor a su tamaño mínimo, ideal para dejarlo en una esquina de la pantalla.
* **Colores Dinámicos:** La interfaz adapta automáticamente su paleta de colores extrayendo el color de acento de la carátula de la canción actual.
* **Bandeja del Sistema:** Opción para minimizar el reproductor a la bandeja del sistema (System Tray) para que no moleste cuando no lo necesites.
* **Siempre Encima (Topmost):** Mantén el widget siempre visible por encima del resto de ventanas
