/** Tailwind v3 — compilado en el build de Docker (sin CDN runtime).
 *  Escanea static/index.html y genera exactamente las clases usadas,
 *  incluyendo valores arbitrarios (text-[10px], w-[110px]...) y dark:.
 *
 *  Rediseño 2026-11 ("claro y limpio"): la escala `slate` se reemplaza por
 *  tonos papel cálidos, así todo el sitio (secciones y ventanas) toma el look
 *  nuevo sin tocar cada clase. Inter para texto, Fraunces (font-serif) para
 *  los números grandes. Tarjetas más redondeadas con sombra suave.
 */
module.exports = {
  darkMode: 'class',
  content: ['./static/**/*.html'],
  theme: {
    extend: {
      colors: {
        slate: {
          50: '#faf9f6', 100: '#f3f1ec', 200: '#e8e4dc', 300: '#d7d1c6', 400: '#a9a297',
          500: '#7e786e', 600: '#605a52', 700: '#47433d', 800: '#2d2a26', 900: '#1e1c19', 950: '#141311',
        },
        papel: '#f6f5f2',
        tinta: '#1e1c19',
        esencial: { DEFAULT: '#6aa3cc', suave: '#e3eef6', fuerte: '#2d6187' },
        estilo:   { DEFAULT: '#a99bd6', suave: '#eeeaf8', fuerte: '#5b4b9a' },
        gusto:    { DEFAULT: '#e5a54b', suave: '#fbf0dc', fuerte: '#9a6413' },
        alerta:   { DEFAULT: '#e07a5f', suave: '#fbe9e3', fuerte: '#b0472c' },
        ahorro:   { DEFAULT: '#2f6b47', suave: '#e8f1ea', medio: '#3d7a55' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        serif: ['Fraunces', 'Georgia', 'serif'],
      },
      borderRadius: {
        '2xl': '1.25rem',
        '3xl': '1.5rem',
      },
      boxShadow: {
        sm: '0 1px 2px rgba(30, 28, 25, .04), 0 6px 20px rgba(30, 28, 25, .05)',
        card: '0 1px 2px rgba(30, 28, 25, .04), 0 8px 24px rgba(30, 28, 25, .06)',
      },
    },
  },
};
