# reproductor-multimedia-BETA-
Primera versión del reproductor multimedia flotante para Windows

# 🎵 Windows Media Player Widget (v0.3)

Un widget flotante minimalista, moderno y sin bordes para Windows que se integra nativamente con los controles multimedia del sistema (compatible con Spotify, YouTube, VLC, etc.).

## ✨ Características Principales (v0.3)

* 🎤 **Modo Karaoke (Letras Sincronizadas):** Extrae automáticamente las letras desde *LRCLib*, realiza desplazamiento (*scroll*) en tiempo real y resalta la frase activa.
* 🎨 **Colores Adaptativos Dinámicos:** La interfaz, el progreso y el texto destacado cambian de color en tiempo real basándose en los tonos dominantes de la carátula de la canción.
* 🌊 **Redimensión Fluida y Optimizada:** Ajusta el tamaño de la ventana con el tirador integrado sin tirones ni bloques grises, consumiendo mínimos recursos.
* 🔄 **Modo Normal y Modo Mini:** Cambia entre la vista completa con detalles y el modo compacto de doble clic.
* 📌 **Opciones Avanzadas:** 
  * Ventana siempre encima (*Topmost*).
  * Control deslizante de opacidad de la ventana (30% - 100%).
  * Inicio automático con Windows (con opción de autostart).
  * Minimizado inteligente a la bandeja del sistema (*System Tray*).

## 📥 Descarga rápida
Puedes descargar el ejecutable listo para usar sin necesidad de instalar Python en la sección de [Releases](../../releases/tag/v1.0.0).

## 🛠️ Tecnologías utilizadas
- Python 3
- CustomTkinter
- Pillow (PIL)
- WinRT (Windows Media Transport Controls)
