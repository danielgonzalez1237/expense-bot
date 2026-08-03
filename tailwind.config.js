/** Tailwind v3 — compilado en el build de Docker (sin CDN runtime).
 *  Escanea static/index.html y genera exactamente las clases usadas,
 *  incluyendo valores arbitrarios (text-[10px], w-[110px]...) y dark:.
 */
module.exports = {
  darkMode: 'class',
  content: ['./static/**/*.html'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Inter', 'sans-serif'],
      },
    },
  },
};
