/** Tailwind v3 — compilado en el build de Docker (sin CDN runtime).
 *  Escanea static/index.html y genera exactamente las clases usadas,
 *  incluyendo valores arbitrarios (text-[10px], w-[110px]...) y dark:.
 *
 *  Marca Family Office GG (2026-11-07): mismos tonos, letra y radios que la
 *  plataforma de patrimonio. La escala `slate` se reemplaza por los grises
 *  fríos de patrimonio (así todo el sitio cambia sin tocar cada clase), los
 *  colores de tipo de gasto toman la paleta de patrimonio, solo Inter
 *  (600 es la única negrita) y radios 16 tarjeta / 10 interior.
 */
module.exports = {
  darkMode: 'class',
  content: ['./static/**/*.html'],
  theme: {
    extend: {
      colors: {
        // claro: 50–700 · oscuro: 800 (pista), 900 (tarjeta), 950 (fondo)
        slate: {
          50: '#f7f8fa', 100: '#f2f4f7', 200: '#e5e8ec', 300: '#d3d8de', 400: '#a3a9b1',
          500: '#6f7680', 600: '#5f6771', 700: '#3b424b', 800: '#22262e', 900: '#13161c', 950: '#0b0d11',
        },
        // verdes, rojos y ámbar genéricos de Tailwind → los de patrimonio (botones, avisos, badges)
        emerald: {
          50: '#ecf8f1', 100: '#e2f4ea', 200: '#bfe6cf', 300: '#7fe0ab', 400: '#4cc38a', 500: '#1baf7a',
          600: '#0b7f48', 700: '#096b3c', 800: '#0a5531', 900: '#15291f', 950: '#0d1c15',
        },
        red: {
          50: '#fdf2f0', 100: '#fbe4e0', 200: '#f6c3bb', 300: '#ff9c8e', 400: '#f07a6a', 500: '#e5675a',
          600: '#c63a2d', 700: '#a8301f', 800: '#7f2418', 900: '#3a1f1c', 950: '#2a1513',
        },
        amber: {
          50: '#fef8ea', 100: '#fdf1d3', 200: '#fbe3a6', 300: '#f5c75a', 400: '#fab219', 500: '#eda100',
          600: '#b57800', 700: '#9a6a00', 800: '#7a5300', 900: '#33280f', 950: '#241c0a',
        },
        papel: '#f5f6f8',
        tinta: '#14171c',
        esencial: { DEFAULT: '#3987e5', suave: '#e3eefb', fuerte: '#1d5fae' },
        estilo:   { DEFAULT: '#9085e9', suave: '#ece9fb', fuerte: '#5b48c9' },
        gusto:    { DEFAULT: '#eda100', suave: '#fdf1d3', fuerte: '#9a6a00' },
        alerta:   { DEFAULT: '#e5675a', suave: '#fbe4e0', fuerte: '#c63a2d' },
        ahorro:   { DEFAULT: '#0b7f48', suave: '#e2f4ea', medio: '#1baf7a' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        serif: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      fontWeight: {
        bold: '600',
        extrabold: '600',
      },
      borderRadius: {
        lg: '0.625rem',
        xl: '0.625rem',
        '2xl': '1rem',
        '3xl': '1.25rem',
      },
      // Tarjetas como en patrimonio: borde fino en vez de sombra grande
      // (en modo noche el borde lo pone index.html).
      boxShadow: {
        sm: '0 0 0 1px rgba(20, 23, 28, .06), 0 1px 2px rgba(20, 23, 28, .04)',
        card: '0 0 0 1px rgba(20, 23, 28, .07), 0 1px 3px rgba(20, 23, 28, .04)',
      },
    },
  },
};
